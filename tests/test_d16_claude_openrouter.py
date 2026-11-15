import json

from dc import prelim as P
from dc.providers import OpenRouterBackend
from dc.scenarios import ROOT, control_arm, load_study, main_scenarios

STUDY, TEMPLATES = load_study()
ITEMS = control_arm(main_scenarios(), STUDY)
SCHEMA_RF = STUDY["json_schema_response_format"]


def _bounds(x):
    s = json.dumps(x)
    return "minimum" in s or "maximum" in s


def test_schema_bounds_removed_for_anthropic_models_only():
    b = OpenRouterBackend("k")
    assert _bounds(SCHEMA_RF)
    claude = b.body("anthropic/claude-haiku-5.5", [], {"response_format": SCHEMA_RF}, 300, None)
    other = b.body("openai/gpt-oss-120b", [], {"response_format": SCHEMA_RF}, 300, None)
    assert not _bounds(claude["response_format"]) and claude["response_format"]["type"] == "json_schema"
    assert _bounds(other["response_format"])
    assert _bounds(SCHEMA_RF)


def test_frozen_claude_entries_route_through_pinned_anthropic_host():
    r = json.loads((ROOT / "configs/models_resolved.json").read_text())
    f = json.loads((ROOT / "configs/frozen_competition.json").read_text())
    for name in ("Claude Opus 5.5", "Claude Haiku 5.5"):
        e = next(x for x in r["models"] if x["name"] == name)
        m = next(x for x in f["models"] if x["name"] == name)
        assert e["route"] == m["route"] == "openrouter" and e["model_id"].startswith("anthropic/")
        assert e["pinned_host"]["tag"] == "anthropic" and m["host"] == "anthropic"
        assert e["off_config"]["setting"]["params"] == m["reasoning_setting"]["params"]

        class Ctx:
            study = STUDY
            def provider(self, route):
                return route
        job = P.off_job(Ctx(), ITEMS[0], e)
        assert job.route == "openrouter" and job.temperature is None
        assert job.params["provider"] == {"order": ["anthropic"], "allow_fallbacks": False}
        assert job.expected_host == "Anthropic"
        assert "thinking" not in job.params and "output_config" not in job.params
    haiku = next(x for x in r["models"] if x["name"] == "Claude Haiku 5.5")
    assert haiku["off_config"]["response_format"] == "json_schema"
    assert haiku["reasoning_on_setting"]["params"] == {"reasoning": {"effort": "medium"}}
    on = next(x for x in f["reasoning_on_rows"] if x["name"] == "Claude Haiku 5.5")
    assert on["route"] == "openrouter"
    assert not any(x["route"] == "anthropic" for x in f["models"] + f["reasoning_on_rows"])
