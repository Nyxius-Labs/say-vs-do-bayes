import asyncio
import json

import httpx
import pytest
from conftest import reply

from dc import prelim as P
from dc import prelim_d11 as D
from dc.costs import OpenRouterLedger
from dc.providers import OpenRouterBackend, ProviderError, call_with_retry
from dc.runner import Runner, read_jsonl
from dc.scenarios import ROOT, control_arm, load_json, load_study, main_scenarios

STUDY, TEMPLATES = load_study()
SCS = main_scenarios()
BY = {s.scenario_id: s for s in SCS}
ITEMS = control_arm(SCS, STUDY)
NO_SLEEP = {"sleep": lambda s: asyncio.sleep(0)}

PINNED = {"name": "gpt-oss-120b", "developer": "OpenAI", "route": "openrouter", "model_id": "openai/gpt-oss-120b",
          "price_in": 0.1, "price_out": 0.5,
          "pinned_host": {"tag": "crusoe/bf16", "provider_name": "Crusoe"},
          "off_config": {"tag": "off@openrouter", "label_class": "thinking not disableable, lowest setting",
                         "setting": {"label": "reasoning.effort=minimal", "params": {"reasoning": {"effort": "minimal"}}},
                         "response_format": "json_only", "max_tokens": 962}}


class StubCtx:
    def __init__(self, prov):
        self.study, self._prov = STUDY, prov

    def provider(self, route):
        return self._prov


def or_body(text, provider):
    return {"id": "gen-1", "model": "openai/gpt-oss-120b", "provider": provider,
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 600, "completion_tokens": 300, "cost": 0.0001,
                      "completion_tokens_details": {"reasoning_tokens": 280}}}


def backend(handler):
    return OpenRouterBackend("k", base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler))


def run_jobs(tmp_path, prov, n=3, retry=None):
    led = OpenRouterLedger(1.0, 0.0, None, 0.0)
    r = Runner(tmp_path / "log.jsonl", led, BY, TEMPLATES, default_concurrency=1, retry_kwargs=retry or NO_SLEEP,
               trackers={"openrouter": led})
    jobs = [P.off_job(StubCtx(prov), it, PINNED) for it in ITEMS[:n]]
    return r, asyncio.run(r.run(jobs))


def test_pinned_request_always_names_the_host_and_disables_fallbacks(tmp_path):
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=or_body(reply(40), "Crusoe"))
    r, recs = run_jobs(tmp_path, backend(handler), n=4)
    assert len(bodies) == 4
    for b in bodies:
        assert b["provider"] == {"order": ["crusoe/bf16"], "allow_fallbacks": False}
        assert b["reasoning"] == {"effort": "minimal"} and b["usage"] == {"include": True}
    assert all(x["pinned_host"] == "Crusoe" and x["host_mismatch"] is False for x in recs)
    on = P.on_job(StubCtx(None), ITEMS[0], {**PINNED, "reasoning_on_setting": {"label": "on", "params": {"reasoning": {"effort": "medium"}}}})
    assert on.params["provider"] == {"order": ["crusoe/bf16"], "allow_fallbacks": False}
    assert "provider" not in P.with_pin({**PINNED, "pinned_host": None}, {"reasoning": {}})
    assert "provider" not in P.with_pin({**PINNED, "route": "anthropic"}, {})


def test_response_from_a_different_host_is_logged_and_flagged(tmp_path):
    def handler(req):
        return httpx.Response(200, json=or_body(reply(40), "PrimeIntellect"))
    r, recs = run_jobs(tmp_path, backend(handler), n=2)
    assert len(recs) == 2
    for x in recs:
        assert x["host_mismatch"] is True and x["upstream_providers"] == ["PrimeIntellect"]
        assert any("served by PrimeIntellect although pinned to Crusoe" in q for q in x["quirks"])
    assert all(x["host_mismatch"] for x in read_jsonl(r.log_path))


