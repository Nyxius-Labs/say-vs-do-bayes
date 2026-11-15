from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class HashMismatch(RuntimeError):
    pass


def _components(root: Path) -> dict[str, list[str]]:
    models = "configs/models_resolved.json" if (root / "configs/models_resolved.json").exists() else "configs/models.json"
    return {
        "prompt_templates": ["configs/prompt_templates.json", "src/dc/prompts.py"],
        "thresholds_config": ["configs/study.json"],
        "scoring_code": ["src/dc/scoring.py"],
        "model_ids": [models],
        "reasoning_on_order": ["configs/reasoning_on_order.json"],
        "stopping_rule": ["configs/stopping_rule.json"],
        "frozen_competition_config": ["configs/frozen_competition.json"],
        "decisions": ["DECISIONS.md"],
        "gate_config": ["configs/gate.json"] + (["configs/d11.json"] if (root / "configs/d11.json").exists() else []),
    }


def sha256_files(root: Path, rels: list[str]) -> str:
    h = hashlib.sha256()
    for rel in rels:
        h.update(rel.encode() + b"\0")
        h.update((root / rel).read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def compute(root: Path = ROOT) -> dict:
    comps = _components(root)
    return {name: {"sha256": sha256_files(root, rels), "files": rels} for name, rels in comps.items()
            if all((root / r).exists() for r in rels)}


def freeze(root: Path = ROOT, extra: dict | None = None) -> dict:
    out = {"frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "algorithm": "sha256",
           "hashes": compute(root)}
    if extra:
        out.update(extra)
    (root / "hashes.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


def verify(root: Path = ROOT) -> list[str]:
    path = root / "hashes.json"
    if not path.exists():
        return ["hashes.json missing"]
    frozen = json.loads(path.read_text())["hashes"]
    now = compute(root)
    bad = [f"{k} missing" for k, rels in _components(root).items() if not all((root / r).exists() for r in rels)]
    bad += [k for k in now if k not in frozen or frozen[k]["sha256"] != now[k]["sha256"]]
    bad += [k for k in frozen if k not in now]
    return bad


def require_registered(root: Path = ROOT) -> None:
    bad = verify(root)
    if bad:
        raise HashMismatch("registered mode refused: hash mismatch in " + ", ".join(bad))
