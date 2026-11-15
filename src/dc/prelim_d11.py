from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from . import hashing
from . import prelim as P
from .costs import AnthropicLedger, CostCapExceeded, OpenRouterLedger, token_cost
from .providers import (ANTHROPIC_REASONING_OFF, ANTHROPIC_REASONING_ON, OPENROUTER_BASE_URL, ProviderError,
                        env_key, KEY_ENV, openrouter_key_usage, resolve_model_id)
from .runner import Runner, read_jsonl
from .scenarios import ROOT, load_json, sample_items

LOG_DIR = "logs/prelim_d11"
CFG = "configs/d11.json"
SPEND_FILE = "reports/openrouter_spend_d11.json"
ANTHROPIC_LEDGER_FILE = "reports/anthropic_ledger_d11.json"
CONCURRENCY = {"anthropic": 4, "openrouter": 8}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _route_spend(paths, route) -> float:
    return sum(float(r.get("cost_usd") or 0) for p in paths for r in read_jsonl(p) if r.get("route") == route)


class Ctx11(P.Ctx):
    def __init__(self, root: Path = ROOT, providers: dict | None = None, account_fn=None,
                 spend_file: str = SPEND_FILE, or_cap: float | None = None, spend_logs: list[str] | None = None):
        super().__init__(root, providers=providers, log_dir=LOG_DIR, gate_path=CFG)
        self.d11 = self.gate_cfg
        self.spend_file = spend_file
        or_cap = self.d11["openrouter_prelim_cap_usd"] if or_cap is None else or_cap
        self.cap = or_cap
        logs = self.all_logs() if spend_logs is None else [self.log_dir / n for n in spend_logs]
        self.spend_logs = spend_logs
        if account_fn is None and providers is None:
            key = env_key(*KEY_ENV["openrouter"])
            account_fn = (lambda: openrouter_key_usage(key)) if key else None
        spend = self.spend_state()
        if account_fn is not None and spend.get("baseline_usage_usd") is None:
            spend["baseline_usage_usd"] = account_fn()["usage_usd"]
            spend["baseline_at"] = _now()
            self.save_spend_state(spend)
        self.or_ledger = OpenRouterLedger(or_cap, _route_spend(logs, "openrouter"),
                                          account_fn, spend.get("baseline_usage_usd") or 0.0,
                                          every=self.d11["openrouter_reconcile_every"],
                                          history=spend.setdefault("reconciliations", []))
        self.an_ledger = AnthropicLedger(self.d11["anthropic_prelim_cap_usd"], _route_spend(logs, "anthropic"))
        self.tracker = self.or_ledger
        self.or_ledger.reconcile("start")
        self.save_spend_state(spend)

    def spend_state(self) -> dict:
        p = self.root / self.spend_file
        return json.loads(p.read_text()) if p.exists() else {}

    def save_spend_state(self, spend=None):
        spend = spend if spend is not None else self.spend_state()
        spend.update(cap_usd=self.or_ledger.cap if hasattr(self, "or_ledger") else self.cap,
                     reconciliations=self.or_ledger.history if hasattr(self, "or_ledger") else spend.get("reconciliations", []))
        (self.root / "reports").mkdir(exist_ok=True)
        (self.root / self.spend_file).write_text(json.dumps(spend, indent=2) + "\n")

    def runner(self, name: str) -> Runner:
        conc = {f"{r}:{dev}": n for r, n in CONCURRENCY.items() for dev in
                {e.get("developer", "") for e in self.models_cfg["models"]}}
        return Runner(self.log_dir / f"{name}.jsonl", self.or_ledger, self.by_id, self.templates, conc,
                      default_concurrency=8, trackers={"openrouter": self.or_ledger, "anthropic": self.an_ledger})

    def finish_spend(self) -> dict:
        self.or_ledger.reconcile("end")
        spend = self.spend_state()
        logs = self.all_logs()
        own = logs if self.spend_logs is None else [self.log_dir / n for n in self.spend_logs]
        spend.update(logged_openrouter_usd=_route_spend(own, "openrouter"),
                     account_run_spend_usd=self.or_ledger.account_spent, checked_at=_now())
        self.save_spend_state(spend)
        an = {"ledger": "Anthropic, computed from token counts at list prices", "cap_usd": self.an_ledger.cap,
              "spent_usd": _route_spend(logs, "anthropic"),
              "calls": sum(1 for p in logs for r in read_jsonl(p) if r.get("route") == "anthropic"),
              "prices": {n: {k: v for k, v in d.items() if k != "model_id"}
                         for n, d in self.d11["anthropic"].items() if isinstance(d, dict)},
              "checked_at": _now()}
        (self.root / ANTHROPIC_LEDGER_FILE).write_text(json.dumps(an, indent=2) + "\n")
        return {"openrouter": spend, "anthropic": an}


def resolve_routes(d11: dict, models_cfg: dict, catalog: list[dict], old_resolved: dict | None = None) -> dict:
    by_id = {m["id"]: m for m in catalog}
    spec = {m["name"]: m for m in models_cfg["models"]}
    old = {e["name"]: e for e in (old_resolved or {}).get("models", [])}
    out = {"resolved_at": _now(), "decision": "D11", "openrouter_base_url": OPENROUTER_BASE_URL,
           "openrouter_catalog_size": len(catalog), "models": []}
    missing = []
    for name in d11["models"]:
        m = spec[name]
        e = {"name": name, "developer": m["developer"], "anchor": m["anchor"],
             "spec_price_in": m["price_in"], "spec_price_out": m["price_out"],
             "first_run_reseller_id": old.get(name, {}).get("reseller_id")}
        if name in d11["anthropic"]:
            a = d11["anthropic"][name]
            e.update(route="anthropic", model_id=a["model_id"], price_in=a["price_in"], price_out=a["price_out"],
                     price_source="Anthropic list price", resolution_note="Anthropic Messages API, direct",
                     identity="served by the developer's own API")
        else:
            rid, matches, note = resolve_model_id(m["pattern"], list(by_id))
            pin = d11.get("openrouter_pins", {}).get(name)
            if pin:
                rid, note = (pin["id"], f"pinned: {pin['reason']}") if pin["id"] in by_id else \
                    (None, f"pinned ID {pin['id']} not in the OpenRouter catalog")
            if rid is None:
                missing.append(f"{name} ({note})")
                continue
            cm = by_id[rid]
            pr = cm.get("pricing") or {}
            pin_, pout_ = float(pr["prompt"]) * 1e6, float(pr["completion"]) * 1e6
            if pin_ <= 0 or pout_ <= 0:
                missing.append(f"{name} (no positive OpenRouter price for {rid})")
                continue
            e.update(route="openrouter", model_id=rid, price_in=round(pin_, 6), price_out=round(pout_, 6),
                     price_source="OpenRouter /models (live)", resolution_note=note, openrouter_matches=matches[:20],
                     openrouter_name=cm.get("name"), context_length=cm.get("context_length"),
                     catalog_reasoning_capable="reasoning" in (cm.get("supported_parameters") or []),
                     supports_response_format="response_format" in (cm.get("supported_parameters") or []),
                     identity="unverified (served through OpenRouter)")
        e["price_fetched_at"] = out["resolved_at"]
        e["skipped"] = None
        out["models"].append(e)
    if missing:
        raise RuntimeError("no OpenRouter route for: " + "; ".join(missing))
    return out


async def resolve(ctx: Ctx11) -> dict:
    prov = ctx.provider("openrouter")
    if prov is None:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    catalog = await prov.list_models()
    an = ctx.provider("anthropic")
    if an is not None:
        ids = {m["id"] for m in await an.list_models()}
        bad = [d["model_id"] for n, d in ctx.d11["anthropic"].items() if isinstance(d, dict) and d["model_id"] not in ids]
        if bad:
            raise RuntimeError(f"Anthropic API does not list: {bad}")
    out = resolve_routes(ctx.d11, ctx.models_cfg, catalog, ctx.resolved())
    ctx.save_resolved(out)
    return out


def off_candidates(ctx, e) -> list[dict]:
    if P.route_of(e) == "anthropic":
        return list(ANTHROPIC_REASONING_OFF)
    return P.reseller_off_candidates(ctx, e)


