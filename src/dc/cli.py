from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from . import hashing
from .costs import CostCapExceeded
from .scenarios import ROOT

EXIT_HASH_MISMATCH = 2
EXIT_COST_CAP = 3


def _templates_sha(root):
    return hashlib.sha256((root / "configs/prompt_templates.json").read_bytes()).hexdigest()


def run_mock_tests(root) -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", str(root / "tests")], cwd=root,
                       capture_output=True, text=True)
    (root / "logs/prelim").mkdir(parents=True, exist_ok=True)
    (root / "logs/prelim/pytest_output.txt").write_text(r.stdout[-5000:] + r.stderr[-2000:])
    return r.returncode == 0


async def prelim(root: Path, skip_tests=False, providers=None) -> int:
    from . import prelim as P
    ctx = P.Ctx(root, providers=providers)
    notes = []
    code = 0
    gate = None
    mock_ok = True if skip_tests else run_mock_tests(root)
    try:
        if not ctx.resolved_path().exists():
            await P.resolve(ctx)
        notes.append(f"Reseller catalog: {ctx.resolved().get('reseller_status')}.")
        if ctx.provider("reseller") is None:
            notes.append("No CheaperInference key is set (CHEAPER_API_KEY or CHEAPINFERENCE_API_KEY, environment or "
                         ".env), so every reseller model is SKIPPED and no live calls were made.")
        notes.append("Costs are the per-call billing.billed_cost_usd returned by CheaperInference; requests carry "
                     "x-ci-techniques: off, x-ci-model-lock: true and x-ci-prompt-cache: off for a clean benchmark.")
        await P.part_a(ctx)
        P.apply_decisions(ctx)
        notes += await P.resmoke(ctx)
        st = P.gate_state(ctx)
        attempt = ctx.gate_cfg["current_attempt"]
        if attempt <= ctx.gate_cfg["max_gate_attempts"] and ctx.provider("reseller") is not None:
            if not any(h["attempt"] == attempt for h in st["attempts"]):
                prev = st["attempts"][-1] if st["attempts"] else {}
                st["attempts"].append({"attempt": attempt, "templates_sha": _templates_sha(root),
                                       "templates_version": ctx.templates.get("version"),
                                       "revision": prev.get("pending_revision"),
                                       "n_practice": P.attempt_cfg(ctx, attempt)["n_practice"]})
            await P.part_b(ctx, attempt)
            gate = P.evaluate_gate(ctx, attempt)
            next(h for h in st["attempts"] if h["attempt"] == attempt)["result"] = "PASS" if gate["pass"] else "FAIL"
            P.gate_attempts_path(ctx).write_text(json.dumps(st, indent=2) + "\n")
    except CostCapExceeded as e:
        notes.append(f"ABORTED at cost cap: {e}")
        code = EXIT_COST_CAP
    finally:
        await ctx.close()
    if gate is None and P.gate_state(ctx)["attempts"]:
        gate = P.evaluate_gate(ctx, ctx.gate_cfg["current_attempt"])
    smoke = P.smoke_summary(ctx)
    if P.gate_state(ctx)["attempts"]:
        P.write_gate_history(ctx)
    if not any(s.get("n") for s in smoke.values()):
        (root / "prelim_report.md").write_text("# Preliminary run report\n\nNo live calls were made: no reseller key. "
                                               "G1: FAIL. G2: FAIL. G3: FAIL. G4: FAIL. Part C: NOT RUN: no direct "
                                               "provider access.\n")
        print("no live calls; minimal report written")
        return code
    proj = P.project_d5(ctx, smoke, gate)
    frozen = P.write_frozen_config(ctx, proj, gate)
    P.write_decisions_frozen(ctx, frozen)
    hashes = hashing.freeze(root, {"gate_passed": bool(gate and gate["pass"]),
                                   "status": "frozen at the end of the preliminary run"})
    P.write_report(ctx, smoke, gate, P.gate_state(ctx)["attempts"], proj, frozen, mock_ok, hashes, notes)
    print(f"prelim_report.md written; gate {'PASS' if gate and gate['pass'] else 'FAIL'}; "
          f"spent ${ctx.tracker.spent:.4f} of ${ctx.tracker.cap:.2f}; draws {frozen['draws_main']}; "
          f"reasoning-on rows {[r['name'] for r in frozen['reasoning_on_rows']]}")
    return code