def test_host_unavailable_is_retried_then_surfaced_not_rerouted(tmp_path):
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(503, json={"error": {"code": 503, "message":
                                                   "No allowed providers are available for the selected model."}})
    prov = backend(handler)
    with pytest.raises(ProviderError) as e:
        asyncio.run(call_with_retry(prov, "openai/gpt-oss-120b", [], P.with_pin(PINNED, {}), 10, None,
                                    max_attempts=4, **NO_SLEEP))
    assert e.value.status == 503 and len(bodies) == 4
    assert all(b["provider"] == {"order": ["crusoe/bf16"], "allow_fallbacks": False} for b in bodies)
    bodies.clear()
    r, recs = run_jobs(tmp_path, prov, n=1, retry={**NO_SLEEP, "max_attempts": 3})
    assert recs == [] and len(bodies) == 3
    err = read_jsonl(r.err_path)
    assert len(err) == 1 and err[0]["status"] == 503 and err[0]["permanent"] is False
    assert all(b["provider"]["order"] == ["crusoe/bf16"] and b["provider"]["allow_fallbacks"] is False for b in bodies)


def ep(tag, name, tp, pin, pout, params=("max_tokens", "temperature", "reasoning"), status=0):
    return {"tag": tag, "provider_name": name, "throughput_last_30m": None if tp is None else {"p50": tp},
            "pricing": {"prompt": str(pin / 1e6), "completion": str(pout / 1e6)}, "supported_parameters": list(params),
            "quantization": "bf16", "status": status}


REQ = ["max_tokens", "reasoning", "temperature"]


def test_first_party_host_is_used_even_if_slower():
    eps = [ep("azure", "Azure", 300, 1, 5), ep("openai", "OpenAI", 50, 2, 10)]
    chosen, rec = D.select_host("OpenAI", eps, REQ)
    assert chosen["tag"] == "openai" and rec["rule_step"].startswith("1")


def test_first_party_standard_endpoint_not_premium_or_flex_tiers():
    eps = [ep("openai/flex", "OpenAI", 40, 1, 5), ep("openai", "OpenAI", 35, 2, 10),
           ep("openai/ultrafast", "OpenAI", 120, 12, 60), ep("xai/zdr/us", "OpenAI", 200, 1, 1)]
    chosen, rec = D.select_host("OpenAI", eps, REQ)
    assert chosen["tag"] == "openai" and rec["first_party_standard_endpoints"] == ["openai"]
    assert D.is_standard({"tag": "google-vertex/us"}) and D.is_standard({"tag": "moonshotai/mxfp4"})
    assert not D.is_standard({"tag": "moonshotai/highspeed"}) and not D.is_standard({"tag": "google-ai-studio/priority"})


def test_several_standard_first_party_endpoints_filtered_by_parameters_then_throughput_then_price():
    no_temp = ("max_tokens", "reasoning")
    eps = [ep("google-vertex/us", "Google", 121, 0.8, 4.1, params=no_temp), ep("google-ai-studio", "Google AI Studio", 80, 1.5, 3.75),
           ep("google-vertex/global", "Google", 67, 1.5, 3.75, params=no_temp), ep("google-ai-studio/priority", "Google AI Studio", 81, 1.35, 6.75)]
    chosen, _ = D.select_host("Google", eps, REQ)
    assert chosen["tag"] == "google-ai-studio"
    tie = [ep("openai", "OpenAI", 90, 2, 10), ep("openai/us", "OpenAI", 90, 2, 9)]
    assert D.select_host("OpenAI", tie, REQ)[0]["tag"] == "openai/us"


def test_no_first_party_highest_throughput_among_hosts_supporting_every_parameter():
    eps = [ep("fast/fp4", "Fast", 900, 0.1, 0.2, params=("max_tokens", "temperature")),
           ep("mid/bf16", "Mid", 120, 0.2, 0.6), ep("slow/bf16", "Slow", 60, 0.1, 0.3), ep("unknown", "Quiet", None, 0.01, 0.01)]
    chosen, rec = D.select_host("Meta", eps, REQ)
    assert chosen["tag"] == "mid/bf16" and rec["rule_step"].startswith("2")
    fast = next(c for c in rec["candidates"] if c["tag"] == "fast/fp4")
    assert fast["supports_required"] is False and fast["missing_parameters"] == ["reasoning"]


