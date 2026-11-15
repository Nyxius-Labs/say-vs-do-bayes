import json
import math
import random
import sys
from pathlib import Path
from statistics import NormalDist

import matplotlib

matplotlib.use("pdf")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.colors import CenteredNorm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dc import scoring
from dc.scenarios import load_study

LOG = ROOT / "logs/competition_d11"
OUT = ROOT / "figures"
OUT.mkdir(exist_ok=True)
B = 10_000
SEED = 20261113
N = NormalDist()
FRONTIER = ["Claude Opus 5.5", "GPT-6.1 Sol", "Gemini 4 Argon", "Gemini 3.8 Flash", "Grok 4.7", "Qwen3.8 Max"]
NONREASONING = ["GPT-6 Luna", "Kimi K3.1 Pro", "DeepSeek V4 Pro", "Claude Haiku 5.5", "GPT-4o", "Qwen3.8 27B",
                "Llama 3.1 8B"]
CONSISTENT = FRONTIER + ["gpt-oss-120b", "DeepSeek V4 Pro [on]", "Claude Haiku 5.5 [on]"]
LAMS = ["1/4", "1", "4", "16"]
LOGLAM = {lam: math.log(eval(lam)) for lam in LAMS}

ACCENT, GREY, LIGHT, INK, MUTED, PANEL = "#B2182B", "#9A9A9A", "#D9D9D9", "#222222", "#777777", "#F0EFEC"

from matplotlib import font_manager
for _f in sorted((ROOT / "paper/fonts").glob("*.ttf")):
    font_manager.fontManager.addfont(str(_f))

