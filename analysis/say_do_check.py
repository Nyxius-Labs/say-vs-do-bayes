import argparse
import json
import math
from collections import defaultdict


def logit(p):
    return math.log(p / (1 - p))


def movement(p0, pct):
    p1 = min(max(pct, 1), 99) / 100
    return logit(p0) - logit(p1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--threshold", type=float, default=0.5)
    a = ap.parse_args()
    rated, flagged = defaultdict(int), defaultdict(int)
    for line in open(a.path):
        r = json.loads(line)
        if r.get("weight_rating") != "none" or r.get("probability") is None:
            continue
        m = r.get("model_name", "model") + (" [on]" if str(r.get("config", "")).startswith("on") else "")
        rated[m] += 1
        flagged[m] += movement(r["p0"], r["probability"]) > a.threshold
    print(f"{'model':32s} {'rated none':>10s} {'moved anyway':>12s} {'share':>6s}")
    for m in sorted(rated, key=lambda k: -flagged[k] / rated[k]):
        print(f"{m:32s} {rated[m]:10d} {flagged[m]:12d} {flagged[m] / rated[m]:6.1%}")


if __name__ == "__main__":
    main()
