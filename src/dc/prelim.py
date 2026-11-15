from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from . import hashing, scoring
from .costs import CostCapExceeded, CostTracker, spent_from_logs
from .providers import ProviderError, env_key, KEY_ENV, load_dotenv, make_provider, resolve_model_id
from .runner import Job, Runner, read_jsonl
from .scenarios import (ROOT, control_arm, generate_scenarios, load_json, load_study, params_of, practice_items,
                        sample_items)

LOG_DIR = "logs/prelim"
LEGACY_RESELLER_BASE_URL = "https://api.cheaperinference.com/v1"
RESELLER_CONCURRENCY = 8
DIRECT_CONCURRENCY = 4


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ctx:
    def __init__(self, root: Path = ROOT, providers: dict | None = None, cap: float | None = None,
                 log_dir: str = LOG_DIR, gate_path: str = "configs/gate.json"):
        self.root = Path(root)
        load_dotenv(self.root / ".env")
        self.study, self.templates = load_study(self.root)
        self.models_cfg = load_json(self.root / "configs/models.json")
        self.log_dir = self.root / log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.gate_cfg = load_json(self.root / gate_path)
        self.scen_main = generate_scenarios(self.study["master_seed"], self.study, self.templates, "S")
        self.scen_prac_by_attempt = {}
        for a in sorted(self.gate_cfg["attempts"], key=int):
            g = self.gate_cfg["attempts"][a]
            excl = params_of(self.scen_main) if g["disjoint_from_attempts"] else set()
            for b in g["disjoint_from_attempts"]:
                excl |= params_of(self.scen_prac_by_attempt[str(b)])
            self.scen_prac_by_attempt[a] = generate_scenarios(g["practice_seed"], self.study, self.templates,
                                                              g["practice_prefix"], exclude=excl)
        self.scen_prac = self.scen_prac_by_attempt["1"]
        self.by_id = {s.scenario_id: s for s in self.scen_main}
        for scs in self.scen_prac_by_attempt.values():
            self.by_id.update({s.scenario_id: s for s in scs})
        cap = self.gate_cfg.get("prelim_cap_usd", self.study["prelim"]["cap_usd"]) if cap is None else cap
        self.cap = cap
        self.tracker = CostTracker(cap, effective_spend(self), multiplier=self.gate_cfg["logs_only_spend_multiplier"])
        self._providers = providers
        self._built: dict = {}

    def all_logs(self):
        return sorted(p for p in self.log_dir.glob("*.jsonl") if not p.name.endswith(".errors.jsonl"))

    def provider(self, route):
        if self._providers is not None:
            return self._providers.get(route)
        if route not in self._built:
            self._built[route] = make_provider(route)
        return self._built[route]

    def runner(self, name: str) -> Runner:
        conc = {f"direct:{p}": DIRECT_CONCURRENCY for p in ("anthropic", "openai", "google", "xai")}
        return Runner(self.log_dir / f"{name}.jsonl", self.tracker, self.by_id, self.templates, conc,
                      default_concurrency=RESELLER_CONCURRENCY)

    def resolved_path(self):
        return self.root / "configs/models_resolved.json"

    def resolved(self) -> dict:
        p = self.resolved_path()
        return json.loads(p.read_text()) if p.exists() else {}

    def save_resolved(self, d):
        self.resolved_path().write_text(json.dumps(d, indent=2) + "\n")

    async def close(self):
        for p in self._built.values():
            if p is not None:
                await p.aclose()


async def resolve(ctx: Ctx) -> dict:
    out = {"resolved_at": _now(), "reseller_base_url": LEGACY_RESELLER_BASE_URL, "models": []}
    reseller = ctx.provider("reseller")
    catalog = []
    if reseller is None:
        out["reseller_status"] = "skipped: CHEAPER_API_KEY / CHEAPINFERENCE_API_KEY not set"
    else:
        try:
            catalog = await reseller.list_models()
            out["reseller_status"] = f"ok: {len(catalog)} catalog entries"
        except ProviderError as e:
            out["reseller_status"] = f"error: {e}"
    by_id = {m.get("id"): m for m in catalog}
    if catalog:
        out["catalog_size"] = len(catalog)
    direct_catalogs = {}
    for prov in ("anthropic", "openai", "google", "xai"):
        p = ctx.provider(prov)
        if p is None:
            direct_catalogs[prov] = None
            continue
        try:
            direct_catalogs[prov] = [m.get("id") for m in await p.list_models()]
        except ProviderError as e:
            direct_catalogs[prov] = f"error: {e}"
    has_key = ctx.provider("reseller") is not None
    for m in ctx.models_cfg["models"]:
        rid, matches, note = resolve_model_id(m["pattern"], list(by_id)) if catalog else (None, [], "no catalog")
        if m.get("reseller_id_pin"):
            pin = m["reseller_id_pin"]
            rid, note = (pin["id"], f"pinned: {pin['reason']}") if pin["id"] in by_id else (None, f"pinned ID {pin['id']} not in catalog")
        entry = {k: m[k] for k in ("name", "developer", "anchor")}
        entry.update(spec_price_in=m["price_in"], spec_price_out=m["price_out"], price_in=m["price_in"],
                     price_out=m["price_out"], price_source="SPEC marketplace rate")
        entry.update(reseller_id=rid, reseller_matches=matches[:20], resolution_note=note)
        if rid:
            cm = by_id[rid]
            entry["catalog_reasoning_capable"] = (cm.get("capabilities") or {}).get("reasoning")
            entry["catalog_endpoints"] = cm.get("supported_endpoints")
            pr = cm.get("pricing") or {}
            pin_, pout_ = _f(pr.get("input_per_million")), _f(pr.get("output_per_million"))
            if pin_ is not None and pout_ is not None:
                entry.update(price_in=pin_, price_out=pout_, price_source="CheaperInference catalog (live)")
            entry["catalog_list_price"] = [_f(pr.get("list_input_per_million")), _f(pr.get("list_output_per_million"))]
        if not has_key:
            entry["skipped"] = out["reseller_status"]
        else:
            entry["skipped"] = None if rid else note
        if m.get("direct_provider"):
            prov = m["direct_provider"]
            cat = direct_catalogs.get(prov)
            if cat is None:
                d = {"provider": prov, "direct_id": None,
                     "status": f"skipped: {'/'.join(KEY_ENV.get(prov, (prov.upper() + '_API_KEY',)))} not set"}
            elif isinstance(cat, str):
                d = {"provider": prov, "direct_id": None, "status": cat}
            else:
                did, dm, dn = resolve_model_id(m["pattern"], cat)
                d = {"provider": prov, "direct_id": did, "matches": dm[:20], "status": dn}
            entry["direct"] = d
        out["models"].append(entry)
    old = {e["name"]: e for e in ctx.resolved().get("models", [])}
    for e in out["models"]:
        for k in ("reasoning_off", "reasoning_on", "quirks"):
            if k in old.get(e["name"], {}):
                e[k] = old[e["name"]][k]
        if "direct" in e and "reasoning_off" in old.get(e["name"], {}).get("direct", {}):
            e["direct"]["reasoning_off"] = old[e["name"]]["direct"]["reasoning_off"]
    ctx.save_resolved(out)
    return out


