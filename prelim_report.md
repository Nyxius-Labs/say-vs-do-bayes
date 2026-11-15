# Preliminary run report (D11): disavowed-capitulation

Generated 2026-11-12T06:52:25+00:00. Governed by SPEC.txt section 4 as amended by DECISIONS.md (D1-D11). Routes: Anthropic Messages API for Claude Opus 5.5 and Claude Haiku 5.5; OpenRouter (`https://openrouter.ai/api/v1`, usage accounting on) for the other 12 models. The first run's reports (CheaperInference) are archived in `reports/archive_cheaperinference/` and linked from `reports/gate_history.md`.

## Summary

| Item | Result |
|---|---|
| Gate attempt 4 (DeepSeek V4 Pro, gpt-oss-120b) | **FAIL** |
| G1 | PASS |
| G2 | PASS |
| G3 | FAIL |
| G4 | PASS |
| OpenRouter spend this run (account usage endpoint) | $1.0968 (logged usage.cost $1.0972; cap $5.00) |
| Anthropic spend this run (computed ledger) | $0.1388 (80 calls) |
| Projected competition cost at live prices | $35.07 |
| Projected cumulative (D5 stop $55) | $48.75 |

## Projection

Projected competition cost at live prices: $35.07 (14 models x 960 calls, plus reasoning-on rows Claude Haiku 5.5, DeepSeek V4 Pro x 640 calls; abort threshold $50).

## Per-model configuration and smoke (D11 routes)

| Model | Route | Model ID | Label | Setting | Format | max_tokens | Smoke calls | Parsed | Truncated | Mean thinking tok | Mean in / out tok | Upstream providers |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Claude Opus 5.5 | anthropic | `claude-opus-5-5` | thinking not disableable, lowest setting | `output_config.effort=low` | json_only | 622 | 20 | 100.0% | 0 | 210 | 431 / 231 | anthropic |
| Claude Haiku 5.5 | anthropic | `claude-haiku-5-5` | reasoning-off | `thinking.type=disabled` | json_schema | 300 | 20 | 100.0% | 0 | 0 | 683 / 20 | anthropic |
| GPT-6.1 Sol | openrouter | `openai/gpt-6.1-sol` | thinking not disableable, lowest setting | `reasoning.effort=minimal` | json_only | 172 | 20 | 100.0% | 0 | 45 | 296 / 62 | OpenAI |
| GPT-6 Luna | openrouter | `openai/gpt-6-luna` | reasoning-off | `reasoning.enabled=false` | json_only | 300 | 20 | 100.0% | 0 | 0 | 296 / 16 | OpenAI |
| gpt-oss-120b | openrouter | `openai/gpt-oss-120b` | thinking not disableable, lowest setting | `reasoning.effort=minimal` | json_only | 962 | 20 | 100.0% | 0 | 110 | 357 / 422 | AkashML, Cerebras, Crusoe, PrimeIntellect |
| GPT-4o | openrouter | `openai/gpt-4o` | reasoning-off | `none sent (catalog: non-reasoning model)` | json_only | 300 | 20 | 100.0% | 0 | 0 | 295 / 18 | OpenAI |
| Gemini 4 Argon | openrouter | `google/gemini-4-argon` | reasoning-off | `reasoning.effort=minimal` | json_only | 300 | 20 | 100.0% | 0 | 0 | 338 / 19 | Google |
| Gemini 3.8 Flash | openrouter | `google/gemini-3.8-flash` | thinking not disableable, lowest setting | `reasoning.effort=minimal` | json_only | 2773 | 20 | 100.0% | 0 | 773 | 298 / 794 | Google |
| Grok 4.7 | openrouter | `x-ai/grok-4.7` | thinking not disableable, lowest setting | `reasoning.effort=minimal` | json_only | 4096 | 20 | 100.0% | 0 | 1547 | 1529 / 1561 | xAI |
| Qwen3.8 Max | openrouter | `qwen/qwen3.8-max-0902` | thinking not disableable, lowest setting | `reasoning.effort=minimal` | json_only | 4096 | 20 | 100.0% | 0 | 1319 | 376 / 1336 | Alibaba |
| Qwen3.8 27B | openrouter | `qwen/qwen3.8-27b` | reasoning-off | `reasoning.enabled=false` | json_only | 300 | 20 | 100.0% | 0 | 0 | 318 / 87 | CoreWeave, Ionstream, Mancer 2, Modal, Near AI, Nebius, Wafer |
| DeepSeek V4 Pro | openrouter | `deepseek/deepseek-v4-pro` | reasoning-off | `reasoning.enabled=false` | json_only | 300 | 20 | 100.0% | 0 | 0 | 292 / 15 | Alibaba, Baidu, DigitalOcean, Novita, Relace, SiliconFlow, StreamLake |
| Kimi K3.1 Pro | openrouter | `moonshotai/kimi-k3.1-pro` | reasoning-off | `reasoning.enabled=false` | json_only | 300 | 20 | 100.0% | 0 | 0 | 326 / 20 | AkashML, DigitalOcean, InferenceNet, Morph, Parasail, PrimaLabs, Sail Research, Wafer |
| Llama 3.1 8B | openrouter | `meta-llama/llama-3.1-8b-instruct` | reasoning-off | `none sent (catalog: non-reasoning model)` | json_only | 300 | 20 | 100.0% | 0 | 0 | 328 / 45 | Cloudflare, Groq |