def finalize(root: Path, mock_ok: bool | None = None) -> int:
    from . import prelim as P
    ctx = P.Ctx(root)
    mock_ok = run_mock_tests(root) if mock_ok is None else mock_ok
    rec = P.write_reconciliation(ctx)
    st = P.gate_state(ctx)
    attempt = ctx.gate_cfg["current_attempt"]
    gate = P.evaluate_gate(ctx, attempt) if any(h["attempt"] == attempt for h in st["attempts"]) else None
    smoke = P.smoke_summary(ctx)
    P.write_gate_history(ctx)
    notes = [f"Reseller catalog: {ctx.resolved().get('reseller_status')}.",
             "Costs are the per-call billing.billed_cost_usd returned by CheaperInference; requests carry "
             "x-ci-techniques: off, x-ci-model-lock: true and x-ci-prompt-cache: off. About a quarter of prompt "
             "tokens were still served from providers' implicit prompt caches, which the reseller documents as "
             "impossible to disable; this lowers cost and does not change outputs.",
             f"Preliminary cap: ${ctx.cap:.2f} of account-adjusted spend (D6; the original D3 cap was $5)."]
    for h in st["attempts"]:
        notes.append(f"Gate attempt {h['attempt']}: {h.get('result', 'not evaluated')} "
                     f"({P.attempt_cfg(ctx, h['attempt'])['decision']}; {h['n_practice']} practice prompts per model).")
    if gate and not gate["pass"]:
        notes.append("The current gate attempt failed: see reports/gate_history.md and reports/gate_blocked.md. "
                     "The competition run must not start: `run --registered` refuses while gate_passed is false.")
    proj = P.project_d5(ctx, smoke, gate)
    frozen = P.write_frozen_config(ctx, proj, gate)
    P.write_decisions_frozen(ctx, frozen)
    hashes = hashing.freeze(root, {"gate_passed": bool(gate and gate["pass"]),
                                   "status": "frozen at the end of the preliminary run"})
    P.write_report(ctx, smoke, gate, st["attempts"], proj, frozen, mock_ok, hashes, notes)
    print(f"finalized: gate {'PASS' if gate and gate['pass'] else 'FAIL'}; draws {frozen['draws_main']}; "
          f"models {len(frozen['models'])}; reasoning-on {[r['name'] for r in frozen['reasoning_on_rows']]}; "
          f"cumulative ${frozen['projected_cumulative_usd']:.2f}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dc")
    ap.add_argument("--root", type=Path, default=ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prelim")
    p.add_argument("--skip-tests", action="store_true")
    sub.add_parser("resolve")
    sub.add_parser("finalize", help="recompute gate, projection, freeze and report from logs; no inference calls")
    sub.add_parser("freeze")
    sub.add_parser("verify")
    r = sub.add_parser("run")
    r.add_argument("--registered", action="store_true", required=True)
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("resolve-d11")
    sub.add_parser("freeze-d11")
    pd = sub.add_parser("prelim-d11")
    pd.add_argument("--no-freeze", action="store_true")
    sub.add_parser("pin-hosts")
    sub.add_parser("freeze-d12")
    gd = sub.add_parser("gate-d12")
    gd.add_argument("--no-freeze", action="store_true")
    sub.add_parser("freeze-d13", help="D13 gate-override record, run order and D14 max_tokens; no inference calls")
    sub.add_parser("freeze-v3", help="D15 design v3 into the frozen configuration and hashes.json; no inference calls")
    sp = sub.add_parser("smoke-pinned", help="pre-launch smoke: practice prompts on pinned hosts, own $3 cap")
    sp.add_argument("--section", default="pinned_smoke", help="d11.json section: pinned_smoke (D13) or pinned_smoke_d16")
    lr = sub.add_parser("log-revision")
    lr.add_argument("description")
    a = ap.parse_args(argv)
    root = a.root.resolve()

    if a.cmd == "prelim":
        return asyncio.run(prelim(root, a.skip_tests))
    if a.cmd == "finalize":
        return finalize(root)
    if a.cmd == "resolve":
        from . import prelim as P
        ctx = P.Ctx(root)
        out = asyncio.run(P.resolve(ctx))
        print(json.dumps({m["name"]: m["reseller_id"] for m in out["models"]}, indent=1))
        return 0
    if a.cmd == "freeze":
        hashing.freeze(root)
        return 0
    if a.cmd == "resolve-d11":
        from . import prelim_d11 as D
        ctx = D.Ctx11(root, account_fn=None, providers=None)
        try:
            out = asyncio.run(D.resolve(ctx))
        except RuntimeError as e:
            print(f"STOP: {e}", file=sys.stderr)
            return 1
        print(json.dumps({m["name"]: [m["route"], m["model_id"], m["price_in"], m["price_out"]] for m in out["models"]},
                         indent=1))
        return 0
    if a.cmd == "freeze-d11":
        from . import prelim_d11 as D
        f = D.freeze_pending(root)
        print(f"pending D11 freeze written: {len(f['models'])} models; hashes.json updated")
        return 0
    if a.cmd == "pin-hosts":
        from . import prelim_d11 as D
        ctx = D.Ctx11(root, providers={}, account_fn=None, spend_file="reports/openrouter_spend_pinning.json")
        try:
            chosen = asyncio.run(D.pin_hosts(ctx))
        except RuntimeError as e:
            print(f"STOP: {e}", file=sys.stderr)
            return 1
        print(json.dumps(chosen, indent=1))
        return 0
    if a.cmd == "freeze-d12":
        from . import prelim_d11 as D
        f = D.freeze_pending_gate(root)
        print(f"D12 freeze written (status {f['status']}); hashes.json updated")
        return 0
    if a.cmd == "freeze-d13":
        from . import prelim_d11 as D
        f = D.freeze_d13(root)
        print(f"D13/D14 freeze written ({f['decision']}); hashes.json updated")
        return 0
    if a.cmd == "freeze-v3":
        from . import prelim_d11 as D
        f = D.freeze_v3(root)
        print(f"design v3 frozen: projected ${f['projected_competition_usd']:.2f} "
              f"({f['projected_by_route_usd']}); stop ${f['stop_usd']:.0f}; hashes.json updated")
        return 0
    if a.cmd == "smoke-pinned":
        from . import prelim_d11 as D
        return asyncio.run(D.run_pinned_smoke(root, section=a.section))
    if a.cmd == "gate-d12":
        from . import prelim_d11 as D
        return asyncio.run(D.run_gate_attempt(root, freeze=not a.no_freeze))
    if a.cmd == "prelim-d11":
        from . import prelim_d11 as D
        return asyncio.run(D.run(root, freeze=not a.no_freeze))
    if a.cmd in ("verify", "run"):
        bad = hashing.verify(root)
        if bad:
            print("REFUSED: registered hash mismatch: " + ", ".join(bad), file=sys.stderr)
            return EXIT_HASH_MISMATCH
        print("hashes verified")
        if a.cmd == "run":
            from . import competition
            plan = competition.plan(root)
            print(json.dumps(competition.describe(plan), indent=1))
            if not a.dry_run:
                try:
                    return asyncio.run(competition.run(root, plan))
                except CostCapExceeded as e:
                    print(f"STOPPED at spend limit: {e}", file=sys.stderr)
                    return EXIT_COST_CAP
        return 0
    if a.cmd == "log-revision":
        path = root / "logs/prelim/gate_attempts.json"
        st = json.loads(path.read_text()) if path.exists() else {"attempts": []}
        if st["attempts"]:
            st["attempts"][-1]["pending_revision"] = a.description
        with open(root / "logs/prelim/revisions.jsonl", "a") as f:
            f.write(json.dumps({"description": a.description, "templates_sha_before":
                                (st["attempts"][-1]["templates_sha"] if st["attempts"] else None)}) + "\n")
        path.write_text(json.dumps(st, indent=2) + "\n")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