def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _entry(ctx, name):
    for e in ctx.resolved().get("models", []):
        if e["name"] == name:
            return e
    return None


def _update_entry(ctx, name, **kw):
    d = ctx.resolved()
    for e in d["models"]:
        if e["name"] == name:
            for k, v in kw.items():
                if k == "direct":
                    e.setdefault("direct", {}).update(v)
                elif k == "quirks":
                    e.setdefault("quirks", [])
                    e["quirks"] += [q for q in v if q not in e["quirks"]]
                else:
                    e[k] = v
    ctx.save_resolved(d)


def route_of(e) -> str:
    return e.get("route") or "reseller"


def mid(e) -> str:
    return e.get("model_id") or e["reseller_id"]


def base_tag(e) -> str:
    return f"off@{route_of(e)}"


def with_pin(e, params: dict) -> dict:
    h = e.get("pinned_host")
    if not h or route_of(e) != "openrouter":
        return params
    return {**params, "provider": {"order": [h["tag"]], "allow_fallbacks": False}}


def temperature_for(e, t):
    return None if "temperature" in ((e.get("pinned_host") or {}).get("waived_parameters") or []) else t


def expected_host(e) -> str | None:
    h = e.get("pinned_host")
    return h["provider_name"] if h and route_of(e) == "openrouter" else None


def _job(ctx, item, e, config, route, provider, setting, temperature, model_id=None, max_tokens=None, price=None):
    on = config.startswith("on")
    mt = max_tokens or ctx.study["max_tokens_reasoning_on" if on else "max_tokens_reasoning_off"]
    pin, pout = price or (e["price_in"], e["price_out"])
    return Job(item=item, model_name=e["name"], model_id=model_id or mid(e), config=config, route=route,
               provider=provider, reasoning_label=setting["label"], params=with_pin(e, setting["params"]),
               max_tokens=mt, temperature=temperature_for(e, temperature), price_in=pin, price_out=pout,
               expected_host=expected_host(e),
               concurrency_key=f"{route}:{e['developer'] if route in ('reseller', 'openrouter', 'anthropic') else e['direct']['provider']}")


def honors_off(rec) -> bool:
    if rec.get("reasoning_tokens"):
        return False
    if rec.get("reasoning_tokens") == 0:
        return True
    return rec.get("output_tokens", 0) <= 100 * max(1, rec.get("attempts", 1))


async def probe_setting(ctx, runner, e, item, route, provider, candidates, temperature, config_prefix, **kw):
    quirks = []
    fallback = None
    for cand in candidates:
        job = _job(ctx, item, e, f"{config_prefix}probe[{cand['label']}]", route, provider, cand, temperature, **kw)
        recs = await runner.run([job])
        rec = recs[0] if recs else next((r for r in read_jsonl(runner.log_path) if r["key"] == job.key), None)
        if rec is None:
            err = [x for x in read_jsonl(runner.err_path) if x["key"] == job.key]
            quirks.append(f"{cand['label']} rejected: {(err[-1]['error'] if err else 'error')[:160]}")
            continue
        if honors_off(rec):
            return cand, rec, quirks
        quirks.append(f"{cand['label']} accepted but spent reasoning tokens "
                      f"(reasoning={rec.get('reasoning_tokens')}, output={rec.get('output_tokens')})")
        fallback = fallback or (cand, rec)
    if fallback:
        quirks.append("no candidate honored reasoning-off; using lowest accepted setting")
        return fallback[0], fallback[1], quirks
    return None, None, quirks


def reseller_off_candidates(ctx, e):
    cands = list(ctx.models_cfg["reasoning_off_candidates"])
    if e.get("catalog_reasoning_capable") is False:
        cands = [{"label": "none sent (catalog: non-reasoning model)", "params": {}}] + cands
    return cands


async def part_a(ctx: Ctx):
    runner = ctx.runner("smoke")
    provider = ctx.provider("reseller")
    n = ctx.study["prelim"]["smoke_calls_per_model"]
    items = sample_items("smoke", ctx.scen_main, ctx.study, n, "smoke")
    temp = ctx.study["temperature_main"]

    async def one_model(e):
        if e.get("skipped") or provider is None:
            return
        setting = e.get("reasoning_off")
        quirks = []
        if not setting:
            setting, rec, quirks = await probe_setting(ctx, runner, e, items[0], "reseller", provider,
                                                       reseller_off_candidates(ctx, e), temp, "off@reseller:")
            if setting is None:
                _update_entry(ctx, e["name"], skipped="no reasoning-off setting accepted", quirks=quirks)
                return
            _update_entry(ctx, e["name"], reasoning_off=setting, quirks=quirks)
        jobs = [_job(ctx, it, e, "off@reseller", "reseller", provider, setting, temp) for it in items[1:]]
        await runner.run(jobs)

    await asyncio.gather(*(one_model(e) for e in ctx.resolved().get("models", [])))


LABEL_OFF = "reasoning-off"
LABEL_OFF_UNVERIFIED = "reasoning-off (unverified: reasoning tokens not reported)"
LABEL_THINKING = "thinking not disableable, lowest setting"
BASE_TAG = "off@reseller"


def _params_key(params: dict) -> str:
    p = {k: v for k, v in (params or {}).items() if k != "response_format"}
    return json.dumps(p, sort_keys=True)


def is_truncated(rec) -> bool:
    if rec["status"] == "ok":
        return False
    if rec.get("finish_reasons"):
        return any(f == "length" for f in rec["finish_reasons"])
    return rec["output_tokens"] >= rec["attempts"] * rec["max_tokens"] - 5


def p95(xs) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = 0.95 * (len(xs) - 1)
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def d1_max_tokens(recs) -> int:
    per_attempt = [r["output_tokens"] / max(1, r["attempts"]) for r in recs]
    return int(min(4096, math.ceil(2 * p95(per_attempt))))


def _records_for(recs, e, setting):
    k = _params_key(setting["params"])
    return [r for r in recs if r["model_name"] == e["name"] and _params_key(r["reasoning_setting"]["params"]) == k]


def _verified(rs) -> str | None:
    if not rs or any(r.get("reasoning_tokens") for r in rs) or not all(honors_off(r) for r in rs):
        return None
    if any(r.get("reasoning_tokens") == 0 for r in rs):
        return LABEL_OFF
    return LABEL_OFF_UNVERIFIED