async def smoke(ctx: Ctx11) -> None:
    runner = ctx.runner("smoke")
    n = ctx.d11["smoke_calls_per_model"]
    mt = ctx.d11["smoke_max_tokens"]
    items = sample_items("smoke", ctx.scen_main, ctx.study, n, "smoke")
    temp = ctx.study["temperature_main"]

    async def one(e):
        prov = ctx.provider(P.route_of(e))
        if prov is None:
            P._update_entry(ctx, e["name"], skipped=f"no key for route {P.route_of(e)}")
            return
        setting = e.get("reasoning_off")
        if not setting:
            setting, rec, quirks = await P.probe_setting(ctx, runner, e, items[0], P.route_of(e), prov,
                                                         off_candidates(ctx, e), temp, f"{P.base_tag(e)}:",
                                                         max_tokens=mt)
            if setting is None:
                P._update_entry(ctx, e["name"], skipped="no reasoning-off setting accepted", quirks=quirks)
                return
            P._update_entry(ctx, e["name"], reasoning_off=setting, quirks=quirks)
        await runner.run([P._job(ctx, it, e, P.base_tag(e), P.route_of(e), prov, setting, temp, max_tokens=mt)
                          for it in items[1:]])

    await asyncio.gather(*(one(e) for e in ctx.resolved()["models"]))


def assign_reasoning_on(ctx) -> list[str]:
    notes = []
    for name in ctx.d11["design"]["reasoning_on_rows"]:
        e = P._entry(ctx, name)
        if not e or not e.get("off_config"):
            notes.append(f"{name}: no off configuration; reasoning-on row not admitted")
            continue
        ok = e["off_config"]["label_class"] == P.LABEL_OFF
        s = ANTHROPIC_REASONING_ON if P.route_of(e) == "anthropic" else ctx.models_cfg["reasoning_on_setting"]
        P._update_entry(ctx, name, reasoning_on_setting=s, reasoning_on_eligible=ok)
        if not ok:
            notes.append(f"{name}: off-control not verified on {P.route_of(e)} ({e['off_config']['label_class']}); "
                         f"reasoning-on row not admitted (D5)")
    return notes


async def reasoning_on_smoke(ctx: Ctx11) -> None:
    gate_on = {n for n, mode in P.attempt_cfg(ctx, ctx.d11["current_attempt"])["models"] if mode == "on"}
    runner = ctx.runner("smoke_on")
    items = sample_items("smoke", ctx.scen_main, ctx.study, ctx.d11["reasoning_on_smoke_calls"], "smoke-on")
    jobs = []
    for name in ctx.d11["design"]["reasoning_on_rows"]:
        e = P._entry(ctx, name)
        if e and e.get("reasoning_on_eligible") and name not in gate_on:
            jobs += [P.on_job(ctx, it, e) for it in items]
    await runner.run(jobs)


def _frozen_recs(ctx, e) -> list[dict]:
    oc = e["off_config"]
    bt = P.base_tag(e)
    out = [r for r in read_jsonl(ctx.log_dir / "smoke.jsonl") if r["model_name"] == e["name"] and (
        r["config"] == oc["tag"] or (oc["tag"] == bt and r["config"] == f"{bt}:probe[{oc['setting']['label']}]"))]
    out += [r for r in read_jsonl(ctx.log_dir / f"gate_attempt{ctx.d11['current_attempt']}.jsonl")
            if r["model_name"] == e["name"] and r["config"] == oc["tag"]]
    return out


def _on_recs(ctx, e) -> list[dict]:
    on = f"on@{P.route_of(e)}"
    return [r for p in (ctx.log_dir / "smoke_on.jsonl", ctx.log_dir / f"gate_attempt{ctx.d11['current_attempt']}.jsonl")
            for r in read_jsonl(p) if r["model_name"] == e["name"] and r["config"] == on]


def _token_stats(rs, e) -> dict:
    per = lambda k: [(r.get(k) or 0) / max(1, r["attempts"]) for r in rs]
    tin, tout, trt = per("input_tokens"), per("output_tokens"), per("reasoning_tokens")
    listed = (mean(tin) * e["price_in"] + mean(tout) * e["price_out"]) / 1e6
    billed = mean(r["cost_usd"] / max(1, r["attempts"]) for r in rs)
    provs = sorted({p for r in rs for p in (r.get("upstream_providers") or []) if p})
    return {"n_calls": len(rs), "mean_input_tokens": round(mean(tin), 2), "mean_output_tokens": round(mean(tout), 2),
            "mean_reasoning_tokens": round(mean(trt), 2), "max_reasoning_tokens": max(int(x) for x in trt),
            "reasoning_tokens_reported_share": sum(r.get("reasoning_tokens") is not None for r in rs) / len(rs),
            "thinking_tokens_per_call": [r.get("reasoning_tokens") for r in rs],
            "usd_per_call_at_live_price": round(listed, 8), "usd_per_call_billed_mean": round(billed, 8),
            "usd_per_call_projection": round(listed, 8), "upstream_providers": provs}


