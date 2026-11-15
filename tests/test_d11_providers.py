import asyncio
import json
import re

import httpx
import httpx2
import pytest
from conftest import bayes_p1, reply

from dc import hashing
from dc.costs import AnthropicLedger, CostCapExceeded, CostTracker, OpenRouterLedger
from dc.providers import (AnthropicBackend, MockProvider, OpenRouterBackend, ProviderError, call_with_retry)
from dc.runner import Job, Runner, read_jsonl
from dc.scenarios import control_arm, load_study, main_scenarios

STUDY, TEMPLATES = load_study()
SCS = main_scenarios()
BY = {s.scenario_id: s for s in SCS}
ITEMS = control_arm(SCS, STUDY)
MSGS = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}, {"role": "user", "content": "q2"}]


def _sleeps():
    waits = []

    async def fake_sleep(s):
        waits.append(s)
    return waits, fake_sleep


def or_response(text, cost=0.000123, provider="Fireworks", rt=0, status=200, headers=None):
    body = {"id": "gen-1", "model": "openai/gpt-oss-120b", "provider": provider,
            "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 650, "completion_tokens": 40, "cost": cost,
                      "completion_tokens_details": {"reasoning_tokens": rt}}}
    return httpx.Response(status, json=body, headers=headers or {})


def anthropic_message(text, thinking_tokens=0, stop="end_turn", thinking_block=False):
    content = ([{"type": "thinking", "thinking": "", "signature": "sig"}] if thinking_block else []) + \
              [{"type": "text", "text": text}]
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5", "content": content,
            "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 640, "output_tokens": 12 + thinking_tokens, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0, "output_tokens_details": {"thinking_tokens": thinking_tokens}}}


def anthropic_backend(handler):
    client = __import__("anthropic").DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    return AnthropicBackend("test-key", http_client=client, base_url="https://api.anthropic.test")


def _job(item, provider, route, model="m", price=(1.0, 2.0), params=None):
    return Job(item=item, model_name=model, model_id=model, config=f"off@{route}", route=route, provider=provider,
               reasoning_label="t", params=params or {}, max_tokens=300, temperature=1.0, price_in=price[0],
               price_out=price[1], concurrency_key=f"{route}:test")


def test_openrouter_backend_requests_usage_and_logs_cost_and_provider(tmp_path):
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return or_response(reply(30), cost=0.000321, provider="DeepInfra", rt=17)
    prov = OpenRouterBackend("k", base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler))
    tracker = OpenRouterLedger(1.0, 0.0, None, 0.0)
    r = Runner(tmp_path / "log.jsonl", tracker, BY, TEMPLATES, trackers={"openrouter": tracker})
    recs = asyncio.run(r.run([_job(ITEMS[0], prov, "openrouter", "openai/gpt-oss-120b")]))
    assert seen[0]["usage"] == {"include": True} and seen[0]["model"] == "openai/gpt-oss-120b"
    assert seen[0]["max_tokens"] == 300 and seen[0]["temperature"] == 1.0
    rec = recs[0]
    assert rec["cost_usd"] == pytest.approx(0.000321) and rec["cost_source"] == "billed"
    assert rec["upstream_providers"] == ["DeepInfra"] and rec["reasoning_tokens"] == 17
    assert tracker.spent == pytest.approx(0.000321)


def test_openrouter_upstream_error_in_200_body_is_raised():
    with pytest.raises(ProviderError) as e:
        OpenRouterBackend.parse({"error": {"code": 502, "message": "upstream died"}})
    assert e.value.retryable
    with pytest.raises(ProviderError) as e:
        OpenRouterBackend.parse({"error": {"code": 400, "message": "bad"}})
    assert not e.value.retryable


def test_anthropic_backend_thinking_tokens_format_mapping_and_computed_cost(tmp_path):
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx2.Response(200, json=anthropic_message(reply(40), thinking_tokens=32, thinking_block=True))
    prov = anthropic_backend(handler)
    schema_rf = STUDY["json_schema_response_format"]
    params = {"output_config": {"effort": "low"}, "response_format": schema_rf}
    led = AnthropicLedger(1.0)
    r = Runner(tmp_path / "log.jsonl", led, BY, TEMPLATES, trackers={"anthropic": led})
    recs = asyncio.run(r.run([_job(ITEMS[0], prov, "anthropic", "claude-haiku-5-5", price=(0.10, 0.50), params=params)]))
    body = seen[0]
    assert "temperature" not in body and "response_format" not in body
    assert body["output_config"]["effort"] == "low"
    sent = body["output_config"]["format"]["schema"]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "minimum" not in json.dumps(sent) and "maximum" not in json.dumps(sent)
    assert sent["properties"]["weight_rating"]["enum"] == ["none", "small", "moderate", "large"]
    assert sent["required"] == ["weight_rating", "probability"] and sent["additionalProperties"] is False
    assert sent["properties"]["probability"]["type"] == "integer"
    rec = recs[0]
    assert any("removed (unsupported by Anthropic" in q for q in rec["quirks"])
    assert rec["reasoning_tokens"] == 32 and rec["upstream_providers"] == ["anthropic"]
    assert rec["cost_source"] == "computed"
    assert rec["cost_usd"] == pytest.approx((640 * 0.10 + 44 * 0.50) / 1e6)
    assert led.spent == pytest.approx(rec["cost_usd"])
    assert any("temperature not sent" in q for q in rec["quirks"])


