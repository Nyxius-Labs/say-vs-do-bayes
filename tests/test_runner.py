import asyncio

import pytest
from conftest import bayes_p1, reply

from dc import scoring
from dc.costs import CostCapExceeded, CostTracker, spent_from_logs
from dc.providers import MockProvider, ProviderError, call_with_retry
from dc.runner import Job, Runner, read_jsonl
from dc.scenarios import control_arm, load_study, main_grid, main_scenarios

STUDY, TEMPLATES = load_study()
SCS = main_scenarios()
BY = {s.scenario_id: s for s in SCS}
NO_SLEEP = {"sleep": lambda s: asyncio.sleep(0)}


def jobs_for(items, provider, price=(1.0, 2.0), model="m"):
    return [Job(item=i, model_name=model, model_id=model, config="off@reseller", route="reseller", provider=provider,
                reasoning_label="test", params={}, max_tokens=100, temperature=1.0, price_in=price[0],
                price_out=price[1], concurrency_key="reseller:test") for i in items]


def runner(tmp_path, cap=100.0, name="log"):
    log = tmp_path / f"{name}.jsonl"
    return Runner(log, CostTracker(cap, spent_from_logs([log])), BY, TEMPLATES, retry_kwargs=NO_SLEEP)


def test_bayesian_mock_scores_U1_C0_and_passes_gates(tmp_path):
    prov = MockProvider(lambda m: reply(100 * bayes_p1(m)))
    recs = asyncio.run(runner(tmp_path).run(jobs_for(control_arm(SCS, STUDY), prov)))
    assert len(recs) == 320 and all(r["status"] == "ok" for r in recs)
    r = scoring.with_deltas(recs)
    assert abs(scoring.U_mean(r) - 1) < 0.05
    assert abs(scoring.capitulation(r)) < 0.03
    assert scoring.gate_G1(r, 0.25)["pass"] and scoring.gate_G3(r, 0.1)["pass"]
    assert scoring.gate_G2(r, 4)["pass"] and scoring.gate_G4(r, 0.8)["pass"]
    need = {"model_id", "route", "timestamp", "reasoning_setting", "prompt_hash", "scenario_id", "cell",
            "rating_order", "raw_response", "weight_rating", "probability", "input_tokens", "output_tokens",
            "reasoning_tokens", "latency_s", "cost_usd"}
    assert need <= set(recs[0])


def test_capitulating_mock_is_detected(tmp_path):
    def capitulate(m):
        p0 = float(m[1]["content"].split(" is ")[-1].rstrip("%."))
        return reply(p0 - 20, "none")
    prov = MockProvider(capitulate)
    recs = asyncio.run(runner(tmp_path).run(jobs_for(main_grid(SCS, STUDY), prov)))
    s = scoring.summarize(recs, STUDY)
    assert s["C"] > 0.5 and s["disavowed_share"] > 0.9 and s["disavowed_n_qualifying"] == 240
    assert s["conservatism"] > 0.4


def test_parse_retry_once_then_success(tmp_path):
    prov = MockProvider(lambda m: reply(40) if "could not be parsed" in m[-1]["content"] else "I think 40%.")
    recs = asyncio.run(runner(tmp_path).run(jobs_for(control_arm(SCS, STUDY)[:5], prov, price=(1e6 / 700, 0))))
    assert prov.calls == 10
    for r in recs:
        assert r["attempts"] == 2 and r["status"] == "ok" and r["probability"] == 40
        assert len(r["raw_response"]) == 2 and r["cost_usd"] == pytest.approx(2.0)


def test_parse_failure_recorded_missing_not_imputed(tmp_path):
    prov = MockProvider(lambda m: "no idea")
    recs = asyncio.run(runner(tmp_path).run(jobs_for(control_arm(SCS, STUDY)[:3], prov)))
    assert prov.calls == 6
    assert all(r["status"] == "unparseable" and r["probability"] is None for r in recs)
    assert scoring.with_deltas(recs) == []