def test_ties_broken_by_lowest_price_and_unreported_throughput_ranks_last():
    eps = [ep("a", "A", None, 0.0, 0.0), ep("b", "B", 100, 0.3, 0.9), ep("c", "C", 100, 0.2, 0.9)]
    chosen, _ = D.select_host("Meta", eps, REQ)
    assert chosen["tag"] == "c"


def test_rule_stops_for_owner_when_no_host_qualifies():
    assert D.select_host("Meta", [ep("x", "X", 100, 1, 1, params=("max_tokens",))], REQ)[0] is None
    chosen, rec = D.select_host("OpenAI", [ep("openai", "OpenAI", 100, 1, 1, params=("max_tokens",)),
                                           ep("azure", "Azure", 100, 1, 1)], REQ)
    assert chosen is None and "owner decision" in rec["rule_step"]


def test_select_host_takes_no_gate_data():
    import inspect
    assert list(inspect.signature(D.select_host).parameters) == ["developer", "endpoints", "required"]


def test_pin_hosts_records_host_data_and_timestamp_in_models_resolved(tmp_root):
    from test_d11_providers import _catalog
    d11, mc = load_json(ROOT / "configs/d11.json"), load_json(ROOT / "configs/models.json")
    res = D.resolve_routes(d11, mc, _catalog())
    for e in res["models"]:
        e["off_config"] = {"setting": {"label": "x", "params": {"reasoning": {"effort": "minimal"}}},
                           "response_format": "json_only"}
    (tmp_root / "configs/models_resolved.json").write_text(json.dumps(res))
    eps = {e["model_id"]: [ep("host-a/fp8", "HostA", 80, 0.5, 1.0), ep("host-b", "HostB", 120, 0.6, 1.2),
                           ep("openai", "OpenAI", 30, 2.0, 8.0), ep("xai", "xAI", 30, 2.0, 6.0)]
           for e in res["models"] if e["route"] == "openrouter"}
    ctx = D.Ctx11(tmp_root, providers={})
    chosen = asyncio.run(D.pin_hosts(ctx, eps))
    assert len(chosen) == 12
    by = {m["name"]: m for m in json.loads((tmp_root / "configs/models_resolved.json").read_text())["models"]}
    assert chosen["GPT-4o"] == "openai" and chosen["Grok 4.7"] == "xai" and chosen["Llama 3.1 8B"] == "host-b"
    for n, m in by.items():
        if m["route"] == "anthropic":
            assert "pinned_host" not in m
            continue
        h = m["pinned_host"]
        assert h["tag"] and h["provider_name"] and h["selected_at"] and h["candidates"] and h["required_parameters"]
        assert m["price_in"] == pytest.approx(h["price_in"]) and m["price_out"] == pytest.approx(h["price_out"])
    assert by["Llama 3.1 8B"]["price_out"] == pytest.approx(1.2)