def test_anthropic_stop_reason_max_tokens_maps_to_length():
    prov = anthropic_backend(lambda req: httpx2.Response(200, json=anthropic_message("{\"weig", stop="max_tokens")))
    res = asyncio.run(prov.complete("claude-haiku-5-5", MSGS, {}, 10, None))
    assert res.finish_reason == "length" and res.reasoning_tokens == 0


def test_429_storm_backs_off_exponentially_with_jitter_then_succeeds():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        if n["calls"] <= 6:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return or_response(reply(25))
    prov = OpenRouterBackend("k", base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler))
    waits, fake_sleep = _sleeps()
    res = asyncio.run(call_with_retry(prov, "m", MSGS, {}, 10, None, sleep=fake_sleep, base_delay=1.0, max_delay=8.0))
    assert "25" in res.text and n["calls"] == 7 and len(waits) == 6
    for k, w in enumerate(waits, 1):
        assert 0 <= w <= min(8.0, 2 ** (k - 1))


def test_429_storm_honors_retry_after_and_gives_up_after_max_attempts(tmp_path):
    def handler(req):
        return httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "5"})
    prov = OpenRouterBackend("k", base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler))
    waits, fake_sleep = _sleeps()
    with pytest.raises(ProviderError) as e:
        asyncio.run(call_with_retry(prov, "m", MSGS, {}, 10, None, sleep=fake_sleep, max_attempts=5, max_delay=60))
    assert e.value.status == 429 and len(waits) == 4 and all(w >= 5 for w in waits)
    tr = CostTracker(1.0)
    r = Runner(tmp_path / "log.jsonl", tr, BY, TEMPLATES, retry_kwargs={"sleep": fake_sleep, "max_attempts": 3})
    assert asyncio.run(r.run([_job(ITEMS[0], prov, "openrouter")])) == []
    err = read_jsonl(r.err_path)
    assert len(err) == 1 and err[0]["status"] == 429 and err[0]["permanent"] is False and tr.spent == 0
    ok = MockProvider(lambda m: reply(50))
    r2 = Runner(tmp_path / "log.jsonl", tr, BY, TEMPLATES)
    assert len(asyncio.run(r2.run([_job(ITEMS[0], ok, "openrouter")]))) == 1


def test_mid_run_permanent_failure_is_logged_once_and_not_retried(tmp_path):
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        if n["calls"] == 3:
            return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error",
                                                                          "message": "prompt rejected"}})
        return httpx2.Response(200, json=anthropic_message(reply(40)))
    prov = anthropic_backend(handler)
    waits, fake_sleep = _sleeps()
    led = AnthropicLedger(1.0)
    r = Runner(tmp_path / "log.jsonl", led, BY, TEMPLATES, default_concurrency=1, retry_kwargs={"sleep": fake_sleep},
               trackers={"anthropic": led})
    recs = asyncio.run(r.run([_job(it, prov, "anthropic", "claude-haiku-5-5") for it in ITEMS[:5]]))
    assert len(recs) == 4 and n["calls"] == 5 and waits == []
    err = read_jsonl(r.err_path)
    assert len(err) == 1 and err[0]["status"] == 400 and err[0]["permanent"] is True and err[0]["route"] == "anthropic"


def test_openrouter_malformed_200_body_is_retried_not_fatal():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        if n["calls"] == 1:
            return httpx.Response(200, content=b"\n\n         \n\n         \n")
        return or_response(reply(33))
    prov = OpenRouterBackend("k", base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler))
    waits, fake_sleep = _sleeps()
    res = asyncio.run(call_with_retry(prov, "m", MSGS, {}, 10, None, sleep=fake_sleep))
    assert "33" in res.text and n["calls"] == 2 and len(waits) == 1


