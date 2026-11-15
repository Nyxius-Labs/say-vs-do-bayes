from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from . import prelim as P
from . import scoring
from .costs import AnthropicLedger, CostCapExceeded, CostTracker, OpenRouterLedger, spent_from_logs
from .runner import RateLimiter, Runner, read_jsonl
from .scenarios import build_items, control_arm

LOG_DIR = "logs/competition"


MIN_VALID_MODELS = 10
MIN_VALID_CONFIGS = 5
START_MARGIN = 1.2
RESELLER_RPM = None
EXIT_INCOMPLETE = 5
EXIT_STOP_RULE = 6
FLAG = "instrument not valid for this model"
GATES = ("G1", "G2", "G3", "G4")


def certified(f: dict) -> tuple[bool, str]:
    c = f.get("instrument_certification") or {}
    if c.get("certified") is True and c.get("model") and c.get("evidence"):
        return True, f"certified on {c['model']} ({c['evidence']})"
    if f.get("gate_passed"):
        return True, "preliminary gate passed"
    return False, "no instrument certification recorded in the frozen configuration"


ON_SUFFIX = " [reasoning on]"


def config_label(name: str, config: str) -> str:
    return name + ON_SUFFIX if str(config).startswith("on@") else name


def main_grid_order(f: dict) -> list[str]:
    names = [m["name"] for m in f["models"]]
    order = f.get("main_grid_order") or names
    assert sorted(order) == sorted(names), "main_grid_order must list every frozen model exactly once"
    return order


def reasoning_on_order(f: dict) -> list[dict]:
    rows = {r["name"]: r for r in f["reasoning_on_rows"]}
    order = f.get("reasoning_on_order") or list(rows)
    assert sorted(order) == sorted(rows), "reasoning_on_order must list every reasoning-on row exactly once"
    return [rows[n] for n in order]


def plan(root: Path, providers: dict | None = None) -> dict:
    frozen = json.loads((root / "configs/frozen_competition.json").read_text())
    ctx = P.Ctx(root, providers=providers)
    by = {e["name"]: e for e in ctx.resolved()["models"]}
    for m in frozen["models"]:
        if frozen.get("status") == "pending":
            break
        oc = by[m["name"]]["off_config"]
        assert (oc["setting"], oc["response_format"], oc["max_tokens"]) == (
            m["reasoning_setting"], m["response_format"], m["max_tokens"]), m["name"]
    study = dict(ctx.study, draws_main=frozen["draws_main"])
    main = build_items("main", ctx.scen_main, study, study["master_seed"], frozen["draws_main"])
    return {"ctx": ctx, "frozen": frozen, "control": control_arm(ctx.scen_main, ctx.study), "main": main}


def describe(p: dict) -> dict:
    f = p["frozen"]
    return {"models": [m["name"] for m in f["models"]], "draws_main": f["draws_main"],
            "control_calls": len(p["control"]) * (len(f["models"]) + len(f["reasoning_on_rows"])),
            "main_calls": len(p["main"]) * len(f["models"]),
            "reasoning_on_rows": [r["name"] for r in f["reasoning_on_rows"]],
            "reasoning_on_calls": len(p["main"]) * len(f["reasoning_on_rows"]),
            "escalation_models": (f.get("escalation") or {}).get("models", []),
            "escalation_calls_max": sum(1 for it in p["main"] if it.lam == "1" and it.draw == 0)
            * 2 * len((f.get("escalation") or {}).get("models", [])),
            "projected_competition_usd": f["projected_competition_usd"], "stop_usd": f["stop_usd"]}