def test_gate_attempt_4_on_pinned_hosts_end_to_end(tmp_root):
    from test_d11_providers import d11_providers
    from dc import hashing
    cfg_path = tmp_root / "configs/d11.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["current_attempt"] = 3
    cfg_path.write_text(json.dumps(cfg))
    assert asyncio.run(D.run(tmp_root, providers=d11_providers(), account_fn=lambda: {"usage_usd": 1.0})) == 0
    cfg["current_attempt"] = 4
    cfg_path.write_text(json.dumps(cfg))
    res = json.loads((tmp_root / "configs/models_resolved.json").read_text())
    eps = {e["model_id"]: [ep("mock/bf16", "Mock", 100, 0.1, 0.2)] for e in res["models"] if e["route"] == "openrouter"}
    asyncio.run(D.pin_hosts(D.Ctx11(tmp_root, providers={}), eps))
    f = D.freeze_pending_gate(tmp_root)
    assert f["status"] == "pending-gate" and not f["gate_passed"] and hashing.verify(tmp_root) == []
    prov = d11_providers()
    code = asyncio.run(D.run_gate_attempt(tmp_root, providers=prov, account_fn=lambda: {"usage_usd": 1.0}))
    assert code == 0
    sent = [r for r in prov["openrouter"].requests]
    assert sent and all(r["params"]["provider"] == {"order": ["mock/bf16"], "allow_fallbacks": False} for r in sent)
    hist = (tmp_root / "reports/gate_history.md").read_text()
    assert "### Attempt 3: PASS" in hist and "### Attempt 4: PASS" in hist and "67897034" in hist
    assert "G1-G4 for attempt 4: G1 PASS, G2 PASS, G3 PASS, G4 PASS" in hist
    assert "Per-host data, gpt-oss-120b (attempt 4; pinned to Mock; calls served by another host: 0)" in hist
    mt = json.loads((tmp_root / "configs/measured_tokens.json").read_text())
    assert mt["models"]["gpt-oss-120b"]["pinned_host"] == "mock/bf16"
    assert mt["required_openrouter_balance"]["openrouter_required_balance_usd"] > mt["projected_by_route_usd"]["openrouter"]
    frozen = json.loads((tmp_root / "configs/frozen_competition.json").read_text())
    assert frozen["status"] == "final" and frozen["gate_passed"] and frozen["gate_attempt"] == 4
    assert all(m["host"] for m in frozen["models"]) and hashing.verify(tmp_root) == []
    sp = json.loads((tmp_root / "reports/openrouter_spend_attempt4.json").read_text())
    assert sp["cap_usd"] == 3.0


def test_temperature_waived_only_where_no_host_lists_it(tmp_root):
    from test_d11_providers import _catalog
    d11, mc = load_json(ROOT / "configs/d11.json"), load_json(ROOT / "configs/models.json")
    res = D.resolve_routes(d11, mc, _catalog())
    for e in res["models"]:
        e["off_config"] = {"setting": {"label": "x", "params": {"reasoning": {"effort": "minimal"}}},
                           "response_format": "json_only", "tag": "off@openrouter", "label_class": "x", "max_tokens": 300}
    (tmp_root / "configs/models_resolved.json").write_text(json.dumps(res))
    no_temp = ("max_tokens", "reasoning")
    eps = {e["model_id"]: [ep("host/bf16", "Host", 90, 1, 2)] for e in res["models"] if e["route"] == "openrouter"}
    eps["openai/gpt-6.1-sol"] = [ep("openai", "OpenAI", 50, 2, 10, params=no_temp), ep("azure", "Azure", 90, 2, 10, params=no_temp)]
    eps["openai/gpt-6-luna"] = [ep("openai", "OpenAI", 50, 2, 10, params=no_temp), ep("other/bf16", "Other", 90, 1, 2)]
    with pytest.raises(RuntimeError, match="GPT-6 Luna: 1: no standard first-party endpoint"):
        asyncio.run(D.pin_hosts(D.Ctx11(tmp_root, providers={}), eps))
    eps["openai/gpt-6-luna"] = [ep("openai", "OpenAI", 50, 2, 10)]
    asyncio.run(D.pin_hosts(D.Ctx11(tmp_root, providers={}), eps))
    by = {m["name"]: m for m in json.loads((tmp_root / "configs/models_resolved.json").read_text())["models"]}
    sol = by["GPT-6.1 Sol"]
    assert sol["pinned_host"]["tag"] == "openai" and sol["pinned_host"]["waived_parameters"] == ["temperature"]
    assert "temperature" not in sol["pinned_host"]["required_parameters"]
    job = P.off_job(StubCtx(None), ITEMS[0], sol)
    assert job.temperature is None and job.params["provider"] == {"order": ["openai"], "allow_fallbacks": False}
    luna = by["GPT-6 Luna"]
    assert luna["pinned_host"]["waived_parameters"] == [] and "temperature" in luna["pinned_host"]["required_parameters"]
    assert P.off_job(StubCtx(None), ITEMS[0], luna).temperature == 1.0
