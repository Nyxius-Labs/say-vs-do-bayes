import asyncio
import json
import re
import subprocess
import sys

import pytest
from conftest import bayes_p1, reply

from dc import cli, hashing
from dc.costs import spent_from_logs
from dc.providers import MockProvider, ProviderError

IDS = ["claude-opus-5.5", "claude-opus-5.5-fast", "claude-haiku-5.5", "gpt-6.1-sol", "gpt-6-luna", "gpt-oss-120b",
       "gpt-4o", "gpt-4o-mini", "gemini-4-argon", "gemini-3.8-flash", "gemini-3.8-flash-tts", "grok-4.7",
       "qwen3.8-max", "qwen3.8-max-prime", "qwen3.8-27b", "deepseek-v4-pro", "kimi-k3.1-pro",
       "kimi-k3.1-pro-highspeed", "mistral-large-4", "llama-3.1-8b", "gpt-6-luna-decisions"]
NON_REASONING = ("gpt-4o", "llama-3.1-8b")
CATALOG = [{"id": i, "capabilities": {"reasoning": i not in NON_REASONING},
            "pricing": {"input_per_million": "0.100000", "output_per_million": "0.200000",
                        "list_input_per_million": "0.125000", "list_output_per_million": "0.250000"}}
           for i in IDS] + [{"id": "grok-4.7-batch"}]


def full(model_id, messages, params, max_tokens):
    answer = reply(100 * bayes_p1(messages), "large")
    on = params.get("reasoning", {}).get("effort") == "medium" if isinstance(params.get("reasoning"), dict) else False
    if on:
        return {"text": answer, "input_tokens": 650, "output_tokens": 1800, "reasoning_tokens": 1775}
    if model_id == "claude-opus-5.5":
        return {"text": answer, "input_tokens": 650, "output_tokens": 520, "reasoning_tokens": 500}
    if model_id == "gpt-oss-120b":
        if max_tokens < 700:
            return {"text": "", "input_tokens": 650, "output_tokens": max_tokens, "reasoning_tokens": max_tokens,
                    "finish_reason": "length"}
        return {"text": answer, "input_tokens": 650, "output_tokens": 670, "reasoning_tokens": 650}
    if model_id == "deepseek-v4-pro" and params.get("reasoning") is not False:
        return {"text": answer, "input_tokens": 650, "output_tokens": 70, "reasoning_tokens": 50}
    if model_id == "claude-haiku-5.5" and "response_format" not in params:
        if int(re.search(r"is (\d+)\.", messages[1]["content"]).group(1)) % 2:
            return {"text": "Let me work through this carefully...", "input_tokens": 650, "output_tokens": 300,
                    "reasoning_tokens": 0, "finish_reason": "length"}
    return {"text": answer, "input_tokens": 650, "output_tokens": 25, "reasoning_tokens": 0}


def reject(model_id, params):
    if model_id == "gemini-3.8-flash" and (params.get("reasoning") is False or (
            isinstance(params.get("reasoning"), dict) and (params["reasoning"].get("enabled") is False
                                                          or params["reasoning"].get("effort") == "none"))):
        return ProviderError(400, "Gemini 3 thinking cannot be disabled.", "reasoning")
    return None


def flash_thinks(model_id, messages, params, max_tokens):
    out = full(model_id, messages, params, max_tokens)
    if model_id == "gemini-3.8-flash":
        out = {**out, "output_tokens": 300, "reasoning_tokens": 280}
    return out


def providers():
    return {"reseller": MockProvider(None, catalog=CATALOG, reject_fn=reject, full_fn=flash_thinks,
                                     billed_cost_usd=0.00004)}


def _cli(root, *args):
    return subprocess.run([sys.executable, "-m", "dc.cli", "--root", str(root), *args],
                          cwd=root, capture_output=True, text=True, env={"PYTHONPATH": str(root / "src"), "PATH": ""})


