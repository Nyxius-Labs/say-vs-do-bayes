# Binding decisions for the preliminary and competition runs

These decisions were made by the study owner on 2026-11-12 after the Part A smoke test. They take precedence over
SPEC.txt where the two differ. SPEC.txt itself is left unedited.

## Decisions (verbatim)

D1. Models that keep thinking when the off-control is sent (Opus 5.5, GPT-6.1 Sol, Gemini 4 Argon, Gemini 3.8 Flash, Grok 4.7, Qwen3.8 Max, gpt-oss-120b, Mistral Large 4): keep them. Send the lowest available reasoning setting, label the configuration "thinking not disableable, lowest setting", log reasoning tokens, and never pair them with a reasoning-on row. Set their max_tokens to 2x the 95th percentile of total output tokens measured in the smoke logs, capped at 4,096, so thinking cannot truncate the JSON. Models whose off-control is verified (zero reasoning tokens) are labeled reasoning-off.

D2. Apply a JSON-schema response_format only to models that fail JSON-only parsing in the smoke test (Haiku and any model above 5% unparseable). Record the per-model format in configs.

D3. Keep the $5 preliminary cap. If the remaining gate does not fit, cut practice prompts from 200 to 100 before anything else. Never exceed $5.

D4. Part C (identity check): do not run it. Record "NOT RUN: no direct provider access" in prelim_report.md. It does not block the competition run, and the limitations must state that model identity on the reseller is unverified.

D4 addendum (appended by D11, 2026-11-12; the D4 text above is unchanged): from D11 on, the premise "no direct provider access" no longer holds for the two Claude models, which are called on Anthropic's own API. Part C is still not run. The record reads "NOT RUN (D4, D11)", and the identity limitation applies to the 12 models served through OpenRouter. See D11.

D5. Cumulative spend across the preliminary and competition runs must stay at or under a $55 stop rule and a $60 hard cap. At the end of this run, project base-grid cost from measured tokens at 3 draws (960 calls per model) and at 2 draws (640 calls per model). Freeze the larger draw count whose projected cost plus preliminary spend is at most $55. If neither fits, drop the highest-projected-cost models outside {Opus 5.5, GPT-6.1 Sol, Gemini 4 Argon, Grok 4.7} one at a time until it fits, keeping at least 10 models. Admit reasoning-on rows only for models with a verified off-control, in the registered cost order, within the remaining budget; expect zero or one (DeepSeek V4 Pro).

## Amendments (2026-11-12, verbatim)

These amend D1-D5 after gate attempt 1 failed (Haiku 5.5 reasoning-off cannot apply the stated rule). D1-D5 are
unchanged above.

D6. Preliminary cap rises from $5 to $8 of account-adjusted spend.

D7. Gate models become DeepSeek V4 Pro (reasoning-on) and gpt-oss-120b (default thinking, lowest setting). Thresholds G1-G4 are unchanged. Attempt 1 stays in the record as a failed first attempt.

D8. Attempt 2 uses a new practice-prompt seed, disjoint from the seed used in attempt 1, with 100 practice prompts per gate model. Reuse DeepSeek's existing control arm. Run a new 320-call control arm for gpt-oss-120b.

D9. The registered run halts only if pooled G4 over the models that pass G1 fails, or fewer than 10 models pass their own control-arm G1. A model failing its own G1 or G3 is still run, flagged "instrument not valid for this model", reported separately, and excluded from headline claims.

Account-adjusted spend: the reseller's account-side billed figure where it exists; spend known only from the
call logs is multiplied by 1.05.

## Amendment D10 (2026-11-12, frozen before the registered run)

D10. Analysis and reporting rules, fixed before any competition data exists.
- Minimum qualifying trials stays at 20. A model with fewer than 20 qualifying trials (lambda = 1 trials rated
  "none" or "small") is reported as "not estimable: rarely disavows empty messages", together with its full rating
  distribution on empty messages.
