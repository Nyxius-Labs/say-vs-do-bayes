# Instrument gate history

Thresholds (configs/study.json, unchanged since the original freeze): G1 abs(U - 1) <= 0.25; G2 evidence effect >= 4x tone effect; G3 abs(C) < 0.1 nats; G4 Spearman(ln lambda, Delta_obs) >= 0.8 (trial level). G1 and G3 use the control arm; G2 and G4 use control arm plus practice prompts. An attempt passes only if each gate model passes G1-G4 on its own and the pooled G2 and G4 pass.

## Attempt 1: FAIL

Decision basis: D3 (original design). Gate models: Claude Haiku 5.5 (reasoning-off or lowest setting), DeepSeek V4 Pro (reasoning on). Practice seed 7311977, 200 practice prompts per model.

| Scope | U (control) | C (control, nats) | Evidence / tone effect (nats) | Spearman rho | G1 | G2 | G3 | G4 |
|---|---|---|---|---|---|---|---|---|
| Claude Haiku 5.5 | 0.182 | 1.439 | 0.419 / 0.025 | 0.201 (n=390) | FAIL | PASS | FAIL | FAIL |
| DeepSeek V4 Pro | 1.001 | 0.016 | 4.164 / 0.002 | 0.943 (n=389) | PASS | PASS | PASS | PASS |
| pooled | 0.592 | 0.728 | 2.286 / 0.014 | 0.645 (n=779) | FAIL | PASS | FAIL | FAIL |

Attempt 1 headline numbers: Haiku U=0.18, C=1.44; pooled rho=0.645. DeepSeek alone: U=1.00, C=0.02, rho=0.943. Haiku (reasoning-off, JSON schema) answers about 34% at every evidence level, so it cannot apply the stated rule; D7 replaces it as a gate model. Attempt 1 stays in the record as a failed first attempt.

## Attempt 2: PASS

Decision basis: D7, D8. Gate models: DeepSeek V4 Pro (reasoning on), gpt-oss-120b (reasoning-off or lowest setting). Practice seed 97799913, 100 practice prompts per model; control arm reused from attempt 1 for DeepSeek V4 Pro.

| Scope | U (control) | C (control, nats) | Evidence / tone effect (nats) | Spearman rho | G1 | G2 | G3 | G4 |
|---|---|---|---|---|---|---|---|---|
| DeepSeek V4 Pro | 1.001 | 0.016 | 4.168 / 0.035 | 0.943 (n=313) | PASS | PASS | PASS | PASS |
| gpt-oss-120b | 0.975 | -0.090 | 4.133 / 0.056 | 0.926 (n=313) | PASS | PASS | PASS | PASS |
| pooled | 0.988 | -0.037 | 4.150 / 0.010 | 0.934 (n=626) | PASS | PASS | PASS | PASS |

