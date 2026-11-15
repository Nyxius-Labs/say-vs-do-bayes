import argparse
import asyncio
import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dc import prelim as P
from dc.competition import plan
from dc.costs import CostTracker
from dc.prompts import build_messages
from dc.providers import MockProvider
from dc.runner import Runner, read_jsonl

MODELS = ["GPT-6 Luna", "Kimi K3.1 Pro", "DeepSeek V4 Pro", "Claude Haiku 5.5", "GPT-4o", "Qwen3.8 27B",
          "Llama 3.1 8B"]
RATING_FIRST_TEST_ONLY = (
    'Reply with only a JSON object of the form {{"weight_rating": "none|small|moderate|large", '
    '"probability": <integer 0-100>}}. First, "weight_rating": how much weight the result of {second_test} '
    'deserves in revising your estimate. Second, "probability": your updated probability, as an integer percent, '
    'that {hypothesis}.')
LOG = ROOT / "logs/followup_rating_first/main.jsonl"


def build(mock: bool):
    providers = None
    if mock:
        m = MockProvider(lambda msgs: '{"weight_rating": "none", "probability": 50}', billed_cost_usd=0.0)
        providers = {"openrouter": m, "anthropic": m}
    p = plan(ROOT, providers=providers)
    ctx = p["ctx"]
    templates = copy.deepcopy(ctx.templates)
    templates["format"]["rating_first"] = RATING_FIRST_TEST_ONLY
    items = [replace(it, rating_first=True) for it in p["main"]]
    jobs = []
    for name in MODELS:
        e = P._entry(ctx, name)
        base = P.off_job(ctx, items[0], e).config
        jobs += [P.off_job(ctx, it, e, config=f"{base}+rating-first-test-only") for it in items]
    return ctx, templates, items, jobs


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--cap", type=float, default=6.0)
    a = ap.parse_args()
    ctx, templates, items, jobs = build(a.mock)
    sc = ctx.by_id[items[0].scenario_id]
    print(build_messages(items[0], sc, templates)[-1]["content"].split("\n\n")[-1])
    print(f"{len(jobs)} calls across {len(MODELS)} models")
    log = LOG.with_name("mock.jsonl") if a.mock else LOG
    if a.mock and log.exists():
        log.unlink()
    runner = Runner(log, CostTracker(a.cap), ctx.by_id, templates, default_concurrency=P.RESELLER_CONCURRENCY)
    try:
        await runner.run(jobs)
    finally:
        await ctx.close()
    recs = read_jsonl(log)
    spent = sum(r.get("cost_usd") or 0 for r in recs)
    ok = sum(r["status"] == "ok" for r in recs)
    print(json.dumps({"logged": len(recs), "parsed": ok, "spent_usd": round(spent, 4)}))


if __name__ == "__main__":
    asyncio.run(main())