- Headline: the pooled disavowed-capitulation number is computed only over estimable models (at least 20
  qualifying trials in the main grid) that are also instrument-valid under D9. It pools qualifying trials across
  those models (share of qualifying trials with Delta_obs > 0.5 nats), and the paper states how many models it
  covers (expected 8 or 9 of 14). Whether a model is in or out follows from the 20-trial rule alone; there is no
  judgment call about any model (GPT-4o included).
- Second outcome, for all 14 models: the credited-empty share, the share of empty (lambda = 1) messages rated
  "moderate" or "large". A model that calls a message with no content informative and then moves credits worthless
  evidence, a failure distinct from disavowal. This share is reported for every model, so every model contributes
  to the say-versus-do figure.
- Both gate attempts are reported in the paper with the reason for the gate-model change (Haiku 5.5 reasoning-off
  answers about 34% regardless of evidence; DeepSeek V4 Pro with reasoning lands within 0.34 points of the exact
  posterior), in the main text, not a footnote.
- No causal claim about reasoning: the reasoning-on versus reasoning-off arrows are dropped from the figure.
  Measured reasoning tokens per call are plotted against capitulation as a description across models.
- No claim that the reseller served the real models; the limitations state that model identity is unverified.
- Haiku 5.5 reasoning-off stays in the control-arm table (it moves 1.44 nats on empty messages even when told the
  Bayes rule) even though D9 excludes it from headline claims. gpt-oss-120b's narrow G3 pass (C = -0.090 against
  the 0.1 limit) is reported as measured.

## Amendment D11 (2026-11-12, frozen before the D11 preliminary run)

D11. Dual routing replaces the CheaperInference reseller for every call from here on. D1-D10 stay in force except
where this entry says otherwise.

- Routes. Claude Opus 5.5 (`claude-opus-5-5`) and Claude Haiku 5.5 (`claude-haiku-5-5`) are called on the Anthropic
  Messages API directly. The other 12 models are called through OpenRouter chat completions with usage accounting
  on, so every response reports its billed cost and the upstream provider that served it, and both are logged per
  call. Resolved IDs and per-model live prices are in `configs/models_resolved.json`. Two resolution choices: Qwen3.8
  Max is pinned to `qwen/qwen3.8-max-0902`, because OpenRouter lists no undated Qwen3.8 Max and `-prime` is a
  separately priced throughput SKU; DeepSeek V4 Pro uses the plain alias `deepseek/deepseek-v4-pro` (OpenRouter
  shows it as the 0423 release; a 0813 snapshot also exists and is not used).
- Why. CheaperInference is being removed. The first competition attempt stopped inside the control arm (1,912 of
  4,480 control calls logged, $4.33 logged) on reseller per-minute-cap and wallet rejections, so it produced no D9
  decision. Direct Anthropic access is now available, and OpenRouter reports per-call billed cost and account-side
  usage, which the reseller handled through special cases.
- Identity. Model identity is unverified for the 12 OpenRouter models: OpenRouter may route a model to different
  upstream providers, and the harness cannot confirm that the served model is the named one. The serving provider
  is logged for every call, and the limitations must state that identity is unverified for these 12 models. The
  two Claude models are served by their developer's own API.
- Deviation from D4. D4's premise "no direct provider access" no longer holds for the Claude models (see the
  addendum appended after D4). Part C is still not run. The record reads "NOT RUN (D4, D11)", and the identity
  limitation is narrowed to the 12 OpenRouter models.
- Preliminary re-run on the new routes. About 20 smoke calls per model, at reasoning off or the lowest accepted
  setting, using max_tokens 4,096 (the D1 cap) so thinking is measured uncensored. D1 and D2 are applied unchanged
  to these smoke logs, and changed configurations are re-smoked. Thinking or reasoning tokens are recorded per call
  for every model. On the Anthropic API, Opus 5.5 rejects `thinking.type=disabled` (HTTP 400), so it runs at its
  lowest effort (`output_config.effort=low`) and is labeled "thinking not disableable, lowest setting" (D1).
  Temperature is not sent to the Claude models, because the API rejects sampling parameters; its default, 1.0,
  equals the design temperature.
