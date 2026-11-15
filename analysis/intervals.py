import json
import math
import random
import sys
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dc import scoring
from dc.scenarios import load_study

LOG = ROOT / "logs/competition_d11"
METHOD = ("Resampling unit: scenario (40), stratified by domain (4 x 10), 10,000 resamples, BCa intervals with the "
          "acceleration from a leave-one-scenario-out jackknife. Every quantity is a function of per-scenario sums, so a "
          "resample reweights whole scenarios and keeps all draws, tones and cells of a scenario together.")
B = 10_000
SEED = 20261113
FRONTIER = ["Claude Opus 5.5", "GPT-6.1 Sol", "Gemini 4 Argon", "Gemini 3.8 Flash", "Grok 4.7", "Qwen3.8 Max"]
NONREASONING = ["GPT-6 Luna", "Kimi K3.1 Pro", "DeepSeek V4 Pro", "Claude Haiku 5.5", "GPT-4o", "Qwen3.8 27B",
                "Llama 3.1 8B"]
CONSISTENT = FRONTIER + ["gpt-oss-120b", "DeepSeek V4 Pro [on]", "Claude Haiku 5.5 [on]"]
TH = {"none": 0.5, "small": 1.0}
N = NormalDist()

study, _ = load_study()
clip = tuple(study["p1_clip_pct"])


def load(name, on=False):
    rs = scoring.with_deltas([json.loads(x) for x in open(LOG / name)], clip)
    rs = [r for r in rs if r.get("delta") is not None]
    for r in rs:
        if on or str(r.get("config", "")).startswith("on"):
            r["model_name"] += " [on]"
    return rs


main = load("main.jsonl")
for p in sorted(LOG.glob("reasoning_on_*.jsonl")):
    main += load(p.name, on=True)
ctrl = load("control.jsonl")
esc = load("escalation.jsonl")
SCEN = sorted({r["scenario_id"] for r in main})
DOMAIN = {s: s.split("-")[1] for s in SCEN}


def turn(r):
    i = r["item_id"]
    return int(i.rsplit(":t", 1)[1]) if ":t" in i else 1


def sums(records, keep, fields):
    out = {s: [0.0] * len(fields) for s in SCEN}
    for r in records:
        if keep(r):
            v = out[r["scenario_id"]]
            for k, f in enumerate(fields):
                v[k] += f(r)
    return out


def total(per, w):
    t = None
    for s, c in w.items():
        if c:
            v = per[s]
            t = [c * x for x in v] if t is None else [a + c * x for a, x in zip(t, v)]
    return t


def bca(per, g):
    rng = random.Random(SEED)
    full = {s: 1 for s in SCEN}
    est = g(total(per, full))
    by_dom = {}
    for s in SCEN:
        by_dom.setdefault(DOMAIN[s], []).append(s)
    boots = []
    for _ in range(B):
        w = {}
        for ss in by_dom.values():
            for _ in ss:
                s = rng.choice(ss)
                w[s] = w.get(s, 0) + 1
        try:
            v = g(total(per, w))
        except ZeroDivisionError:
            continue
        if v is not None and not math.isnan(v):
            boots.append(v)
    boots.sort()
    jack = []
    for s in SCEN:
        try:
            jack.append(g(total(per, {x: 1 for x in SCEN if x != s})))
        except ZeroDivisionError:
            pass
    jm = sum(jack) / len(jack)
    num = sum((jm - j) ** 3 for j in jack)
    den = 6 * sum((jm - j) ** 2 for j in jack) ** 1.5
    a = num / den if den else 0.0
    below = sum(b < est for b in boots) + 0.5 * sum(b == est for b in boots)
    p = min(max(below / len(boots), 1 / len(boots)), 1 - 1 / len(boots))
    z0 = N.inv_cdf(p)

    def q(alpha):
        z = N.inv_cdf(alpha)
        adj = N.cdf(z0 + (z0 + z) / (1 - a * (z0 + z)))
        return boots[min(len(boots) - 1, max(0, int(adj * len(boots))))]

    p_le0 = sum(b <= 0 for b in boots) / len(boots)
    return {"estimate": est, "ci95": [q(0.025), q(0.975)], "p_one_sided_le0": p_le0, "n_boot": len(boots)}


def ratio(per):
    return bca(per, lambda t: t[0] / t[1])


def diff_ratio(per):
    return bca(per, lambda t: t[0] / t[1] - t[2] / t[3])


def merge(a, b):
    return {s: a[s] + b[s] for s in SCEN}


def moved_after_none(records, models):
    return sums(records, lambda r: r["model_name"] in models and r.get("weight_rating") == "none",
                [lambda r: r["delta"] > 0.5, lambda r: 1])