def d9_decision(ctx, control_records, model_names, flagged: dict | None = None) -> dict:
    flagged = flagged or {}
    g = ctx.study["gates"]
    recs = scoring.with_deltas(control_records, tuple(ctx.study["p1_clip_pct"]))
    for r in recs:
        r["config_label"] = config_label(r["model_name"], r.get("config", ""))
    per = {}
    for name in model_names:
        r = [x for x in recs if x["config_label"] == name]
        g1 = scoring.gate_G1(r, g["G1_max_abs_U_minus_1"])
        g3 = scoring.gate_G3(r, g["G3_max_abs_C_control"])
        pre = name in flagged
        cap_ok = g3["pass"] and not pre
        per[name] = {"G1": g1, "G3": g3, "instrument_valid": cap_ok, "valid_for_capitulation": cap_ok,
                     "valid_for_conservatism": cap_ok and g1["pass"],
                     "flag": None if cap_ok and g1["pass"] else FLAG,
                     "flag_scope": None if cap_ok and g1["pass"] else
                     ("all claims" if not cap_ok else "conservatism claims only"),
                     "flag_reason": flagged.get(name)}
    g1_pass = [n for n in model_names if per[n]["G1"]["pass"]]
    g3_pass = [n for n in model_names if per[n]["G3"]["pass"]]
    g4 = scoring.gate_G4([x for x in recs if x["config_label"] in g1_pass], g["G4_min_spearman"], g["G4_level"])
    reasons = []
    if len(g3_pass) < MIN_VALID_CONFIGS:
        reasons.append(f"only {len(g3_pass)} configurations pass their own control-arm G3 (need {MIN_VALID_CONFIGS})")
    if not g4["pass"]:
        reasons.append(f"pooled G4 over the G1-passing configurations fails (rho={g4['rho']})")
    return {"per_model": per, "n_g1_pass": len(g1_pass), "g1_pass_models": g1_pass, "n_g3_pass": len(g3_pass),
            "g3_pass_models": g3_pass, "pooled_G4_over_G1_pass": g4, "halt": bool(reasons), "halt_reasons": reasons}


def log_dir_of(root: Path, frozen: dict) -> Path:
    return root / frozen.get("log_dir", LOG_DIR)


