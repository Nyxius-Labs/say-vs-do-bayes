# Preliminary run report: disavowed-capitulation

Generated 2026-11-12T04:36:07+00:00. Governed by SPEC.txt section 4 as amended by DECISIONS.md (D1-D10). All calls went through CheaperInference (`https://api.cheaperinference.com/v1`).

## Summary

| Item | Result |
|---|---|
| Mock tests (exact posteriors, hand-computed scores, parser, resume, cap, hash refusal) | PASS |
| Gate models (attempt 2) | DeepSeek V4 Pro, gpt-oss-120b |
| G1 control arm within 0.25 of U = 1 (each gate model) | PASS |
| G2 evidence effect >= 4x tone effect (pooled) | PASS |
| G3 control arm abs(C) < 0.1 nats (each gate model) | PASS |
| G4 Spearman(ln lambda, Delta_obs) >= 0.8 (trial level, pooled) | PASS |
| Part C identity check | NOT RUN: no direct provider access |
| Preliminary spend, account-adjusted (D6) | $5.6728 of $8.00 cap |
| Preliminary spend (call logs only) | $5.4456 |
| Account-side billed spend at last reconciliation | $5.6728 (2786 requests, checked 2026-11-12T04:36:07+00:00) |
| Projected competition spend | $42.45 (cumulative $48.12, stop $55) |

## Run notes

- Reseller catalog: ok: 569 catalog entries.
- Costs are the per-call billing.billed_cost_usd returned by CheaperInference; requests carry x-ci-techniques: off, x-ci-model-lock: true and x-ci-prompt-cache: off. About a quarter of prompt tokens were still served from providers' implicit prompt caches, which the reseller documents as impossible to disable; this lowers cost and does not change outputs.
- Preliminary cap: $8.00 of account-adjusted spend (D6; the original D3 cap was $5).
- Gate attempt 1: FAIL (D3 (original design); 200 practice prompts per model).
- Gate attempt 2: PASS (D7, D8; 100 practice prompts per model).

## Spend reconciliation

The reseller's per-request usage records show 15 requests that never reached the call log, billed $0.2272 in total. They are calls the client abandoned at its former 180 s timeout while the server finished and billed them; the retry was then logged. The client timeout is now 800 s (above the reseller's 795 s ceiling), and the D5 projection and the competition cost tracker start from the larger, account-side figure ($5.6728).

## Gate (Part B)

Current attempt: 2 (D7, D8). Gate models: DeepSeek V4 Pro (reasoning on), gpt-oss-120b (lowest setting / reasoning-off); 320-call control arm each (reused from attempt 1 for DeepSeek V4 Pro) and 100 practice prompts per model from seed 97799913. Overall: **PASS**. Full history: reports/gate_history.md.

| Scope | U (control) | C (control, nats) | Evidence / tone effect (nats) | Spearman rho | G1 | G2 | G3 | G4 |
|---|---|---|---|---|---|---|---|---|
| DeepSeek V4 Pro | 1.001 | 0.016 | 4.168 / 0.035 | 0.943 (n=313) | PASS | PASS | PASS | PASS |
| gpt-oss-120b | 0.975 | -0.090 | 4.133 / 0.056 | 0.926 (n=313) | PASS | PASS | PASS | PASS |
| pooled | 0.988 | -0.037 | 4.150 / 0.010 | 0.934 (n=626) | PASS | PASS | PASS | PASS |

Pooled G1 and G3 above are computed on pooled records for information; the pooled gate decision for G1 and G3 is that every gate model passes on its own.

| Gate model | Arm | Valid | Missing | U | Conservatism | C | C neutral | C insistent | Disavowed share (n) |
|---|---|---|---|---|---|---|---|---|---|
| DeepSeek V4 Pro | control | 320 | 0 | 1.001 | -0.001 | 0.016 | -0.002 | 0.034 | 0.000 (76) |
| DeepSeek V4 Pro | practice | 97 | 3 | 0.999 | 0.001 | 0.090 | -0.003 | 0.200 | 0.000 (22) |
| gpt-oss-120b | control | 319 | 1 | 0.975 | 0.025 | -0.090 | -0.040 | -0.140 | 0.000 (76) |
| gpt-oss-120b | practice | 100 | 0 | 1.004 | -0.004 | 0.013 | 0.028 | -0.002 | 0.000 (19) |