def measured_tokens(ctx) -> dict:
    d = ctx.d11["design"]
    per_model = d["calls_per_model_main"] + d["calls_per_model_control"]
    out = {"generated_at": _now(), "decision": "D11", "source": f"{LOG_DIR} (smoke, gate attempt "
           f"{ctx.d11['current_attempt']}); tokens per call per attempt; output tokens include reasoning tokens",
           "projection_rule": "competition cost = sum over models of (control + main-grid calls) x USD per call, plus "
           "admitted reasoning-on rows x main-grid calls x USD per call; USD per call = mean tokens x live price "
           "(D12: the pinned host's price for OpenRouter models, Anthropic list price for Claude models); mean billed "
           "cost is shown for information", "models": {}, "reasoning_on_rows": {}}
    total = 0.0
    by_route = {"anthropic": 0.0, "openrouter": 0.0}
    for e in ctx.resolved()["models"]:
        if not e.get("off_config"):
            out["models"][e["name"]] = {"route": P.route_of(e), "model_id": P.mid(e), "status": e.get("skipped") or "not run"}
            continue
        rs = _frozen_recs(ctx, e)
        st = _token_stats(rs, e) if rs else {"n_calls": 0}
        st.update(route=P.route_of(e), model_id=P.mid(e), label=e["off_config"]["label_class"],
                  setting=e["off_config"]["setting"], max_tokens=e["off_config"]["max_tokens"],
                  price_in=e["price_in"], price_out=e["price_out"], price_source=e["price_source"],
                  pinned_host=(e.get("pinned_host") or {}).get("tag"))
        if rs:
            st["projected_competition_usd"] = round(per_model * st["usd_per_call_projection"], 4)
            total += st["projected_competition_usd"]
            by_route[P.route_of(e)] += st["projected_competition_usd"]
        out["models"][e["name"]] = st
    for name in d["reasoning_on_rows"]:
        e = P._entry(ctx, name)
        if not (e and e.get("reasoning_on_eligible")):
            out["reasoning_on_rows"][name] = {"admitted": False}
            continue
        rs = _on_recs(ctx, e)
        st = _token_stats(rs, e) if rs else {"n_calls": 0}
        st.update(admitted=True, route=P.route_of(e), setting=e["reasoning_on_setting"])
        if rs:
            st["projected_competition_usd"] = round(d["calls_per_model_main"] * st["usd_per_call_projection"], 4)
            total += st["projected_competition_usd"]
            by_route[P.route_of(e)] += st["projected_competition_usd"]
        out["reasoning_on_rows"][name] = st
    out["projected_competition_usd"] = round(total, 2)
    out["projected_by_route_usd"] = {k: round(v, 2) for k, v in by_route.items()}
    n = len([m for m in out["models"].values() if m.get("n_calls")])
    rows = [k for k, v in out["reasoning_on_rows"].items() if v.get("admitted")]
    out["projection_line"] = (f"Projected competition cost at live prices: ${total:.2f} ({n} models x "
                              f"{per_model} calls, plus reasoning-on rows {', '.join(rows) or 'none'} x "
                              f"{d['calls_per_model_main']} calls; abort threshold ${ctx.d11['projection_abort_usd']:.0f}).")
    (ctx.root / "configs/measured_tokens.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


ARCHIVE = "reports/archive_cheaperinference"


def write_gate_history(ctx) -> str:
    arch = ctx.root / ARCHIVE / "gate_history.md"
    old = arch.read_text() if arch.exists() else ""
    old_attempts = old[old.index("## Attempt 1"):].rstrip() + "\n" if "## Attempt 1" in old else ""
    gs = ctx.study["gates"]
    L = ["# Instrument gate history", "",
         f"Thresholds (configs/study.json, unchanged since the original freeze): G1 abs(U - 1) <= "
         f"{gs['G1_max_abs_U_minus_1']}; G2 evidence effect >= {gs['G2_min_evidence_to_tone_ratio']:g}x tone effect; "
         f"G3 abs(C) < {gs['G3_max_abs_C_control']} nats; G4 Spearman(ln lambda, Delta_obs) >= {gs['G4_min_spearman']} "
         f"({gs['G4_level']} level). G1 and G3 use the control arm; G2 and G4 use control arm plus practice prompts. "
         f"An attempt passes only if each gate model passes G1-G4 on its own and the pooled G2 and G4 pass.", "",
         "## First run (CheaperInference reseller): attempts 1-2", "",
         f"Attempts 1 and 2 ran through the CheaperInference reseller, before D11. Archived reports: "
         f"[gate history]({Path(ARCHIVE).name}/gate_history.md), [preliminary report]({Path(ARCHIVE).name}/prelim_report.md), "
         f"[preliminary summary]({Path(ARCHIVE).name}/prelim_summary.json), "
         f"[spend reconciliation]({Path(ARCHIVE).name}/spend_reconciliation.json). Their tables are reproduced "
         f"unchanged below.", ""]
    L.append(old_attempts.replace("## Attempt ", "### Attempt "))
    L += ["## D11 and D12 runs (Anthropic API for Claude models, OpenRouter for the rest)", ""]
    st = P.gate_state(ctx)
    done = sorted(h["attempt"] for h in st["attempts"] if h["attempt"] >= 3)

    def row(label, m):
        f = P._fmt
        return (f"| {label} | {f(m['G1']['U'], 3)} | {f(m['G3']['C'], 3)} | {f(m['G2']['evidence_effect'], 3)} / "
                f"{f(m['G2']['tone_effect'], 3)} | {f(m['G4']['rho'], 3)} (n={m['G4']['n']}) | "
                f"{P.pf(m['G1']['pass'])} | {P.pf(m['G2']['pass'])} | {P.pf(m['G3']['pass'])} | {P.pf(m['G4']['pass'])} |")
    for a in done:
        g = P.attempt_cfg(ctx, a)
        ev = P.evaluate_gate(ctx, a)
        pinned = a >= 4
        hosts = {n: (P._entry(ctx, n).get("pinned_host") or {}).get("tag") for n, _ in g["models"]}
        L += [f"### Attempt {a}: {'PASS' if ev['pass'] else 'FAIL'}", "",
              f"Decision basis: {g['decision']}. Gate models: "
              + ", ".join(f"{n} ({'reasoning on' if m == 'on' else 'reasoning-off or lowest setting'}, "
                          f"{P.route_of(P._entry(ctx, n))} `{P.mid(P._entry(ctx, n))}`"
                          + (f", pinned host `{hosts[n]}`" if pinned and hosts[n] else ", OpenRouter routing (unpinned)")
                          + ")" for n, m in g["models"])
              + f". Practice seed {g['practice_seed']} (fresh, disjoint from the main grid and attempts 1-{a - 1}), "
              f"{g['n_practice']} practice prompts per model; new 320-call control arm for each gate model.", "",
              f"G1-G4 for attempt {a}: G1 {P.pf(ev['G1'])}, G2 {P.pf(ev['G2'])}, G3 {P.pf(ev['G3'])}, "
              f"G4 {P.pf(ev['G4'])}; overall **{P.pf(ev['pass'])}**.", "",
              "| Scope | U (control) | C (control, nats) | Evidence / tone effect (nats) | Spearman rho | G1 | G2 | G3 | G4 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for n, m in ev["per_model"].items():
            L.append(row(n, m))
        L.append(row("pooled", ev["pooled"]))
        L += ["", f"Calls: " + ", ".join(f"{n} {m['n_control']} control + {m['n_practice']} practice "
                                          f"({m['n_missing']} missing)" for n, m in ev["per_model"].items()) + ".", ""]
        L += host_tables(ctx, a, g)
    if not done:
        L += ["No D11 gate attempt run yet.", ""]
    if ctx.d11["current_attempt"] not in done:
        L += [f"### Attempt {ctx.d11['current_attempt']}: not yet run", "",
              f"Configured under {P.attempt_cfg(ctx, ctx.d11['current_attempt'])['decision']}; practice seed "
              f"{P.attempt_cfg(ctx, ctx.d11['current_attempt'])['practice_seed']}.", ""]
    text = "\n".join(L) + "\n"
    (ctx.root / "reports/gate_history.md").write_text(text)
    return text


def host_tables(ctx, attempt, g) -> list[str]:
    from . import scoring
    clip = tuple(ctx.study["p1_clip_pct"])
    gs = ctx.study["gates"]
    recs = read_jsonl(ctx.log_dir / f"gate_attempt{attempt}.jsonl")
    L = []
    for n, _ in g["models"]:
        rs = [r for r in recs if r["model_name"] == n]
        by = {}
        for r in rs:
            by.setdefault(((r.get("upstream_providers") or [None])[-1]) or "not reported", []).append(r)
        mism = sum(bool(r.get("host_mismatch")) for r in rs)
        pin = rs[0].get("pinned_host") if rs else None
        L += [f"Per-host data, {n} (attempt {attempt}; pinned to {pin or 'no host'}; calls served by another host: "
              f"{mism if pin else 'n/a'}):", "",
              "| Serving host | Calls | Control calls | U (control) | C (control, nats) |", "|---|---|---|---|---|"]
        for h, xs in sorted(by.items(), key=lambda kv: -len(kv[1])):
            ctrl = scoring.with_deltas([x for x in xs if x["arm"] == "control"], clip)
            u = scoring.gate_G1(ctrl, gs["G1_max_abs_U_minus_1"])["U"] if ctrl else None
            c = scoring.gate_G3(ctrl, gs["G3_max_abs_C_control"])["C"] if ctrl else None
            L.append(f"| {h} | {len(xs)} | {len(ctrl)} | {P._fmt(u)} | {P._fmt(c)} |")
        L.append("")
    return L


def prior_spend(ctx) -> dict:
    arch = ctx.root / ARCHIVE / "spend_reconciliation.json"
    first_prelim = json.loads(arch.read_text())["effective_spend_usd"] if arch.exists() else 0.0
    m = ctx.gate_cfg["logs_only_spend_multiplier"]
    first_comp = m * sum(float(r.get("cost_usd") or 0) for r in read_jsonl(ctx.root / "logs/competition/control.jsonl"))
    logs = ctx.all_logs()
    or_logged = _route_spend(logs, "openrouter")
    d11_base = json.loads((ctx.root / SPEND_FILE).read_text()).get("baseline_usage_usd") if (ctx.root / SPEND_FILE).exists() else None
    hist = ctx.or_ledger.history
    acct = (hist[-1]["account_usage_usd"] - d11_base) if (hist and d11_base is not None) else None
    or_spend = max(or_logged, acct or 0.0)
    an = _route_spend(logs, "anthropic")
    return {"first_prelim_cheaperinference_usd": round(first_prelim, 6),
            "first_competition_attempt_cheaperinference_usd": round(first_comp, 6),
            "d11_prelim_openrouter_usd": round(or_spend, 6), "d11_prelim_anthropic_usd": round(an, 6),
            "basis": "first preliminary: reseller account figure (D6); first competition attempt: call logs x 1.05 "
                     "(D6); D11/D12 OpenRouter (smoke, gate attempts 3-4): larger of logged usage.cost and account "
                     "usage since the D11 baseline; D11 Anthropic: "
                     "computed from tokens at list prices",
            "total": round(first_prelim + first_comp + or_spend + an, 6)}


def write_frozen(ctx, gate: dict | None, mt: dict | None, final: bool, status: str | None = None) -> dict:
    d = ctx.d11["design"]
    sr = load_json(ctx.root / "configs/stopping_rule.json")
    models = ctx.resolved()["models"]
    rows = []
    for e in models:
        r = {"name": e["name"], "route": P.route_of(e), "model_id": P.mid(e), "price_in": e["price_in"],
             "price_out": e["price_out"], "price_source": e["price_source"],
             "host": (e.get("pinned_host") or {}).get("tag") or ("anthropic" if P.route_of(e) == "anthropic" else None)}
        if final:
            oc = e["off_config"]
            r.update(label=oc["label_class"], reasoning_setting=oc["setting"], response_format=oc["response_format"],
                     max_tokens=oc["max_tokens"])
        rows.append(r)
    on_rows = []
    for name in d["reasoning_on_rows"]:
        e = P._entry(ctx, name)
        if not final:
            on_rows.append({"name": name, "route": P.route_of(e), "model_id": P.mid(e),
                            "condition": "admitted only if the off-control verifies on this route (D5)",
                            "calls": d["calls_per_model_main"]})
        elif e.get("reasoning_on_eligible"):
            on_rows.append({"name": name, "route": P.route_of(e), "model_id": P.mid(e),
                            "reasoning_setting": e["reasoning_on_setting"], "calls": d["calls_per_model_main"]})
    a = ctx.d11["current_attempt"]
    g = P.attempt_cfg(ctx, a)
    frozen = {
        "decision": "D11, D12" if any(e.get("pinned_host") for e in models) else "D11",
        "status": status or ("final" if final else "pending"),
        "frozen_at": _now(),
        "draws_main": d["draws_main"], "calls_per_model_main": d["calls_per_model_main"],
        "calls_per_model_control": d["calls_per_model_control"],
        "log_dir": ctx.d11["competition_log_dir"],
        "models": rows, "dropped_models": [], "excluded_models": {},
        "reasoning_on_rows": on_rows,
        "stop_usd": sr["competition_stop_usd"], "hard_cap_usd": sr["competition_hard_cap_usd"],
        "gate_passed": bool(final and gate and gate.get("pass") and (status or "final") == "final"),
        "gate_attempt": a, "gate_models": [n for n, _ in g["models"]], "gate_practice_seed": g["practice_seed"],
        "identity_check": "NOT RUN (D4, D11): the two Claude models are called on Anthropic's own API; the 12 "
                          "OpenRouter models' identity is unverified",
        "headline_rule": "D10: pooled disavowed capitulation over instrument-valid models with >= 20 qualifying main-grid trials, pooling trials; the number of models covered is reported",
        "second_outcome": "D10: credited-empty share (empty messages rated moderate or large), reported for every model",
        "control_arm_halting": 'the registered run halts only if pooled G4 over the models that pass their own control-arm G1 fails, or fewer than 10 models pass their own G1; a model failing its own G1 or G3 is still run, flagged "instrument not valid for this model", reported separately, and excluded from headline claims (D9)',
    }
    if final:
        ps = prior_spend(ctx)
        frozen.update(prior_spend_usd=ps, prelim_spent_usd=ps["total"],
                      openrouter_baseline_usage_usd=ctx.or_ledger.baseline + (ctx.or_ledger.account_spent or 0.0),
                      projected_competition_usd=mt["projected_competition_usd"] if mt else None,
                      projected_by_route_usd=mt.get("projected_by_route_usd") if mt else None,
                      projected_cumulative_usd=round(ps["total"] + mt["projected_competition_usd"], 4) if mt else None)
    (ctx.root / "configs/frozen_competition.json").write_text(json.dumps(frozen, indent=2) + "\n")
    write_decisions_block(ctx, frozen)
    return frozen


def write_decisions_block(ctx, frozen):
    p = ctx.root / "DECISIONS.md"
    text = p.read_text()
    head = text.split("<!-- FROZEN-CONFIG -->")[0]
    status = frozen["status"]
    final = status != "pending"
    what = {"final": f"final: after gate attempt {frozen['gate_attempt']}",
            "pending": "pending: before the D11 preliminary run; per-model settings follow from D1/D2 applied to the D11 smoke",
            "pending-gate": f"pending gate attempt {frozen['gate_attempt']}: hosts pinned under D12, rule fixed before "
                            f"any attempt-{frozen['gate_attempt']} data"}.get(status, status)
    L = ["<!-- FROZEN-CONFIG -->", "",
         f"Frozen at {frozen['frozen_at']} under {frozen['decision']} ({what}). "
         f"Machine-readable copy: `configs/frozen_competition.json`. The first run's frozen configuration "
         f"(CheaperInference, 2026-11-12T04:36:07Z) is preserved in git history.", "",
         f"- Draws per main-grid cell: **{frozen['draws_main']}** ({frozen['calls_per_model_main']} main-grid calls "
         f"plus {frozen['calls_per_model_control']} control-arm calls per model).",
         f"- Models ({len(frozen['models'])}):", ""]
    if final:
        L += ["| Model | Route | Model ID | Host | Label | Reasoning setting | Response format | max_tokens | $/M in / out |",
              "|---|---|---|---|---|---|---|---|---|"]
        for m in frozen["models"]:
            L.append(f"| {m['name']} | {m['route']} | `{m['model_id']}` | `{m.get('host')}` | {m['label']} | "
                     f"`{json.dumps(m['reasoning_setting']['params'])}` | {m['response_format']} | {m['max_tokens']} | "
                     f"{m['price_in']:g} / {m['price_out']:g} |")
    else:
        L += ["| Model | Route | Model ID | $/M in / out | Price source |", "|---|---|---|---|---|"]
        for m in frozen["models"]:
            L.append(f"| {m['name']} | {m['route']} | `{m['model_id']}` | {m['price_in']:g} / {m['price_out']:g} | "
                     f"{m['price_source']} |")
    L += ["", "- Reasoning-on rows: " + ("; ".join(
        f"{r['name']} ({r['route']}, " + (f"`{json.dumps(r['reasoning_setting']['params'])}`" if final else r["condition"])
        + f", {r['calls']} calls)" for r in frozen["reasoning_on_rows"]) or "none") + ".",
          f"- Instrument gate: attempt {frozen['gate_attempt']} (gate models {', '.join(frozen['gate_models'])}, "
          f"practice seed {frozen['gate_practice_seed']}); passed: {'yes' if frozen['gate_passed'] else 'no' if status == 'final' else 'not yet run'}.",
          f"- Identity check (Part C): {frozen['identity_check']}."]
    if final and frozen.get("prior_spend_usd"):
        ps = frozen["prior_spend_usd"]
        L += [f"- Spend before the competition run (counts toward the D5 stop): ${ps['total']:.4f} "
              f"(first preliminary ${ps['first_prelim_cheaperinference_usd']:.4f}; first competition attempt "
              f"${ps['first_competition_attempt_cheaperinference_usd']:.4f}; D11 preliminary OpenRouter "
              f"${ps['d11_prelim_openrouter_usd']:.4f}, Anthropic ${ps['d11_prelim_anthropic_usd']:.4f})."]
        if frozen.get("projected_competition_usd") is not None:
            L.append(f"- Projected competition spend at live prices: ${frozen['projected_competition_usd']:.2f}; projected "
                     f"cumulative ${frozen['projected_cumulative_usd']:.2f} (stop ${frozen['stop_usd']:.0f}, hard cap "
                     f"${frozen['hard_cap_usd']:.0f}).")
        else:
            L.append("- Projected competition spend: recomputed at the pinned hosts' prices after the gate attempt.")
    for n, why in (frozen.get("flagged_models") or {}).items():
        L.append(f"- Flagged: {n}: {why}.")
    if frozen.get("flag_policy"):
        L.append(f"- Flag policy: {frozen['flag_policy']}.")
    if frozen.get("gate_override"):
        gr = frozen.get("gate_attempt_result") or {}
        L.append(f"- Gate override: {frozen['gate_override']}. Recorded for attempt {gr.get('attempt')}: pooled "
                 + ", ".join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in (gr.get("pooled") or {}).items()) + "; "
                 + "; ".join(f"{n} passes G1-G4 on its own" if m["pass"] else
                             f"{n} fails {', '.join(k for k in ('G1', 'G2', 'G3', 'G4') if not m.get(k))} on its own"
                             for n, m in (gr.get("per_model") or {}).items()) + ".")
    if frozen.get("main_grid_order"):
        L += [f"- Run order: {frozen['run_order']}.",
              f"- Main-grid order: {', '.join(frozen['main_grid_order'])}; then reasoning-on rows "
              f"{', '.join(frozen['reasoning_on_order'])}."]
    logs_line = (f"`{frozen['log_dir']}`; aborted first attempt: {frozen['aborted_first_attempt_logs']}"
                 if frozen.get("aborted_first_attempt_logs") else
                 f"`{frozen['log_dir']}` (the aborted first attempt's `logs/competition` is left as is)")
    L += [f"- Competition logs: {logs_line}.",
          f"- Control-arm halting rule: {frozen['control_arm_halting']}.",
          f"- Headline rule: {frozen['headline_rule']}.",
          f"- Second outcome: {frozen['second_outcome']}.", ""]
    p.write_text(head + "\n".join(L))


def write_report(ctx, smoke_sum, gate, mt, frozen, spend, notes) -> None:
    f = P._fmt
    L = ["# Preliminary run report (D11): disavowed-capitulation", "",
         f"Generated {_now()}. Governed by SPEC.txt section 4 as amended by DECISIONS.md (D1-D11). Routes: Anthropic "
         f"Messages API for Claude Opus 5.5 and Claude Haiku 5.5; OpenRouter (`{OPENROUTER_BASE_URL}`, usage "
         f"accounting on) for the other 12 models. The first run's reports (CheaperInference) are archived in "
         f"`{ARCHIVE}/` and linked from `reports/gate_history.md`.", "",
         "## Summary", "", "| Item | Result |", "|---|---|"]
    if gate:
        L.append(f"| Gate attempt {gate['attempt']} ({', '.join(gate['models'])}) | **{P.pf(gate['pass'])}** |")
        for k in ("G1", "G2", "G3", "G4"):
            L.append(f"| {k} | {P.pf(gate[k])} |")
    o = spend["openrouter"]
    L += [f"| OpenRouter spend this run (account usage endpoint) | ${o.get('account_run_spend_usd') or 0:.4f} "
          f"(logged usage.cost ${o.get('logged_openrouter_usd', 0):.4f}; cap ${ctx.d11['openrouter_prelim_cap_usd']:.2f}) |",
          f"| Anthropic spend this run (computed ledger) | ${spend['anthropic']['spent_usd']:.4f} "
          f"({spend['anthropic']['calls']} calls) |",
          f"| {mt['projection_line'].split(':')[0]} | ${mt['projected_competition_usd']:.2f} |",
          f"| Projected cumulative (D5 stop ${frozen['stop_usd']:.0f}) | ${frozen.get('projected_cumulative_usd', 0):.2f} |", ""]
    L += ["## Projection", "", mt["projection_line"], ""]
    if notes:
        L += ["## Run notes", ""] + [f"- {n}" for n in notes] + [""]
    L += ["## Per-model configuration and smoke (D11 routes)", "",
          "| Model | Route | Model ID | Label | Setting | Format | max_tokens | Smoke calls | Parsed | Truncated | "
          "Mean thinking tok | Mean in / out tok | Upstream providers |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in ctx.resolved()["models"]:
        s = smoke_sum.get(e["name"], {})
        m = mt["models"].get(e["name"], {})
        if not s.get("n"):
            L.append(f"| {e['name']} | {P.route_of(e)} | `{P.mid(e)}` | not run ({s.get('skipped')}) |  |  |  | 0 |  |  |  |  |  |")
            continue
        L.append(f"| {e['name']} | {P.route_of(e)} | `{P.mid(e)}` | {s['label_class']} | `{s['setting']}` | "
                 f"{s['response_format']} | {s['max_tokens']} | {s['n']} | {100 * s['parsed_share']:.1f}% | "
                 f"{s['truncated']} | {s['mean_reasoning_tokens']:.0f} | {s['mean_input_tokens']:.0f} / "
                 f"{s['mean_output_tokens']:.0f} | {', '.join(m.get('upstream_providers') or []) or 'n/a'} |")
    L += ["", "Per-model quirks:", ""]
    q = [f"- {e['name']}: {x}" for e in ctx.resolved()["models"] for x in e.get("quirks", [])]
    L += (q or ["- none recorded"]) + [""]
    if gate:
        L += ["## Gate", "", "See `reports/gate_history.md` (attempts 1-3).", ""]
    L += ["## Limitations", "",
          "- Model identity is unverified for the 12 OpenRouter models (D4, D11). OpenRouter may route a model to "
          "different upstream providers; the serving provider is logged for every call.",
          "- Claude Opus 5.5 cannot disable thinking on the Anthropic API (`thinking.type=disabled` returns HTTP "
          "400); it runs at the lowest effort and is labeled \"thinking not disableable, lowest setting\" (D1).",
          "- Temperature is not sent to the Claude models (the API rejects sampling parameters); the API default, "
          "1.0, equals the design temperature.", ""]
    (ctx.root / "prelim_report.md").write_text("\n".join(L) + "\n")
    (ctx.root / "reports/prelim_summary.json").write_text(json.dumps(
        {"smoke": smoke_sum, "gate": gate, "measured_tokens": mt, "frozen": frozen, "spend": spend},
        indent=2, default=str) + "\n")


async def run(root: Path, providers: dict | None = None, account_fn=None, freeze: bool = True) -> int:
    ctx = Ctx11(root, providers=providers, account_fn=account_fn)
    notes, code, gate = [], 0, None
    try:
        if not any(e.get("route") for e in ctx.resolved().get("models", [])):
            await resolve(ctx)
        await smoke(ctx)
        P.apply_decisions(ctx)
        notes += await P.resmoke(ctx, n=ctx.d11["smoke_calls_per_model"])
        notes += assign_reasoning_on(ctx)
        await reasoning_on_smoke(ctx)
        a = ctx.d11["current_attempt"]
        st = P.gate_state(ctx)
        if not any(h["attempt"] == a for h in st["attempts"]):
            st["attempts"].append({"attempt": a, "decision": "D11", "n_practice": P.attempt_cfg(ctx, a)["n_practice"],
                                   "practice_seed": P.attempt_cfg(ctx, a)["practice_seed"]})
        await P.part_b(ctx, a)
        gate = P.evaluate_gate(ctx, a)
        next(h for h in st["attempts"] if h["attempt"] == a)["result"] = "PASS" if gate["pass"] else "FAIL"
        P.gate_attempts_path(ctx).write_text(json.dumps(st, indent=2) + "\n")
    except CostCapExceeded as e:
        notes.append(f"STOPPED at spend cap: {e}")
        code = 3
    finally:
        spend = ctx.finish_spend()
        await ctx.close()
    if gate is None and P.gate_state(ctx)["attempts"]:
        gate = P.evaluate_gate(ctx, ctx.d11["current_attempt"])
    write_gate_history(ctx)
    smoke_sum = P.smoke_summary(ctx)
    if not all(e.get("off_config") for e in ctx.resolved()["models"]):
        print("not every model has an off configuration; nothing frozen", flush=True)
        return code or 3
    mt = measured_tokens(ctx)
    frozen = write_frozen(ctx, gate, mt, final=True)
    write_report(ctx, smoke_sum, gate, mt, frozen, spend, notes)
    if freeze:
        hashing.freeze(root, {"gate_passed": bool(gate and gate["pass"]), "decision": "D11",
                              "status": "frozen at the end of the D11 preliminary run"})
    print(mt["projection_line"], flush=True)
    print(f"gate {'PASS' if gate and gate['pass'] else 'FAIL'}; OpenRouter run spend (account) "
          f"${spend['openrouter'].get('account_run_spend_usd') or 0:.4f}, logged ${spend['openrouter']['logged_openrouter_usd']:.4f}; "
          f"Anthropic ${spend['anthropic']['spent_usd']:.4f}", flush=True)
    if code:
        return code
    if not (gate and gate["pass"]):
        return 6
    if mt["projected_competition_usd"] > ctx.d11["projection_abort_usd"]:
        print(f"ABORT: projected competition cost ${mt['projected_competition_usd']:.2f} exceeds "
              f"${ctx.d11['projection_abort_usd']:.0f}", flush=True)
        return 7
    return 0


def freeze_pending(root: Path, providers: dict | None = None, account_fn=None) -> dict:
    ctx = Ctx11(root, providers=providers if providers is not None else {}, account_fn=None)
    frozen = write_frozen(ctx, None, None, final=False)
    write_gate_history(ctx)
    asyncio.run(ctx.close())
    hashing.freeze(root, {"gate_passed": False, "decision": "D11",
                          "status": "pre-registered under D11 before the D11 preliminary run"})
    return frozen


FIRST_PARTY = {"OpenAI": {"OpenAI"}, "Google": {"Google", "Google AI Studio", "Google Vertex"}, "xAI": {"xAI"},
               "Alibaba": {"Alibaba"}, "DeepSeek": {"DeepSeek"}, "Moonshot": {"Moonshot AI", "MoonshotAI"},
               "Meta": {"Meta"}, "Mistral": {"Mistral"}}


def required_parameters(ctx, e) -> list[str]:
    req = {"max_tokens", "temperature"}
    oc = e.get("off_config") or {}
    params = dict((oc.get("setting") or {}).get("params") or {})
    if "reasoning" in params or (e.get("reasoning_on_eligible") and e["name"] in ctx.d11["design"]["reasoning_on_rows"]):
        req.add("reasoning")
    if oc.get("response_format") == "json_schema":
        req.add("response_format")
    return sorted(req)


TIER_SUFFIXES = {"flex", "fast", "ultrafast", "priority", "highspeed", "zdr"}


def is_standard(ep) -> bool:
    return not any(seg in TIER_SUFFIXES for seg in str(ep.get("tag", "")).split("/")[1:])


def _throughput(ep):
    t = ep.get("throughput_last_30m")
    if isinstance(t, dict):
        t = t.get("p50")
    return None if t is None else float(t)


def select_host(developer: str, endpoints: list[dict], required: list[str]) -> tuple[dict | None, dict]:
    def price(ep):
        pr = ep.get("pricing") or {}
        return float(pr.get("completion") or 0) * 1e6, float(pr.get("prompt") or 0) * 1e6

    def order(ep):
        t = _throughput(ep)
        return (t is None, -(t or 0.0), *price(ep), ep.get("tag", ""))

    cands = []
    for ep in endpoints:
        sp = set(ep.get("supported_parameters") or [])
        out, inp = price(ep)
        cands.append({"tag": ep.get("tag"), "provider_name": ep.get("provider_name"),
                      "first_party": ep.get("provider_name") in FIRST_PARTY.get(developer, set()),
                      "supports_required": all(r in sp for r in required),
                      "missing_parameters": sorted(r for r in required if r not in sp),
                      "throughput_p50": _throughput(ep), "price_in": inp, "price_out": out,
                      "quantization": ep.get("quantization"), "status": ep.get("status")})
    rec = {"required_parameters": required, "candidates": cands}
    fp = [ep for ep in endpoints if ep.get("provider_name") in FIRST_PARTY.get(developer, set())]
    if fp:
        std = [ep for ep in fp if is_standard(ep)] or fp
        ok = [ep for ep in std if all(r in set(ep.get("supported_parameters") or []) for r in required)]
        rec["first_party_standard_endpoints"] = [ep.get("tag") for ep in std]
        if not ok:
            rec.update(rule_step="1: no standard first-party endpoint supports every required parameter; owner "
                                 "decision needed")
            return None, rec
        rec.update(rule_step="1: first-party host, standard endpoint" + (
            " (highest throughput, then lowest price, among standard first-party endpoints supporting every "
            "required parameter)" if len(std) > 1 else ""))
        return sorted(ok, key=order)[0], rec
    ok = [ep for ep in endpoints if all(r in set(ep.get("supported_parameters") or []) for r in required)]
    if not ok:
        rec.update(rule_step="2: no host supports every required parameter; owner decision needed")
        return None, rec
    rec.update(rule_step="2: highest reported throughput among hosts supporting every required parameter; "
                         "ties by lowest price")
    return sorted(ok, key=order)[0], rec


async def fetch_endpoints(model_id: str, api_key: str, transport=None) -> list[dict]:
    import httpx
    async with httpx.AsyncClient(timeout=60, transport=transport) as c:
        r = await c.get(f"{OPENROUTER_BASE_URL}/models/{model_id}/endpoints",
                        headers={"Authorization": f"Bearer {api_key}"})
        if r.status_code != 200:
            raise ProviderError(r.status_code, r.text)
        return r.json()["data"]["endpoints"]


async def pin_hosts(ctx, endpoints_by_model: dict | None = None) -> dict:
    key = env_key(*KEY_ENV["openrouter"])
    d = ctx.resolved()
    chosen, stops = {}, []
    at = _now()
    for e in d["models"]:
        if P.route_of(e) != "openrouter":
            continue
        eps = (endpoints_by_model or {}).get(e["model_id"])
        if eps is None:
            eps = await fetch_endpoints(e["model_id"], key)
        req = required_parameters(ctx, e)
        waived = [r for r in req if r == "temperature"
                  and not any("temperature" in (x.get("supported_parameters") or []) for x in eps)]
        req = [r for r in req if r not in waived]
        ep, rec = select_host(e["developer"], eps, req)
        rec["waived_parameters"] = waived
        if ep is None:
            stops.append(f"{e['name']}: {rec['rule_step']}")
            continue
        pr = ep.get("pricing") or {}
        e["model_list_price"] = {"price_in": e.get("model_list_price", {}).get("price_in", e["price_in"]),
                                 "price_out": e.get("model_list_price", {}).get("price_out", e["price_out"]),
                                 "source": "OpenRouter /models (live, before pinning)"}
        e["pinned_host"] = {"tag": ep["tag"], "provider_name": ep["provider_name"],
                            "quantization": ep.get("quantization"), "throughput_p50": _throughput(ep),
                            "price_in": float(pr["prompt"]) * 1e6, "price_out": float(pr["completion"]) * 1e6,
                            "status": ep.get("status"), "selected_at": at, "decision": "D12", **rec}
        e["price_in"], e["price_out"] = e["pinned_host"]["price_in"], e["pinned_host"]["price_out"]
        e["price_source"] = f"OpenRouter endpoint {ep['tag']} (live, pinned under D12)"
        e["price_fetched_at"] = at
        chosen[e["name"]] = ep["tag"]
    if stops:
        raise RuntimeError("D12 host rule needs an owner decision: " + "; ".join(stops))
    d["hosts_pinned_at"] = at
    ctx.save_resolved(d)
    return chosen


def freeze_pending_gate(root: Path) -> dict:
    cfg = load_json(Path(root) / CFG)
    ctx = Ctx11(root, providers={}, account_fn=None, spend_file=f"reports/openrouter_spend_attempt{_cfg_attempt(root)}.json",
                or_cap=cfg.get("attempt_openrouter_cap_usd", {}).get(str(_cfg_attempt(root))))
    frozen = write_frozen(ctx, None, None, final=True, status="pending-gate")
    write_gate_history(ctx)
    asyncio.run(ctx.close())
    hashing.freeze(root, {"gate_passed": False, "decision": "D11, D12",
                          "status": f"pre-registered under D12 before gate attempt {_cfg_attempt(root)}"})
    return frozen


def _cfg_attempt(root: Path) -> int:
    return load_json(Path(root) / CFG)["current_attempt"]


def required_balance(mt: dict, buffer: float = 0.15) -> dict:
    need = mt["projected_by_route_usd"]["openrouter"]
    return {"openrouter_projected_usd": round(need, 2), "buffer_share": buffer,
            "openrouter_required_balance_usd": round(need * (1 + buffer), 2)}


async def run_gate_attempt(root: Path, providers: dict | None = None, account_fn=None, freeze: bool = True) -> int:
    a = _cfg_attempt(root)
    cfg = load_json(Path(root) / CFG)
    cap = cfg["attempt_openrouter_cap_usd"][str(a)]
    ctx = Ctx11(root, providers=providers, account_fn=account_fn, spend_file=f"reports/openrouter_spend_attempt{a}.json",
                or_cap=cap, spend_logs=[f"gate_attempt{a}.jsonl"])
    notes, code, gate = [], 0, None
    try:
        for n, _ in P.attempt_cfg(ctx, a)["models"]:
            e = P._entry(ctx, n)
            if P.route_of(e) == "openrouter" and not e.get("pinned_host"):
                raise RuntimeError(f"{n} has no pinned host (D12)")
        st = P.gate_state(ctx)
        if not any(h["attempt"] == a for h in st["attempts"]):
            st["attempts"].append({"attempt": a, "decision": P.attempt_cfg(ctx, a)["decision"],
                                   "n_practice": P.attempt_cfg(ctx, a)["n_practice"],
                                   "practice_seed": P.attempt_cfg(ctx, a)["practice_seed"]})
            P.gate_attempts_path(ctx).write_text(json.dumps(st, indent=2) + "\n")
        await P.part_b(ctx, a)
        gate = P.evaluate_gate(ctx, a)
        next(h for h in st["attempts"] if h["attempt"] == a)["result"] = "PASS" if gate["pass"] else "FAIL"
        P.gate_attempts_path(ctx).write_text(json.dumps(st, indent=2) + "\n")
    except CostCapExceeded as e:
        notes.append(f"STOPPED at the attempt-{a} OpenRouter cap: {e}")
        code = 3
    finally:
        spend = ctx.finish_spend()
        await ctx.close()
    if gate is None:
        gate = P.evaluate_gate(ctx, a)
    write_gate_history(ctx)
    mt = measured_tokens(ctx)
    bal = required_balance(mt)
    mt["required_openrouter_balance"] = bal
    (ctx.root / "configs/measured_tokens.json").write_text(json.dumps(mt, indent=2) + "\n")
    frozen = write_frozen(ctx, gate, mt, final=True)
    write_report(ctx, P.smoke_summary(ctx), gate, mt, frozen, spend, notes)
    if freeze:
        hashing.freeze(root, {"gate_passed": bool(gate and gate["pass"]), "decision": "D11, D12",
                              "status": f"frozen after gate attempt {a}"})
    o = spend["openrouter"]
    print(f"gate attempt {a}: {'PASS' if gate['pass'] else 'FAIL'} (G1 {P.pf(gate['G1'])}, G2 {P.pf(gate['G2'])}, "
          f"G3 {P.pf(gate['G3'])}, G4 {P.pf(gate['G4'])})", flush=True)
    print(f"attempt {a} OpenRouter spend: account ${o.get('account_run_spend_usd') or 0:.4f}, logged "
          f"${o.get('logged_openrouter_usd', 0):.4f} (cap ${cap:.2f})", flush=True)
    print(mt["projection_line"], flush=True)
    print(f"OpenRouter share ${bal['openrouter_projected_usd']:.2f}; Anthropic share "
          f"${mt['projected_by_route_usd']['anthropic']:.2f}; required OpenRouter balance "
          f"${bal['openrouter_required_balance_usd']:.2f} (projection + {int(bal['buffer_share'] * 100)}% buffer); "
          f"projected cumulative ${frozen['projected_cumulative_usd']:.2f} (D5 stop ${frozen['stop_usd']:.0f})", flush=True)
    return code or (0 if gate["pass"] else 6)


def apply_failed_gate_flags(root: Path) -> dict:
    a = _cfg_attempt(root)
    cap = load_json(Path(root) / CFG)["attempt_openrouter_cap_usd"][str(a)]
    ctx = Ctx11(root, providers={}, account_fn=None, spend_file=f"reports/openrouter_spend_attempt{a}.json", or_cap=cap)
    ev = P.evaluate_gate(ctx, a)
    p = Path(root) / "configs/frozen_competition.json"
    frozen = json.loads(p.read_text())
    flags = {}
    if not ev["pass"] and a >= 4:
        m = ev["per_model"]["gpt-oss-120b"]
        failed = [k for k in ("G1", "G2", "G3", "G4") if not m[k]["pass"]]
        flags["gpt-oss-120b"] = (f'instrument not valid for this model (D12: gate attempt {a} failed; gpt-oss-120b '
                                 f'fails {", ".join(failed) or "none"} on its own, C = {m["G3"]["C"]:.3f}); reported '
                                 f'separately, not dropped')
    frozen["flagged_models"] = flags
    frozen["gate_attempt_result"] = {"attempt": a, "pass": ev["pass"],
                                     **{k: ev[k] for k in ("G1", "G2", "G3", "G4")}}
    p.write_text(json.dumps(frozen, indent=2) + "\n")
    write_decisions_block(ctx, frozen)
    asyncio.run(ctx.close())
    hashing.freeze(root, {"gate_passed": bool(ev["pass"]), "decision": "D11, D12",
                          "status": f"frozen after gate attempt {a}"})
    return frozen


GATE_KEYS = ("G1", "G2", "G3", "G4")


def gate_result_record(ev: dict) -> dict:
    return {"attempt": ev["attempt"], "pass": ev["pass"], **{k: ev[k] for k in GATE_KEYS},
            "pooled": {k: bool(ev["pooled"][k]["pass"]) for k in GATE_KEYS},
            "per_model": {n: {**{k: bool(m[k]["pass"]) for k in GATE_KEYS}, "pass": bool(m["pass"]),
                              "U": m["G1"].get("U"), "C": m["G3"].get("C")} for n, m in ev["per_model"].items()}}


def freeze_d13(root: Path) -> dict:
    raise RuntimeError("freeze-d13 is superseded by freeze-v3 (D15)")
    root = Path(root)
    cfg = load_json(root / CFG)
    ctx = P.Ctx(root, providers={}, log_dir=LOG_DIR, gate_path=CFG)
    a = cfg["current_attempt"]
    ev = P.evaluate_gate(ctx, a)
    d14 = cfg["d14"]["max_tokens"]
    res = ctx.resolved()
    for e in res["models"]:
        if e["name"] in d14:
            oc = e["off_config"]
            oc.setdefault("basis", {})["d1_max_tokens"] = oc["basis"].get("d1_max_tokens", oc["max_tokens"])
            oc["basis"]["d14"] = (f"max_tokens raised from {oc['basis']['d1_max_tokens']} to {d14[e['name']]} (D14): "
                                  f"observed reasoning peak {oc['basis'].get('max_reasoning_tokens')} tokens exceeds the "
                                  f"D1 cap of 4,096")
            oc["max_tokens"] = d14[e["name"]]
    ctx.save_resolved(res)
    p = root / "configs/frozen_competition.json"
    f = json.loads(p.read_text())
    for m in f["models"]:
        if m["name"] in d14:
            m["max_tokens"] = d14[m["name"]]
    d13 = cfg["d13"]
    f.update(decision="D11, D12, D13, D14", status="final", frozen_at=_now(),
             gate_attempt_result=gate_result_record(ev),
             gate_override=d13["gate_override"], flag_policy=d13["flag_policy"],
             main_grid_order=d13["main_grid_order"], reasoning_on_order=d13["reasoning_on_order"],
             run_order=d13["run_order"], aborted_first_attempt_logs=d13["aborted_first_attempt_logs"])
    p.write_text(json.dumps(f, indent=2) + "\n")
    write_decisions_block(ctx, f)
    asyncio.run(ctx.close())
    hashing.freeze(root, {"gate_passed": False, "decision": "D11, D12, D13, D14",
                          "status": "frozen under D13 (gate override) and D14 (max_tokens), before the pinned-host smoke"})
    return f


PINNED_SMOKE_LOG = "smoke_pinned"


def pinned_smoke_items(ctx, sc: dict):
    from .scenarios import generate_scenarios, params_of, practice_items
    excl = set(params_of(ctx.scen_main))
    for a in sc["disjoint_from_attempts"]:
        excl |= params_of(ctx.scen_prac_by_attempt[str(a)])
    scs = generate_scenarios(sc["practice_seed"], ctx.study, ctx.templates, sc["practice_prefix"], exclude=excl)
    assert not (params_of(scs) & excl)
    ctx.by_id.update({s.scenario_id: s for s in scs})
    items = practice_items(scs, ctx.study, sc["calls_per_model"], seed=sc["practice_seed"])
    assert all(it.arm == "practice" and it.scenario_id.startswith(sc["practice_prefix"] + "-") for it in items)
    return items


def evaluate_pinned_smoke(ctx, sc: dict, n_items: int) -> dict:
    from collections import Counter
    log = ctx.log_dir / f"{PINNED_SMOKE_LOG}.jsonl"
    errp = ctx.log_dir / f"{PINNED_SMOKE_LOG}.errors.jsonl"
    recs = read_jsonl(log) if log.exists() else []
    errs = read_jsonl(errp) if errp.exists() else []
    out = {}
    for name in sc["models"]:
        e = P._entry(ctx, name)
        oc = e["off_config"]
        rs = [r for r in recs if r["model_name"] == name and not str(r.get("config", "")).startswith("on")]
        es = [x for x in errs if x["key"].split("|")[0] == name]
        rt = [r.get("reasoning_tokens") for r in rs]
        unparse = sum(r["status"] != "ok" for r in rs)
        limit = sum(bool(r.get("truncated")) or "length" in (r.get("finish_reasons") or []) for r in rs)
        served = Counter(h for r in rs for h in (r.get("upstream_providers") or []))
        off = oc["label_class"] == P.LABEL_OFF
        checks = {
            "all_calls_returned": len(rs) == n_items,
            "answers_parse": bool(rs) and unparse / len(rs) <= sc["max_unparseable_share"],
            "output_limit_not_hit": bool(rs) and limit == 0,
            "reasoning_setting_honored": bool(rs) and (all(x == 0 for x in rt) if off else all(x is not None for x in rt)),
            "served_by_pinned_host": bool(rs) and not any(r.get("host_mismatch") for r in rs),
        }
        out[name] = {"pinned_host": e["pinned_host"]["tag"], "label": oc["label_class"],
                     "setting": oc["setting"]["label"], "max_tokens": oc["max_tokens"],
                     "temperature_sent": rs[0].get("temperature") if rs else None,
                     "calls": len(rs), "errors": len(es), "error_samples": sorted({x["error"][:160] for x in es})[:3],
                     "unparseable": unparse, "output_limit_hit": limit,
                     "reasoning_tokens_min": min((x for x in rt if x is not None), default=None),
                     "reasoning_tokens_max": max((x for x in rt if x is not None), default=None),
                     "reasoning_tokens_not_reported": sum(x is None for x in rt),
                     "max_output_tokens": max((r.get("output_tokens") or 0 for r in rs), default=None),
                     "served_by": dict(served), "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rs), 6),
                     "checks": checks, "pass": all(checks.values())}
    for name in sc.get("on_models", []):
        e = P._entry(ctx, name)
        rs = [r for r in recs if r["model_name"] == name and str(r.get("config", "")).startswith("on")]
        rt = [r.get("reasoning_tokens") for r in rs]
        unparse = sum(r["status"] != "ok" for r in rs)
        limit = sum(bool(r.get("truncated")) or "length" in (r.get("finish_reasons") or []) for r in rs)
        checks = {
            "all_calls_returned": len(rs) == n_items,
            "answers_parse": bool(rs) and unparse / len(rs) <= sc["max_unparseable_share"],
            "output_limit_not_hit": bool(rs) and limit == 0,
            "reasoning_setting_honored": bool(rs) and all(x is not None and x > 0 for x in rt),
            "served_by_pinned_host": bool(rs) and not any(r.get("host_mismatch") for r in rs),
        }
        out[name + " [reasoning on]"] = {
            "pinned_host": e["pinned_host"]["tag"], "setting": e["reasoning_on_setting"]["label"], "calls": len(rs),
            "unparseable": unparse, "output_limit_hit": limit,
            "reasoning_tokens_min": min((x for x in rt if x is not None), default=None),
            "reasoning_tokens_max": max((x for x in rt if x is not None), default=None),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rs), 6),
            "checks": checks, "pass": all(checks.values())}
    return out


