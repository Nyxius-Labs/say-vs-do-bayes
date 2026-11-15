from dc.prompts import build_messages, parse_response
from dc.scenarios import control_arm, load_study, main_grid, main_scenarios


def _setup():
    study, t = load_study()
    scs = main_scenarios()
    return study, t, {s.scenario_id: s for s in scs}, scs


def test_conversation_structure_and_fixed_rebuttal_wording():
    study, t, by, scs = _setup()
    items = [i for i in main_grid(scs, study) if i.scenario_id == scs[0].scenario_id and i.draw == 0]
    msgs = {(i.lam, i.tone): build_messages(i, by[i.scenario_id], t) for i in items}
    for m in msgs.values():
        assert [x["role"] for x in m] == ["user", "assistant", "user"]
        assert f"{float(scs[0].p0) * 100:.1f}%" in m[1]["content"]
    import re
    strip = lambda s: re.sub(r"\d+%", "N%", s.split("\n\n")[0])
    firsts = {strip(m[2]["content"]) for (lam, tone), m in msgs.items()}
    assert len(firsts) == 1
    assert "WRONG" in msgs[("1", "insistent")][2]["content"]
    assert "WRONG" not in msgs[("1", "neutral")][2]["content"]
    assert "Bayes" not in msgs[("1", "neutral")][2]["content"]


def test_control_arm_states_odds_rule():
    study, t, by, scs = _setup()
    it = control_arm(scs, study)[0]
    last = build_messages(it, by[it.scenario_id], t)[-1]["content"]
    assert "posterior odds = prior odds x likelihood ratio" in last


def test_rating_order_changes_instruction_and_key_order():
    study, t, by, scs = _setup()
    g = main_grid(scs, study)
    rf = next(i for i in g if i.rating_first)
    pf = next(i for i in g if not i.rating_first)
    a = build_messages(rf, by[rf.scenario_id], t)[-1]["content"]
    b = build_messages(pf, by[pf.scenario_id], t)[-1]["content"]
    assert a.index('"weight_rating"') < a.index('"probability"')
    assert b.index('"probability"') < b.index('"weight_rating"')


def test_parser():
    assert parse_response('{"weight_rating": "small", "probability": 62}') == {"weight_rating": "small", "probability": 62}
    assert parse_response('```json\n{"probability": 7, "weight_rating": "Large"}\n```')["weight_rating"] == "large"
    assert parse_response('Sure. {"weight_rating": "none", "probability": "40"}')["probability"] == 40
    assert parse_response('{"weight_rating": "none", "probability": 40.0}')["probability"] == 40
    assert parse_response('{"weight_rating": "none", "probability": 40.5}') is None
    assert parse_response('{"weight_rating": "none", "probability": 140}') is None
    assert parse_response('{"weight_rating": "huge", "probability": 40}') is None
    assert parse_response('{"weight_rating": "none", "probability": NaN}') is None
    assert parse_response('{"weight_rating": "none", "probability": true}') is None
    assert parse_response('probability is 40%') is None
    assert parse_response('') is None and parse_response(None) is None
    assert parse_response('{"note": "a } b", "weight_rating": "small", "probability": 3} then '
                          '{"weight_rating": "moderate", "probability": 4}')["probability"] == 4
