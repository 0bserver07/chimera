"""A benchmark run that produced nothing must not report success.

`run_matrix` deliberately contains a per-cell exception as a `status="error"`
cell so one bad pair cannot abort the grid. That containment is correct — and
it had no run-level floor: `run_bench_matrix` ended in an unconditional
`return 0`, so an unresolvable model, absent credentials or a dead endpoint
produced `total=0/passed=0/status=error` for every cell, wrote a JSON file
shaped exactly like a scorecard, and exited 0. Any `&&` chain, CI step or
script reading the exit code saw a passing benchmark run.

That is the same failure class as every fabricated number this repo has had to
retract — a failure that renders as data — which is why it is now pinned.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from chimera.cli import bench_matrix as bm
from chimera.eval.matrix import MatrixCell, MatrixReport


def _cell(status: str, total: int, agent: str = "react", bench: str = "mbpp") -> MatrixCell:
    return MatrixCell(
        agent_id=agent, benchmark=bench, total=total,
        passed=total, pass_rate=1.0 if total else 0.0, cost_usd=0.0,
        tool_calls=0, wall_clock_sec=0.0, status=status,
        budget_honored=True, budget_note="boom" if status == "error" else "",
        category="unknown",
    )




def _args(tmp_path: Path, **over: Any) -> argparse.Namespace:
    base = dict(
        model="glm-5.1", agents="react", benchmarks="mbpp", limit=1,
        dataset=None, fmt="table", output=str(tmp_path / "out.json"),
        env_kind="local", sandbox_image=None, modal_gpu=None,
        modal_image=None, registry=None,
        max_tool_calls=None, max_llm_calls=None, max_wall_clock=None, max_cost=None,
        tasks_dir=None,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _run(monkeypatch, tmp_path, cells, **over):
    """Drive the exit path with a stubbed matrix run.

    ``run_bench_matrix`` imports its collaborators lazily *inside* the
    function, so they must be patched on their source modules rather than on
    this one.
    """
    import chimera.cli.main as cli_main
    import chimera.eval.matrix as mat
    import chimera.eval.runners.registry as reg
    import chimera.providers.factory as fac

    report = MatrixReport(cells=cells, model="glm-5.1")
    monkeypatch.setattr(mat, "run_matrix", lambda *a, **k: report)
    monkeypatch.setattr(fac, "create_provider", lambda **k: object())
    monkeypatch.setattr(cli_main, "_load_benchmark", lambda *a, **k: object())
    monkeypatch.setattr(reg, "resolve", lambda *a, **k: object())
    monkeypatch.setattr(reg, "load_registry", lambda *a, **k: {"react": object()})
    return bm.run_bench_matrix(_args(tmp_path, **over))


class TestExitCodeFloor:
    def test_all_cells_errored_exits_nonzero(self, monkeypatch, tmp_path, capsys) -> None:
        rc = _run(monkeypatch, tmp_path, [_cell("error", 0)])
        err = capsys.readouterr().err
        assert rc == bm._EXIT_ALL_CELLS_FAILED, "a run that graded nothing exited 0"
        assert rc != 0
        assert "FAILED: 0 of 1 cell(s) graded" in err

    def test_all_errored_warns_the_report_is_not_a_scorecard(
        self, monkeypatch, tmp_path, capsys
    ) -> None:
        out = tmp_path / "out.json"
        _run(monkeypatch, tmp_path, [_cell("error", 0)], output=str(out))
        err = capsys.readouterr().err
        assert "NO graded cell" in err
        assert "do not promote it to data/" in err
        # The file is still written — a failure record is evidence.
        assert json.loads(out.read_text())["cells"]

    def test_partial_failure_is_distinguishable_from_both(
        self, monkeypatch, tmp_path, capsys
    ) -> None:
        rc = _run(
            monkeypatch, tmp_path,
            [_cell("error", 0, bench="mbpp"), _cell("completed", 5, bench="human-eval")],
        )
        err = capsys.readouterr().err
        assert rc == bm._EXIT_SOME_CELLS_FAILED
        assert rc not in (0, bm._EXIT_ALL_CELLS_FAILED)
        assert "PARTIAL: 1 of 2" in err
        assert "never\nas zero" in err or "never as zero" in err.replace("\n", " ")

    def test_clean_run_still_exits_zero(self, monkeypatch, tmp_path) -> None:
        assert _run(monkeypatch, tmp_path, [_cell("completed", 5)]) == 0

    def test_error_detail_is_surfaced_not_swallowed(
        self, monkeypatch, tmp_path, capsys
    ) -> None:
        # The reason a cell died is the only actionable thing about it.
        _run(monkeypatch, tmp_path, [_cell("error", 0)])
        assert "error: react x mbpp: boom" in capsys.readouterr().err

    def test_exit_codes_do_not_collide_with_existing_returns(self) -> None:
        # The module already uses 0/1/2 for usage and credential gates.
        assert bm._EXIT_ALL_CELLS_FAILED not in (0, 1, 2)
        assert bm._EXIT_SOME_CELLS_FAILED not in (0, 1, 2)
        assert bm._EXIT_ALL_CELLS_FAILED != bm._EXIT_SOME_CELLS_FAILED

    def test_an_errored_cell_with_a_nonzero_total_is_not_graded(
        self, monkeypatch, tmp_path, capsys
    ) -> None:
        """The exact shape a dead endpoint produces.

        A benchmark loads its tasks, THEN the provider call fails, so the cell
        is ``status="error", total=2, passed=0``. An earlier version of this
        gate tested only ``total > 0`` and therefore called a completely dead
        run PARTIAL instead of FAILED — reporting a 0-of-2 as a measured
        result. ``total`` is evidence that tasks were loaded, never that any
        were graded.
        """
        rc = _run(monkeypatch, tmp_path, [_cell("error", 2)])
        assert rc == bm._EXIT_ALL_CELLS_FAILED
        assert "FAILED: 0 of 1" in capsys.readouterr().err