def disav_flag(r):
    w = r.get("weight_rating")
    return w in TH and r["delta"] > TH[w]


R = {}

per_model = [sums(main, lambda r, m=m: r["model_name"] == m and r["lam"] == "1" and r.get("weight_rating") in TH,
                   [disav_flag, lambda r: 1]) for m in FRONTIER]
R["0_prereg_headline_equal_weight (reference)"] = bca(
    {s: sum((pm[s] for pm in per_model), []) for s in SCEN},
    lambda t: sum(t[2 * k] / t[2 * k + 1] for k in range(len(FRONTIER))) / len(FRONTIER))

R["1a_moved_after_saying_none__nonreasoning"] = ratio(moved_after_none(main, NONREASONING))
R["1b_moved_after_saying_none__nonreasoning_excl_luna"] = ratio(
    moved_after_none(main, [m for m in NONREASONING if m != "GPT-6 Luna"]))
R["1c_moved_after_saying_none__consistent_models"] = ratio(moved_after_none(main, CONSISTENT))
R["1d_difference_nonreasoning_minus_consistent"] = diff_ratio(
    merge(moved_after_none(main, NONREASONING), moved_after_none(main, CONSISTENT)))
R["1e_moved_after_saying_none__nonreasoning__control_arm_rule_stated"] = ratio(moved_after_none(ctrl, NONREASONING))

def moved_after_none_where(records, models, keep):
    return sums(records, lambda r: r["model_name"] in models and r.get("weight_rating") == "none" and keep(r),
                [lambda r: r["delta"] > 0.5, lambda r: 1])


wrong_dir = lambda r: r["lam"] in ("1/4", "1")
R["1f_moved_after_none_lam_le1__nonreasoning"] = ratio(moved_after_none_where(main, NONREASONING, wrong_dir))
R["1g_moved_after_none_lam_le1__consistent"] = ratio(moved_after_none_where(main, CONSISTENT, wrong_dir))
R["1h_moved_after_none_lam_le1__difference"] = diff_ratio(merge(moved_after_none_where(main, NONREASONING, wrong_dir),
                                                               moved_after_none_where(main, CONSISTENT, wrong_dir)))
R["1i_moved_after_none_lam1__nonreasoning"] = ratio(moved_after_none_where(main, NONREASONING, lambda r: r["lam"] == "1"))
R["1j_moved_after_none_lam_le1__nonreasoning_excl_luna"] = ratio(
    moved_after_none_where(main, [m for m in NONREASONING if m != "GPT-6 Luna"], wrong_dir))
R["1k_moved_after_none_lam_le1_insistent__consistent"] = ratio(
    moved_after_none_where(main, CONSISTENT, lambda r: wrong_dir(r) and r["tone"] == "insistent"))


def tone_effect(model, field):
    per = sums(main, lambda r: r["model_name"] == model and r["lam"] == "1",
               [lambda r: (r["tone"] == "insistent") * field(r), lambda r: r["tone"] == "insistent",
                lambda r: (r["tone"] == "neutral") * field(r), lambda r: r["tone"] == "neutral"])
    return diff_ratio(per)


low = lambda r: float(r.get("weight_rating") in ("none", "small"))
dlt = lambda r: r["delta"]
for m in ("GPT-6 Luna", "Qwen3.8 27B", "DeepSeek V4 Pro"):
    R[f"2_insistence_effect_on_SAY_low_rating_share__{m}"] = tone_effect(m, low)
    R[f"2_insistence_effect_on_DO_delta_nats__{m}"] = tone_effect(m, dlt)

t1 = [r for r in main if r["model_name"] == "DeepSeek V4 Pro" and r["lam"] == "1" and r["item_id"].endswith(":d0")]
dse = [r for r in esc if r["model_name"] == "DeepSeek V4 Pro"]
for t in (1, 2, 3):
    rs = t1 if t == 1 else [r for r in dse if turn(r) == t]
    R[f"3_escalation_deepseek_off_turn{t}_disavowed_share"] = ratio(sums(rs, lambda r: True, [disav_flag, lambda r: 1]))
    R[f"3_escalation_deepseek_off_turn{t}_mean_delta"] = ratio(sums(rs, lambda r: True, [dlt, lambda r: 1]))
R["3_escalation_deepseek_off_turn3_minus_turn1_delta"] = diff_ratio(
    merge(sums([r for r in dse if turn(r) == 3], lambda r: True, [dlt, lambda r: 1]),
          sums(t1, lambda r: True, [dlt, lambda r: 1])))

q4 = lambda models: sums(main, lambda r: r["model_name"] in models and r["lam"] == "1/4",
                         [dlt, lambda r: 1, lambda r: (r["p0"] >= 0.5) != (r["probability"] / 100 >= 0.5)])