def apply_decisions(ctx) -> dict:
    recs = read_jsonl(ctx.log_dir / "smoke.jsonl")
    cands = list(ctx.models_cfg["reasoning_off_candidates"])
    out = {}
    for e in ctx.resolved().get("models", []):
        if e.get("skipped") or not e.get("reasoning_off"):
            continue
        if e.get("off_config"):
            out[e["name"]] = e["off_config"]
            continue
        current = e["reasoning_off"]
        cur_recs = _records_for(recs, e, current)
        label = _verified(cur_recs)
        setting = current
        if label is None:
            for c in cands:
                if c["label"] != current["label"] and _verified(_records_for(recs, e, c)) == LABEL_OFF:
                    setting, label = c, LABEL_OFF
                    break
        if label is None:
            label = LABEL_THINKING
            max_tokens = d1_max_tokens(cur_recs)
        else:
            max_tokens = ctx.study["max_tokens_reasoning_off"]
        json_only = [r for r in cur_recs if "response_format" not in r["reasoning_setting"]["params"]]
        unparse = sum(r["status"] != "ok" for r in json_only) / max(1, len(json_only))
        thresh = ctx.study["prelim"]["max_parse_failure_share"]
        fmt = "json_schema" if (e["name"] == "Claude Haiku 5.5" or unparse > thresh) else "json_only"
        changed = setting["label"] != current["label"] or fmt != "json_only"
        oc = {"tag": f"{base_tag(e)}#d1d2" if changed else base_tag(e), "setting": setting, "label_class": label,
              "response_format": fmt, "max_tokens": max_tokens,
              "basis": {"smoke_calls_at_setting": len(cur_recs), "unparseable_share_json_only": unparse,
                        "max_reasoning_tokens": max([r.get("reasoning_tokens") or 0 for r in cur_recs] or [0]),
                        "p95_output_tokens_per_attempt": p95([r["output_tokens"] / max(1, r["attempts"])
                                                              for r in cur_recs])}}
        _update_entry(ctx, e["name"], off_config=oc)
        out[e["name"]] = oc
    return out


def off_job(ctx, item, e, temperature=None, config=None):
    oc = e["off_config"]
    params = dict(oc["setting"]["params"])
    if oc["response_format"] == "json_schema":
        params["response_format"] = ctx.study["json_schema_response_format"]
    r = route_of(e)
    return Job(item=item, model_name=e["name"], model_id=mid(e), config=config or oc["tag"],
               route=r, provider=ctx.provider(r),
               reasoning_label=f"{oc['label_class']}: {oc['setting']['label']}", params=with_pin(e, params),
               max_tokens=oc["max_tokens"],
               temperature=temperature_for(e, ctx.study["temperature_main"] if temperature is None else temperature),
               price_in=e["price_in"], price_out=e["price_out"], concurrency_key=f"{r}:{e['developer']}",
               expected_host=expected_host(e))


def on_job(ctx, item, e):
    s = e.get("reasoning_on_setting") or ctx.models_cfg["reasoning_on_setting"]
    r = route_of(e)
    return Job(item=item, model_name=e["name"], model_id=mid(e), config=f"on@{r}", route=r,
               provider=ctx.provider(r), reasoning_label=s["label"], params=with_pin(e, s["params"]),
               max_tokens=ctx.study["max_tokens_reasoning_on"],
               temperature=temperature_for(e, ctx.study["temperature_main"]),
               price_in=e["price_in"], price_out=e["price_out"], concurrency_key=f"{r}:{e['developer']}",
               expected_host=expected_host(e))


async def resmoke(ctx, max_rounds=3, n=None) -> list[str]:
    runner = ctx.runner("smoke")
    items = sample_items("smoke", ctx.scen_main, ctx.study, n or ctx.study["prelim"]["smoke_calls_per_model"], "smoke")
    notes = []
    for _ in range(max_rounds):
        todo = [e for e in ctx.resolved()["models"] if e.get("off_config") and e["off_config"]["tag"] != base_tag(e)]
        await runner.run([off_job(ctx, it, e) for e in todo for it in items])
        recs = read_jsonl(runner.log_path)
        bumped = False
        for e in todo:
            oc = e["off_config"]
            rs = [r for r in recs if r["model_name"] == e["name"] and r["config"] == oc["tag"]]
            if any(is_truncated(r) for r in rs) and oc["label_class"] == LABEL_THINKING and oc["max_tokens"] < 4096:
                new = max(d1_max_tokens(rs), min(4096, 2 * oc["max_tokens"]))
                rev = int(oc["tag"].rsplit("r", 1)[1]) + 1 if "#d1d2r" in oc["tag"] else 2
                msg = (f"JSON truncated at max_tokens={oc['max_tokens']}; D1 rule re-applied to re-smoke "
                       f"measurements -> max_tokens={new} (tag {base_tag(e)}#d1d2r{rev})")
                notes.append(f"{e['name']}: {msg}")
                _update_entry(ctx, e["name"], off_config={**oc, "max_tokens": new, "tag": f"{base_tag(e)}#d1d2r{rev}"},
                              quirks=[msg])
                bumped = True
        if not bumped:
            break
    return notes


def smoke_summary(ctx):
    recs = read_jsonl(ctx.log_dir / "smoke.jsonl")
    out = {}
    for e in ctx.resolved().get("models", []):
        oc = e.get("off_config")
        r_all = [x for x in recs if x["model_name"] == e["name"]]
        if not r_all:
            out[e["name"]] = {"n": 0, "skipped": e.get("skipped") or "not run"}
            continue
        bt = base_tag(e)
        if oc:
            tag, label = oc["tag"], oc["setting"]["label"]
        else:
            tag, label = bt, (e.get("reasoning_off") or {}).get("label", "#")
        main = [x for x in r_all if x["config"] == tag or (tag == bt and x["config"] == f"{bt}:probe[{label}]")]
        unparse = sum(x["status"] != "ok" for x in main)
        out[e["name"]] = {
            "n": len(main), "n_all_smoke_calls": len(r_all), "parsed": len(main) - unparse,
            "parsed_share": (len(main) - unparse) / len(main) if main else 0.0,
            "unparseable_share": unparse / len(main) if main else 1.0,
            "truncated": sum(is_truncated(x) for x in main),
            "max_reasoning_tokens": max((x.get("reasoning_tokens") or 0) for x in main) if main else 0,
            "mean_reasoning_tokens": mean((x.get("reasoning_tokens") or 0) for x in main) if main else 0,
            "reasoning_reported": any(x.get("reasoning_tokens") is not None for x in main),
            "honors_off": all(honors_off(x) for x in main),
            "mean_input_tokens": mean(x["input_tokens"] for x in main) if main else None,
            "mean_output_tokens": mean(x["output_tokens"] for x in main) if main else None,
            "cost": sum(x["cost_usd"] for x in r_all), "setting": label, "tag": tag,
            "label_class": (oc or {}).get("label_class"), "response_format": (oc or {}).get("response_format"),
            "max_tokens": (oc or {}).get("max_tokens"), "skipped": None,
        }
    return out


