"""A toolkit ``result.json`` is a bench receipt the observatory already reads.

The spec asks that a curated result be *copyable* into ``data/``. That claim is
only worth anything if the copy actually loads, so this file tests it against
the real reader — ``scripts/render_observatory.py``, loaded by path the way
``tests/scripts/test_render_observatory.py`` loads it — rather than against a
restatement of its rules.

Copying stays a deliberate human act. Nothing in the toolkit writes to
``data/``; these tests write to ``tmp_path`` and hand the file to the loader.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from chimera.experiments import start

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "render_observatory.py"
_spec = importlib.util.spec_from_file_location("render_observatory", _SCRIPT)
assert _spec is not None and _spec.loader is not None
obs = importlib.util.module_from_spec(_spec)
sys.modules["render_observatory"] = obs  # dataclasses resolve via sys.modules
_spec.loader.exec_module(obs)


def test_a_finished_runs_receipt_loads_as_observatory_cells(tmp_path: Path) -> None:
    """Copy ``result.json`` into a data dir and load it — no reshaping."""
    run = start("copyable", config={"model": "glm-5.2"})
    run.finish(
        {
            "cells": [
                {"agent_id": "coding-agent", "benchmark": "mbpp", "passed": 9, "total": 10, "cost_usd": 0.5},
                {"agent_id": "coding-agent", "benchmark": "humaneval", "passed": 5, "total": 5, "cost_usd": 0.25},
            ]
        }
    )

    curated = tmp_path / "data" / "example-copyable-results.json"
    curated.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(run.result_path, curated)

    _role, cells, doc = obs._load_file(curated)
    assert doc["model"] == "glm-5.2"
    assert [(c.agent, c.bench_raw, c.passed, c.total, c.pass_rate) for c in cells] == [
        ("coding-agent", "mbpp", 9, 10, 0.9),
        ("coding-agent", "humaneval", 5, 5, 1.0),
    ]
    assert [c.cost_usd for c in cells] == [0.5, 0.25]


def test_load_inputs_picks_up_a_copied_receipt(tmp_path: Path) -> None:
    """The generator's own directory scan finds it, not just the file loader.

    Promotion is a copy *and a rename*: the observatory scans a fixed pattern
    list (``DEFAULT_PATTERNS``), so a receipt filed under a name it does not
    glob is simply not read. Filed as an ``modal-grid-observatory*`` receipt,
    the toolkit's output lands in the depth matrix untouched.
    """
    run = start("scanned")
    run.finish({"agent_id": "coding-agent", "benchmark": "mbpp", "passed": 7, "total": 10})
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy2(run.result_path, data_dir / "modal-grid-observatory-scanned.json")

    inputs = obs.load_inputs(data_dir)
    assert [c.bench_raw for c in inputs.depth] == ["mbpp"]
    assert inputs.depth[0].passed == 7


@pytest.mark.parametrize(
    "summary",
    [
        {"passed": 11, "total": 10},
        {"passed": 1, "total": 10, "pass_rate": 0.9},
        {"passed": 1, "total": 10, "status_counts": {"completed": 3}},
        {"passed": 3, "total": 10, "status": "error"},
    ],
)
def test_finish_rejects_exactly_what_the_observatory_rejects(summary: dict) -> None:
    """The two gates agree, so a receipt cannot pass one and fail the other.

    Each summary below is refused by :meth:`Run.finish`; the same cell shape is
    refused by ``_validate_cell``. Enforcing at write time means the person who
    ran the experiment learns about it, instead of a release-time renderer
    failing on a number nobody can re-derive.
    """
    run = start("agreeing")
    with pytest.raises(ValueError):
        run.finish(dict(summary))

    cell = {"agent_id": "a", "benchmark": "b", "cost_usd": 0.0, **summary}
    cell.setdefault("status", "completed")
    total = int(cell["total"])
    cell.setdefault("pass_rate", (int(cell["passed"]) / total) if total else 0.0)
    with pytest.raises(obs.IntegrityError):
        obs._validate_cell(cell, "hand-written.json")


def test_a_uniform_zero_is_written_but_flagged_by_the_renderer(tmp_path: Path) -> None:
    """The toolkit records the measurement; the publish gate decides.

    A clean-status 0/n is the harness-gap signature. Refusing to *record* it
    would be wrong — the run really did produce it, and the ledger is the
    evidence you diagnose from. Refusing to *publish* it is the renderer's job,
    and it still does.
    """
    run = start("uniform-zero")
    run.finish({"agent_id": "a", "benchmark": "b", "passed": 0, "total": 50})
    assert json.loads(run.result_path.read_text(encoding="utf-8"))["cells"][0]["passed"] == 0

    curated = tmp_path / "uniform-zero-results.json"
    shutil.copy2(run.result_path, curated)
    with pytest.raises(obs.IntegrityError, match="harness-gap signature"):
        obs._load_file(curated)