def test_5xx_is_retried():
    seq = [ProviderError(500, "oops"), ProviderError(529, "overloaded"), reply(10)]
    prov = MockProvider(lambda m: seq.pop(0))
    waits, fake_sleep = _sleeps()
    res = asyncio.run(call_with_retry(prov, "m", MSGS, {}, 10, None, sleep=fake_sleep))
    assert prov.calls == 3 and len(waits) == 2 and "10" in res.text


def test_openrouter_ledger_reconciles_at_start_and_every_n_calls(tmp_path):
    usage = {"usd": 10.0}
    calls = []

    def account():
        calls.append(usage["usd"])
        return {"usage_usd": usage["usd"]}
    led = OpenRouterLedger(1.0, 0.0, account, baseline_usage_usd=10.0, every=3)
    led.reconcile("start")
    prov = MockProvider(lambda m: reply(50), billed_cost_usd=0.01)
    r = Runner(tmp_path / "log.jsonl", led, BY, TEMPLATES, default_concurrency=1, trackers={"openrouter": led})
    usage["usd"] = 10.05
    asyncio.run(r.run([_job(it, prov, "openrouter") for it in ITEMS[:3]]))
    assert len(calls) == 2
    assert led.history[-1]["gap_added_usd"] == pytest.approx(0.02)
    assert led.spent == pytest.approx(0.05) and led.account_spent == pytest.approx(0.05)


def test_openrouter_cap_refuses_calls_that_would_exceed_it(tmp_path):
    led = OpenRouterLedger(0.05, 0.0, None, 0.0)
    prov = MockProvider(lambda m: reply(50), billed_cost_usd=0.01)
    r = Runner(tmp_path / "log.jsonl", led, BY, TEMPLATES, default_concurrency=1, trackers={"openrouter": led})
    with pytest.raises(CostCapExceeded):
        asyncio.run(r.run([_job(it, prov, "openrouter", price=(1.0, 20.0)) for it in ITEMS[:50]]))
    assert led.spent <= 0.05 and sum(x["cost_usd"] for x in read_jsonl(r.log_path)) <= 0.05


def test_grouped_ledgers_share_the_stop_rule_cap():
    group = CostTracker(1.0, 0.7)
    an, orl = AnthropicLedger(float("inf"), group=group), OpenRouterLedger(float("inf"), 0.0, None, 0.0, group=group)
    t = an.reserve(0.2)
    with pytest.raises(CostCapExceeded):
        orl.reserve(0.2)
    an.settle(t, 0.1)
    assert an.spent == pytest.approx(0.1) and group.spent == pytest.approx(0.8) and group.outstanding == 0
    assert orl.spent == 0


def _catalog(missing=()):
    ids = {"openai/gpt-6.1-sol": (2, 10), "openai/gpt-6.1-sol-pro": (2, 10), "openai/gpt-6-luna": (0.1, 0.5),
           "openai/gpt-oss-120b": (0.15, 0.6), "openai/gpt-4o": (2.5, 10), "openai/gpt-4o-mini": (0.15, 0.6),
           "google/gemini-4-argon": (2, 10), "google/gemini-3.8-flash": (0.825, 4.125), "x-ai/grok-4.7": (2, 6),
           "x-ai/grok-4.7:batch": (1, 3), "qwen/qwen3.8-max-0902": (2, 6), "qwen/qwen3.8-max-prime": (4, 12),
           "qwen/qwen3.8-27b": (0.15, 1.875), "deepseek/deepseek-v4-pro": (0.955, 1.91),
           "deepseek/deepseek-v4-pro-0813": (0.111, 4.44), "moonshotai/kimi-k3.1-pro": (2, 10),
           "meta-llama/llama-3.1-8b-instruct": (0.02, 0.04), "aion-labs/aion-rp-llama-3.1-8b": (0.8, 1.6),
           "anthropic/claude-opus-5.5": (4, 20)}
    non_reasoning = ("openai/gpt-4o", "meta-llama/llama-3.1-8b-instruct")
    return [{"id": i, "name": i, "pricing": {"prompt": str(a / 1e6), "completion": str(b / 1e6)},
             "supported_parameters": ["max_tokens", "temperature", "response_format"]
             + ([] if i in non_reasoning else ["reasoning"])} for i, (a, b) in ids.items() if i not in missing]


