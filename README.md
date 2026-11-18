# Direction Without Magnitude

Code and data for the paper in `paper/main.pdf` (Apart Research AI x Epistemics Sprint, November 2026).

[Apart Research Blog Post](https://apartresearch.com/sprints/projects/direction-without-magnitude-language-models-that-update-by-the-same-amount-whatever-the-evidence-is-worth-8wkctdedmh)

The evaluation code and experimental design are preexisting; the model runs, analysis and paper were done during the sprint.

```bash
uv venv .venv && uv pip install --python .venv/bin/python httpx matplotlib pytest
.venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python analysis/intervals.py
PYTHONPATH=src .venv/bin/python analysis/figures.py
```

MIT License.