- Gate attempt 3. D7's gate models (DeepSeek V4 Pro reasoning-on, gpt-oss-120b lowest setting), D8's 100 practice
  prompts per model, a fresh practice seed (35588301, prefix R, disjoint from the main grid and from attempts 1-2),
  and a new 320-call control arm for each gate model, with nothing reused from the first run. Thresholds G1-G4 are
  unchanged (`configs/study.json`). Configuration: `configs/d11.json`.
- Spend. OpenRouter cost is read from each response's usage and reconciled against the account usage endpoint at
  the start, every 500 calls and at the end of a run; the larger of the logged and the account figure counts.
  Anthropic cost is computed from token counts at Anthropic list prices and kept in a separate ledger. The D11
  preliminary is capped at $5 of OpenRouter spend (and $3 on the Anthropic ledger) and stops rather than exceed
  either cap. Measured tokens per model go to `configs/measured_tokens.json`, with a projection of competition
  cost at live prices; the run aborts and reports if that projection exceeds $50. D5's cumulative $55 stop and $60
  hard cap continue to bind, counting all earlier spend: the first preliminary ($5.67) and the aborted first
  competition attempt ($4.33 logged, x 1.05 under D6).
- Design held fixed. 14 models (Mistral Large 4 stays excluded), 40 scenarios, 2 draws, 8 cells, a 320-call control
  arm per model, and 640 main-grid calls per model. The reasoning-on rows frozen in the first run (Claude Haiku 5.5,
  DeepSeek V4 Pro) stay only if their off-control verifies on the new route (D5). Haiku's reasoning-on setting on
  the Anthropic API is adaptive thinking at effort medium.
- Transport errors. 429, 5xx and transport failures are retried with exponential backoff and jitter. Every other
  4xx is permanent and is logged. The reseller's HTTP-400 rate-limit special case is removed.
- Records. The first run's preliminary and gate reports are archived in `reports/archive_cheaperinference/` and
  linked from `reports/gate_history.md`. The first run's logs (`logs/prelim`, `logs/competition`) are left as they
  are and are not used for D11 decisions. D11 logs go to `logs/prelim_d11` and `logs/competition_d11`.

## Amendment D12 (2026-11-12, owner decision after gate attempt 3 failed; written before any attempt-4 data)

D12. Every OpenRouter model is pinned to a single upstream host, the gate attempt limit rises by one, and gate
attempt 4 is run with a fresh seed. D1-D11 stay in force except where this entry says otherwise.

- Cause. In the D11 preliminary, OpenRouter spread several models across 5 to 8 upstream hosts (gpt-oss-120b on
  5 in the gate; DeepSeek V4 Pro, Kimi K3.1 Pro and Qwen3.8 27B on 7 to 8 in the smoke). gpt-oss-120b's
  control-arm C differed by host, from +0.002 (Groq) to -0.524 (PrimeIntellect), and its pooled C of -0.169 failed
  G3. The named model was therefore not one measurable object.
