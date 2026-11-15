import json

import pytest

from dc import prelim as P
from dc.scenarios import sample_items

OFF = {"label": "reasoning.enabled=false", "params": {"reasoning": {"enabled": False}}}
FALSE = {"label": "reasoning=false", "params": {"reasoning": False}}


def _rec(name, setting, out, rt, status="ok", attempts=1, max_tokens=300, finish=None, config="off@reseller"):
    return {"key": f"{name}|{config}|{out}|{rt}|{status}|{id(object())}", "model_name": name, "config": config,
            "reasoning_setting": setting, "output_tokens": out, "reasoning_tokens": rt, "status": status,
            "attempts": attempts, "max_tokens": max_tokens, "finish_reasons": finish or ["stop"] * attempts,
            "input_tokens": 300, "cost_usd": 0.0}


def _setup(tmp_root, recs, models):
    ctx = P.Ctx(tmp_root, providers={})
    (ctx.log_dir / "smoke.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    ctx.save_resolved({"models": [{"name": n, "developer": "x", "reseller_id": n.lower(), "price_in": 1.0,
                                   "price_out": 2.0, "anchor": False, "reasoning_off": OFF, "skipped": None}
                                  for n in models]})
    return ctx


def test_p95_and_d1_max_tokens():
    assert P.p95(list(range(1, 101))) == pytest.approx(95.05)
    recs = [{"output_tokens": o, "attempts": 1} for o in range(100, 1100, 10)]
    assert P.d1_max_tokens(recs) == 2081
    assert P.d1_max_tokens([{"output_tokens": 5000, "attempts": 1}]) == 4096
    assert P.d1_max_tokens([{"output_tokens": 600, "attempts": 2}]) == 600


def test_apply_decisions_labels_formats_and_max_tokens(tmp_root):
    recs = (
        [_rec("Thinker", OFF, 400 + i, 380 + i) for i in range(40)]
        + [_rec("Clean", OFF, 20, 0) for _ in range(40)]
        + [_rec("Prose", OFF, 20, 0) for _ in range(30)]
        + [_rec("Prose", OFF, 600, 0, "unparseable", 2, finish=["length", "length"]) for _ in range(10)]
        + [_rec("Switch", OFF, 70, 50 if i == 0 else 0) for i in range(40)]
        + [_rec("Switch", FALSE, 15, 0, config="off@reseller:probe2[reasoning=false]") for _ in range(2)]
        + [_rec("Unreported", OFF, 20, None) for _ in range(40)]
    )
    ctx = _setup(tmp_root, recs, ["Thinker", "Clean", "Prose", "Switch", "Unreported"])
    oc = P.apply_decisions(ctx)
    assert oc["Thinker"]["label_class"] == P.LABEL_THINKING and oc["Thinker"]["setting"] == OFF
    assert oc["Thinker"]["max_tokens"] == int(-(-2 * P.p95([400 + i for i in range(40)]) // 1))
    assert oc["Thinker"]["response_format"] == "json_only" and oc["Thinker"]["tag"] == "off@reseller"
    assert oc["Clean"] == {**oc["Clean"], "label_class": P.LABEL_OFF, "max_tokens": 300, "response_format": "json_only"}
    assert oc["Prose"]["response_format"] == "json_schema" and oc["Prose"]["tag"] == "off@reseller#d1d2"
    assert oc["Switch"]["setting"] == FALSE and oc["Switch"]["label_class"] == P.LABEL_OFF
    assert oc["Unreported"]["label_class"] == P.LABEL_OFF_UNVERIFIED
    assert P.apply_decisions(ctx) == oc


def test_off_job_carries_per_model_format_and_limit(tmp_root):
    recs = [_rec("Prose", OFF, 600, 596, "unparseable", 2, finish=["length", "length"]) for _ in range(40)]
    ctx = _setup(tmp_root, recs, ["Prose"])
    P.apply_decisions(ctx)
    e = P._entry(ctx, "Prose")
    it = sample_items("smoke", ctx.scen_main, ctx.study, 1, "smoke")[0]
    job = P.off_job(ctx, it, e)
    assert job.params["response_format"] == ctx.study["json_schema_response_format"]
    assert job.params["reasoning"] == {"enabled": False}
    assert job.max_tokens == e["off_config"]["max_tokens"] == 600
    assert job.reasoning_label.startswith(P.LABEL_THINKING)


def test_truncation_detection():
    assert P.is_truncated(_rec("m", OFF, 600, 0, "unparseable", 2, finish=["length", "stop"]))
    assert not P.is_truncated(_rec("m", OFF, 40, 0, "unparseable", 2))
    old = _rec("m", OFF, 600, 596, "unparseable", 2)
    del old["finish_reasons"]
    assert P.is_truncated(old)


def test_projection_excludes_models_failing_parse_criterion(tmp_root):
    recs = [_rec("Good", OFF, 20, 0) for _ in range(40)] + [_rec("Bad", OFF, 20, 0) for _ in range(40)]
    ctx = _setup(tmp_root, recs, ["Good", "Bad"])
    P.apply_decisions(ctx)
    smoke = {"Good": {"n": 40, "parsed_share": 1.0, "truncated": 0, "mean_input_tokens": 300, "mean_output_tokens": 20,
                      "max_tokens": 300},
             "Bad": {"n": 40, "parsed_share": 0.975, "truncated": 1, "mean_input_tokens": 300,
                     "mean_output_tokens": 2600, "max_tokens": 4096}}
    proj = P.project_d5(ctx, smoke, None)
    assert proj["models"] == ["Good"] and "D1 cap 4,096" in proj["excluded"]["Bad"]
