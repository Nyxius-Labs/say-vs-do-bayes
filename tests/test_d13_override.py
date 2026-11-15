import asyncio
import json
import types

import pytest

from dc import competition as C
from dc import prelim as P
from dc import prelim_d11 as D
from dc.costs import CostCapExceeded
from dc.scenarios import control_arm, load_study, main_scenarios, params_of

STUDY, TEMPLATES = load_study()
SCS = main_scenarios()
BY = {s.scenario_id: s for s in SCS}

CERT = {"certified": True, "model": "DeepSeek V4 Pro (reasoning on)",
        "evidence": "pre-sprint control arms: U 0.986-1.001, |C| <= 0.016"}


def test_v3_certification_replaces_the_gate_override():
    assert C.certified({"gate_passed": False, "instrument_certification": CERT})[0]
    assert C.certified({"gate_passed": True})[0]
    for bad in ({}, {"instrument_certification": dict(CERT, certified=False)},
                {"instrument_certification": dict(CERT, evidence="")}):
        assert not C.certified(dict(bad, gate_passed=False))[0]
    assert not hasattr(C, "gate_override")


def _bayes_control(names):
    recs = []
    for name in names:
        for it in control_arm(SCS, STUDY):
            sc = BY[it.scenario_id]
            recs.append({"model_name": name, "scenario_id": it.scenario_id, "lam": it.lam, "tone": it.tone,
                         "p0": float(sc.p0), "weight_rating": "moderate",
                         "probability": round(100 * float(sc.p_star(it.lam)))})
    return recs


def _moving_control(names, shift):
    out = _bayes_control(names)
    for r in out:
        if r["lam"] == "1":
            p = BY[r["scenario_id"]].p0
            o = float(p / (1 - p)) / 2.718281828459045 ** shift
            r["probability"] = round(100 * o / (1 + o))
    return out


def test_v3_validity_is_per_configuration_and_scoped_by_claim():
    ctx = types.SimpleNamespace(study=STUDY)
    names = ["A", "B", "C [reasoning on]"]
    recs = _bayes_control(["A", "C"])
    for r in recs:
        if r["model_name"] == "C":
            r["config"] = "on@openrouter"
    d = C.d9_decision(ctx, recs + _moving_control(["B"], 0.4), names)
    assert d["per_model"]["A"]["instrument_valid"] and d["per_model"]["A"]["valid_for_conservatism"]
    assert not d["per_model"]["B"]["instrument_valid"] and d["per_model"]["B"]["flag_scope"] == "all claims"
    assert d["per_model"]["C [reasoning on]"]["instrument_valid"]
    flagged = C.d9_decision(ctx, recs, ["A"], {"A": "pre-set"})
    assert not flagged["per_model"]["A"]["instrument_valid"]


def test_v3_halting_counts_g3_passes_not_g1():
    ctx = types.SimpleNamespace(study=STUDY)
    names = [f"M{i}" for i in range(14)]
    ok = C.d9_decision(ctx, _bayes_control(names[:5]) + _moving_control(names[5:], 0.4), names)
    assert ok["n_g3_pass"] == 5 and not ok["halt"]
    bad = C.d9_decision(ctx, _bayes_control(names[:4]) + _moving_control(names[4:], 0.4), names)
    assert bad["halt"] and "only 4 configurations" in bad["halt_reasons"][0]


def test_v3_start_check():
    assert C.admit_step(40.0, 10.0, 55.0)
    assert not C.admit_step(45.0, 10.0, 55.0)
    assert C.admit_step(54.0, None, 55.0)


ORDER = ["Llama 3.1 8B", "gpt-oss-120b", "GPT-6 Luna", "Claude Haiku 5.5", "Gemini 3.8 Flash", "GPT-4o", "Qwen3.8 27B",
         "DeepSeek V4 Pro", "Kimi K3.1 Pro", "Gemini 4 Argon", "GPT-6.1 Sol", "Claude Opus 5.5", "Qwen3.8 Max",
         "Grok 4.7"]


def test_frozen_main_grid_order_must_list_every_model_once():
    f = {"models": [{"name": n} for n in ORDER], "main_grid_order": ORDER,
         "reasoning_on_rows": [{"name": "DeepSeek V4 Pro"}, {"name": "Claude Haiku 5.5"}],
         "reasoning_on_order": ["Claude Haiku 5.5", "DeepSeek V4 Pro"]}
    assert C.main_grid_order(f) == ORDER
    assert [r["name"] for r in C.reasoning_on_order(f)] == ["Claude Haiku 5.5", "DeepSeek V4 Pro"]
    for bad in (ORDER[:-1], ORDER + [ORDER[0]]):
        try:
            C.main_grid_order(dict(f, main_grid_order=bad))
        except AssertionError:
            continue
        raise AssertionError("an incomplete or duplicated order was accepted")