def test_resolve_routes_two_anthropic_twelve_openrouter_with_live_prices():
    from dc import prelim_d11 as D
    from dc.scenarios import load_json, ROOT
    d11, mc = load_json(ROOT / "configs/d11.json"), load_json(ROOT / "configs/models.json")
    out = D.resolve_routes(d11, mc, _catalog())
    by = {m["name"]: m for m in out["models"]}
    assert len(by) == 14 and sum(m["route"] == "anthropic" for m in by.values()) == 2
    assert by["Claude Opus 5.5"]["model_id"] == "claude-opus-5-5" and by["Claude Opus 5.5"]["price_out"] == 20.0
    assert by["Claude Haiku 5.5"]["model_id"] == "claude-haiku-5-5"
    assert by["Qwen3.8 Max"]["model_id"] == "qwen/qwen3.8-max-0902" and "pinned" in by["Qwen3.8 Max"]["resolution_note"]
    assert by["DeepSeek V4 Pro"]["model_id"] == "deepseek/deepseek-v4-pro"
    assert by["Llama 3.1 8B"]["model_id"] == "meta-llama/llama-3.1-8b-instruct"
    assert by["GPT-4o"]["model_id"] == "openai/gpt-4o" and by["GPT-4o"]["price_in"] == pytest.approx(2.5)
    assert by["Grok 4.7"]["model_id"] == "x-ai/grok-4.7"
    assert all(m["price_in"] > 0 and m["price_out"] > 0 for m in by.values())


def test_resolve_routes_stops_when_a_model_has_no_openrouter_route():
    from dc import prelim_d11 as D
    from dc.scenarios import load_json, ROOT
    d11, mc = load_json(ROOT / "configs/d11.json"), load_json(ROOT / "configs/models.json")
    with pytest.raises(RuntimeError, match="Grok 4.7"):
        D.resolve_routes(d11, mc, _catalog(missing=("x-ai/grok-4.7",)))


def _answer(messages):
    return reply(100 * bayes_p1(messages), "large")


def or_full(model_id, messages, params, max_tokens):
    r = params.get("reasoning")
    if isinstance(r, dict) and r.get("effort") == "medium":
        return {"text": _answer(messages), "input_tokens": 650, "output_tokens": 1800, "reasoning_tokens": 1775,
                "upstream_provider": "Mock"}
    if model_id == "openai/gpt-oss-120b":
        return {"text": _answer(messages), "input_tokens": 650, "output_tokens": 670, "reasoning_tokens": 650,
                "upstream_provider": "Mock"}
    return {"text": _answer(messages), "input_tokens": 650, "output_tokens": 25, "reasoning_tokens": 0,
            "upstream_provider": "Mock"}


def or_reject(model_id, params):
    if params.get("reasoning") is False:
        return ProviderError(400, "reasoning must be an object")
    return None


def an_full(model_id, messages, params, max_tokens):
    if model_id == "claude-opus-5-5":
        return {"text": _answer(messages), "input_tokens": 640, "output_tokens": 500, "reasoning_tokens": 480,
                "upstream_provider": "anthropic", "billed_cost_usd": None}
    t = params.get("thinking", {}).get("type")
    rt = 300 if t == "adaptive" else 0
    return {"text": _answer(messages), "input_tokens": 640, "output_tokens": 15 + rt, "reasoning_tokens": rt,
            "upstream_provider": "anthropic", "billed_cost_usd": None}


def an_reject(model_id, params):
    if model_id == "claude-opus-5-5" and params.get("thinking", {}).get("type") == "disabled":
        return ProviderError(400, '"thinking.type.disabled" is not supported for this model.')
    return None


def d11_providers(or_fn=or_full):
    return {"openrouter": MockProvider(None, catalog=_catalog(), reject_fn=or_reject, full_fn=or_fn,
                                       billed_cost_usd=0.00004),
            "anthropic": MockProvider(None, catalog=["claude-opus-5-5", "claude-haiku-5-5"], reject_fn=an_reject,
                                      full_fn=an_full)}


