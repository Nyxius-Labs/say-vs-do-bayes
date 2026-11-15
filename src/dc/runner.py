from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .costs import CostCapExceeded, CostTracker, estimate_input_tokens, token_cost
from .prompts import build_messages, parse_response, prompt_hash
from .providers import ProviderError, call_with_retry
from .scenarios import Item, Scenario


class RateLimiter:
    def __init__(self, rpm: int, clock=time.monotonic, sleep=asyncio.sleep):
        self.rpm, self.clock, self.sleep = rpm, clock, sleep
        self.starts: deque = deque()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            while True:
                now = self.clock()
                while self.starts and now - self.starts[0] >= 60:
                    self.starts.popleft()
                if len(self.starts) < self.rpm:
                    self.starts.append(now)
                    return
                await self.sleep(60 - (now - self.starts[0]) + 0.01)


@dataclass
class Job:
    item: Item
    model_name: str
    model_id: str
    config: str
    route: str
    provider: object
    reasoning_label: str
    params: dict
    max_tokens: int
    temperature: float | None
    price_in: float
    price_out: float
    concurrency_key: str
    extra: dict = field(default_factory=dict)
    expected_host: str | None = None

    @property
    def key(self) -> str:
        return f"{self.model_name}|{self.config}|{self.item.item_id}|{self.item.draw}"


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


class Runner:
    def __init__(self, log_path: Path, tracker: CostTracker, scenarios: dict[str, Scenario], templates: dict,
                 concurrency: dict[str, int] | None = None, default_concurrency: int = 8, retry_kwargs=None,
                 limiter: RateLimiter | None = None, trackers: dict | None = None):
        self.log_path = Path(log_path)
        self.err_path = self.log_path.with_suffix(".errors.jsonl")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.tracker = tracker
        self.trackers = trackers or {}
        self.scenarios = scenarios
        self.templates = templates
        self.concurrency = concurrency or {}
        self.default_concurrency = default_concurrency
        self._sems: dict[str, asyncio.Semaphore] = {}
        self.retry_kwargs = retry_kwargs or {}
        self.limiter = limiter
        self.done = {r["key"] for r in read_jsonl(self.log_path)}

    def tracker_for(self, job: Job):
        return self.trackers.get(job.route, self.tracker)

    def _sem(self, key):
        if key not in self._sems:
            self._sems[key] = asyncio.Semaphore(self.concurrency.get(key, self.default_concurrency))
        return self._sems[key]

    def _append(self, path: Path, rec: dict):
        with open(path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()

    async def _one(self, job: Job):
        sc = self.scenarios[job.item.scenario_id]
        raws, attempts, rids, quirks, finishes = [], 0, [], [], []
        tot_in = tot_out = 0
        tot_rt = None
        cost = 0.0
        cost_sources = set()
        parsed = None
        latency = 0.0
        served = None
        upstream = []
        tracker = self.tracker_for(job)
        msgs0 = build_messages(job.item, sc, self.templates)
        async with self._sem(job.concurrency_key):
            for retry in (False, True):
                msgs = build_messages(job.item, sc, self.templates, parse_retry=retry)
                reserve = token_cost(estimate_input_tokens(msgs), job.max_tokens, job.price_in, job.price_out)
                try:
                    tok = tracker.reserve(reserve)
                except CostCapExceeded:
                    if attempts == 0:
                        raise
                    quirks.append("parse retry skipped: cost cap")
                    break
                if self.limiter is not None:
                    await self.limiter.acquire()
                try:
                    res = await call_with_retry(job.provider, job.model_id, msgs, job.params, job.max_tokens,
                                                job.temperature, **self.retry_kwargs)
                except ProviderError as e:
                    tracker.release(tok)
                    if attempts == 0:
                        self._append(self.err_path, {"key": job.key, "timestamp": _now(), "error": str(e)[:800],
                                                     "status": e.status, "route": job.route,
                                                     "permanent": not e.retryable})
                        return None
                    break
                attempts += 1
                if res.billed_cost_usd is not None:
                    c, src = res.billed_cost_usd, "billed"
                else:
                    c, src = token_cost(res.input_tokens, res.output_tokens, job.price_in, job.price_out), "computed"
                tracker.settle(tok, c)
                if hasattr(tracker, "after_call"):
                    await tracker.after_call()
                cost += c
                cost_sources.add(src)
                tot_in += res.input_tokens
                tot_out += res.output_tokens
                if res.reasoning_tokens is not None:
                    tot_rt = (tot_rt or 0) + res.reasoning_tokens
                latency += res.latency_s
                raws.append(res.text)
                finishes.append(res.finish_reason)
                rids.append(res.request_id)
                quirks += res.quirks
                served = res.served_model
                upstream.append(getattr(res, "upstream_provider", None))
                parsed = parse_response(res.text)
                if parsed is not None:
                    break
        mismatch = None
        if job.expected_host:
            other = sorted({u for u in upstream if u and u.lower() != job.expected_host.lower()})
            mismatch = bool(other)
            if other:
                quirks.append(f"served by {', '.join(other)} although pinned to {job.expected_host} (D12)")
        rec = {
            "key": job.key, "model_name": job.model_name, "model_id": job.model_id, "served_model": served,
            "route": job.route, "config": job.config, "timestamp": _now(),
            "reasoning_setting": {"label": job.reasoning_label, "params": job.params},
            "temperature": job.temperature, "max_tokens": job.max_tokens,
            "prompt_hash": prompt_hash(msgs0), "arm": job.item.arm, "scenario_id": job.item.scenario_id,
            "item_id": job.item.item_id,
            "cell": {"evidence": job.item.lam, "tone": job.item.tone, "draw": job.item.draw},
            "lam": job.item.lam, "tone": job.item.tone,
            "rating_order": "rating_first" if job.item.rating_first else "probability_first",
            "p0": float(sc.p0), "p_star": float(sc.p_star(job.item.lam)),
            "raw_response": raws, "attempts": attempts,
            "weight_rating": parsed and parsed["weight_rating"], "probability": parsed and parsed["probability"],
            "status": "ok" if parsed else "unparseable",
            "input_tokens": tot_in, "output_tokens": tot_out, "reasoning_tokens": tot_rt,
            "latency_s": round(latency, 3), "cost_usd": cost, "cost_source": "+".join(sorted(cost_sources)),
            "request_ids": rids, "upstream_providers": upstream, "pinned_host": job.expected_host,
            "host_mismatch": mismatch, "quirks": quirks, "finish_reasons": finishes,
            "truncated": parsed is None and any(f == "length" for f in finishes), **job.extra,
        }
        self._append(self.log_path, rec)
        self.done.add(job.key)
        return rec

    async def run(self, jobs: list[Job]) -> list[dict]:
        todo, seen = [], set()
        for j in jobs:
            if j.key in self.done or j.key in seen:
                continue
            seen.add(j.key)
            todo.append(j)
        results = await asyncio.gather(*(self._one(j) for j in todo), return_exceptions=True)
        cap = [r for r in results if isinstance(r, CostCapExceeded)]
        other = [r for r in results if isinstance(r, BaseException) and not isinstance(r, CostCapExceeded)]
        if other:
            raise other[0]
        if cap:
            raise cap[0]
        return [r for r in results if r is not None]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