def test_prelim_end_to_end_with_mocks(tmp_root):
    prov = providers()
    code = asyncio.run(cli.prelim(tmp_root, skip_tests=True, providers=prov))
    assert code == 0
    report = (tmp_root / "prelim_report.md").read_text()
    for g in ("G1", "G2", "G3", "G4"):
        assert re.search(rf"\| {g} .*\| PASS \|", report), g
    assert "NOT RUN: no direct provider access" in report
    assert "TODO" not in report and "nan" not in report.lower()

    resolved = {m["name"]: m for m in json.loads((tmp_root / "configs/models_resolved.json").read_text())["models"]}
    oc = {n: m["off_config"] for n, m in resolved.items()}
    assert oc["Claude Opus 5.5"]["label_class"] == "thinking not disableable, lowest setting"
    assert oc["Claude Opus 5.5"]["max_tokens"] == 1040 and oc["Claude Opus 5.5"]["tag"] == "off@reseller"
    assert oc["Gemini 3.8 Flash"]["setting"]["label"] == "reasoning.effort=minimal"
    assert oc["Gemini 3.8 Flash"]["label_class"].startswith("thinking not disableable")
    assert oc["gpt-oss-120b"]["max_tokens"] == 1200 and oc["gpt-oss-120b"]["response_format"] == "json_schema"
    assert oc["DeepSeek V4 Pro"]["setting"]["params"] == {"reasoning": False}
    assert oc["DeepSeek V4 Pro"]["label_class"] == "reasoning-off"
    assert oc["GPT-6 Luna"]["label_class"] == "reasoning-off" and oc["GPT-6 Luna"]["max_tokens"] == 300
    assert oc["Claude Haiku 5.5"]["response_format"] == "json_schema"
    assert oc["GPT-4o"]["response_format"] == "json_only"

    sent = prov["reseller"].requests
    assert any(r["model_id"] == "claude-haiku-5.5" and "response_format" in r["params"] for r in sent)
    assert not any(r["model_id"] == "gpt-4o" and "response_format" in r["params"] for r in sent)
    assert any(r["model_id"] == "gpt-oss-120b" and r["max_tokens"] == 1200 for r in sent)

    summ = json.loads((tmp_root / "reports/prelim_summary.json").read_text())
    for name, s in summ["smoke"].items():
        assert s["parsed_share"] >= 0.95 and s["truncated"] == 0, name
    frozen = json.loads((tmp_root / "configs/frozen_competition.json").read_text())
    assert frozen["draws_main"] == 3
    on_rows = [r["name"] for r in frozen["reasoning_on_rows"]]
    assert "gpt-oss-120b" not in on_rows and "Claude Opus 5.5" not in on_rows
    assert "DeepSeek V4 Pro" in on_rows
    assert summ["projection"]["projected_total"] <= 55
    assert "| Claude Opus 5.5 |" in (tmp_root / "DECISIONS.md").read_text()

    h = json.loads((tmp_root / "hashes.json").read_text())
    assert {"prompt_templates", "thresholds_config", "scoring_code", "model_ids", "reasoning_on_order",
            "stopping_rule", "frozen_competition_config", "decisions", "gate_config"} == set(h["hashes"])
    assert hashing.verify(tmp_root) == []
    assert spent_from_logs(p for p in (tmp_root / "logs/prelim").glob("*.jsonl") if "errors" not in p.name) <= 5.0
    assert _cli(tmp_root, "run", "--registered", "--dry-run").returncode == 0

    p2 = providers()
    asyncio.run(cli.prelim(tmp_root, skip_tests=True, providers=p2))
    assert p2["reseller"].calls == 0


@pytest.mark.parametrize("rel", ["configs/prompt_templates.json", "configs/study.json", "src/dc/scoring.py",
                                 "configs/models_resolved.json", "configs/reasoning_on_order.json",
                                 "configs/stopping_rule.json", "configs/frozen_competition.json", "DECISIONS.md",
                                 "configs/gate.json"])
def test_registered_mode_refuses_when_any_frozen_file_changes(tmp_root, rel):
    asyncio.run(cli.prelim(tmp_root, skip_tests=True, providers=providers()))
    assert _cli(tmp_root, "run", "--registered", "--dry-run").returncode == 0
    f = tmp_root / rel
    f.write_bytes(f.read_bytes() + b"\n")
    r = _cli(tmp_root, "run", "--registered", "--dry-run")
    assert r.returncode == 2 and "REFUSED" in r.stderr