def _plan_v3(tmp_root):
    from test_prelim_mock import providers
    from dc import cli
    asyncio.run(cli.prelim(tmp_root, skip_tests=True, providers=providers()))
    p = C.plan(tmp_root, providers=providers())
    f = p["frozen"]
    names = [m["name"] for m in f["models"]]
    f.update(gate_passed=False, instrument_certification=CERT, flagged_models={}, main_grid_order=list(reversed(names)),
             reasoning_on_rows=f["reasoning_on_rows"][:2], escalation={"models": list(reversed(names))[:2]})
    f["reasoning_on_order"] = [r["name"] for r in f["reasoning_on_rows"]]
    p["main"] = p["main"][:8]
    return p, list(reversed(names))


def test_registered_run_v3_order_escalation_and_start_check(tmp_root):
    p, order = _plan_v3(tmp_root)
    on = [r["name"] for r in p["frozen"]["reasoning_on_rows"]]
    code = asyncio.run(C.run(tmp_root, p, rpm=None))
    assert code == 0
    v = json.loads((tmp_root / "reports/competition_validity.json").read_text())
    assert set(v["per_model"]) == set(order) | {n + C.ON_SUFFIX for n in on}
    control = [json.loads(l) for l in (tmp_root / "logs/competition/control.jsonl").read_text().splitlines()]
    assert {r["config"].split("@")[0] for r in control} == {"off", "on"}
    main = [json.loads(l) for l in (tmp_root / "logs/competition/main.jsonl").read_text().splitlines()]
    assert list(dict.fromkeys(r["model_name"] for r in main)) == order
    esc = [json.loads(l) for l in (tmp_root / "logs/competition/escalation.jsonl").read_text().splitlines()]
    assert {r["model_name"] for r in esc} == set(order[:2]) and {r["turn"] for r in esc} == {2, 3}
    assert all(r["lam"] == "1" for r in esc)
    rep = json.loads((tmp_root / "reports/competition_escalation.json").read_text())
    assert set(rep) == set(order[:2]) and any(k.endswith(":t3") for k in rep[order[0]])


def test_registered_run_start_check_cuts_whole_steps_from_the_end(tmp_root):
    p, order = _plan_v3(tmp_root)
    p["frozen"]["projected_by_step_usd"] = {order[5]: 1e6}
    code = asyncio.run(C.run(tmp_root, p, rpm=None))
    assert code == C.EXIT_STOP_RULE
    main = [json.loads(l) for l in (tmp_root / "logs/competition/main.jsonl").read_text().splitlines()]
    assert list(dict.fromkeys(r["model_name"] for r in main)) == order[:5]
    cut = json.loads((tmp_root / "logs/competition/stop_rule_cut.json").read_text())
    assert cut["completed"] == order[:5] and cut["not_run"][0] == order[5]
    assert cut["not_run"][-1].endswith(C.ON_SUFFIX)


def test_competition_refuses_without_certification(tmp_root):
    p, _ = _plan_v3(tmp_root)
    p["frozen"].pop("instrument_certification")
    assert asyncio.run(C.run(tmp_root, p, rpm=None)) == 4
    assert not (tmp_root / "logs/competition/control.jsonl").exists()


SMOKE_CFG = {"models": ["Off model", "Thinking model"], "practice_seed": 66548569, "practice_prefix": "V",
             "calls_per_model": 20, "disjoint_from_attempts": [1, 2, 3, 4], "max_unparseable_share": 0.05}


def test_pinned_smoke_sends_practice_prompts_only(tmp_root):
    ctx = P.Ctx(tmp_root, providers={}, log_dir="logs/prelim_d11", gate_path="configs/d11.json")
    items = D.pinned_smoke_items(ctx, SMOKE_CFG)
    assert len(items) == 20 and {it.arm for it in items} == {"practice"}
    ids = {it.scenario_id for it in items}
    assert not ids & {s.scenario_id for s in SCS}
    used = [ctx.by_id[i] for i in ids]
    assert not params_of(used) & params_of(ctx.scen_main)
    for a in (1, 2, 3, 4):
        assert not params_of(used) & params_of(ctx.scen_prac_by_attempt[str(a)])
    assert len({(it.lam, it.tone) for it in items}) == 8