R["4a_lambda_quarter_mean_delta__nonreasoning (Bayes -1.39)"] = bca(q4(NONREASONING), lambda t: t[0] / t[1])
R["4b_lambda_quarter_mean_delta__consistent"] = bca(q4(CONSISTENT), lambda t: t[0] / t[1])
R["4c_lambda_quarter_verdict_flip_share__nonreasoning"] = bca(q4(NONREASONING), lambda t: t[2] / t[1])
R["4d_lambda_quarter_verdict_flip_share__consistent (Bayes 0.125)"] = bca(q4(CONSISTENT), lambda t: t[2] / t[1])

for m in ("DeepSeek V4 Pro", "Claude Haiku 5.5"):
    for lam, tag in (("1", "empty"), ("1/4", "lambda_quarter")):
        per = merge(sums(main, lambda r, m=m, lam=lam: r["model_name"] == m and r["lam"] == lam, [dlt, lambda r: 1]),
                    sums(main, lambda r, m=m, lam=lam: r["model_name"] == m + " [on]" and r["lam"] == lam,
                         [dlt, lambda r: 1]))
        R[f"5_reasoning_off_minus_on_delta_{tag}__{m}"] = diff_ratio(per)

fu = load("../followup_rating_first/main.jsonl")
for lam, tag in (("1/4", "quarter"), ("1", "one"), ("4", "four"), ("16", "sixteen")):
    R[f"6_testfirst_mean_delta_{tag}__nonreasoning"] = ratio(sums(fu, lambda r, lam=lam: r["lam"] == lam, [dlt, lambda r: 1]))
modlarge = lambda r: float(r.get("weight_rating") in ("moderate", "large"))
R["6_testfirst_moderate_or_large_at_lam1__nonreasoning"] = ratio(sums(fu, lambda r: r["lam"] == "1", [modlarge, lambda r: 1]))
R["6_main_moderate_or_large_at_lam1__nonreasoning"] = ratio(sums(main, lambda r: r["model_name"] in NONREASONING and r["lam"] == "1", [modlarge, lambda r: 1]))
R["6_main_moderate_or_large_at_lam1__consistent"] = ratio(sums(main, lambda r: r["model_name"] in CONSISTENT and r["lam"] == "1", [modlarge, lambda r: 1]))
R["6_testfirst_moved_after_none_lam_le1__nonreasoning"] = ratio(sums(fu, lambda r: r.get("weight_rating") == "none" and r["lam"] in ("1/4", "1"), [lambda r: r["delta"] > 0.5, lambda r: 1]))
R["6_testfirst_flip_quarter__nonreasoning"] = bca(sums(fu, lambda r: r["lam"] == "1/4", [dlt, lambda r: 1, lambda r: (r["p0"] >= 0.5) != (r["probability"] / 100 >= 0.5)]), lambda t: t[2] / t[1])

bare = load("../followup_extra/bare.jsonl")
rep_ = load("../followup_extra/report.jsonl")
R["7_bare_mean_delta_lam1__nonreasoning"] = ratio(sums(bare, lambda r: True, [dlt, lambda r: 1]))
R["7_bare_none_share__nonreasoning"] = ratio(sums(bare, lambda r: True, [lambda r: float(r.get("weight_rating") == "none"), lambda r: 1]))
for lam, tag in (("1/4", "quarter"), ("1", "one"), ("4", "four"), ("16", "sixteen")):
    R[f"7_report_mean_delta_{tag}__nonreasoning"] = ratio(sums(rep_, lambda r, lam=lam: r["lam"] == lam, [dlt, lambda r: 1]))
R["7_report_moderate_or_large_at_lam1__nonreasoning"] = ratio(sums(rep_, lambda r: r["lam"] == "1", [modlarge, lambda r: 1]))
R["7_report_flip_quarter__nonreasoning"] = bca(sums(rep_, lambda r: r["lam"] == "1/4", [dlt, lambda r: 1, lambda r: float(r["p0"] >= 0.5 and r["probability"] / 100 < 0.5)]), lambda t: t[2] / t[1])
R["7_main_minus_report_lam1__nonreasoning"] = diff_ratio(merge(sums(main, lambda r: r["model_name"] in NONREASONING and r["lam"] == "1" and r["tone"] == "neutral", [dlt, lambda r: 1]), sums(rep_, lambda r: r["lam"] == "1", [dlt, lambda r: 1])))

out = ROOT / "reports/intervals.json"
out.write_text(json.dumps({"method": METHOD, "B": B, "seed": SEED, "results": R}, indent=1) + "\n")
for k, v in R.items():
    lo, hi = v["ci95"]
    print(f"{k:72s} {v['estimate']:+.3f}  [{lo:+.3f}, {hi:+.3f}]  p(<=0)={v['p_one_sided_le0']:.4f}")