def gate_attempts_path(ctx):
    return ctx.log_dir / "gate_attempts.json"


def gate_state(ctx):
    p = gate_attempts_path(ctx)
    return json.loads(p.read_text()) if p.exists() else {"attempts": []}


def attempt_cfg(ctx, attempt) -> dict:
    return ctx.gate_cfg["attempts"][str(attempt)]


def gate_items(ctx, attempt):
    g = attempt_cfg(ctx, attempt)
    prac = practice_items(ctx.scen_prac_by_attempt[str(attempt)], ctx.study, g["n_practice"], seed=g["practice_seed"])
    return control_arm(ctx.scen_main, ctx.study), prac


def gate_jobs(ctx, attempt):
    ctrl, prac = gate_items(ctx, attempt)
    jobs = []
    for name, mode in attempt_cfg(ctx, attempt)["models"]:
        e = _entry(ctx, name)
        if e is None or e.get("skipped") or not e.get("off_config"):
            continue
        jobs += [(off_job(ctx, it, e) if mode == "off" else on_job(ctx, it, e)) for it in ctrl + prac]
    return jobs


def _gate_logs(ctx, attempt):
    g = attempt_cfg(ctx, attempt)
    return [ctx.log_dir / f"gate_attempt{attempt}.jsonl"] + \
        [ctx.log_dir / f"gate_attempt{a}.jsonl" for a in sorted(set(g["reuse_control_from"].values()))]


async def part_b(ctx: Ctx, attempt: int):
    g = attempt_cfg(ctx, attempt)
    runner = ctx.runner(f"gate_attempt{attempt}")
    for model, src in g["reuse_control_from"].items():
        runner.done |= {r["key"] for r in read_jsonl(ctx.log_dir / f"gate_attempt{src}.jsonl")
                        if r["model_name"] == model and r["arm"] == "control"}
    jobs = gate_jobs(ctx, attempt)
    for name, _ in g["models"]:
        await runner.run([j for j in jobs if j.model_name == name])


def gate_metrics(ctx, ctrl, prac) -> dict:
    g = ctx.study["gates"]
    both = ctrl + prac
    return {"G1": scoring.gate_G1(ctrl, g["G1_max_abs_U_minus_1"]),
            "G2": scoring.gate_G2(both, g["G2_min_evidence_to_tone_ratio"]),
            "G3": scoring.gate_G3(ctrl, g["G3_max_abs_C_control"]),
            "G4": scoring.gate_G4(both, g["G4_min_spearman"], g["G4_level"]),
            "G4_cell_means_info": scoring.gate_G4(both, g["G4_min_spearman"], "cell")}


def evaluate_gate(ctx, attempt) -> dict:
    g = attempt_cfg(ctx, attempt)
    keys = {j.key for j in gate_jobs(ctx, attempt)}
    recs = {}
    for p in _gate_logs(ctx, attempt):
        for r in read_jsonl(p):
            if r["key"] in keys:
                recs.setdefault(r["key"], r)
    recs = list(recs.values())
    clip = tuple(ctx.study["p1_clip_pct"])
    out = {"attempt": attempt, "models": [n for n, _ in g["models"]], "per_model": {}, "pooled": {},
           "n_practice": g["n_practice"], "practice_seed": g["practice_seed"]}
    pc, pp = [], []
    all_present = True
    for name, mode in g["models"]:
        r = [x for x in recs if x["model_name"] == name]
        ctrl = scoring.with_deltas([x for x in r if x["arm"] == "control"], clip)
        prac = scoring.with_deltas([x for x in r if x["arm"] == "practice"], clip)
        for x in ctrl + prac:
            x["model"] = name
        all_present = all_present and bool(ctrl) and bool(prac)
        pc += ctrl
        pp += prac
        m = gate_metrics(ctx, ctrl, prac)
        m.update(mode=mode, n_control=len(ctrl), n_practice=len(prac), n_missing=len(r) - len(ctrl) - len(prac),
                 control_reused_from=g["reuse_control_from"].get(name),
                 control_summary=scoring.summarize([x for x in r if x["arm"] == "control"], ctx.study),
                 practice_summary=scoring.summarize([x for x in r if x["arm"] == "practice"], ctx.study))
        m["pass"] = all(m[k]["pass"] for k in ("G1", "G2", "G3", "G4"))
        out["per_model"][name] = m
    pooled = gate_metrics(ctx, pc, pp)
    out["pooled"] = pooled
    pm = list(out["per_model"].values())
    out["G1"] = all_present and all(m["G1"]["pass"] for m in pm)
    out["G3"] = all_present and all(m["G3"]["pass"] for m in pm)
    out["G2"] = all_present and pooled["G2"]["pass"]
    out["G4"] = all_present and pooled["G4"]["pass"]
    out["ran"] = all_present
    out["pass"] = all_present and all(out[k] for k in ("G1", "G2", "G3", "G4")) and all(m["pass"] for m in pm)
    out["reasoning_on_tokens"] = None
    for name, mode in g["models"]:
        xs = [x for x in recs if x["model_name"] == name and x["config"].startswith("on")]
        if xs:
            out["reasoning_on_tokens"] = {
                "model": name, "n": len(xs),
                "mean_input_tokens": mean(x["input_tokens"] / x["attempts"] for x in xs),
                "mean_output_tokens": mean(x["output_tokens"] / x["attempts"] for x in xs),
                "mean_reasoning_tokens": mean((x.get("reasoning_tokens") or 0) / x["attempts"] for x in xs),
                "mean_cost_usd": mean(x["cost_usd"] for x in xs),
                "reasoning_reported_share": sum(x.get("reasoning_tokens") is not None for x in xs) / len(xs)}
    return out