def openrouter_credits(api_key: str) -> dict:
    import httpx
    with httpx.Client(timeout=60) as c:
        r = c.get(f"{OPENROUTER_BASE_URL}/credits", headers={"Authorization": f"Bearer {api_key}"})
        if r.status_code != 200:
            raise ProviderError(r.status_code, r.text)
        d = r.json()["data"]
    return {"total_credits_usd": d["total_credits"], "total_usage_usd": d["total_usage"],
            "balance_usd": round(d["total_credits"] - d["total_usage"], 6), "checked_at": _now()}


async def run_pinned_smoke(root: Path, providers: dict | None = None, account_fn=None, endpoints=None,
                           credits=None, section: str = "pinned_smoke") -> int:
    root = Path(root)
    sc = load_json(root / CFG)[section]
    ctx = Ctx11(root, providers=providers, account_fn=account_fn, spend_file=sc["spend_file"],
                or_cap=sc["openrouter_cap_usd"], spend_logs=[f"{PINNED_SMOKE_LOG}.jsonl"])
    items = pinned_smoke_items(ctx, sc)
    jobs = []
    for name in sc["models"]:
        e = P._entry(ctx, name)
        assert P.route_of(e) == "openrouter" and e.get("pinned_host"), name
        jobs += [P.off_job(ctx, it, e) for it in items]
    for name in sc.get("on_models", []):
        e = P._entry(ctx, name)
        assert P.route_of(e) == "openrouter" and e.get("pinned_host"), name
        jobs += [P.on_job(ctx, it, e) for it in items]
    started = _now()
    capped = None
    try:
        await ctx.runner(PINNED_SMOKE_LOG).run(jobs)
    except CostCapExceeded as exc:
        capped = str(exc)
    ctx.or_ledger.reconcile("end")
    spend = ctx.spend_state()
    spend.update(logged_openrouter_usd=_route_spend([ctx.log_dir / f"{PINNED_SMOKE_LOG}.jsonl"], "openrouter"),
                 account_run_spend_usd=ctx.or_ledger.account_spent, checked_at=_now())
    ctx.save_spend_state(spend)
    res = evaluate_pinned_smoke(ctx, sc, len(items))
    key = env_key(*KEY_ENV["openrouter"]) if providers is None else None
    status = {}
    for name in sc["record_endpoint_status"]:
        e = P._entry(ctx, name)
        eps = endpoints(name) if endpoints else (await fetch_endpoints(P.mid(e), key) if key else [])
        tag = e["pinned_host"]["tag"]
        ep = next((x for x in eps if x.get("tag") == tag), None)
        status[name] = {"pinned_tag": tag, "status_at_pinning": e["pinned_host"].get("status"),
                        "status_now": ep.get("status") if ep else "endpoint not listed",
                        "uptime_last_30m": ep.get("uptime_last_30m") if ep else None, "checked_at": _now()}
    cred = credits() if credits else (openrouter_credits(key) if key else None)
    report = {"decision": sc["decision"], "started_at": started, "finished_at": _now(),
              "practice_seed": sc["practice_seed"], "practice_prefix": sc["practice_prefix"],
              "calls_per_model": len(items), "items": [it.item_id for it in items],
              "competition_scenarios_sent": 0, "cap_usd": sc["openrouter_cap_usd"], "cap_stopped": capped,
              "spend": {"logged_usd": spend["logged_openrouter_usd"], "account_usage_endpoint_usd": spend["account_run_spend_usd"],
                        "baseline_usage_usd": spend.get("baseline_usage_usd")},
              "models": res, "failed": [n for n, r in res.items() if not r["pass"]],
              "endpoint_status": status, "openrouter_credits": cred}
    (root / "reports").mkdir(exist_ok=True)
    (root / sc["report_file"]).write_text(json.dumps(report, indent=2) + "\n")
    await ctx.close()
    return 6 if capped else (3 if report["failed"] else 0)


