import json

from dc.providers import resolve_model_id

CATALOG = ["anthropic/claude-opus-5.5", "claude-opus-5.5-fast", "claude-opus-5-5-20261001", "claude-haiku-5.5",
           "gpt-4o", "gpt-4o-mini", "gpt-4o-2024-08-06", "gpt-4o-audio-preview", "gpt-6.1-sol", "gpt-6.1-sol-batch",
           "meta-llama/llama-3.1-8b-instruct", "aion-labs/aion-rp-llama-3.1-8b", "qwen/qwen3.8-max-prime", "deepseek-v4-pro", "deepseek-v4-pro-thinking", "qwen3.8-max",
           "qwen3.8-27b", "kimi-k3.1-pro", "gemini-3.8-flash", "gemini-3.8-flash-lite", "grok-4.7", "grok-4.7-fast"]


def test_plain_variant_chosen():
    cfg = json.load(open("configs/models.json"))
    want = {"Claude Opus 5.5": "anthropic/claude-opus-5.5", "GPT-4o": "gpt-4o", "GPT-6.1 Sol": "gpt-6.1-sol",
            "Llama 3.1 8B": "meta-llama/llama-3.1-8b-instruct", "DeepSeek V4 Pro": "deepseek-v4-pro",
            "Qwen3.8 Max": "qwen3.8-max", "Qwen3.8 27B": "qwen3.8-27b", "Kimi K3.1 Pro": "kimi-k3.1-pro",
            "Gemini 3.8 Flash": "gemini-3.8-flash", "Grok 4.7": "grok-4.7", "Claude Haiku 5.5": "claude-haiku-5.5"}
    for m in cfg["models"]:
        if m["name"] in want:
            assert resolve_model_id(m["pattern"], CATALOG)[0] == want[m["name"]], m["name"]


def test_unresolvable_and_nonstandard_only():
    assert resolve_model_id("mistral-large-?4", CATALOG)[0] is None
    cid, matches, note = resolve_model_id("grok-4[.-]7", ["grok-4.7-fast"])
    assert cid is None and matches == ["grok-4.7-fast"] and "manual" in note
    assert resolve_model_id("gpt-4o", ["gpt-4o1"])[0] is None