def test_resume_without_double_counting(tmp_path):
    items = control_arm(SCS, STUDY)[:40]
    crash_sid = items[0].scenario_id

    marker = f" {BY[crash_sid].base_rate_pct}% "
    p1 = MockProvider(lambda m: RuntimeError("simulated crash") if marker in m[0]["content"] else reply(50))
    with pytest.raises(RuntimeError):
        asyncio.run(runner(tmp_path).run(jobs_for(items, p1, price=(1e6 / 700, 0))))
    first = read_jsonl(tmp_path / "log.jsonl")
    assert 0 < len(first) < len(items)
    p2 = MockProvider(lambda m: reply(50))
    r2 = runner(tmp_path)
    assert r2.tracker.spent == pytest.approx(len(first))
    asyncio.run(r2.run(jobs_for(items, p2, price=(1e6 / 700, 0))))
    assert p2.calls == len(items) - len(first)
    final = read_jsonl(tmp_path / "log.jsonl")
    assert len(final) == len(items) == len({r["key"] for r in final})
    assert spent_from_logs([tmp_path / "log.jsonl"]) == pytest.approx(len(items))
    p3 = MockProvider(lambda m: reply(50))
    asyncio.run(runner(tmp_path).run(jobs_for(items, p3)))
    assert p3.calls == 0


def test_cost_cap_aborts_and_logged_cost_stays_under_cap(tmp_path):
    items = control_arm(SCS, STUDY)[:100]
    prov = MockProvider(lambda m: reply(50), input_tokens=700, output_tokens=20)
    price = (10.0, 200.0)
    r = runner(tmp_path, cap=0.5)
    with pytest.raises(CostCapExceeded):
        asyncio.run(r.run(jobs_for(items, prov, price=price)))
    logged = spent_from_logs([tmp_path / "log.jsonl"])
    n = len(read_jsonl(tmp_path / "log.jsonl"))
    assert 0 < n < len(items)
    assert logged <= 0.5 and logged == pytest.approx(r.tracker.spent)
    r2 = runner(tmp_path, cap=0.5)
    with pytest.raises(CostCapExceeded):
        asyncio.run(r2.run(jobs_for(items, prov, price=price)))
    assert spent_from_logs([tmp_path / "log.jsonl"]) <= 0.5


def test_tracker_reservation_blocks_concurrent_overshoot():
    t = CostTracker(1.0)
    a = t.reserve(0.6)
    with pytest.raises(CostCapExceeded):
        t.reserve(0.6)
    t.settle(a, 0.1)
    assert t.aborted
    with pytest.raises(CostCapExceeded):
        t.reserve(0.01)


def test_retry_with_backoff_on_429_and_5xx_but_not_400(tmp_path):
    seq = [ProviderError(429, "rate"), ProviderError(503, "busy"), reply(30)]
    prov = MockProvider(lambda m: seq.pop(0))
    res = asyncio.run(call_with_retry(prov, "m", [], {}, 10, None, **NO_SLEEP))
    assert prov.calls == 3 and "30" in res.text
    bad = MockProvider(lambda m: ProviderError(400, "unsupported_parameter"))
    r = runner(tmp_path)
    out = asyncio.run(r.run(jobs_for(control_arm(SCS, STUDY)[:2], bad)))
    assert out == [] and bad.calls == 2
    assert len(read_jsonl(r.err_path)) == 2 and read_jsonl(r.log_path) == []


def test_billed_cost_preferred_over_computed(tmp_path):
    prov = MockProvider(lambda m: reply(50), billed_cost_usd=0.000123)
    recs = asyncio.run(runner(tmp_path).run(jobs_for(control_arm(SCS, STUDY)[:1], prov)))
    assert recs[0]["cost_usd"] == pytest.approx(0.000123) and recs[0]["cost_source"] == "billed"


def test_http_400_is_permanent_even_with_rate_limit_wording():
    cap = ProviderError(400, "Key per-minute cap hit: this key already sent as many prompts this minute as its rule "
                             "permits, so this prompt was not sent. Nothing was charged.")
    assert not cap.rate_limited and not cap.retryable
    assert not ProviderError(400, "unsupported_parameter: reasoning").retryable
    assert ProviderError(429, "slow down").retryable and ProviderError(529, "overloaded").retryable
    assert ProviderError(None, "ConnectError").retryable
    waits = []

    async def fake_sleep(s):
        waits.append(s)
    prov = MockProvider(lambda m: cap)
    with pytest.raises(ProviderError):
        asyncio.run(call_with_retry(prov, "m", [], {}, 10, None, sleep=fake_sleep))
    assert prov.calls == 1 and waits == []


def test_rate_limiter_caps_starts_per_rolling_minute():
    from dc.runner import RateLimiter
    t = [0.0]

    async def fake_sleep(s):
        t[0] += s

    lim = RateLimiter(3, clock=lambda: t[0], sleep=fake_sleep)

    async def go():
        stamps = []
        for _ in range(7):
            await lim.acquire()
            stamps.append(t[0])
        return stamps
    s = asyncio.run(go())
    assert s[:3] == [0.0, 0.0, 0.0] and 60 <= s[3] < 61 and 60 <= s[5] < 61 and 120 <= s[6] < 121