V3_MAIN_ORDER = ["Claude Opus 5.5", "GPT-6.1 Sol", "Gemini 4 Argon", "Grok 4.7", "Qwen3.8 Max", "Gemini 3.8 Flash",
                 "GPT-6 Luna", "Kimi K3.1 Pro", "DeepSeek V4 Pro", "Claude Haiku 5.5", "gpt-oss-120b", "GPT-4o",
                 "Qwen3.8 27B", "Llama 3.1 8B"]
V3_ESCALATION = ["Claude Opus 5.5", "GPT-6.1 Sol", "Gemini 4 Argon", "Grok 4.7", "Qwen3.8 Max", "DeepSeek V4 Pro"]
V3_ON_ORDER = ["DeepSeek V4 Pro", "Claude Haiku 5.5"]


def v3_projection(mt: dict, study: dict) -> dict:
    n_main = 40 * len(study["lambdas"]) * len(study["tones"]) * study["draws_main"]
    n_ctrl = 40 * len(study["lambdas"]) * len(study["tones"]) * study["draws_control"]
    esc = study["escalation"]
    n_esc = 40 * len(esc["tones"]) * esc["draws"] * (esc["turns"] - 1)
    out = {}
    for n, v in mt["models"].items():
        out[n] = round((n_main + n_ctrl) * v["usd_per_call_projection"], 4)
    for n in V3_ESCALATION:
        v = mt["models"][n]
        extra_in = (esc["turns"] / 2) * (v["mean_output_tokens"] + 60)
        per = ((v["mean_input_tokens"] + extra_in) * v["price_in"] + v["mean_output_tokens"] * v["price_out"]) / 1e6
        out[f"{n} [escalation]"] = round(n_esc * per, 4)
    for n in V3_ON_ORDER:
        out[f"{n} [reasoning on]"] = round((n_main + n_ctrl) * mt["reasoning_on_rows"][n]["usd_per_call_projection"], 4)
    return out


