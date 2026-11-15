# Instrument gate history

Thresholds (configs/study.json, unchanged since the original freeze): G1 abs(U - 1) <= 0.25; G2 evidence effect >= 4x tone effect; G3 abs(C) < 0.1 nats; G4 Spearman(ln lambda, Delta_obs) >= 0.8 (trial level). G1 and G3 use the control arm; G2 and G4 use control arm plus practice prompts. An attempt passes only if each gate model passes G1-G4 on its own and the pooled G2 and G4 pass.

## First run (CheaperInference reseller): attempts 1-2

Attempts 1 and 2 ran through the CheaperInference reseller, before D11. Archived reports: [gate history](archive_cheaperinference/gate_history.md), [preliminary report](archive_cheaperinference/prelim_report.md), [preliminary summary](archive_cheaperinference/prelim_summary.json), [spend reconciliation](archive_cheaperinference/spend_reconciliation.json). Their tables are reproduced unchanged below.

### Attempt 1: FAIL

Decision basis: D3 (original design). Gate models: Claude Haiku 5.5 (reasoning-off or lowest setting), DeepSeek V4 Pro (reasoning on). Practice seed 7311977, 200 practice prompts per model.

| Scope | U (control) | C (control, nats) | Evidence / tone effect (nats) | Spearman rho | G1 | G2 | G3 | G4 |
|---|---|---|---|---|---|---|---|---|
| Claude Haiku 5.5 | 0.182 | 1.439 | 0.419 / 0.025 | 0.201 (n=390) | FAIL | PASS | FAIL | FAIL |
| DeepSeek V4 Pro | 1.001 | 0.016 | 4.164 / 0.002 | 0.943 (n=389) | PASS | PASS | PASS | PASS |
| pooled | 0.592 | 0.728 | 2.286 / 0.014 | 0.645 (n=779) | FAIL | PASS | FAIL | FAIL |

Attempt 1 headline numbers: Haiku U=0.18, C=1.44; pooled rho=0.645. DeepSeek alone: U=1.00, C=0.02, rho=0.943. Haiku (reasoning-off, JSON schema) answers about 34% at every evidence level, so it cannot apply the stated rule; D7 replaces it as a gate model. Attempt 1 stays in the record as a failed first attempt.

### Attempt 2: PASS

Decision basis: D7, D8. Gate models: DeepSeek V4 Pro (reasoning on), gpt-oss-120b (reasoning-off or lowest setting). Practice seed 97799913, 100 practice prompts per model; control arm reused from attempt 1 for DeepSeek V4 Pro.

| Scope | U (control) | C (control, nats) | Evidence / tone effect (nats) | Spearman rho | G1 | G2 | G3 | G4 |
|---|---|---|---|---|---|---|---|---|
| DeepSeek V4 Pro | 1.001 | 0.016 | 4.168 / 0.035 | 0.943 (n=313) | PASS | PASS | PASS | PASS |
| gpt-oss-120b | 0.975 | -0.090 | 4.133 / 0.056 | 0.926 (n=313) | PASS | PASS | PASS | PASS |
| pooled | 0.988 | -0.037 | 4.150 / 0.010 | 0.934 (n=626) | PASS | PASS | PASS | PASS |

## D11 and D12 runs (Anthropic API for Claude models, OpenRouter for the rest)

### Attempt 3: FAIL

Decision basis: D11 (gate re-run on the new routes; D7 gate models, D8 practice size, fresh seed). Gate models: DeepSeek V4 Pro (reasoning on, openrouter `deepseek/deepseek-v4-pro`, OpenRouter routing (unpinned)), gpt-oss-120b (reasoning-off or lowest setting, openrouter `openai/gpt-oss-120b`, OpenRouter routing (unpinned)). Practice seed 35588301 (fresh, disjoint from the main grid and attempts 1-2), 100 practice prompts per model; new 320-call control arm for each gate model.

G1-G4 for attempt 3: G1 PASS, G2 PASS, G3 FAIL, G4 PASS; overall **FAIL**.

