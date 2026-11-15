import json
import subprocess
import sys

from dc import hashing

FIELDS = {"prompt_templates", "thresholds_config", "scoring_code", "model_ids", "reasoning_on_order", "stopping_rule"}


def _frozen(root):
    (root / "configs/frozen_competition.json").write_text('{"draws_main": 2}\n')


def _cli(root, *args):
    return subprocess.run([sys.executable, "-m", "dc.cli", "--root", str(root), *args],
                          cwd=root, capture_output=True, text=True, env={"PYTHONPATH": str(root / "src"), "PATH": ""})


def test_freeze_has_spec_fields_and_decisions(tmp_root):
    _frozen(tmp_root)
    out = hashing.freeze(tmp_root)
    assert set(out["hashes"]) == FIELDS | {"decisions", "frozen_competition_config", "gate_config"}
    assert all(len(v["sha256"]) == 64 for v in out["hashes"].values())
    assert hashing.verify(tmp_root) == []


def test_registered_mode_refuses_when_template_changes(tmp_root):
    _frozen(tmp_root)
    hashing.freeze(tmp_root)
    ok = _cli(tmp_root, "verify")
    assert ok.returncode == 0, ok.stderr
    p = tmp_root / "configs/prompt_templates.json"
    t = json.loads(p.read_text())
    t["tone"]["insistent"] += " Seriously."
    p.write_text(json.dumps(t, indent=2))
    bad = _cli(tmp_root, "run", "--registered", "--dry-run")
    assert bad.returncode == 2
    assert "prompt_templates" in bad.stderr
    assert _cli(tmp_root, "verify").returncode == 2


def test_each_component_is_guarded(tmp_root):
    _frozen(tmp_root)
    for rel, field in [("configs/study.json", "thresholds_config"), ("src/dc/scoring.py", "scoring_code"),
                       ("configs/reasoning_on_order.json", "reasoning_on_order"),
                       ("configs/stopping_rule.json", "stopping_rule"),
                       (hashing._components(tmp_root)["model_ids"][0], "model_ids"),
                       ("configs/frozen_competition.json", "frozen_competition_config"), ("DECISIONS.md", "decisions"),
                       ("configs/gate.json", "gate_config")]:
        hashing.freeze(tmp_root)
        f = tmp_root / rel
        orig = f.read_bytes()
        f.write_bytes(orig + b"\n")
        assert hashing.verify(tmp_root) == [field]
        f.write_bytes(orig)


def test_missing_frozen_config_refuses(tmp_root):
    hashing.freeze(tmp_root)
    assert hashing.verify(tmp_root) == ["frozen_competition_config missing"]
    assert _cli(tmp_root, "run", "--registered", "--dry-run").returncode == 2


def test_missing_hashes_file_refuses(tmp_root):
    assert hashing.verify(tmp_root) == ["hashes.json missing"]
    assert _cli(tmp_root, "run", "--registered", "--dry-run").returncode == 2