def test_d11_prelim_end_to_end_then_incomplete_control_arm_exits_5(tmp_root):
    from dc import competition
    from dc import prelim_d11 as D
    cfg = json.loads((tmp_root / "configs/d11.json").read_text())
    cfg["current_attempt"] = 3
    (tmp_root / "configs/d11.json").write_text(json.dumps(cfg))
    usage = {"usd": 3.0}
    acct = lambda: {"usage_usd": usage["usd"]}
    prov = d11_providers()
    code = asyncio.run(D.run(tmp_root, providers=prov, account_fn=acct))
    assert code == 0, (tmp_root / "prelim_report.md").read_text()[:2000]
    res = {m["name"]: m for m in json.loads((tmp_root / "configs/models_resolved.json").read_text())["models"]}
    assert len(res) == 14 and {m["route"] for m in res.values()} == {"anthropic", "openrouter"}
    oc = {n: m["off_config"] for n, m in res.items()}
    assert oc["Claude Opus 5.5"]["setting"]["label"] == "output_config.effort=low"
    assert oc["Claude Opus 5.5"]["label_class"] == "thinking not disableable, lowest setting"
    assert oc["Claude Opus 5.5"]["tag"] == "off@anthropic"
    assert oc["Claude Haiku 5.5"]["label_class"] == "reasoning-off"
    assert oc["Claude Haiku 5.5"]["response_format"] == "json_schema"
    assert oc["gpt-oss-120b"]["label_class"].startswith("thinking not disableable")
    assert oc["gpt-oss-120b"]["max_tokens"] == 1340
    assert oc["GPT-4o"]["setting"]["label"].startswith("none sent")
    an_reqs = prov["anthropic"].requests
    assert all(r["model_id"].startswith("claude-") for r in an_reqs)
    assert not any(r["model_id"].startswith("claude") for r in prov["openrouter"].requests)
    summ = json.loads((tmp_root / "reports/prelim_summary.json").read_text())
    assert all(18 <= s["n"] <= 21 for s in summ["smoke"].values()), {k: v["n"] for k, v in summ["smoke"].items()}
    hist = (tmp_root / "reports/gate_history.md").read_text()
    assert "### Attempt 3: PASS" in hist and "35588301" in hist
    mt = json.loads((tmp_root / "configs/measured_tokens.json").read_text())
    assert set(mt["models"]) == set(res) and all(m["n_calls"] > 0 for m in mt["models"].values())
    assert mt["models"]["Claude Opus 5.5"]["mean_reasoning_tokens"] == 480
    assert all(len(m["thinking_tokens_per_call"]) == m["n_calls"] for m in mt["models"].values())
    assert mt["reasoning_on_rows"]["Claude Haiku 5.5"]["admitted"] and mt["reasoning_on_rows"]["DeepSeek V4 Pro"]["admitted"]
    assert mt["projection_line"].startswith("Projected competition cost at live prices: $")
    sp = json.loads((tmp_root / "reports/openrouter_spend_d11.json").read_text())
    assert sp["baseline_usage_usd"] == 3.0 and sp["logged_openrouter_usd"] <= 5.0
    an = json.loads((tmp_root / "reports/anthropic_ledger_d11.json").read_text())
    assert an["calls"] > 0 and an["spent_usd"] > 0
    frozen = json.loads((tmp_root / "configs/frozen_competition.json").read_text())
    assert frozen["status"] == "final" and frozen["gate_passed"] and len(frozen["models"]) == 14
    assert frozen["log_dir"] == "logs/competition_d11"
    assert hashing.verify(tmp_root) == []

    def failing(model_id, messages, params, max_tokens):
        if model_id == "meta-llama/llama-3.1-8b-instruct":
            raise ProviderError(403, "This model is not available in your region")
        return or_full(model_id, messages, params, max_tokens)
    p = competition.plan(tmp_root, providers=d11_providers(failing))
    code = asyncio.run(competition.run(tmp_root, p, rpm=None, retry_kwargs={"sleep": lambda s: asyncio.sleep(0)}))
    assert code == competition.EXIT_INCOMPLETE == 5
    log_dir = tmp_root / "logs/competition_d11"
    errs = read_jsonl(log_dir / "control.errors.jsonl")
    assert len(errs) == 320 and all(e["permanent"] and e["status"] == 403 for e in errs)
    assert not (tmp_root / "reports/competition_validity.json").exists()
    assert not (log_dir / "main.jsonl").exists()
    recs = read_jsonl(log_dir / "control.jsonl")
    assert {r["route"] for r in recs} == {"anthropic", "openrouter"}
    assert not (tmp_root / "logs/competition/control.jsonl").exists()


def test_d11_pending_freeze_refuses_competition(tmp_root):
    from dc import competition
    from dc import prelim_d11 as D
    ctx = D.Ctx11(tmp_root, providers=d11_providers())
    asyncio.run(D.resolve(ctx))
    D.freeze_pending(tmp_root, providers=d11_providers())
    assert hashing.verify(tmp_root) == []
    f = json.loads((tmp_root / "configs/frozen_competition.json").read_text())
    assert f["status"] == "pending" and not f["gate_passed"] and len(f["models"]) == 14
    p = competition.plan(tmp_root, providers=d11_providers())
    assert asyncio.run(competition.run(tmp_root, p, rpm=None)) == 4
    assert "pending" in (tmp_root / "DECISIONS.md").read_text()