def test_pinned_smoke_checks_flag_each_failure_kind(tmp_root):
    ctx = P.Ctx(tmp_root, providers={}, log_dir="logs/prelim_d11", gate_path="configs/d11.json")

    def entry(name, label):
        return {"name": name, "developer": "x", "route": "openrouter", "model_id": name.lower(),
                "pinned_host": {"tag": "h", "provider_name": "H"},
                "off_config": {"tag": "off@openrouter", "label_class": label, "max_tokens": 300,
                               "setting": {"label": "s", "params": {}}, "response_format": "json_only"}}
    ctx.save_resolved({"models": [entry("Off model", P.LABEL_OFF),
                                  entry("Thinking model", "thinking not disableable, lowest setting")]})

    def rec(name, i, rt, status="ok", finish="stop", mismatch=False):
        return {"key": f"{name}|off@openrouter|practice:V-x-{i}", "model_name": name, "status": status,
                "reasoning_tokens": rt, "finish_reasons": [finish], "truncated": finish == "length",
                "host_mismatch": mismatch, "upstream_providers": ["H"], "output_tokens": 20, "cost_usd": 0.0,
                "temperature": 1.0}
    recs = [rec("Off model", i, 0) for i in range(20)] + [rec("Thinking model", i, 300) for i in range(20)]
    log = ctx.log_dir / "smoke_pinned.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in recs))
    ok = D.evaluate_pinned_smoke(ctx, SMOKE_CFG, 20)
    assert ok["Off model"]["pass"] and ok["Thinking model"]["pass"]
    bad = {"reasoning_setting_honored": rec("Off model", 0, 12), "output_limit_not_hit": rec("Off model", 0, 0, finish="length"),
           "served_by_pinned_host": rec("Off model", 0, 0, mismatch=True)}
    for check, r in bad.items():
        log.write_text("".join(json.dumps(x) + "\n" for x in [r] + recs[1:20] + recs[20:]))
        res = D.evaluate_pinned_smoke(ctx, SMOKE_CFG, 20)["Off model"]
        assert not res["pass"] and not res["checks"][check], check
    two_unparseable = [rec("Thinking model", i, 300, status="unparseable") for i in range(2)]
    log.write_text("".join(json.dumps(x) + "\n" for x in recs[:20] + two_unparseable + recs[22:]))
    assert not D.evaluate_pinned_smoke(ctx, SMOKE_CFG, 20)["Thinking model"]["checks"]["answers_parse"]
    log.write_text("".join(json.dumps(x) + "\n" for x in recs[:20] + recs[21:]))
    assert not D.evaluate_pinned_smoke(ctx, SMOKE_CFG, 20)["Thinking model"]["checks"]["all_calls_returned"]


def test_v3_projection_covers_every_step():
    from dc.scenarios import load_json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    mt = load_json(root / "configs/measured_tokens.json")
    proj = D.v3_projection(mt, STUDY)
    assert set(D.V3_MAIN_ORDER) <= set(proj)
    assert {f"{n} [escalation]" for n in D.V3_ESCALATION} <= set(proj)
    assert {f"{n} [reasoning on]" for n in D.V3_ON_ORDER} <= set(proj)
    assert proj["Claude Opus 5.5"] == pytest.approx(1280 * mt["models"]["Claude Opus 5.5"]["usd_per_call_projection"],
                                                    abs=1e-3)
    assert 40 < sum(proj.values()) < 62


def test_v3_caps_exclude_prior_spend_and_keep_the_launch_baseline(tmp_root):
    ctx = types.SimpleNamespace(gate_cfg={"logs_only_spend_multiplier": 1.05}, _providers=None)
    f = {"stop_usd": 62.0, "caps_scope": "competition run only; prior spend excluded (D15)",
         "log_dir": "logs/competition_d11"}
    usage = {"usage_usd": 20.0}
    orig = P.env_key
    P.env_key = lambda *a: "k"
    import dc.providers as PR
    real = PR.openrouter_key_usage
    PR.openrouter_key_usage = lambda key: dict(usage)
    try:
        group, _ = C.spend_trackers(tmp_root, ctx, f, [])
        assert group.spent == pytest.approx(0.0)
        base = json.loads((tmp_root / "logs/competition_d11/openrouter_baseline.json").read_text())
        assert base["baseline_usage_usd"] == pytest.approx(20.0)
        usage["usage_usd"] = 23.0
        group2, _ = C.spend_trackers(tmp_root, ctx, f, [])
        assert group2.spent == pytest.approx(3.0)
    finally:
        P.env_key = orig
        PR.openrouter_key_usage = real