def write_results(root: Path, ctx, decision: dict, log_dir: Path | None = None) -> None:
    log_dir = log_dir or root / LOG_DIR
    rows = []
    for path in sorted(log_dir.glob("*.jsonl")):
        if path.name.endswith(".errors.jsonl"):
            continue
        recs = read_jsonl(path)
        for name in sorted({r["model_name"] for r in recs}):
            for cfg in sorted({r["config"] for r in recs if r["model_name"] == name}):
                rs = [r for r in recs if r["model_name"] == name and r["config"] == cfg]
                s = scoring.summarize(rs, ctx.study)
                valid = decision["per_model"].get(config_label(name, cfg), {}).get("instrument_valid", False)
                rt = [r.get("reasoning_tokens") for r in rs]
                rows.append({"model": name, "config": cfg, "arm": path.stem, "instrument_valid": valid,
                             "mean_reasoning_tokens": (sum(x or 0 for x in rt) / len(rt)) if rt else None,
                             "reasoning_tokens_reported": any(x is not None for x in rt),
                             "flag": "" if valid else FLAG, "n_valid": s["n_valid"], "n_missing": s["n_missing"],
                             "U": s["U"], "conservatism": s["conservatism"], "C": s["C"], "C_neutral": s["C_neutral"],
                             "C_insistent": s["C_insistent"], "S": s["S"], "exp_S": s["exp_S"],
                             "disavowed_share": s["disavowed_share"], "disavowed_n": s["disavowed_n_qualifying"],
                             "disavowed_status": s["disavowed_status"],
                             "credited_empty_share": s["credited_empty_share"], "credited_empty_n": s["credited_empty_n"],
                             "empty_ratings": " ".join(f"{k}={v}" for k, v in s["empty_rating_distribution"].items())})
    out = root / "reports"
    out.mkdir(exist_ok=True)
    if rows:
        import csv
        with open(out / "competition_results.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    fmt = lambda x: "n/a" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x))
    L = ["# Competition results", "", f"Halting decision (D9): {'HALT' if decision['halt'] else 'continue'}; "
         f"{decision.get('n_g3_pass')} configurations pass their own control-arm G3 and {decision['n_g1_pass']} pass G1; "
         f"pooled G4 over the G1 passes rho = "
         f"{fmt(decision['pooled_G4_over_G1_pass']['rho'])}.", ""]
    head = headline(root, ctx, decision, log_dir)
    if head:
        L += ["## Headline (D10)", "",
              f"Disavowed capitulation (equal-weight mean of per-model shares of qualifying empty-message trials "
              f"moving more than 0.5 nats after 'none' or 1 nat after 'small', base configurations passing G3) = "
              f"**{fmt(head['share'])}** from **{head['n_models']} models** ({head['n_trials']} qualifying trials; "
              f"trial-pooled share {fmt(head['share_pooled_trials'])}): {', '.join(head['models']) or 'none'}. "
              f"Inclusion follows from G3 and the {ctx.study['disavowed']['min_qualifying_trials']}-trial rule alone.", "",
              "Not in the headline: " + ("; ".join(f"{n} ({why})" for n, why in head["left_out"].items()) or "none") + ".", ""]
        (out / "competition_headline.json").write_text(json.dumps(head, indent=2) + "\n")
    for title, valid in (("Instrument valid (eligible for headline claims)", True),
                         (f"Flagged: {FLAG} (reported separately, excluded from headline claims)", False)):
        L += [f"## {title}", "", "| Model | Config | Arm | U | Conservatism | C | S | Disavowed share (n) | "
              "Credited-empty share (n) | Empty-message ratings |", "|---|---|---|---|---|---|---|---|---|---|"]
        sel = [r for r in rows if r["instrument_valid"] == valid]
        L += [f"| {r['model']} | {r['config']} | {r['arm']} | {fmt(r['U'])} | {fmt(r['conservatism'])} | {fmt(r['C'])} | "
              f"{fmt(r['S'])} | {fmt(r['disavowed_share']) if r['disavowed_share'] is not None else r['disavowed_status']} "
              f"({r['disavowed_n']}) | {fmt(r['credited_empty_share'])} ({r['credited_empty_n']}) | {r['empty_ratings']} |"
              for r in sel] or ["| none | | | | | | | | | |"]
        L.append("")
    L += ["## Control arm, all models (flagged models included)", "",
          "| Model | U | C (nats) | G1 | G3 | Instrument valid | Flag |", "|---|---|---|---|---|---|---|"]
    for name, d in decision["per_model"].items():
        L.append(f"| {name} | {fmt(d['G1']['U'])} | {fmt(d['G3']['C'])} | {'PASS' if d['G1']['pass'] else 'FAIL'} | "
                 f"{'PASS' if d['G3']['pass'] else 'FAIL'} | {d['instrument_valid']} | {d['flag'] or ''} |")
    L.append("")
    L += ["## Reasoning tokens versus capitulation (descriptive, not causal)", "",
          "Measured mean reasoning tokens per call against capitulation C on empty cells, per configuration in the "
          "main grid and reasoning-on rows. Most models think even at their lowest setting, so this is a description "
          "across models, not an estimate of the effect of reasoning.", "",
          "| Model | Config | Mean reasoning tokens | C (nats) | Disavowed share (n) | Instrument valid |",
          "|---|---|---|---|---|---|"]
    for r in sorted((r for r in rows if r["arm"] != "control"), key=lambda r: (r["mean_reasoning_tokens"] or 0)):
        tok = fmt(r["mean_reasoning_tokens"]) if r["reasoning_tokens_reported"] else "not reported"
        L.append(f"| {r['model']} | {r['config']} | {tok} | {fmt(r['C'])} | {fmt(r['disavowed_share'])} "
                 f"({r['disavowed_n']}) | {r['instrument_valid']} |")
    L.append("")
    (out / "competition_results.md").write_text("\n".join(L) + "\n")
    (out / "competition_validity.json").write_text(json.dumps(decision, indent=2, default=str) + "\n")


def headline(root: Path, ctx, decision: dict, log_dir: Path | None = None) -> dict | None:
    path = (log_dir or root / LOG_DIR) / "main.jsonl"
    if not path.exists():
        return None
    recs = scoring.with_deltas(read_jsonl(path), tuple(ctx.study["p1_clip_pct"]))
    dv = ctx.study["disavowed"]
    valid = [n for n, d in decision["per_model"].items() if d["instrument_valid"] and not n.endswith(ON_SUFFIX)]
    h = scoring.pooled_headline({n: [r for r in recs if r["model_name"] == n] for n in valid},
                                dv.get("thresholds_by_rating_nats") or dv["headline_threshold_nats"],
                                dv["min_qualifying_trials"], tuple(dv["low_ratings"]))
    left = {n: FLAG for n, d in decision["per_model"].items()
            if not d["instrument_valid"] and not n.endswith(ON_SUFFIX)}
    left.update({n: f"{scoring.NOT_ESTIMABLE} ({k} qualifying trials)" for n, k in h["not_estimable"].items()})
    h["left_out"] = left
    return h


def step_list(f: dict) -> list[tuple[str, str, str]]:
    steps = [("main", n, "main") for n in main_grid_order(f)]
    steps += [("escalation", n, "escalation") for n in (f.get("escalation") or {}).get("models", [])]
    steps += [(f"reasoning_on_{row['name'].replace(' ', '_')}", row["name"], "on") for row in reasoning_on_order(f)]
    return steps


def step_label(log: str, name: str, kind: str) -> str:
    return {"main": name, "escalation": f"{name} [escalation]", "on": name + ON_SUFFIX}[kind]


def admit_step(spent: float, projected: float | None, stop: float, margin: float = START_MARGIN) -> bool:
    return projected is None or spent + margin * projected <= stop


async def run_escalation(ctx, p: dict, e: dict, runner_for, main_log: Path, valid: bool) -> int:
    from .runner import read_jsonl as _r
    from .scenarios import escalation_next
    esc = ctx.study["escalation"]
    by_id = {it.item_id: it for it in p["main"]}
    prev = [r for r in _r(main_log) if r["model_name"] == e["name"] and str(r.get("config", "")).startswith("off")
            and r["lam"] == esc["lambda"] and r["cell"]["draw"] < esc["draws"] and r["status"] == "ok"]
    items = [escalation_next(replace(by_id[r["item_id"]], arm="escalation"), r["raw_response"][-1]) for r in prev]
    r = runner_for("escalation")
    n = 0
    for turn in range(2, esc["turns"] + 1):
        jobs = [replace(P.off_job(ctx, it, e), extra={"instrument_valid": valid, "turn": turn}) for it in items]
        await r.run(jobs)
        n += len(jobs)
        got = {x["item_id"]: x for x in _r(r.log_path) if x["model_name"] == e["name"]}
        items = [escalation_next(it, got[it.item_id]["raw_response"][-1]) for it in items
                 if it.item_id in got and got[it.item_id]["status"] == "ok"]
    return n


async def run(root: Path, p: dict, retry_kwargs: dict | None = None, rpm: int | None = RESELLER_RPM) -> int:
    ctx, f = p["ctx"], p["frozen"]
    ok, why = certified(f)
    if not ok:
        print(f"REFUSED: {why}", flush=True)
        await ctx.close()
        return 4
    print(f"Instrument {why}", flush=True)
    log_dir = log_dir_of(root, f)
    log_dir.mkdir(parents=True, exist_ok=True)
    comp_logs = [x for x in log_dir.glob("*.jsonl") if not x.name.endswith(".errors.jsonl")]
    tracker, trackers = spend_trackers(root, ctx, f, comp_logs)
    print(f"spend at start: ${tracker.spent:.4f} (stop ${f['stop_usd']:.0f})", flush=True)

    limiter = RateLimiter(rpm) if rpm else None

    def runner(name):
        return Runner(log_dir / f"{name}.jsonl", tracker, ctx.by_id, ctx.templates,
                      default_concurrency=P.RESELLER_CONCURRENCY, retry_kwargs=retry_kwargs, limiter=limiter,
                      trackers=trackers)

    models = [P._entry(ctx, m["name"]) for m in f["models"]]
    on_rows = [P._entry(ctx, row["name"]) for row in reasoning_on_order(f)]
    labels = [e["name"] for e in models] + [e["name"] + ON_SUFFIX for e in on_rows]
    proj = f.get("projected_by_step_usd") or {}
    try:
        r = runner("control")
        control_jobs = [P.off_job(ctx, it, e) for e in models for it in p["control"]]
        control_jobs += [P.on_job(ctx, it, e) for e in on_rows for it in p["control"]]
        await r.run(control_jobs)
        logged = {x["key"] for x in read_jsonl(r.log_path)}
        missing = [j.key for j in control_jobs if j.key not in logged]
        if missing:
            print(f"INCOMPLETE control arm: {len(missing)} of {len(control_jobs)} calls not logged; no halting "
                  f"decision was taken. Re-run `run --registered` to resume (logged calls are not repeated).", flush=True)
            return EXIT_INCOMPLETE
        decision = d9_decision(ctx, read_jsonl(r.log_path), labels, f.get("flagged_models"))
        write_results(root, ctx, decision, log_dir)
        if decision["halt"]:
            print("HALT (v3): " + "; ".join(decision["halt_reasons"]), flush=True)
            return 4
        valid = {n: v["instrument_valid"] for n, v in decision["per_model"].items()}
        by_name = {e["name"]: e for e in models}
        main_r = runner("main")
        done = []
        steps = step_list(f)
        for i, (log, name, kind) in enumerate(steps):
            lab = step_label(log, name, kind)
            if not admit_step(tracker.spent, proj.get(lab), f["stop_usd"]):
                cut = [step_label(*s) for s in steps[i:]]
                (log_dir / "stop_rule_cut.json").write_text(json.dumps(
                    {"reason": f"spend ${tracker.spent:.2f} + {START_MARGIN} x projected ${proj.get(lab)} for {lab} "
                               f"exceeds the ${f['stop_usd']:.0f} stop", "completed": done, "not_run": cut,
                     "rule": "v3 start check; whole steps cut from the end of the registered order"}, indent=2) + "\n")
                print(f"STOP RULE (v3 start check): not run: {', '.join(cut)}", flush=True)
                write_results(root, ctx, decision, log_dir)
                return EXIT_STOP_RULE
            try:
                if kind == "escalation":
                    await run_escalation(ctx, p, by_name[name], runner, main_r.log_path, valid[name])
                else:
                    e = by_name[name] if kind == "main" else P._entry(ctx, name)
                    rr = main_r if kind == "main" else runner(log)
                    jobs = [replace(P.off_job(ctx, it, e) if kind == "main" else P.on_job(ctx, it, e),
                                    extra={"instrument_valid": valid[lab]}) for it in p["main"]]
                    await rr.run(jobs)
            except CostCapExceeded as exc:
                cut = [step_label(*s) for s in steps[i:]]
                (log_dir / "stop_rule_cut.json").write_text(json.dumps(
                    {"reason": str(exc), "completed": done, "partial_then_cut": cut[0], "not_run": cut[1:],
                     "rule": "stop reached mid-step despite the start check; reported as a partial step"},
                    indent=2) + "\n")
                print(f"STOP RULE: {exc}; cut: {', '.join(cut)}", flush=True)
                write_results(root, ctx, decision, log_dir)
                return EXIT_STOP_RULE
            done.append(lab)
        write_results(root, ctx, decision, log_dir)
        write_escalation(root, ctx, log_dir, main_r.log_path)
    finally:
        for t in trackers.values():
            if isinstance(t, OpenRouterLedger):
                t.reconcile("end of run")
                (log_dir / "openrouter_reconciliation.json").write_text(json.dumps(t.history, indent=2) + "\n")
        await ctx.close()
    return 0


def write_escalation(root: Path, ctx, log_dir: Path, main_log: Path) -> dict:
    esc_log = log_dir / "escalation.jsonl"
    if not esc_log.exists():
        return {}
    later = read_jsonl(esc_log)
    names = sorted({x["model_name"] for x in later})
    esc = ctx.study["escalation"]
    first = [dict(x, turn=1) for x in read_jsonl(main_log) if x["model_name"] in names
             and str(x.get("config", "")).startswith("off") and x["lam"] == esc["lambda"]
             and x["cell"]["draw"] < esc["draws"]]
    th = ctx.study["disavowed"].get("thresholds_by_rating_nats")
    out = {n: scoring.escalation_summary([x for x in first + later if x["model_name"] == n],
                                         tuple(ctx.study["p1_clip_pct"]), th) for n in names}
    (root / "reports").mkdir(exist_ok=True)
    (root / "reports/competition_escalation.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


def _route_spend(paths, route) -> float:
    return sum(float(r.get("cost_usd") or 0) for p in paths for r in read_jsonl(p) if r.get("route") == route)


def spend_trackers(root: Path, ctx, f: dict, comp_logs) -> tuple[CostTracker, dict]:
    m = ctx.gate_cfg["logs_only_spend_multiplier"]
    if "prior_spend_usd" not in f and not f.get("caps_scope", "").startswith("competition run only"):
        return CostTracker(f["stop_usd"], f["prelim_spent_usd"] + m * spent_from_logs(comp_logs), multiplier=m), {}
    sp_or, sp_an = _route_spend(comp_logs, "openrouter"), _route_spend(comp_logs, "anthropic")
    v3 = "prior_spend_usd" not in f and f.get("caps_scope", "").startswith("competition run only")
    prior = 0.0 if v3 else f["prior_spend_usd"]["total"]
    group = CostTracker(f["stop_usd"], prior + sp_or + sp_an)
    key = P.env_key(*P.KEY_ENV["openrouter"]) if ctx._providers is None else None
    acct = None
    if key:
        from .providers import openrouter_key_usage
        acct = lambda: openrouter_key_usage(key)
    baseline = f.get("openrouter_baseline_usage_usd") or 0.0
    if v3:
        bp = log_dir_of(root, f) / "openrouter_baseline.json"
        if bp.exists():
            baseline = json.loads(bp.read_text())["baseline_usage_usd"]
        else:
            baseline = (acct()["usage_usd"] - sp_or) if acct else 0.0
            bp.parent.mkdir(parents=True, exist_ok=True)
            bp.write_text(json.dumps({"baseline_usage_usd": baseline, "rule": "account usage at first launch "
                                      "minus logged competition OpenRouter spend (D15)"}, indent=2) + "\n")
    orl = OpenRouterLedger(float("inf"), sp_or, acct, baseline, every=500, group=group)
    orl.reconcile("start")
    return group, {"openrouter": orl, "anthropic": AnthropicLedger(float("inf"), sp_an, group=group)}