| Scope | U (control) | C (control, nats) | Evidence / tone effect (nats) | Spearman rho | G1 | G2 | G3 | G4 |
|---|---|---|---|---|---|---|---|---|
| DeepSeek V4 Pro | 0.997 | 0.004 | 4.160 / 0.002 | 0.943 (n=309) | PASS | PASS | PASS | PASS |
| gpt-oss-120b | 0.924 | -0.169 | 3.957 / 0.054 | 0.924 (n=314) | PASS | PASS | FAIL | PASS |
| pooled | 0.960 | -0.084 | 4.057 / 0.028 | 0.933 (n=623) | PASS | PASS | PASS | PASS |

Calls: DeepSeek V4 Pro 314 control + 99 practice (7 missing), gpt-oss-120b 320 control + 100 practice (0 missing).

Per-host data, DeepSeek V4 Pro (attempt 3; pinned to no host; calls served by another host: n/a):

| Serving host | Calls | Control calls | U (control) | C (control, nats) |
|---|---|---|---|---|
| Relace | 260 | 183 | 0.994 | 0.008 |
| Novita | 42 | 37 | 0.997 | -0.003 |
| GMICloud | 33 | 24 | 1.000 | 0.008 |
| StreamLake | 28 | 25 | 1.007 | -0.003 |
| Alibaba | 22 | 16 | 0.993 | 0.006 |
| DigitalOcean | 20 | 18 | 1.007 | -0.015 |
| SiliconFlow | 15 | 11 | 1.003 | -0.018 |

Per-host data, gpt-oss-120b (attempt 3; pinned to no host; calls served by another host: n/a):

| Serving host | Calls | Control calls | U (control) | C (control, nats) |
|---|---|---|---|---|
| Crusoe | 291 | 213 | 0.953 | -0.124 |
| AkashML | 65 | 54 | 0.873 | -0.339 |
| PrimeIntellect | 26 | 16 | 0.744 | -0.524 |
| Sail Research | 19 | 18 | 0.896 | 0.005 |
| Groq | 18 | 18 | 0.949 | 0.002 |
| CoreWeave | 1 | 1 | n/a | n/a |

### Attempt 4: FAIL

Decision basis: D12 (gate re-run on pinned hosts; D7 gate models, D8 practice size, fresh seed). Gate models: DeepSeek V4 Pro (reasoning on, openrouter `deepseek/deepseek-v4-pro`, pinned host `relace/fp4`), gpt-oss-120b (reasoning-off or lowest setting, openrouter `openai/gpt-oss-120b`, pinned host `cerebras/fp16`). Practice seed 67897034 (fresh, disjoint from the main grid and attempts 1-3), 100 practice prompts per model; new 320-call control arm for each gate model.

G1-G4 for attempt 4: G1 PASS, G2 PASS, G3 FAIL, G4 PASS; overall **FAIL**.

| Scope | U (control) | C (control, nats) | Evidence / tone effect (nats) | Spearman rho | G1 | G2 | G3 | G4 |
|---|---|---|---|---|---|---|---|---|
| DeepSeek V4 Pro | 0.986 | -0.001 | 4.122 / 0.024 | 0.939 (n=314) | PASS | PASS | PASS | PASS |
| gpt-oss-120b | 0.928 | -0.113 | 3.995 / 0.022 | 0.931 (n=314) | PASS | PASS | FAIL | PASS |
| pooled | 0.957 | -0.057 | 4.058 / 0.001 | 0.935 (n=628) | PASS | PASS | PASS | PASS |

Calls: DeepSeek V4 Pro 320 control + 100 practice (0 missing), gpt-oss-120b 320 control + 100 practice (0 missing).

Per-host data, DeepSeek V4 Pro (attempt 4; pinned to Relace; calls served by another host: 0):

| Serving host | Calls | Control calls | U (control) | C (control, nats) |
|---|---|---|---|---|
| Relace | 420 | 320 | 0.986 | -0.001 |

Per-host data, gpt-oss-120b (attempt 4; pinned to Cerebras; calls served by another host: 0):

| Serving host | Calls | Control calls | U (control) | C (control, nats) |
|---|---|---|---|---|
| Cerebras | 420 | 320 | 0.928 | -0.113 |