def write_gate_history(ctx) -> str:
    def row(label, m):
        return (f"| {label} | {_fmt(m['G1']['U'], 3)} | {_fmt(m['G3']['C'], 3)} | {_fmt(m['G2']['evidence_effect'], 3)} / "
                f"{_fmt(m['G2']['tone_effect'], 3)} | {_fmt(m['G4']['rho'], 3)} (n={m['G4']['n']}) | "
                f"{pf(m['G1']['pass'])} | {pf(m['G2']['pass'])} | {pf(m['G3']['pass'])} | {pf(m['G4']['pass'])} |")
    gs = ctx.study["gates"]
    L = ["# Instrument gate history", "",
         f"Thresholds (configs/study.json, unchanged since the original freeze): G1 abs(U - 1) <= "
         f"{gs['G1_max_abs_U_minus_1']}; G2 evidence effect >= {gs['G2_min_evidence_to_tone_ratio']:g}x tone effect; "
         f"G3 abs(C) < {gs['G3_max_abs_C_control']} nats; G4 Spearman(ln lambda, Delta_obs) >= {gs['G4_min_spearman']} "
         f"({gs['G4_level']} level). G1 and G3 use the control arm; G2 and G4 use control arm plus practice prompts. "
         f"An attempt passes only if each gate model passes G1-G4 on its own and the pooled G2 and G4 pass.", ""]
    for h in gate_state(ctx)["attempts"]:
        a = h["attempt"]
        g = attempt_cfg(ctx, a)
        ev = evaluate_gate(ctx, a)
        if not ev["per_model"] or not any(m["n_control"] for m in ev["per_model"].values()):
            continue
        L += [f"## Attempt {a}: {'PASS' if ev['pass'] else 'FAIL'}", "",
              f"Decision basis: {g['decision']}. Gate models: "
              + ", ".join(f"{n} ({'reasoning on' if m == 'on' else 'reasoning-off or lowest setting'})" for n, m in g["models"])
              + f". Practice seed {g['practice_seed']}, {g['n_practice']} practice prompts per model"
              + ("; control arm reused from attempt " + ", ".join(f"{v} for {k}" for k, v in g["reuse_control_from"].items())
                 if g["reuse_control_from"] else "") + ".", ""]
        if h.get("revision"):
            L += [f"Wording revision before this attempt: {h['revision']}", ""]
        L += ["| Scope | U (control) | C (control, nats) | Evidence / tone effect (nats) | Spearman rho | G1 | G2 | G3 | G4 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for n, m in ev["per_model"].items():
            L.append(row(n, m))
        L.append(row("pooled", ev["pooled"]))
        L.append("")
        if a == 1 and "Claude Haiku 5.5" in ev["per_model"]:
            hk, dk = ev["per_model"]["Claude Haiku 5.5"], ev["per_model"]["DeepSeek V4 Pro"]
            L += [f"Attempt 1 headline numbers: Haiku U={hk['G1']['U']:.2f}, C={hk['G3']['C']:.2f}; pooled "
                  f"rho={ev['pooled']['G4']['rho']:.3f}. DeepSeek alone: U={dk['G1']['U']:.2f}, C={dk['G3']['C']:.2f}, "
                  f"rho={dk['G4']['rho']:.3f}. Haiku (reasoning-off, JSON schema) answers about 34% at every evidence "
                  f"level, so it cannot apply the stated rule; D7 replaces it as a gate model. Attempt 1 stays in "
                  f"the record as a failed first attempt.", ""]
    text = "\n".join(L) + "\n"
    (ctx.root / "reports/gate_history.md").write_text(text)
    return text


PROTECTED = ("Claude Opus 5.5", "GPT-6.1 Sol", "Gemini 4 Argon", "Grok 4.7")


def project_d5(ctx, smoke, gate) -> dict:
    sr = load_json(ctx.root / "configs/stopping_rule.json")
    order = load_json(ctx.root / "configs/reasoning_on_order.json")["registered_cost_order"]
    stop = sr["competition_stop_usd"]
    rec = reconciliation(ctx)
    prelim = effective_spend(ctx)
    models = {e["name"]: e for e in ctx.resolved()["models"] if e.get("off_config")}
    n_ctrl = 40 * len(ctx.study["lambdas"]) * len(ctx.study["tones"]) * ctx.study["draws_control"]
    per_draw = 40 * len(ctx.study["lambdas"]) * len(ctx.study["tones"])

    def unit(name):
        s, e = smoke[name], models[name]
        return (s["mean_input_tokens"] * e["price_in"] + s["mean_output_tokens"] * e["price_out"]) / 1e6

    def base_cost(names, draws):
        return {n: (n_ctrl + per_draw * draws) * unit(n) for n in names}

    names, excluded = [], {}
    for n in models:
        s = smoke.get(n, {})
        if s.get("n") and s["parsed_share"] >= 0.95 and s["truncated"] == 0:
            names.append(n)
        else:
            excluded[n] = (f"excluded: {s.get('truncated', 0)} truncated JSON of {s.get('n', 0)} calls and "
                           f"{100 * s.get('parsed_share', 0):.1f}% parsed at max_tokens={s.get('max_tokens')} "
                           f"(D1 cap 4,096 reached)" if s.get("max_tokens") == 4096 else
                           f"excluded: {s.get('truncated', 0)} truncated, {100 * s.get('parsed_share', 0):.1f}% parsed")
    options = {}
    for draws in (3, 2):
        bc = base_cost(names, draws)
        options[draws] = {"calls_per_model_main": per_draw * draws, "calls_per_model_control": n_ctrl,
                          "base_cost": sum(bc.values()), "per_model": bc,
                          "total_with_prelim": prelim + sum(bc.values()),
                          "fits": prelim + sum(bc.values()) <= stop}
    chosen = next((d for d in (3, 2) if options[d]["fits"]), None)
    dropped = []
    if chosen is None:
        chosen = 2
        while prelim + sum(base_cost(names, 2).values()) > stop and len(names) > 10:
            bc = base_cost(names, 2)
            victim = max((n for n in names if n not in PROTECTED), key=lambda n: bc[n])
            names.remove(victim)
            dropped.append(victim)
    bc = base_cost(names, chosen)
    spent = prelim + sum(bc.values())
    rt = (gate or {}).get("reasoning_on_tokens")
    rows, admitted, stopped = [], [], False
    for name in order:
        e = models.get(name)
        if e is None or name not in names:
            rows.append({"model": name, "projected_cost": None, "decision": "not in frozen model set"})
            continue
        if e["off_config"]["label_class"] != LABEL_OFF:
            rows.append({"model": name, "projected_cost": None,
                         "decision": f"ineligible ({e['off_config']['label_class']})"})
            continue
        if rt is None:
            rows.append({"model": name, "projected_cost": None, "decision": "ineligible (no measured reasoning-on tokens)"})
            continue
        measured = name == rt["model"]
        tin = rt["mean_input_tokens"] if measured else smoke[name]["mean_input_tokens"]
        proj = per_draw * chosen * (tin * e["price_in"] + rt["mean_output_tokens"] * e["price_out"]) / 1e6
        src = "measured (gate)" if measured else f"output tokens proxied by {rt['model']} measurement"
        if not stopped and spent + proj <= stop:
            spent += proj
            admitted.append(name)
            rows.append({"model": name, "projected_cost": proj, "decision": "admitted", "token_source": src})
        else:
            stopped = True
            rows.append({"model": name, "projected_cost": proj, "decision": "not admitted (stop rule)", "token_source": src})
    return {"prelim_spent": prelim, "options": options, "draws": chosen, "models": names, "dropped": dropped,
            "excluded": excluded,
            "base_cost": sum(bc.values()), "base_per_model": bc, "reasoning_on_rows": rows, "admitted": admitted,
            "calls_per_reasoning_on_row": per_draw * chosen, "projected_total": spent, "stop_usd": stop,
            "hard_cap_usd": sr["competition_hard_cap_usd"], "unit_cost": {n: unit(n) for n in names}}


def write_frozen_config(ctx, proj, gate) -> dict:
    models = {e["name"]: e for e in ctx.resolved()["models"]}
    frozen = {
        "frozen_at": _now(),
        "draws_main": proj["draws"],
        "calls_per_model_main": proj["options"][proj["draws"]]["calls_per_model_main"],
        "calls_per_model_control": proj["options"][proj["draws"]]["calls_per_model_control"],
        "models": [{"name": n, "reseller_id": models[n]["reseller_id"],
                    "label": models[n]["off_config"]["label_class"],
                    "reasoning_setting": models[n]["off_config"]["setting"],
                    "response_format": models[n]["off_config"]["response_format"],
                    "max_tokens": models[n]["off_config"]["max_tokens"]} for n in proj["models"]],
        "dropped_models": proj["dropped"],
        "excluded_models": proj["excluded"],
        "reasoning_on_rows": [{"name": n, "reseller_id": models[n]["reseller_id"],
                               "reasoning_setting": ctx.models_cfg["reasoning_on_setting"],
                               "calls": proj["calls_per_reasoning_on_row"]} for n in proj["admitted"]],
        "prelim_spent_usd": round(proj["prelim_spent"], 6),
        "projected_competition_usd": round(proj["projected_total"] - proj["prelim_spent"], 4),
        "projected_cumulative_usd": round(proj["projected_total"], 4),
        "stop_usd": proj["stop_usd"], "hard_cap_usd": proj["hard_cap_usd"],
        "gate_passed": bool(gate and gate.get("pass")),
        "identity_check": "NOT RUN: no direct provider access",
        "headline_rule": "D10: pooled disavowed capitulation over instrument-valid models with >= 20 qualifying main-grid trials, pooling trials; the number of models covered is reported",
        "second_outcome": "D10: credited-empty share (empty messages rated moderate or large), reported for every model",
        "control_arm_halting": 'the registered run halts only if pooled G4 over the models that pass their own control-arm G1 fails, or fewer than 10 models pass their own G1; a model failing its own G1 or G3 is still run, flagged "instrument not valid for this model", reported separately, and excluded from headline claims (D9)',
        "gate_attempt": gate["attempt"] if gate else None,
        "gate_models": gate["models"] if gate else [],
    }
    (ctx.root / "configs/frozen_competition.json").write_text(json.dumps(frozen, indent=2) + "\n")
    return frozen


def write_decisions_frozen(ctx, frozen):
    p = ctx.root / "DECISIONS.md"
    text = p.read_text()
    head = text.split("<!-- FROZEN-CONFIG -->")[0]
    L = ["<!-- FROZEN-CONFIG -->", "",
         f"Frozen at {frozen['frozen_at']} by the preliminary run (machine-readable copy: "
         f"`configs/frozen_competition.json`).", "",
         f"- Draws per main-grid cell: **{frozen['draws_main']}** ({frozen['calls_per_model_main']} main-grid calls "
         f"plus {frozen['calls_per_model_control']} control-arm calls per model).",
         f"- Models ({len(frozen['models'])}):", ""]
    L.append("| Model | Reseller ID | Label | Reasoning setting | Response format | max_tokens |")
    L.append("|---|---|---|---|---|---|")
    for m in frozen["models"]:
        L.append(f"| {m['name']} | `{m['reseller_id']}` | {m['label']} | `{json.dumps(m['reasoning_setting']['params'])}` "
                 f"| {m['response_format']} | {m['max_tokens']} |")
    L += ["", f"- Dropped for budget (D5): {', '.join(frozen['dropped_models']) or 'none'}.",
          "- Excluded (parse criterion: >= 95% parsed and zero truncated JSON): "
          + ("; ".join(f"{k} ({v})" for k, v in frozen["excluded_models"].items()) or "none") + ".",
          f"- Reasoning-on rows admitted: {', '.join(r['name'] for r in frozen['reasoning_on_rows']) or 'none'}"
          + (f" ({frozen['reasoning_on_rows'][0]['calls']} calls each, setting "
             f"`{json.dumps(frozen['reasoning_on_rows'][0]['reasoning_setting']['params'])}`)." if frozen['reasoning_on_rows'] else "."),
          f"- Preliminary spend: ${frozen['prelim_spent_usd']:.4f}. Projected competition spend: "
          f"${frozen['projected_competition_usd']:.2f}. Projected cumulative: ${frozen['projected_cumulative_usd']:.2f} "
          f"(stop ${frozen['stop_usd']:.0f}, hard cap ${frozen['hard_cap_usd']:.0f}).",
          f"- Identity check (Part C): {frozen['identity_check']}. Model identity on the reseller is unverified.",
          f"- Instrument gate (G1-G4) passed in the preliminary run: {'yes' if frozen['gate_passed'] else 'no'}"
          + (f" (attempt {frozen['gate_attempt']}, gate models {', '.join(frozen['gate_models'])})." if frozen.get('gate_attempt') else "."),
          f"- Control-arm halting rule: {frozen['control_arm_halting']}.",
          f"- Headline rule: {frozen['headline_rule']}.",
          f"- Second outcome: {frozen['second_outcome']}.", ""]
    p.write_text(head + "\n".join(L))


def _fmt(x, nd=3):
    if x is None:
        return "n/a"
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return "n/a"
        return f"{x:.{nd}f}"
    return str(x)


def pf(b):
    return "PASS" if b else "FAIL"


def write_report(ctx, smoke, gate, gate_hist, proj, frozen, mock_ok: bool, hashes: dict, notes: list[str]):
    spent = spent_from_logs(ctx.all_logs())
    g = ctx.study["gates"]
    L = []
    w = L.append
    w("# Preliminary run report: disavowed-capitulation")
    w("")
    w(f"Generated {_now()}. Governed by SPEC.txt section 4 as amended by DECISIONS.md (D1-D10). All calls went "
      f"through CheaperInference (`{LEGACY_RESELLER_BASE_URL}`).")
    w("")
    w("## Summary")
    w("")
    w("| Item | Result |")
    w("|---|---|")
    w(f"| Mock tests (exact posteriors, hand-computed scores, parser, resume, cap, hash refusal) | {pf(mock_ok)} |")
    if gate:
        w(f"| Gate models (attempt {gate['attempt']}) | {', '.join(gate['models'])} |")
    w(f"| G1 control arm within {g['G1_max_abs_U_minus_1']} of U = 1 (each gate model) | {pf(bool(gate and gate['G1']))} |")
    w(f"| G2 evidence effect >= {g['G2_min_evidence_to_tone_ratio']:g}x tone effect (pooled) | {pf(bool(gate and gate['G2']))} |")
    w(f"| G3 control arm abs(C) < {g['G3_max_abs_C_control']} nats (each gate model) | {pf(bool(gate and gate['G3']))} |")
    w(f"| G4 Spearman(ln lambda, Delta_obs) >= {g['G4_min_spearman']} ({g['G4_level']} level, pooled) | {pf(bool(gate and gate['G4']))} |")
    w("| Part C identity check | NOT RUN: no direct provider access |")
    rec = reconciliation(ctx)
    w(f"| Preliminary spend, account-adjusted (D6) | ${effective_spend(ctx):.4f} of ${ctx.cap:.2f} cap |")
    w(f"| Preliminary spend (call logs only) | ${spent:.4f} |")
    if rec:
        w(f"| Account-side billed spend at last reconciliation | ${rec['account_spend_usd']:.4f} "
          f"({rec['account_requests']} requests, checked {rec['checked_at']}) |")
    w(f"| Projected competition spend | ${proj['projected_total'] - proj['prelim_spent']:.2f} "
      f"(cumulative ${proj['projected_total']:.2f}, stop ${proj['stop_usd']:.0f}) |")
    w("")
    if notes:
        w("## Run notes")
        w("")
        for n in notes:
            w(f"- {n}")
        w("")
    if rec and rec["unlogged_requests"]:
        w("## Spend reconciliation")
        w("")
        w(f"The reseller's per-request usage records show {rec['unlogged_requests']} requests that never reached the "
          f"call log, billed ${rec['unlogged_billed_usd']:.4f} in total. They are calls the client abandoned at its "
          f"former 180 s timeout while the server finished and billed them; the retry was then logged. The client "
          f"timeout is now 800 s (above the reseller's 795 s ceiling), and the D5 projection and the competition "
          f"cost tracker start from the larger, account-side figure (${rec['effective_spend_usd']:.4f}).")
        w("")
    w("## Gate (Part B)")
    w("")
    if gate:
        pm = gate["per_model"]
        g_cfg = attempt_cfg(ctx, gate["attempt"])
        w(f"Current attempt: {gate['attempt']} ({g_cfg['decision']}). Gate models: "
          + ", ".join(f"{n} ({'reasoning on' if m == 'on' else 'lowest setting / reasoning-off'})" for n, m in g_cfg["models"])
          + f"; 320-call control arm each"
          + (f" (reused from attempt {', '.join(f'{v} for {k}' for k, v in g_cfg['reuse_control_from'].items())})"
             if g_cfg["reuse_control_from"] else "")
          + f" and {gate['n_practice']} practice prompts per model from seed {gate['practice_seed']}. "
          f"Overall: **{pf(gate['pass'])}**. Full history: reports/gate_history.md.")
        w("")
        w("| Scope | U (control) | C (control, nats) | Evidence / tone effect (nats) | Spearman rho | G1 | G2 | G3 | G4 |")
        w("|---|---|---|---|---|---|---|---|---|")
        for label, m in list(pm.items()) + [("pooled", gate["pooled"])]:
            w(f"| {label} | {_fmt(m['G1']['U'])} | {_fmt(m['G3']['C'])} | {_fmt(m['G2']['evidence_effect'])} / "
              f"{_fmt(m['G2']['tone_effect'])} | {_fmt(m['G4']['rho'])} (n={m['G4']['n']}) | {pf(m['G1']['pass'])} | "
              f"{pf(m['G2']['pass'])} | {pf(m['G3']['pass'])} | {pf(m['G4']['pass'])} |")
        w("")
        w("Pooled G1 and G3 above are computed on pooled records for information; the pooled gate decision for "
          "G1 and G3 is that every gate model passes on its own.")
        w("")
        w("| Gate model | Arm | Valid | Missing | U | Conservatism | C | C neutral | C insistent | Disavowed share (n) |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        for n, m in pm.items():
            for arm in ("control", "practice"):
                s = m[f"{arm}_summary"]
                w(f"| {n} | {arm} | {s['n_valid']} | {s['n_missing']} | {_fmt(s['U'])} | {_fmt(s['conservatism'])} | "
                  f"{_fmt(s['C'])} | {_fmt(s['C_neutral'])} | {_fmt(s['C_insistent'])} | "
                  f"{_fmt(s['disavowed_share_raw'])} ({s['disavowed_n_qualifying']}) |")
        w("")
    else:
        w("Gate not run: G1-G4 are FAIL because they were not evaluated.")
        w("")
    w(f"Gate attempts: {len(gate_hist)} (maximum {ctx.gate_cfg['max_gate_attempts']}: the failed attempt 1, attempt 2 "
      f"under D7-D8, and at most one wording revision).")
    for h in gate_hist:
        w(f"- attempt {h['attempt']}: {h.get('result', 'n/a')}; practice prompts {h.get('n_practice', 'n/a')}; "
          f"{h.get('revision') or 'no wording revision'}")
    w("")
    w("## Part C identity check")
    w("")
    w("NOT RUN: no direct provider access (D4). This does not block the competition run. Limitation: model identity "
      "on the reseller is unverified.")
    w("")
    w("## Per-model configuration and smoke results (Part A and step-3 re-smoke)")
    w("")
    w("| # | Model | Reseller ID | Label | Setting sent | Format | max_tokens | Calls | Parsed | Truncated | Mean reasoning tok | Mean in / out tok |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, m in enumerate(ctx.models_cfg["models"], 1):
        s = smoke.get(m["name"], {})
        e = _entry(ctx, m["name"]) or {}
        if not s.get("n"):
            w(f"| {i} | {m['name']} | n/a | n/a | n/a | n/a | n/a | 0 | n/a | n/a | n/a | n/a |")
            continue
        w(f"| {i} | {m['name']} | `{e.get('reseller_id')}` | {s['label_class']} | `{s['setting']}` | "
          f"{s['response_format']} | {s['max_tokens']} | {s['n']} | {100 * s['parsed_share']:.1f}% | {s['truncated']} | "
          f"{s['mean_reasoning_tokens']:.0f} | {s['mean_input_tokens']:.0f} / {s['mean_output_tokens']:.0f} |")
    w("")
    w("Parse and truncation figures use only the calls made under each model's frozen configuration. Models whose "
      "request did not change (same reasoning setting, JSON-only) keep their Part A calls; models whose request "
      "changed were re-smoked with 40 new calls.")
    w("")
    w("Per-model quirks and fixes:")
    w("")
    any_q = False
    for e in ctx.resolved()["models"]:
        for q in e.get("quirks", []):
            w(f"- {e['name']}: {q}")
            any_q = True
    if not any_q:
        w("- none recorded")
    w("")
    w("## Measured tokens per call")
    w("")
    rt = (gate or {}).get("reasoning_on_tokens")
    if rt:
        w(f"Reasoning-on row ({rt['model']}, `{json.dumps(ctx.models_cfg['reasoning_on_setting']['params'])}`): "
          f"{rt['n']} calls, mean input {rt['mean_input_tokens']:.1f}, mean output {rt['mean_output_tokens']:.1f} "
          f"(of which reasoning {rt['mean_reasoning_tokens']:.1f}), mean cost ${rt['mean_cost_usd']:.5f} per call.")
    else:
        w("Reasoning-on row: not measured.")
    w("")
    w("Reasoning-off and lowest-setting tokens per model are in the table above (frozen configuration only).")
    w("")
    w("## Projected competition cost (D5)")
    w("")
    w(f"Measured USD per call = mean input tokens x input price + mean output tokens x output price, using the frozen "
      f"configuration's smoke calls and live CheaperInference catalog prices. Each projection covers the control arm "
      f"({proj['options'][3]['calls_per_model_control']} calls per model) plus the main grid. Preliminary spend: "
      f"${proj['prelim_spent']:.4f}.")
    w("")
    w("| Draws | Main-grid calls per model | Base cost (control + main) | Plus preliminary | Fits under $55 |")
    w("|---|---|---|---|---|")
    for d in (3, 2):
        o = proj["options"][d]
        w(f"| {d} | {o['calls_per_model_main']} | ${o['base_cost']:.2f} | ${o['total_with_prelim']:.2f} | {'yes' if o['fits'] else 'no'} |")
    w("")
    w("| Model | USD per call | Base cost at frozen draws |")
    w("|---|---|---|")
    for n in proj["models"]:
        w(f"| {n} | ${proj['unit_cost'][n]:.5f} | ${proj['base_per_model'][n]:.2f} |")
    w("")
    w("| Reasoning-on row (registered order) | Projected cost | Decision |")
    w("|---|---|---|")
    for r in proj["reasoning_on_rows"]:
        cost = f"${r['projected_cost']:.2f}" if r["projected_cost"] is not None else "n/a"
        extra = f" ({r['token_source']})" if r.get("token_source") else ""
        w(f"| {r['model']} | {cost} | {r['decision']}{extra} |")
    w("")
    w(f"Reasoning-on rows admitted: **{len(proj['admitted'])}** ({', '.join(proj['admitted']) or 'none'}).")
    w("")
    w("## Frozen competition configuration")
    w("")
    w(f"- Draws: **{frozen['draws_main']}** ({frozen['calls_per_model_main']} main-grid + "
      f"{frozen['calls_per_model_control']} control-arm calls per model).")
    w(f"- Models: {len(frozen['models'])} ({', '.join(m['name'] for m in frozen['models'])}). Dropped for budget: "
      f"{', '.join(frozen['dropped_models']) or 'none'}.")
    for k, v in frozen["excluded_models"].items():
        w(f"- {k}: {v}.")
    w(f"- Reasoning-on rows: {', '.join(r['name'] for r in frozen['reasoning_on_rows']) or 'none'}.")
    w(f"- Projected competition spend ${frozen['projected_competition_usd']:.2f}; projected cumulative "
      f"${frozen['projected_cumulative_usd']:.2f} (at most ${frozen['stop_usd']:.0f}).")
    w(f"- Control-arm halting: {frozen['control_arm_halting']}.")
    w("- Full per-model settings: DECISIONS.md and `configs/frozen_competition.json`.")
    w("")
    w("## Freeze")
    w("")
    w("`hashes.json` (SHA-256); `python -m dc.cli run --registered` refuses to run if any of these differ:")
    w("")
    for k, v in hashes["hashes"].items():
        w(f"- {k}: `{v['sha256'][:16]}...` ({', '.join(v['files'])})")
    w("")
    w("## Limitations noted at freeze")
    w("")
    w("- Model identity on the reseller is unverified (Part C not run; no direct provider access).")
    thinkers = [m["name"] for m in frozen["models"] if m["label"] == LABEL_THINKING]
    w(f"- {len(thinkers)} frozen models cannot disable thinking on this route and run at their lowest setting "
      f"({', '.join(thinkers)}); they are labeled \"thinking not disableable, lowest setting\" and are never "
      f"paired with a reasoning-on row. Their results measure deliberated, not reflexive, answers.")
    w("- Haiku's reasoning-off control-arm answers ignore the stated evidence (about 34% at every evidence level); "
      "this is why gate attempt 1 failed, and it is a finding about Haiku without a scratchpad, not about the "
      "prompts (DeepSeek V4 Pro with reasoning lands within 0.4 points of the exact posterior on the same items, "
      "and attempt 2 passed with D7's gate models). Under D9, reasoning-off Haiku is expected to fail its own "
      "control-arm G1 in the registered run, which flags it \"instrument not valid for this model\" without "
      "halting the run; its paired reasoning-on contrast would then be reported with the flagged models.")
    w("- Attempt 2's practice prompts use a fresh seed disjoint from attempt 1, but DeepSeek's control arm is "
      "reused from attempt 1 (D8), so DeepSeek's G1 and G3 are not independent replications.")
    w("- Two draws per cell instead of the SPEC's three (D5 budget rule); Mistral Large 4 is excluded for "
      "truncated JSON at the D1 output cap.")
    w("- Reasoning-on output tokens for admitted rows other than DeepSeek V4 Pro are proxied by the DeepSeek "
      "measurement; the competition run enforces the $55 stop and $60 hard cap in code regardless.")
    w("")
    (ctx.root / "prelim_report.md").write_text("\n".join(L) + "\n")
    summary = {"smoke": smoke, "gate": gate, "projection": proj, "frozen": frozen, "spent": spent}
    (ctx.root / "reports/prelim_summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")


def reconciliation(ctx) -> dict | None:
    p = ctx.root / "reports/spend_reconciliation.json"
    return json.loads(p.read_text()) if p.exists() else None


def write_reconciliation(ctx) -> dict | None:
    return reconciliation(ctx)


def effective_spend(ctx) -> float:
    m = ctx.gate_cfg["logs_only_spend_multiplier"]
    logged = spent_from_logs(ctx.all_logs())
    rec = reconciliation(ctx)
    if rec:
        return rec["account_spend_usd"] + m * max(0.0, logged - rec["logged_spend_usd"])
    return m * logged