def freeze_v3(root: Path) -> dict:
    root = Path(root)
    p = root / "configs/frozen_competition.json"
    f = json.loads(p.read_text())
    study = load_json(root / "configs/study.json")
    mt = load_json(root / "configs/measured_tokens.json")
    proj = v3_projection(mt, study)
    n_main = 40 * len(study["lambdas"]) * len(study["tones"]) * study["draws_main"]
    n_ctrl = 40 * len(study["lambdas"]) * len(study["tones"]) * study["draws_control"]
    names = [m["name"] for m in f["models"]]
    assert sorted(V3_MAIN_ORDER) == sorted(names), "v3 main order must list every frozen model once"
    for r in f["reasoning_on_rows"]:
        r["calls"] = n_main + n_ctrl
    by_route = {"anthropic": 0.0, "openrouter": 0.0}
    route = {m["name"]: m["route"] for m in f["models"]}
    for k, v in proj.items():
        by_route[route[k.split(" [")[0]]] += v
    total = round(sum(proj.values()), 2)
    f.update(
        decision="D11, D12, D14, D15 (design v3)", status="final", frozen_at=_now(),
        draws_main=study["draws_main"], calls_per_model_main=n_main, calls_per_model_control=n_ctrl,
        instrument_certification={"certified": True, "model": "DeepSeek V4 Pro (reasoning on)",
                                  "evidence": "pre-sprint control arms of gate attempts 1, 3 and 4: U 0.986-1.001, "
                                              "|C| <= 0.016"},
        flagged_models={}, flag_policy="v3 (D15): every configuration is judged on its own competition control arm; "
                                       "G3 for the claims about capitulation and the headline, G1 and G3 for the "
                                       "claims about conservatism; failing configurations run and are reported",
        control_arm_halting="v3 (D15): after all control arms, halt only if fewer than 5 configurations pass their "
                            "own G3 or pooled G4 over the configurations that pass G1 fails",
        headline_rule="v3 (D15): equal-weight mean of per-model disavowed shares (0.5 nats after 'none', 1 nat after "
                      "'small') over base configurations passing G3 with >= 20 qualifying trials",
        main_grid_order=V3_MAIN_ORDER, reasoning_on_order=V3_ON_ORDER,
        escalation={"models": V3_ESCALATION, "turns": study["escalation"]["turns"], "lambda": "1"},
        run_order="control arms for all configurations (reasoning-on rows included), then the halting check; main "
                  "grids in main_grid_order; then the escalation arm; then the reasoning-on rows; a step starts only if "
                  "spend plus 1.2 x its projection stays under the stop (D15)",
        stop_usd=62.0, hard_cap_usd=67.0, caps_scope="competition run only; prior spend excluded (D15)",
        projected_by_step_usd=proj, projected_competition_usd=total,
        projected_by_route_usd={k: round(v, 2) for k, v in by_route.items()},
        projected_cumulative_usd=total)
    f.pop("gate_override", None)
    f["prior_spend_usd_excluded"] = f.pop("prior_spend_usd", f.get("prior_spend_usd_excluded"))
    p.write_text(json.dumps(f, indent=2) + "\n")
    ctx = P.Ctx(root, providers={}, log_dir=LOG_DIR, gate_path=CFG)
    write_decisions_block(ctx, f)
    asyncio.run(ctx.close())
    hashing.freeze(root, {"gate_passed": False, "decision": "D15 (design v3)",
                          "status": "frozen under design v3 before any competition data"})
    return f
