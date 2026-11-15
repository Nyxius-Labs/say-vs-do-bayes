import json
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_P0 = re.compile(r"is (\d+(?:\.\d+)?)%\.")
_T2 = re.compile(r"detects (\d+)% of .*? correctly clears (\d+)%", re.S)


def bayes_p1(messages) -> float:
    p0 = float(_P0.search(messages[1]["content"]).group(1)) / 100
    s2, c2 = (int(x) for x in _T2.search(messages[2]["content"]).groups())
    lam = c2 / (100 - s2)
    o = p0 / (1 - p0) / lam
    return o / (1 + o)


def reply(prob, rating="moderate"):
    return json.dumps({"weight_rating": rating, "probability": int(round(prob))})


@pytest.fixture
def tmp_root(tmp_path):
    for d in ("configs", "src", "tests"):
        shutil.copytree(ROOT / d, tmp_path / d, ignore=shutil.ignore_patterns(
            "__pycache__", "models_resolved.json", "frozen_competition.json"))
    (tmp_path / "logs").mkdir()
    (tmp_path / "reports").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _copy_decisions(request):
    if "tmp_root" in request.fixturenames:
        root = request.getfixturevalue("tmp_root")
        shutil.copy(ROOT / "DECISIONS.md", root / "DECISIONS.md")