Gate attempts: 2 (maximum 3: the failed attempt 1, attempt 2 under D7-D8, and at most one wording revision).
- attempt 1: FAIL; practice prompts 200; no wording revision
- attempt 2: PASS; practice prompts 100; no wording revision

## Part C identity check

NOT RUN: no direct provider access (D4). This does not block the competition run. Limitation: model identity on the reseller is unverified.

## Per-model configuration and smoke results (Part A and step-3 re-smoke)

| # | Model | Reseller ID | Label | Setting sent | Format | max_tokens | Calls | Parsed | Truncated | Mean reasoning tok | Mean in / out tok |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Claude Opus 5.5 | `claude-opus-5.5` | thinking not disableable, lowest setting | `reasoning.enabled=false` | json_only | 3804 | 40 | 100.0% | 0 | 1019 | 395 / 1037 |
| 2 | Claude Haiku 5.5 | `claude-haiku-5.5` | reasoning-off | `reasoning.enabled=false` | json_schema | 300 | 40 | 100.0% | 0 | 0 | 705 / 20 |
| 3 | GPT-6.1 Sol | `gpt-6.1-sol` | thinking not disableable, lowest setting | `reasoning.enabled=false` | json_only | 4096 | 40 | 100.0% | 0 | 1250 | 290 / 1264 |
| 4 | GPT-6 Luna | `gpt-6-luna` | reasoning-off (unverified: reasoning tokens not reported) | `reasoning.enabled=false` | json_only | 300 | 40 | 100.0% | 0 | 0 | 297 / 15 |
| 5 | gpt-oss-120b | `gpt-oss-120b` | thinking not disableable, lowest setting | `none sent (catalog: non-reasoning model)` | json_schema | 1200 | 40 | 100.0% | 0 | 687 | 427 / 713 |
| 6 | GPT-4o | `gpt-4o` | reasoning-off | `none sent (catalog: non-reasoning model)` | json_only | 300 | 40 | 100.0% | 0 | 0 | 296 / 19 |
| 7 | Gemini 4 Argon | `gemini-4-argon` | thinking not disableable, lowest setting | `reasoning.enabled=false` | json_only | 4096 | 40 | 100.0% | 0 | 1188 | 362 / 1205 |
| 8 | Gemini 3.8 Flash | `gemini-3.8-flash` | thinking not disableable, lowest setting | `reasoning.effort=minimal` | json_only | 3098 | 40 | 100.0% | 0 | 777 | 362 / 794 |
| 9 | Grok 4.7 | `grok-4.7` | thinking not disableable, lowest setting | `reasoning.enabled=false` | json_only | 4096 | 40 | 100.0% | 0 | 933 | 337 / 948 |
| 10 | Qwen3.8 Max | `qwen3.8-max` | thinking not disableable, lowest setting | `reasoning.enabled=false` | json_only | 4096 | 40 | 100.0% | 0 | 1123 | 328 / 1138 |
| 11 | Qwen3.8 27B | `qwen3.8-27b` | reasoning-off | `reasoning.enabled=false` | json_only | 300 | 40 | 100.0% | 0 | 0 | 327 / 25 |
| 12 | DeepSeek V4 Pro | `deepseek-v4-pro` | reasoning-off | `reasoning=false` | json_only | 300 | 40 | 100.0% | 0 | 0 | 293 / 18 |
| 13 | Kimi K3.1 Pro | `kimi-k3.1-pro` | reasoning-off (unverified: reasoning tokens not reported) | `reasoning.enabled=false` | json_only | 300 | 40 | 100.0% | 0 | 0 | 329 / 20 |
| 14 | Mistral Large 4 | `mistral-large-4` | thinking not disableable, lowest setting | `reasoning.effort=low` | json_schema | 4096 | 40 | 97.5% | 1 | 2418 | 438 / 2620 |
| 15 | Llama 3.1 8B | `llama-3.1-8b` | reasoning-off | `none sent (catalog: non-reasoning model)` | json_only | 300 | 40 | 100.0% | 0 | 0 | 328 / 17 |

Parse and truncation figures use only the calls made under each model's frozen configuration. Models whose request did not change (same reasoning setting, JSON-only) keep their Part A calls; models whose request changed were re-smoked with 40 new calls.