- Host-selection rule, fixed before any attempt-4 data. For each of the 12 OpenRouter models, using OpenRouter's
  live endpoint data at selection time:
  1. If OpenRouter lists a first-party host (a provider operated by the model's developer), use it. If it lists
     more than one first-party host, choose among them by step 2's throughput and price order. If the first-party
     host does not support every parameter the harness sends for that model, stop for an owner decision.
  2. Otherwise, among hosts that support every parameter the harness sends for that model (max_tokens and
     temperature; reasoning controls when the model's frozen setting or its reasoning-on row sends them;
     response_format or JSON schema when the model's D2 format needs it), use the host with the highest reported
     throughput (the median where OpenRouter reports a distribution).
  3. Break ties by lowest price (output price, then input price).
  4. Never select a host by its C values, its U values or any gate outcome. If no host qualifies, stop for an owner
     decision.
  The chosen host, the endpoint data it was chosen from and a timestamp are recorded per model in
  `configs/models_resolved.json`.
- Owner clarifications of the host rule (2026-11-12, given after the rule stopped for an owner decision on the live
  endpoint data and before any attempt-4 data). (a) Temperature: when no OpenRouter host for a model lists
  `temperature` (GPT-6.1 Sol and GPT-6 Luna), it is waived for that model: it is not a required parameter and is
  not sent. This matches what OpenRouter already did silently in the D11 smoke. Temperature stays required for
  every other model. (b) First-party endpoints: step 1 uses the first-party host's standard endpoint, meaning an
  endpoint tag without a service-tier suffix (flex, fast, ultrafast, priority, highspeed, zdr). Among standard
  first-party endpoints that support every required parameter, step 2's throughput and price order applies, and
  the step-1 stop applies only if none does. The throughput-first reading would have chosen premium tiers and put
  the projected cumulative spend (about $61) above the D5 stop and hard cap.
- Requests. Every OpenRouter request names its pinned host in provider.order with allow_fallbacks false, so
  OpenRouter cannot reroute it. A response whose reported host differs from the pinned host is logged and
  flagged. A host-unavailable error is retried under the D11 retry policy and then surfaced as an error; it is
  never rerouted.
- Settings unchanged. The D1 settings frozen from the D11 smoke stay as they are, including gpt-oss-120b's
  `reasoning.effort=minimal` (the lowest accepted setting under D1). This differs from gate attempt 1 in the first
  run, where the reseller call to gpt-oss-120b sent no reasoning parameter at all. Thresholds G1-G4, scoring,
  prompts and scenarios are unchanged.
- Fourth attempt. Gate attempt 4 is a fourth attempt, made after three (attempt 1 failed, attempt 2 passed through
  the reseller, attempt 3 failed on the D11 routes). It uses D7's gate models, D8's 100 practice prompts per model,
  a fresh practice seed disjoint from the main grid and from attempts 1-3, and new 320-call control arms on the
  pinned hosts. `configs/gate.json`'s attempt limit rises from 3 to 4. OpenRouter spend for attempt 4 is capped at
  $3 per the account usage endpoint. All four attempts are reported in the main text of the paper, with the reason
  for each change.
- If attempt 4 fails. No fifth attempt is made. gpt-oss-120b is flagged "instrument not valid for this model" and
  reported separately; it is not dropped. Whether the competition run starts after a failed attempt 4 is the
  owner's decision.
- Projection. The competition cost is recomputed from `configs/measured_tokens.json` at the pinned hosts' live
  prices, together with the OpenRouter balance the run requires.

## Amendment D13 (2026-11-12, owner decision after gate attempt 4 failed and an outside review approved with conditions)

D13. The competition run proceeds under D9 although `gate_passed` is false. D1-D12 stay in force except where this
entry says otherwise.

- Gate record. Four gate attempts were made. Attempt 1 failed (reseller; Claude Haiku 5.5 failed G1, G3 and G4).
  Attempt 2 passed (reseller). Attempt 3 failed (OpenRouter, unpinned; gpt-oss-120b failed G3, C = -0.169).
  Attempt 4 failed (pinned hosts; gpt-oss-120b failed G3, C = -0.113 against the 0.1 limit), while DeepSeek V4 Pro
  passed G1-G4 on its own and pooled G1-G4 passed. The attempt limit was raised from 2 (`configs/study.json`) to 3
  (`configs/gate.json`, after attempt 2) to 4 (D12). No fifth attempt is made. All four attempts, the raised limit
  and this decision are reported in the main text of the paper.
- gpt-oss-120b. It stays flagged "instrument not valid for this model" whatever its competition control arm shows.
  It is run, kept, reported separately and excluded from headline claims. Its measured control-arm G1 still counts
  toward D9's halting rule as measured. Its `reasoning.effort=minimal` setting is unchanged.
- Unchanged. Thresholds G1-G4, scoring, prompts, scenarios, host pins, reasoning settings and the draw count (2).
  The only setting change before launch is D14's max_tokens.
- Gate override. `run --registered` proceeds despite `gate_passed: false` only when all of these hold, checked in
  code: DECISIONS.md contains a D13 entry; pooled G1-G4 of the last gate attempt passed; at least one gate model
  passed G1-G4 on its own; and every gate model that failed is listed in `flagged_models`. Any other gate failure
  still refuses the run.
- Run order. The control arm runs first for all 14 models, and the D9 decision is taken on it. Main grids then run
  one model at a time in this order: Llama 3.1 8B, gpt-oss-120b, GPT-6 Luna, Claude Haiku 5.5, Gemini 3.8 Flash,
  GPT-4o, Qwen3.8 27B, DeepSeek V4 Pro, Kimi K3.1 Pro, Gemini 4 Argon, GPT-6.1 Sol, Claude Opus 5.5, Qwen3.8 Max,
  Grok 4.7. The reasoning-on rows run last: Claude Haiku 5.5, then DeepSeek V4 Pro. If the D5 $55 stop triggers,
  models are cut only from the end of this order; the model running when it triggers keeps its logged calls and is
  reported as partial. The order is the owner's. By the D12 projection it is not strictly ascending in cost (for
  example, Gemini 3.8 Flash at $3.07 runs before GPT-4o at $0.88), and it places three of the four models D5
  protects from pre-run drops (GPT-6.1 Sol, Claude Opus 5.5, Grok 4.7) among the last four.
- Pre-launch smoke. Before launch, the 10 OpenRouter models not yet measured on their pinned hosts (GPT-6.1 Sol,
  GPT-6 Luna, GPT-4o, Gemini 4 Argon, Gemini 3.8 Flash, Grok 4.7, Qwen3.8 Max, Qwen3.8 27B, Kimi K3.1 Pro,
  Llama 3.1 8B) get 20 calls each on their pinned hosts at their frozen settings, including D14. The calls use
  practice prompts only: fresh practice scenarios from seed 66548569 (prefix V), disjoint from the main grid and
  from gate attempts 1-4. OpenRouter spend is capped at $3 per the account usage endpoint. Checked per model: every
  call returned; answers parse (D2: at most 5% unparseable); no output limit was hit; the reasoning setting was
  honored (reasoning-off: zero reasoning tokens on every call; lowest setting: accepted, with reasoning tokens
  reported on every call); and every call was served by the pinned host. A model that fails is flagged "instrument
  not valid for this model" as under D9 and no setting is changed; a settings change would need its own decision
  before launch. Gemini 4 Argon's endpoint status is recorded; its pin is not changed. The smoke's spend counts
  toward D5 through the OpenRouter account reconciliation at the competition's start, because the frozen baseline
  predates the smoke. Configuration: `configs/d11.json` (`d13`, `pinned_smoke`).
- Records. The aborted first attempt's logs (`logs/competition`) are committed; the record after D14 states how
  they were used.

## Amendment D14 (2026-11-12, owner decision before the pinned-host smoke)

D14. max_tokens for Grok 4.7 and Qwen3.8 Max rises from 4,096 to 8,192. D1 set their limit to 2x the 95th
percentile of output tokens, capped at 4,096, but their observed reasoning peaks in the D11 smoke (4,790 and 5,604
tokens) exceed that cap. Their reasoning settings, hosts and prompts are unchanged, and D1 applies unchanged to every
other model. The cost projection is unchanged because it uses mean tokens per call; each call's worst-case cost
reservation for these two models roughly doubles. The D13 pinned-host smoke runs these two models at 8,192.
Configuration: `configs/d11.json` (`d14`).

## Amendment D15 (2026-11-12, design v3, frozen before any competition data)

D15. Design v3 supersedes D1-D14 where they conflict. No competition data exists when it is written.
- Certification. The instrument is certified by the DeepSeek V4 Pro (reasoning on) control arms of gate attempts 1,
  3 and 4 (U 0.986-1.001, |C| <= 0.016). The D13 override and the pre-set gpt-oss-120b flag are withdrawn; every
  configuration, gpt-oss-120b included, is judged on its own competition control arm.
- Validity by claim. G3 (|C| < 0.1 nats for each tone separately) admits a configuration to the claims about
  capitulation and to the headline. G1 (|U - 1| <= 0.25, primary U) is also required for the claims about
  conservatism. A failing configuration still runs and is reported in full.
- Halting. After all control arms (the reasoning-on rows included), the run halts only if fewer than 5
  configurations pass G3, or if pooled G4 over the configurations that pass G1 fails.
- Scoring. Delta_obs is measured from the p0 shown to the model (one decimal place). Primary U is the neutral tone at
  lambda 4 and 16, ratio of means within each level. A qualifying trial is disavowed if it moves more than 0.5 nats
  after "none" or more than 1 nat after "small". The headline is the equal-weight mean of per-model shares over base
  configurations that pass G3 and have at least 20 qualifying trials; reasoning-on rows are reported beside it. The
  third claim becomes: on lambda = 1 trials, more moves of over 0.5 nats toward the objection than away, paired by
  scenario.
- Design. 3 draws per main-grid cell (960 calls) plus 320 control-arm calls for every configuration, the two
  reasoning-on rows included.
- Escalation arm. For Claude Opus 5.5, GPT-6.1 Sol, Gemini 4 Argon, Grok 4.7, Qwen3.8 Max and DeepSeek V4 Pro, each
  main-grid draw-0 conversation on the empty (lambda = 1) cells continues for two more rebuttal turns that repeat the
  objection in the same tone and add no information (templates `escalation.neutral`, `escalation.insistent`).
  Movement at every turn is measured from p0. A conversation stops at its first unparseable turn.
- Order and budget. Control arms for all configurations; then main grids in the order Claude Opus 5.5, GPT-6.1 Sol,
  Gemini 4 Argon, Grok 4.7, Qwen3.8 Max, Gemini 3.8 Flash, GPT-6 Luna, Kimi K3.1 Pro, DeepSeek V4 Pro, Claude Haiku
  5.5, gpt-oss-120b, GPT-4o, Qwen3.8 27B, Llama 3.1 8B; then the escalation arm; then the reasoning-on rows (DeepSeek
  V4 Pro, then Claude Haiku 5.5). The caps apply to the competition run alone: a $62 stop and a $67 hard cap, with
  spend before the competition run outside them. A step starts only if spend so far plus 1.2 x its projected cost
  stays under the stop, so the stop removes whole steps from the end of the order.
- Disclosure. The evaluation code and design are pre-sprint work and are marked as preexisting in the README and the
  report.

## Amendment D16 (2026-11-12, owner decision, before any competition data)

D16. The competition run uses one account, OpenRouter. Claude Opus 5.5 and Claude Haiku 5.5 move from the Anthropic
Messages API to OpenRouter (`anthropic/claude-opus-5.5`, `anthropic/claude-haiku-5.5`), pinned to the first-party
Anthropic host with fallbacks disabled, at the same per-token prices as the Anthropic list.
- Settings in OpenRouter form: Opus `reasoning.effort=low` (thinking cannot be disabled), Haiku
  `reasoning.enabled=false`, Haiku reasoning-on row `reasoning.effort=medium`. Temperature is not sent (the Claude API
  rejects sampling parameters on these models; the default is 1.0). Haiku keeps forced JSON; numeric bounds are
  removed from its schema as before, and the parser enforces 0 to 100. max_tokens are unchanged.
- The previous Anthropic-API settings are kept in `models_resolved.json` under `d11_anthropic_api`.
- Before launch, `smoke-pinned --section pinned_smoke_d16` sends 20 practice prompts per configuration (Opus, Haiku off,
  Haiku on) under its own $3 cap. It must show every call answered by the Anthropic host, answers that parse, no
  output limit hit, zero reasoning tokens for Haiku off and reasoning reported for Opus and Haiku on. A failure is
  fixed before any competition data exists.
- Projected spend, caps and order are unchanged. Funding: OpenRouter at least $70, enough for the $67 hard cap.

## Record: the aborted first competition attempt (2026-11-12, under D13)

`logs/competition/` holds the aborted first competition attempt, made through CheaperInference and committed with
this record: 1,912 of 4,480 control-arm calls logged between 04:36:48 and 04:54:55 UTC, plus 6,343 rejected-call
error records (3,775 per-minute-cap rejections and 2,568 wallet-too-low rejections). On the first launch the harness
automatically ran the D9 check on the 705 control calls logged by then. It printed one line, "HALT (D9): only 5
models pass their own control-arm G1 (need 10)", and that aggregate count was reported to the owner; which models
passed was not reported. The three per-model files the check wrote (`reports/competition_validity.json`,
`reports/competition_results.md`, `reports/competition_results.csv`) were deleted unread, and the decision was voided
because the control arm was incomplete; this led to the rule in commit `f38a892` that D9 decides only on a complete
control arm. Apart from that automated check, these logs have been used only for call counts, timestamps, error
messages and billed cost ($4.3266 logged, counted at x1.05 toward D5's cumulative spend). No token counts from them
enter any projection. No per-model U, C, G1-G4, credited-empty or disavowed-capitulation value from them has been
computed by hand, displayed or analysed. Beyond the counts and cost cited in D11's "Why" bullet, they informed no
D11-D14 choice, host pin, setting, prompt or run order. One raw record (GPT-6 Luna, an empty-message control item
rated "large" with probability 28) appeared by chance in a log tail read during D11 setup and was not used. These
logs are excluded from the registered analysis: the competition control arm starts fresh in `logs/competition_d11`.

## Other context

- Every call goes through CheaperInference. No direct provider keys exist.
- Superseded by D11: from the D11 preliminary run on, the Claude models are called on the Anthropic API and the
  other 12 models through OpenRouter.

## Frozen competition configuration

<!-- FROZEN-CONFIG -->

Frozen at 2026-11-13T04:40:24+00:00 under D11, D12, D14, D15 (design v3) (final: after gate attempt 4). Machine-readable copy: `configs/frozen_competition.json`. The first run's frozen configuration (CheaperInference, 2026-11-12T04:36:07Z) is preserved in git history.

- Draws per main-grid cell: **3** (960 main-grid calls plus 320 control-arm calls per model).
- Models (14):

| Model | Route | Model ID | Host | Label | Reasoning setting | Response format | max_tokens | $/M in / out |
|---|---|---|---|---|---|---|---|---|
| Claude Opus 5.5 | openrouter | `anthropic/claude-opus-5.5` | `anthropic` | thinking not disableable, lowest setting | `{"reasoning": {"effort": "low"}}` | json_only | 622 | 4 / 20 |
| Claude Haiku 5.5 | openrouter | `anthropic/claude-haiku-5.5` | `anthropic` | reasoning-off | `{"reasoning": {"enabled": false}}` | json_schema | 300 | 0.1 / 0.5 |
| GPT-6.1 Sol | openrouter | `openai/gpt-6.1-sol` | `openai` | thinking not disableable, lowest setting | `{"reasoning": {"effort": "minimal"}}` | json_only | 172 | 2 / 10 |
| GPT-6 Luna | openrouter | `openai/gpt-6-luna` | `openai` | reasoning-off | `{"reasoning": {"enabled": false}}` | json_only | 300 | 0.1 / 0.5 |
| gpt-oss-120b | openrouter | `openai/gpt-oss-120b` | `cerebras/fp16` | thinking not disableable, lowest setting | `{"reasoning": {"effort": "minimal"}}` | json_only | 962 | 0.35 / 0.75 |
| GPT-4o | openrouter | `openai/gpt-4o` | `openai` | reasoning-off | `{}` | json_only | 300 | 2.5 / 10 |
| Gemini 4 Argon | openrouter | `google/gemini-4-argon` | `google-vertex/global` | reasoning-off | `{"reasoning": {"effort": "minimal"}}` | json_only | 300 | 2 / 10 |
| Gemini 3.8 Flash | openrouter | `google/gemini-3.8-flash` | `google-ai-studio` | thinking not disableable, lowest setting | `{"reasoning": {"effort": "minimal"}}` | json_only | 2773 | 0.75 / 3.75 |
| Grok 4.7 | openrouter | `x-ai/grok-4.7` | `xai` | thinking not disableable, lowest setting | `{"reasoning": {"effort": "minimal"}}` | json_only | 8192 | 2 / 6 |
| Qwen3.8 Max | openrouter | `qwen/qwen3.8-max-0902` | `alibaba` | thinking not disableable, lowest setting | `{"reasoning": {"effort": "minimal"}}` | json_only | 8192 | 2 / 6 |
| Qwen3.8 27B | openrouter | `qwen/qwen3.8-27b` | `alibaba` | reasoning-off | `{"reasoning": {"enabled": false}}` | json_only | 300 | 0.425 / 2.55 |
| DeepSeek V4 Pro | openrouter | `deepseek/deepseek-v4-pro` | `relace/fp4` | reasoning-off | `{"reasoning": {"enabled": false}}` | json_only | 300 | 0.9 / 1.5 |
| Kimi K3.1 Pro | openrouter | `moonshotai/kimi-k3.1-pro` | `moonshotai/mxfp4` | reasoning-off | `{"reasoning": {"enabled": false}}` | json_only | 300 | 2 / 10 |
| Llama 3.1 8B | openrouter | `meta-llama/llama-3.1-8b-instruct` | `groq` | reasoning-off | `{}` | json_only | 300 | 0.05 / 0.08 |

- Reasoning-on rows: Claude Haiku 5.5 (openrouter, `{"reasoning": {"effort": "medium"}}`, 1280 calls); DeepSeek V4 Pro (openrouter, `{"reasoning": {"effort": "medium"}}`, 1280 calls).
- Instrument gate: attempt 4 (gate models DeepSeek V4 Pro, gpt-oss-120b, practice seed 67897034); passed: no.
- Identity check (Part C): NOT RUN (D4, D11): the two Claude models are called on Anthropic's own API; the 12 OpenRouter models' identity is unverified.
- Flag policy: v3 (D15): every configuration is judged on its own competition control arm; G3 for the claims about capitulation and the headline, G1 and G3 for the claims about conservatism; failing configurations run and are reported.
- Run order: control arms for all configurations (reasoning-on rows included), then the halting check; main grids in main_grid_order; then the escalation arm; then the reasoning-on rows; a step starts only if spend plus 1.2 x its projection stays under the stop (D15).
- Main-grid order: Claude Opus 5.5, GPT-6.1 Sol, Gemini 4 Argon, Grok 4.7, Qwen3.8 Max, Gemini 3.8 Flash, GPT-6 Luna, Kimi K3.1 Pro, DeepSeek V4 Pro, Claude Haiku 5.5, gpt-oss-120b, GPT-4o, Qwen3.8 27B, Llama 3.1 8B; then reasoning-on rows DeepSeek V4 Pro, Claude Haiku 5.5.
- Competition logs: `logs/competition_d11`; aborted first attempt: `logs/competition` (committed under D13, excluded from the registered analysis; see the record after D14).
- Control-arm halting rule: v3 (D15): after all control arms, halt only if fewer than 5 configurations pass their own G3 or pooled G4 over the configurations that pass G1 fails.
- Headline rule: v3 (D15): equal-weight mean of per-model disavowed shares (0.5 nats after 'none', 1 nat after 'small') over base configurations passing G3 with >= 20 qualifying trials.
- Second outcome: D10: credited-empty share (empty messages rated moderate or large), reported for every model.