def test_prelim_without_keys_makes_no_calls(tmp_root, monkeypatch):
    for k in ("CHEAPER_API_KEY", "CHEAPINFERENCE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
              "GOOGLE_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    asyncio.run(cli.prelim(tmp_root, skip_tests=True))
    report = (tmp_root / "prelim_report.md").read_text()
    assert "G1: FAIL" in report and "NOT RUN" in report and "nan" not in report.lower()
    assert spent_from_logs((tmp_root / "logs/prelim").glob("*.jsonl")) == 0


def test_competition_refuses_when_gate_failed(tmp_root):
    from dc import competition
    asyncio.run(cli.prelim(tmp_root, skip_tests=True, providers=providers()))
    p = competition.plan(tmp_root)
    assert p["frozen"]["gate_passed"] is True
    p["frozen"]["gate_passed"] = False
    assert asyncio.run(competition.run(tmp_root, p, rpm=None)) == 4
    assert not (tmp_root / "logs/competition/control.jsonl").exists()


def _ignores_evidence(model_ids):
    def fn(model_id, messages, params, max_tokens):
        if model_id in model_ids:
            p0 = float(re.search(r"is (\d+(?:\.\d+)?)%\.", messages[1]["content"]).group(1))
            return {"text": reply(p0 - 25, "large"), "input_tokens": 650, "output_tokens": 25, "reasoning_tokens": 0}
        return flash_thinks(model_id, messages, params, max_tokens)
    return {"reseller": MockProvider(None, catalog=CATALOG, reject_fn=reject, full_fn=fn, billed_cost_usd=0.00004)}


def _competition(tmp_root, failing, n_models=14, n_main=64):
    from dc import competition
    asyncio.run(cli.prelim(tmp_root, skip_tests=True, providers=providers()))
    p = competition.plan(tmp_root, providers=_ignores_evidence(failing))
    p["frozen"]["models"] = p["frozen"]["models"][:n_models]
    p["main"] = p["main"][:n_main]
    return competition, p, asyncio.run(competition.run(tmp_root, p, rpm=None))


def test_d9_evidence_ignoring_model_is_flagged_and_run_continues(tmp_root):
    competition, p, code = _competition(tmp_root, {"gpt-4o"})
    assert code == 0
    v = json.loads((tmp_root / "reports/competition_validity.json").read_text())
    base = {k: x for k, x in v["per_model"].items() if not k.endswith(" [reasoning on]")}
    assert v["halt"] is False and sum(x["G1"]["pass"] for x in base.values()) == 13
    g = v["per_model"]["GPT-4o"]
    assert g["valid_for_conservatism"] is False and g["flag"] == "instrument not valid for this model"
    assert g["flag_scope"] in ("conservatism claims only", "all claims")
    main = [json.loads(l) for l in (tmp_root / "logs/competition/main.jsonl").read_text().splitlines()]
    assert all("instrument_valid" in r for r in main)
    import csv
    rows = list(csv.DictReader(open(tmp_root / "reports/competition_results.csv")))
    assert rows and all(r["instrument_valid"] in ("True", "False") for r in rows)
    md = (tmp_root / "reports/competition_results.md").read_text()
    assert "## Headline (D10)" in md and "equal-weight mean" in md
    assert re.search(r"\| GPT-4o \| [-0-9.]+ \| [-0-9.]+ \| FAIL \|", md.split("## Control arm, all models")[1])
    assert "Reasoning tokens versus capitulation (descriptive, not causal)" in md
    assert all(r["mean_reasoning_tokens"] != "" for r in rows)


def test_d9_five_g1_failures_out_of_14_no_longer_halt(tmp_root):
    failing = {"claude-haiku-5.5", "gpt-4o", "qwen3.8-27b", "kimi-k3.1-pro", "gpt-6-luna"}
    competition, p, code = _competition(tmp_root, failing)
    assert len(p["frozen"]["models"]) == 14
    v = json.loads((tmp_root / "reports/competition_validity.json").read_text())
    base = {k: x for k, x in v["per_model"].items() if not k.endswith(" [reasoning on]")}
    assert sum(x["G1"]["pass"] for x in base.values()) == 9 and sum(x["G3"]["pass"] for x in base.values()) == 9
    assert v["halt"] is False and code == 0


def test_d9_pooled_g4_failure_halts_even_with_enough_models(tmp_root):
    from dc import competition
    asyncio.run(cli.prelim(tmp_root, skip_tests=True, providers=providers()))
    p = competition.plan(tmp_root)
    recs = []
    for e in p["frozen"]["models"]:
        for i, it in enumerate(p["control"]):
            sc = p["ctx"].by_id[it.scenario_id]
            recs.append({"model_name": e["name"], "scenario_id": it.scenario_id, "lam": it.lam, "tone": it.tone,
                         "p0": float(sc.p0), "weight_rating": "large",
                         "probability": round(100 * float(sc.p_star(it.lam))) if it.tone == "neutral" or it.lam == "1"
                         else round(100 * float(sc.p_star({"1/4": "16", "16": "1/4", "4": "4"}[it.lam])))})
    d = competition.d9_decision(p["ctx"], recs, [m["name"] for m in p["frozen"]["models"]])
    assert d["pooled_G4_over_G1_pass"]["pass"] is False and d["halt"] is True
    asyncio.run(p["ctx"].close())


def test_d9_not_decided_on_incomplete_control_arm(tmp_root):
    from dc import competition
    asyncio.run(cli.prelim(tmp_root, skip_tests=True, providers=providers()))

    def fn(model_id, messages, params, max_tokens):
        if model_id == "llama-3.1-8b":
            raise ProviderError(400, "Every route rejected this request as invalid")
        return flash_thinks(model_id, messages, params, max_tokens)
    prov = {"reseller": MockProvider(None, catalog=CATALOG, reject_fn=reject, full_fn=fn, billed_cost_usd=0.00004)}
    p = competition.plan(tmp_root, providers=prov)
    code = asyncio.run(competition.run(tmp_root, p, rpm=None))
    assert code == competition.EXIT_INCOMPLETE
    assert not (tmp_root / "reports/competition_validity.json").exists()
    assert not (tmp_root / "logs/competition/main.jsonl").exists()