Per-model quirks:

- Claude Opus 5.5: thinking.type=disabled rejected: HTTP 400: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': '"thinking.type.disabled" is not supported for this model. U
- Claude Opus 5.5: output_config.effort=low accepted but spent reasoning tokens (reasoning=181, output=201)
- Claude Opus 5.5: no candidate honored reasoning-off; using lowest accepted setting
- GPT-6.1 Sol: reasoning.enabled=false rejected: HTTP 400: Reasoning is mandatory for this endpoint and cannot be disabled.
- GPT-6.1 Sol: reasoning.effort=none rejected: HTTP 400: Reasoning is mandatory for this endpoint and cannot be disabled.
- GPT-6.1 Sol: reasoning.effort=minimal accepted but spent reasoning tokens (reasoning=49, output=66)
- GPT-6.1 Sol: reasoning.effort=low accepted but spent reasoning tokens (reasoning=37, output=54)
- GPT-6.1 Sol: reasoning=false rejected: HTTP 400: reasoning: Invalid input: expected object, received boolean
- GPT-6.1 Sol: no candidate honored reasoning-off; using lowest accepted setting
- gpt-oss-120b: reasoning.enabled=false rejected: HTTP 400: Reasoning is mandatory for this endpoint and cannot be disabled.
- gpt-oss-120b: reasoning.effort=none rejected: HTTP 400: Reasoning is mandatory for this endpoint and cannot be disabled.
- gpt-oss-120b: reasoning.effort=minimal accepted but spent reasoning tokens (reasoning=447, output=469)
- gpt-oss-120b: reasoning.effort=low accepted but spent reasoning tokens (reasoning=334, output=356)
- gpt-oss-120b: reasoning=false rejected: HTTP 400: reasoning: Invalid input: expected object, received boolean
- gpt-oss-120b: no candidate honored reasoning-off; using lowest accepted setting
- Gemini 4 Argon: reasoning.enabled=false rejected: HTTP 400: Reasoning is mandatory for this endpoint and cannot be disabled.
- Gemini 4 Argon: reasoning.effort=none rejected: HTTP 400: Reasoning is mandatory for this endpoint and cannot be disabled.
- Gemini 3.8 Flash: reasoning.enabled=false rejected: HTTP 400: Reasoning is mandatory for this endpoint and cannot be disabled.
- Gemini 3.8 Flash: reasoning.effort=none rejected: HTTP 400: Reasoning is mandatory for this endpoint and cannot be disabled.
- Gemini 3.8 Flash: reasoning.effort=minimal accepted but spent reasoning tokens (reasoning=1119, output=1138)
- Gemini 3.8 Flash: reasoning.effort=low accepted but spent reasoning tokens (reasoning=710, output=729)
- Gemini 3.8 Flash: reasoning=false rejected: HTTP 400: reasoning: Invalid input: expected object, received boolean
- Gemini 3.8 Flash: no candidate honored reasoning-off; using lowest accepted setting
- Grok 4.7: reasoning.enabled=false rejected: HTTP 400: Reasoning is mandatory for this endpoint and cannot be disabled.
- Grok 4.7: reasoning.effort=none rejected: HTTP 400: Reasoning is mandatory for this endpoint and cannot be disabled.
- Grok 4.7: reasoning.effort=minimal accepted but spent reasoning tokens (reasoning=1091, output=1105)
- Grok 4.7: reasoning.effort=low accepted but spent reasoning tokens (reasoning=5492, output=5506)
- Grok 4.7: reasoning=false rejected: HTTP 400: reasoning: Invalid input: expected object, received boolean
- Grok 4.7: no candidate honored reasoning-off; using lowest accepted setting
- Qwen3.8 Max: reasoning.enabled=false rejected: HTTP 400: Reasoning is mandatory for this endpoint and cannot be disabled.
- Qwen3.8 Max: reasoning.effort=none rejected: HTTP 400: Reasoning is mandatory for this endpoint and cannot be disabled.
- Qwen3.8 Max: reasoning.effort=minimal accepted but spent reasoning tokens (reasoning=1093, output=1109)
- Qwen3.8 Max: reasoning.effort=low accepted but spent reasoning tokens (reasoning=836, output=852)
- Qwen3.8 Max: reasoning=false rejected: HTTP 400: reasoning: Invalid input: expected object, received boolean
- Qwen3.8 Max: no candidate honored reasoning-off; using lowest accepted setting

## Gate

See `reports/gate_history.md` (attempts 1-3).

## Limitations

- Model identity is unverified for the 12 OpenRouter models (D4, D11). OpenRouter may route a model to different upstream providers; the serving provider is logged for every call.
- Claude Opus 5.5 cannot disable thinking on the Anthropic API (`thinking.type=disabled` returns HTTP 400); it runs at the lowest effort and is labeled "thinking not disableable, lowest setting" (D1).
- Temperature is not sent to the Claude models (the API rejects sampling parameters); the API default, 1.0, equals the design temperature.