Per-model quirks and fixes:

- Claude Opus 5.5: reasoning.enabled=false accepted but spent reasoning tokens (reasoning=381, output=399)
- Claude Opus 5.5: reasoning.effort=none accepted but spent reasoning tokens (reasoning=352, output=370)
- Claude Opus 5.5: reasoning.effort=minimal accepted but spent reasoning tokens (reasoning=981, output=999)
- Claude Opus 5.5: reasoning.effort=low accepted but spent reasoning tokens (reasoning=691, output=709)
- Claude Opus 5.5: no candidate honored reasoning-off; using lowest accepted setting
- GPT-6.1 Sol: reasoning.enabled=false accepted but spent reasoning tokens (reasoning=391, output=402)
- GPT-6.1 Sol: reasoning.effort=none accepted but spent reasoning tokens (reasoning=279, output=293)
- GPT-6.1 Sol: reasoning.effort=minimal accepted but spent reasoning tokens (reasoning=250, output=264)
- GPT-6.1 Sol: reasoning.effort=low accepted but spent reasoning tokens (reasoning=1000, output=1014)
- GPT-6.1 Sol: no candidate honored reasoning-off; using lowest accepted setting
- gpt-oss-120b: none sent (catalog: non-reasoning model) accepted but spent reasoning tokens (reasoning=596, output=600)
- gpt-oss-120b: reasoning.enabled=false accepted but spent reasoning tokens (reasoning=596, output=600)
- gpt-oss-120b: reasoning.effort=none accepted but spent reasoning tokens (reasoning=596, output=600)
- gpt-oss-120b: reasoning.effort=minimal accepted but spent reasoning tokens (reasoning=596, output=600)
- gpt-oss-120b: reasoning.effort=low accepted but spent reasoning tokens (reasoning=596, output=600)
- gpt-oss-120b: no candidate honored reasoning-off; using lowest accepted setting
- gpt-oss-120b: JSON truncated at max_tokens=600; D1 rule re-applied to re-smoke measurements -> max_tokens=1200 (tag off@reseller#d1d2r2)
- Gemini 4 Argon: reasoning.enabled=false accepted but spent reasoning tokens (reasoning=436, output=452)
- Gemini 4 Argon: reasoning.effort=none accepted but spent reasoning tokens (reasoning=346, output=362)
- Gemini 4 Argon: reasoning.effort=minimal accepted but spent reasoning tokens (reasoning=457, output=473)
- Gemini 4 Argon: reasoning.effort=low accepted but spent reasoning tokens (reasoning=583, output=599)
- Gemini 4 Argon: no candidate honored reasoning-off; using lowest accepted setting
- Gemini 3.8 Flash: reasoning.enabled=false rejected: HTTP 400: Gemini 3 thinking cannot be disabled. Supported reasoning effort values are minimal, low, medium, and high.
- Gemini 3.8 Flash: reasoning.effort=none rejected: HTTP 400: Gemini 3 thinking cannot be disabled. Supported reasoning effort values are minimal, low, medium, and high.
- Gemini 3.8 Flash: reasoning.effort=minimal accepted but spent reasoning tokens (reasoning=287, output=303)
- Gemini 3.8 Flash: reasoning.effort=low accepted but spent reasoning tokens (reasoning=301, output=317)
- Gemini 3.8 Flash: no candidate honored reasoning-off; using lowest accepted setting
- Grok 4.7: reasoning.enabled=false accepted but spent reasoning tokens (reasoning=388, output=403)
- Grok 4.7: reasoning.effort=none accepted but spent reasoning tokens (reasoning=440, output=455)
- Grok 4.7: reasoning.effort=minimal accepted but spent reasoning tokens (reasoning=1149, output=1164)
- Grok 4.7: reasoning.effort=low accepted but spent reasoning tokens (reasoning=395, output=410)
- Grok 4.7: no candidate honored reasoning-off; using lowest accepted setting
- Qwen3.8 Max: reasoning.enabled=false accepted but spent reasoning tokens (reasoning=241, output=255)
- Qwen3.8 Max: reasoning.effort=none accepted but spent reasoning tokens (reasoning=2011, output=2025)
- Qwen3.8 Max: reasoning.effort=minimal accepted but spent reasoning tokens (reasoning=508, output=522)
- Qwen3.8 Max: reasoning.effort=low accepted but spent reasoning tokens (reasoning=760, output=774)
- Qwen3.8 Max: no candidate honored reasoning-off; using lowest accepted setting
- Mistral Large 4: reasoning.enabled=false rejected: HTTP 400: reasoning_effort none is not supported for this model, supported values: [<ReasoningEffort.high: 'high'>, <ReasoningEffort.low: 'low'>, <ReasoningEffo
- Mistral Large 4: reasoning.effort=none rejected: HTTP 400: reasoning_effort none is not supported for this model, supported values: [<ReasoningEffort.high: 'high'>, <ReasoningEffort.low: 'low'>, <ReasoningEffo
- Mistral Large 4: reasoning.effort=minimal rejected: HTTP 400: Every route for mistral-large-4 rejected this request as invalid, but none said which part. Check the request against the API reference and retry; quo
- Mistral Large 4: reasoning.effort=low accepted but spent reasoning tokens (reasoning=598, output=600)
- Mistral Large 4: no candidate honored reasoning-off; using lowest accepted setting
- Mistral Large 4: JSON truncated at max_tokens=600; D1 rule re-applied to re-smoke measurements -> max_tokens=1200 (tag off@reseller#d1d2r2)
- Mistral Large 4: JSON truncated at max_tokens=1200; D1 rule re-applied to re-smoke measurements -> max_tokens=2400 (tag off@reseller#d1d2r3)
- Mistral Large 4: JSON truncated at max_tokens=2400; D1 rule re-applied to re-smoke measurements -> max_tokens=4096 (tag off@reseller#d1d2r4)

## Measured tokens per call

Reasoning-on row (DeepSeek V4 Pro, `{"reasoning": {"effort": "medium"}}`): 420 calls, mean input 353.5, mean output 1437.7 (of which reasoning 1400.1), mean cost $0.00433 per call.

Reasoning-off and lowest-setting tokens per model are in the table above (frozen configuration only).

## Projected competition cost (D5)

Measured USD per call = mean input tokens x input price + mean output tokens x output price, using the frozen configuration's smoke calls and live CheaperInference catalog prices. Each projection covers the control arm (320 calls per model) plus the main grid. Preliminary spend: $5.6728.

| Draws | Main-grid calls per model | Base cost (control + main) | Plus preliminary | Fits under $55 |
|---|---|---|---|---|
| 3 | 960 | $52.74 | $58.42 | no |
| 2 | 640 | $39.56 | $45.23 | yes |

| Model | USD per call | Base cost at frozen draws |
|---|---|---|
| Claude Opus 5.5 | $0.01451 | $13.93 |
| Claude Haiku 5.5 | $0.00005 | $0.05 |
| GPT-6.1 Sol | $0.00859 | $8.25 |
| GPT-6 Luna | $0.00002 | $0.02 |
| gpt-oss-120b | $0.00021 | $0.20 |
| GPT-4o | $0.00060 | $0.58 |
| Gemini 4 Argon | $0.00575 | $5.52 |
| Gemini 3.8 Flash | $0.00146 | $1.40 |
| Grok 4.7 | $0.00413 | $3.97 |
| Qwen3.8 Max | $0.00486 | $4.67 |
| Qwen3.8 27B | $0.00016 | $0.15 |
| DeepSeek V4 Pro | $0.00030 | $0.28 |
| Kimi K3.1 Pro | $0.00056 | $0.53 |
| Llama 3.1 8B | $0.00000 | $0.00 |

| Reasoning-on row (registered order) | Projected cost | Decision |
|---|---|---|
| gpt-oss-120b | n/a | ineligible (thinking not disableable, lowest setting) |
| GPT-6 Luna | n/a | ineligible (reasoning-off (unverified: reasoning tokens not reported)) |
| Claude Haiku 5.5 | $0.33 | admitted (output tokens proxied by DeepSeek V4 Pro measurement) |
| DeepSeek V4 Pro | $2.56 | admitted (measured (gate)) |
| Gemini 3.8 Flash | n/a | ineligible (thinking not disableable, lowest setting) |
| Gemini 4 Argon | n/a | ineligible (thinking not disableable, lowest setting) |
| Qwen3.8 Max | n/a | ineligible (thinking not disableable, lowest setting) |
| Grok 4.7 | n/a | ineligible (thinking not disableable, lowest setting) |
| GPT-6.1 Sol | n/a | ineligible (thinking not disableable, lowest setting) |
| Kimi K3.1 Pro | n/a | ineligible (reasoning-off (unverified: reasoning tokens not reported)) |
| Claude Opus 5.5 | n/a | ineligible (thinking not disableable, lowest setting) |

Reasoning-on rows admitted: **2** (Claude Haiku 5.5, DeepSeek V4 Pro).

## Frozen competition configuration

- Draws: **2** (640 main-grid + 320 control-arm calls per model).
- Models: 14 (Claude Opus 5.5, Claude Haiku 5.5, GPT-6.1 Sol, GPT-6 Luna, gpt-oss-120b, GPT-4o, Gemini 4 Argon, Gemini 3.8 Flash, Grok 4.7, Qwen3.8 Max, Qwen3.8 27B, DeepSeek V4 Pro, Kimi K3.1 Pro, Llama 3.1 8B). Dropped for budget: none.
- Mistral Large 4: excluded: 1 truncated JSON of 40 calls and 97.5% parsed at max_tokens=4096 (D1 cap 4,096 reached).
- Reasoning-on rows: Claude Haiku 5.5, DeepSeek V4 Pro.
- Projected competition spend $42.45; projected cumulative $48.12 (at most $55).
- Control-arm halting: the registered run halts only if pooled G4 over the models that pass their own control-arm G1 fails, or fewer than 10 models pass their own G1; a model failing its own G1 or G3 is still run, flagged "instrument not valid for this model", reported separately, and excluded from headline claims (D9).
- Full per-model settings: DECISIONS.md and `configs/frozen_competition.json`.

## Freeze

`hashes.json` (SHA-256); `python -m dc.cli run --registered` refuses to run if any of these differ:

- prompt_templates: `07b51704197fef2b...` (configs/prompt_templates.json, src/dc/prompts.py)
- thresholds_config: `21135a1293c8eee6...` (configs/study.json)
- scoring_code: `09a2d7bb74707ca5...` (src/dc/scoring.py)
- model_ids: `bfb791603813a32f...` (configs/models_resolved.json)
- reasoning_on_order: `c2eeb84728cc550c...` (configs/reasoning_on_order.json)
- stopping_rule: `673a24f0dd274fb4...` (configs/stopping_rule.json)
- frozen_competition_config: `ef5ced78c496b5e9...` (configs/frozen_competition.json)
- decisions: `202ab919509980b5...` (DECISIONS.md)
- gate_config: `c49242a9279b8b83...` (configs/gate.json)

## Limitations noted at freeze

- Model identity on the reseller is unverified (Part C not run; no direct provider access).
- 7 frozen models cannot disable thinking on this route and run at their lowest setting (Claude Opus 5.5, GPT-6.1 Sol, gpt-oss-120b, Gemini 4 Argon, Gemini 3.8 Flash, Grok 4.7, Qwen3.8 Max); they are labeled "thinking not disableable, lowest setting" and are never paired with a reasoning-on row. Their results measure deliberated, not reflexive, answers.
- Haiku's reasoning-off control-arm answers ignore the stated evidence (about 34% at every evidence level); this is why gate attempt 1 failed, and it is a finding about Haiku without a scratchpad, not about the prompts (DeepSeek V4 Pro with reasoning lands within 0.4 points of the exact posterior on the same items, and attempt 2 passed with D7's gate models). Under D9, reasoning-off Haiku is expected to fail its own control-arm G1 in the registered run, which flags it "instrument not valid for this model" without halting the run; its paired reasoning-on contrast would then be reported with the flagged models.
- Attempt 2's practice prompts use a fresh seed disjoint from attempt 1, but DeepSeek's control arm is reused from attempt 1 (D8), so DeepSeek's G1 and G3 are not independent replications.
- Two draws per cell instead of the SPEC's three (D5 budget rule); Mistral Large 4 is excluded for truncated JSON at the D1 output cap.
- Reasoning-on output tokens for admitted rows other than DeepSeek V4 Pro are proxied by the DeepSeek measurement; the competition run enforces the $55 stop and $60 hard cap in code regardless.