plt.rcParams.update({
    "font.family": "serif", "font.serif": ["Old Standard TT", "Times New Roman", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "text.color": INK,
    "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK, "axes.edgecolor": INK, "axes.linewidth": 0.5,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": True, "axes.grid": False, "xtick.major.size": 2.5,
    "ytick.major.size": 2.5, "xtick.major.width": 0.5, "ytick.major.width": 0.5, "xtick.direction": "out",
    "ytick.direction": "out", "xtick.major.pad": 2.5, "ytick.major.pad": 2.5, "legend.frameon": False,
    "pdf.fonttype": 42, "savefig.bbox": "tight", "savefig.pad_inches": 0.02, "lines.linewidth": 1.2,
    "lines.solid_capstyle": "round",
})

study, templates = load_study()
clip = tuple(study["p1_clip_pct"])


def load(name, on=False):
    rs = [r for r in scoring.with_deltas([json.loads(x) for x in open(LOG / name)], clip) if r.get("delta") is not None]
    if on:
        for r in rs:
            r["model_name"] += " [on]"
    return rs


main = load("main.jsonl")
for p in sorted(LOG.glob("reasoning_on_*.jsonl")):
    main += load(p.name, on=True)
esc = load("escalation.jsonl")
fu = load("../followup_rating_first/main.jsonl")
bare = load("../followup_extra/bare.jsonl")
rep_ = load("../followup_extra/report.jsonl")
SCEN = sorted({r["scenario_id"] for r in main})
BY_DOM = {}
for s in SCEN:
    BY_DOM.setdefault(s.split("-")[1], []).append(s)
WEIGHTS = []
_rng = random.Random(SEED)
for _ in range(B):
    w = {}
    for ss in BY_DOM.values():
        for _ in ss:
            s = _rng.choice(ss)
            w[s] = w.get(s, 0) + 1
    WEIGHTS.append(w)


def per_scenario(records, keep, num):
    out = {s: [0.0, 0.0] for s in SCEN}
    for r in records:
        if keep(r):
            out[r["scenario_id"]][0] += num(r)
            out[r["scenario_id"]][1] += 1
    return out


def ratio_ci(per):
    def g(w):
        a = sum(c * per[s][0] for s, c in w.items())
        b = sum(c * per[s][1] for s, c in w.items())
        return a / b if b else None
    est = g({s: 1 for s in SCEN})
    boots = sorted(v for v in (g(w) for w in WEIGHTS) if v is not None)
    jack = [v for v in (g({x: 1 for x in SCEN if x != s}) for s in SCEN) if v is not None]
    jm = sum(jack) / len(jack)
    den = 6 * sum((jm - j) ** 2 for j in jack) ** 1.5
    a = sum((jm - j) ** 3 for j in jack) / den if den else 0.0
    below = sum(b < est for b in boots) + 0.5 * sum(b == est for b in boots)
    z0 = N.inv_cdf(min(max(below / len(boots), 1 / len(boots)), 1 - 1 / len(boots)))

    def q(alpha):
        z = N.inv_cdf(alpha)
        return boots[min(len(boots) - 1, max(0, int(N.cdf(z0 + (z0 + z) / (1 - a * (z0 + z))) * len(boots))))]
    return est, q(0.025), q(0.975)


def mean_delta(models, lam):
    return ratio_ci(per_scenario(main, lambda r: r["model_name"] in models and r["lam"] == lam, lambda r: r["delta"]))



data = {}
LAMLAB = ["1/4", "1", "4", "16"]
W, H = 3.4, 2.3


def despine(ax, xb=None, yb=None):
    for side in ("left", "bottom"):
        ax.spines[side].set_position(("outward", 4))
    if xb:
        ax.spines["bottom"].set_bounds(*xb)
    if yb:
        ax.spines["left"].set_bounds(*yb)


def legend_top(ax, items):
    hs = [Line2D([], [], color=c, ls=ls, lw=1.3 if mk else 0.8, marker=mk, ms=4, mec="white", mew=0.6, label=lab)
          for c, ls, mk, lab in items]
    ax.legend(handles=hs, loc="lower left", bbox_to_anchor=(-0.02, 1.0), ncol=len(hs), frameon=False, fontsize=7,
              handlelength=1.8, handletextpad=0.5, columnspacing=1.3, borderaxespad=0.3)


def save(fig, name):
    fig.savefig(OUT / f"{name}.pdf")
    plt.close(fig)


def point(ax, xs, est, col, lw=1.3, ms=4.2):
    ax.plot(xs, [e[0] for e in est], color=col, lw=lw, zorder=3)
    ax.vlines(xs, [e[1] for e in est], [e[2] for e in est], color=col, lw=0.8, zorder=3)
    ax.plot(xs, [e[0] for e in est], "o", color=col, ms=ms, mec="white", mew=0.7, zorder=4)



key = lambda m: sum(abs(mean_delta([m], l)[0] - LOGLAM[l]) for l in LAMS)
order_c, order_n = sorted(CONSISTENT, key=key), sorted(NONREASONING, key=key)
dev = {m: [sum(r["delta"] for r in main if r["model_name"] == m and r["lam"] == l) / 240 - LOGLAM[l] for l in LAMS]
       for m in CONSISTENT + NONREASONING}
data["deviation"] = dev
norm, cmap = CenteredNorm(vcenter=0, halfrange=3.2), plt.get_cmap("RdBu_r")
fig = plt.figure(figsize=(3.4, 3.3))
gs = fig.add_gridspec(2, 2, height_ratios=[len(order_c), len(order_n)], width_ratios=[1, 0.05], hspace=0.07,
                      wspace=0.08)
for gi, rows in enumerate((order_c, order_n)):
    ax = fig.add_subplot(gs[gi, 0])
    mesh = ax.pcolormesh([dev[m] for m in rows], cmap=cmap, norm=norm, edgecolors="white", linewidth=1.0)
    for i, m in enumerate(rows):
        for j, v in enumerate(dev[m]):
            ax.text(j + 0.5, i + 0.5, "0" if abs(v) < 0.05 else f"{v:+.1f}".replace("-", "\u2212"), ha="center",
                    va="center", fontsize=6.5, color="white" if abs(v) > 1.7 else INK)
    ax.set_yticks([i + 0.5 for i in range(len(rows))], [m.replace(" [on]", ", reasoning on") for m in rows],
                  fontsize=7)
    ax.invert_yaxis()
    ax.tick_params(length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    if gi == 0:
        ax.xaxis.tick_top()
        ax.set_xticks([j + 0.5 for j in range(4)], LAMLAB)
        ax.set_xlabel("Evidence strength $\\lambda$", labelpad=5)
        ax.xaxis.set_label_position("top")
    else:
        ax.set_xticks([])
cax = fig.add_subplot(gs[:, 1])
cb = fig.colorbar(mesh, cax=cax, ticks=[-3, 0, 3])
cb.outline.set_visible(False)
cb.ax.tick_params(length=0, labelsize=7)
cb.ax.set_yticklabels(["−3", "0", "+3"])
cb.set_label("Movement minus Bayesian answer (nats)", fontsize=7.5)
save(fig, "p2_heatmap")

xs = [LOGLAM[l] for l in LAMS]
co_est = [mean_delta(CONSISTENT, l) for l in LAMS]
nr_est = [mean_delta(NONREASONING, l) for l in LAMS]
fu_est = [ratio_ci(per_scenario(rep_, lambda r, l=l: r["lam"] == l, lambda r: r["delta"])) for l in LAMS]
bare_est = ratio_ci(per_scenario(bare, lambda r: True, lambda r: r["delta"]))
data["movement"] = {"nonreasoning": nr_est, "consistent": co_est, "nonreasoning_no_disagreement": fu_est, "nonreasoning_bare_disagreement": bare_est}
fig, ax = plt.subplots(figsize=(W, H))
ax.plot([xs[0], xs[-1]], [xs[0], xs[-1]], color=INK, lw=0.7, ls=(0, (3, 2)), zorder=1)
point(ax, xs, co_est, GREY)
point(ax, xs, nr_est, ACCENT)
xf = [x + 0.12 for x in xs]
ax.plot(xf, [e[0] for e in fu_est], color=ACCENT, lw=1.0, ls=(0, (2, 1.5)), zorder=3)
ax.vlines(xf, [e[1] for e in fu_est], [e[2] for e in fu_est], color=ACCENT, lw=0.8, zorder=3)
ax.plot(xf, [e[0] for e in fu_est], "o", color="white", mec=ACCENT, ms=4.0, mew=1.0, zorder=4)
ax.vlines([xs[1] - 0.12], [bare_est[1]], [bare_est[2]], color=INK, lw=0.8, zorder=3)
ax.plot([xs[1] - 0.12], [bare_est[0]], marker="D", color=INK, ms=3.6, mec="white", mew=0.6, zorder=4, ls="none")
ax.set_xticks(xs, LAMLAB)
ax.set_yticks([-1, 0, 1, 2, 3])
ax.set_xlim(xs[0] - 0.3, xs[-1] + 0.3)
ax.set_ylim(-1.6, 3.1)
despine(ax, (xs[0], xs[-1]), (-1, 3))
ax.set_xlabel("Evidence strength $\\lambda$")
ax.set_ylabel("Movement away from first answer (nats)")
hs = [Line2D([], [], color=ACCENT, lw=1.3, marker="o", ms=4, mec="white", mew=0.6, label="Non-reasoning, with disagreement"),
      Line2D([], [], color=ACCENT, lw=1.0, ls=(0, (2, 1.5)), marker="o", ms=4, mfc="white", mec=ACCENT, mew=1.0,
             label="Non-reasoning, no disagreement"),
      Line2D([], [], color=GREY, lw=1.3, marker="o", ms=4, mec="white", mew=0.6, label="Comparison"),
      Line2D([], [], color=INK, lw=0, marker="D", ms=3.6, mec="white", mew=0.6, label="Disagreement, no test"),
      Line2D([], [], color=INK, lw=0.8, ls=(0, (3, 2)), label="Bayes")]
ax.legend(handles=hs, loc="lower left", bbox_to_anchor=(-0.02, 1.0), ncol=2, frameon=False, fontsize=6.5,
          handlelength=1.8, handletextpad=0.5, columnspacing=1.3, borderaxespad=0.3)
save(fig, "p3_movement")

RATS = ["none", "small", "moderate", "large"]
RCOL = ["#F2F2F2", "#BDBDBD", "#E2AEB3", ACCENT]
groups = [("Non-reasoning: test, disagreement", [r for r in main if r["model_name"] in NONREASONING]),
          ("Non-reasoning: same, rated first", fu),
          ("Non-reasoning: test, no disagreement", rep_),
          ("Non-reasoning: disagreement, no test", bare),
          ("Comparison: test, disagreement", [r for r in main if r["model_name"] in CONSISTENT])]
mix = []
for lab, rs in groups:
    s1 = [r for r in rs if r["lam"] == "1"]
    mix.append((lab, [sum(r["weight_rating"] == k for r in s1) / len(s1) for k in RATS]))
data["empty_objection_ratings"] = mix
fig, ax = plt.subplots(figsize=(W, 2.0))
for y, (lab, sh) in enumerate(mix):
    left = 0
    for k, (v, c) in enumerate(zip(sh, RCOL)):
        ax.barh(y, 100 * v, left=100 * left, height=0.6, color=c, edgecolor="white", lw=0.6)
        left += v
ax.set_yticks(range(len(mix)), [m[0] for m in mix], fontsize=7)
ax.tick_params(axis="y", length=0)
ax.invert_yaxis()
ax.spines["left"].set_visible(False)
ax.set_xlim(0, 100)
ax.set_xticks([0, 25, 50, 75, 100])
despine(ax, (0, 100))
ax.set_xlabel("Rating given to a message with no information (% of trials)")
ax.legend(handles=[Patch(facecolor=c, edgecolor="#999999", lw=0.4, label=k) for k, c in zip(RATS, RCOL)],
          loc="lower left", bbox_to_anchor=(-0.02, 1.0), ncol=4, frameon=False, fontsize=7, handlelength=1.2,
          handletextpad=0.4, columnspacing=1.1, borderaxespad=0.3)
save(fig, "p4_say_vs_do")

esc_models = ["DeepSeek V4 Pro"] + [m for m in FRONTIER if m != "Gemini 3.8 Flash"]
esc_est = {}
for m in esc_models:
    t1 = [r for r in main if r["model_name"] == m and r["lam"] == "1" and r["item_id"].endswith(":d0")]
    res = []
    for t in (1, 2, 3):
        rs = t1 if t == 1 else [r for r in esc if r["model_name"] == m and r["item_id"].endswith(f":t{t}")]
        res.append(ratio_ci(per_scenario(rs, lambda r: True, lambda r: r["delta"])))
    esc_est[m] = res
data["escalation"] = esc_est
fig, ax = plt.subplots(figsize=(W, H))
for m in esc_models[1:]:
    point(ax, [1, 2, 3], esc_est[m], GREY, lw=0.9, ms=3.2)
point(ax, [1, 2, 3], esc_est["DeepSeek V4 Pro"], ACCENT)
ax.set_xticks([1, 2, 3], ["1", "2", "3"])
ax.set_yticks([0, 1, 2])
ax.set_xlim(0.8, 3.2)
ax.set_ylim(-0.1, 2.1)
despine(ax, (1, 3), (0, 2))
ax.set_xlabel("Turn")
ax.set_ylabel("Movement toward user (nats)")
legend_top(ax, [(ACCENT, "-", "o", "DeepSeek V4 Pro, reasoning off"), (GREY, "-", "o", "Five frontier models")])
save(fig, "p5_escalation")

(OUT / "figure_data.json").write_text(json.dumps(data, indent=1, default=str) + "\n")
print("ok")
