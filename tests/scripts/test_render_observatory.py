"""Unit tests for scripts/render_observatory.py — the observatory generator.

The generator is stdlib-only and lives outside the package, so it is loaded
here by file path. Tests drive it over small fixture receipts in tmp_path:
rendering + labeling (EXACT / ~EXACT / lower bound), the integrity aborts
(an error-status cell claiming passes must fail the build), ``--check``
staleness, provenance footnotes, and byte-for-byte determinism.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "render_observatory.py"
_spec = importlib.util.spec_from_file_location("render_observatory", _SCRIPT)
assert _spec is not None and _spec.loader is not None
obs = importlib.util.module_from_spec(_spec)
sys.modules["render_observatory"] = obs  # dataclasses resolve via sys.modules
_spec.loader.exec_module(obs)


def _cell(
    agent: str = "coding-agent",
    bench: str = "mbpp-sanitized",
    total: int = 10,
    passed: int = 9,
    status: str = "completed",
    counts: dict[str, int] | None = None,
    cost: float = 0.5,
    rate: float | None = None,
) -> dict:
    """Build one receipt cell in the modal-grid JSON shape."""
    return {
        "agent_id": agent,
        "benchmark": bench,
        "total": total,
        "passed": passed,
        "pass_rate": (passed / total if total else 0.0) if rate is None else rate,
        "cost_usd": cost,
        "tool_calls": 1,
        "wall_clock_sec": 1.0,
        "status": status,
        "budget_honored": True,
        "budget_note": "",
        "category": "unknown",
        **({"status_counts": counts} if counts is not None else {}),
    }


def _write(data_dir: Path, name: str, cells: list[dict], **top: object) -> Path:
    """Write a receipt file named so the default glob patterns discover it."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / name
    path.write_text(json.dumps({"cells": cells, **top}), encoding="utf-8")
    return path


def _seed(data_dir: Path) -> None:
    """Minimal realistic input set: one clean flagship receipt + breadth grid."""
    _write(
        data_dir,
        "modal-grid-fullscore2-20990101-000000.json",
        [_cell(total=427, passed=423, counts={"completed": 427})],
        run_id="fullscore2",
    )
    _write(
        data_dir,
        "matrix-full-glm52.json",
        [
            _cell(agent="react", bench="human-eval", total=1, passed=1, cost=0.01),
            _cell(agent="react", bench="mbpp-sanitized", total=1, passed=1, cost=0.01),
            _cell(agent="lint-loop", bench="human-eval", total=1, passed=0, cost=0.01),
            _cell(agent="lint-loop", bench="mbpp-sanitized", total=1, passed=0, cost=0.01),
        ],
        model="glm-5.2[1m]",
    )


def _render(data_dir: Path) -> str:
    return obs.render(obs.load_inputs(data_dir))


# ---------------------------------------------------------------- labeling


def test_exact_label_for_clean_status_counts(tmp_path: Path) -> None:
    _seed(tmp_path)
    page = _render(tmp_path)
    assert "EXACT — 0 errors (`{completed: 427}`)" in page
    assert "**99.1%** (423/427)" in page


def test_partial_error_renders_lower_bound(tmp_path: Path) -> None:
    # Uses a NON-retracted benchmark: the lower-bound rendering is still the
    # right treatment for a partial run whose unmeasured tasks could have
    # passed. (livecodebench used to be this fixture; it is now retracted, and
    # a retraction outranks a lower bound — see the tests below.)
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-fullscore1-20990101-000000.json",
        [_cell(bench="math500", total=175, passed=33, status="partial_error")],
        run_id="fullscore1",
    )
    page = _render(tmp_path)
    assert "≥ 18.9% (33/175)" in page
    assert "lower bound — status `partial_error`" in page


def test_retracted_benchmark_never_renders_a_score(tmp_path: Path) -> None:
    # The withdrawal of livecodebench's ≥18.9%: a lower bound is still a claim
    # ("at least this good"), and it is only honest when the unmeasured
    # remainder COULD have passed. 36% of this denominator cannot pass under any
    # answer, so the floor is fiction with an inequality in front of it.
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-fullscore1-20990101-000000.json",
        [_cell(bench="livecodebench-codegeneration", total=175, passed=33, status="partial_error")],
        run_id="fullscore1",
    )
    page = _render(tmp_path)
    assert "RETRACTED" in page
    assert "≥ 18.9% (33/175)" not in page
    assert "**18.9%**" not in page
    # The reason travels with the retraction — a reader never has to go dig.
    assert "36% of the denominator cannot pass" in page
    assert "bench-diagnosis-darklight1.md" in page


def test_retracted_benchmark_is_not_offered_for_reproduction(tmp_path: Path) -> None:
    # Don't invite the reader to re-run and re-quote a number we just withdrew.
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-fullscore1-20990101-000000.json",
        [_cell(bench="livecodebench-codegeneration", total=175, passed=33, status="partial_error")],
        run_id="fullscore1",
    )
    page = _render(tmp_path)
    repro = [ln for ln in page.splitlines() if "--benchmarks" in ln or "--benches" in ln]
    assert repro, "reproduce block missing — the assertion would be vacuous"
    assert all("livecodebench" not in ln for ln in repro)


def test_retraction_registry_reasons_are_substantive() -> None:
    # A retraction with a thin reason is how a retraction quietly gets reverted.
    assert obs.RETRACTED, "registry emptied — was a fix verified?"
    for bench, reason in obs.RETRACTED.items():
        assert len(reason) > 200, f"{bench}: reason too thin to act on"
        assert "docs/notes/" in reason, f"{bench}: reason cites no diagnosis"


def test_near_exact_budget_split_labeled(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-fullscore3-20990101-000000.json",
        [
            _cell(
                bench="math500",
                total=500,
                passed=388,
                status="budget_exhausted",
                counts={"completed": 496, "budget_exhausted": 4},
            )
        ],
        run_id="fullscore3",
    )
    page = _render(tmp_path)
    assert "~EXACT — `{completed: 496, budget_exhausted: 4}` (≤0.8% margin)" in page


def test_flagship_prefers_clean_cell_over_lower_bound(tmp_path: Path) -> None:
    _seed(tmp_path)  # clean mbpp 423/427 EXACT
    _write(
        tmp_path,
        "modal-grid-fullscore1-20990101-000000.json",
        [_cell(total=427, passed=151, status="partial_error")],  # noisy mbpp
        run_id="fullscore1",
    )
    page = _render(tmp_path)
    assert "**99.1%** (423/427)" in page  # the clean run wins the row
    assert "≥ 35.4%" not in page  # the noisy run is superseded, not shown


# ---------------------------------------------------------------- integrity


def test_error_cell_with_passes_aborts(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-observatory1-20990101-000000.json",
        [_cell(total=25, passed=25, status="error", counts={"error": 25}, rate=1.0)],
        run_id="observatory1",
    )
    with pytest.raises(obs.IntegrityError, match="status=error with passed=25"):
        obs.load_inputs(tmp_path)


def test_error_cell_with_passes_fails_build_via_cli(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-observatory1-20990101-000000.json",
        [_cell(total=5, passed=3, status="error", rate=0.6)],
        run_id="observatory1",
    )
    out = tmp_path / "page.md"
    site = tmp_path / "site.md"
    code = obs.main(
        ["--data-dir", str(tmp_path), "--out", str(out), "--site-out", str(site)]
    )
    assert code == 2
    assert not out.exists() and not site.exists()  # nothing written on failure


def test_passed_gt_total_aborts(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-fullscore3-20990101-000000.json",
        [_cell(total=10, passed=11, rate=1.1)],
        run_id="fullscore3",
    )
    with pytest.raises(obs.IntegrityError, match="passed=11 > total=10"):
        obs.load_inputs(tmp_path)


def test_status_counts_sum_mismatch_aborts(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-fullscore3-20990101-000000.json",
        [_cell(total=10, passed=9, counts={"completed": 7})],
        run_id="fullscore3",
    )
    with pytest.raises(obs.IntegrityError, match="status_counts sums to 7"):
        obs.load_inputs(tmp_path)


def test_uniform_zero_clean_cell_aborts(tmp_path: Path) -> None:
    """0/50 with ``{completed: 50}`` is a harness gap, not a 0% score.

    The exact shape of the ``coding-agent × humaneval-x`` cell on the
    darklight1 Modal grid: every task ran to completion, none passed,
    because the grader rejected correctly-shaped answers. Rendering it as
    ``0.0%`` would publish a fabricated measurement.
    """
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-fullscore3-20990101-000000.json",
        [_cell(bench="humaneval-x", total=50, passed=0, counts={"completed": 50})],
        run_id="fullscore3",
    )
    with pytest.raises(obs.IntegrityError, match="harness-gap signature"):
        obs.load_inputs(tmp_path)


def test_uniform_zero_without_status_counts_aborts(tmp_path: Path) -> None:
    """A pre-``status_counts`` run still aborts on a clean ``completed`` zero."""
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-fullscore3-20990101-000000.json",
        [_cell(bench="humaneval-x", total=20, passed=0, status="completed")],
        run_id="fullscore3",
    )
    with pytest.raises(obs.IntegrityError, match="harness-gap signature"):
        obs.load_inputs(tmp_path)


def test_uniform_zero_gate_spares_honest_zeros(tmp_path: Path) -> None:
    """The gate fires only on *clean* zeros of a meaningful size.

    A zero explained by budget exhaustion or errors already renders as a
    lower bound, and a 0/1 sample is noise — neither is the harness-gap
    signature, so neither may block the build. ``lint-loop``'s honest
    ``0/1 budget_exhausted`` rows in ``matrix-full-glm52.json`` are the
    real-world case this protects.
    """
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-fullscore3-20990101-000000.json",
        [
            # zero, but the tasks did not complete cleanly
            _cell(bench="mbpp-plus", total=50, passed=0, status="budget_exhausted",
                  counts={"budget_exhausted": 50}),
            _cell(bench="math500", total=50, passed=0, status="partial_error",
                  counts={"completed": 30, "error": 20}),
            # zero, clean, but too small a sample to be a signature
            _cell(bench="human-eval", total=1, passed=0, counts={"completed": 1}),
        ],
        run_id="fullscore3",
    )
    inputs = obs.load_inputs(tmp_path)
    assert inputs.files  # loaded without raising


def test_pass_rate_mismatch_aborts(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-fullscore3-20990101-000000.json",
        [_cell(total=10, passed=5, rate=0.9)],
        run_id="fullscore3",
    )
    with pytest.raises(obs.IntegrityError, match="pass_rate"):
        obs.load_inputs(tmp_path)


# ---------------------------------------------------------------- depth section


def test_depth_section_in_flight_without_observatory_receipt(tmp_path: Path) -> None:
    _seed(tmp_path)
    page = _render(tmp_path)
    assert "Run `observatory1` is in flight" in page
    assert "no number appears here until its receipt lands" in page
    assert "collect --run-id observatory1" in page


def test_depth_section_renders_observatory_receipt(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write(
        tmp_path,
        "modal-grid-observatory1-20990101-000000.json",
        [
            _cell(agent="react", bench="mbpp-sanitized", total=50, passed=42, counts={"completed": 50}, cost=0.3),
            _cell(
                agent="react",
                bench="math500",
                total=50,
                passed=40,
                status="partial_error",
                counts={"completed": 48, "error": 2},
                cost=0.3,
            ),
        ],
        run_id="observatory1",
    )
    page = _render(tmp_path)
    assert "Run `observatory1` is in flight" not in page
    assert "| react | 42/50 (84.0%) | ≥ 40/50 (80.0%) |" in page
    assert "`react × math500` — completed: 48, error: 2" in page
    assert "`data/modal-grid-observatory1-20990101-000000.json`" in page


# ---------------------------------------------------------------- provenance


def test_every_scorecard_row_cites_its_receipt(tmp_path: Path) -> None:
    _seed(tmp_path)
    page = _render(tmp_path)
    line = next(ln for ln in page.splitlines() if ln.startswith("| mbpp |"))
    assert "`data/modal-grid-fullscore2-20990101-000000.json`" in line
    assert "`data/matrix-full-glm52.json`" in page  # breadth source cited
    footer = next(ln for ln in page.splitlines() if ln.startswith("*Generated by"))
    assert "modal-grid-fullscore2-20990101-000000.json" in footer  # footer list


def test_breadth_grid_marks_and_conditional_lint_loop_note(tmp_path: Path) -> None:
    _seed(tmp_path)
    page = _render(tmp_path)
    assert "| react | ✓ | ✓ |" in page
    assert "| lint-loop | · | · |" in page
    assert "`lint-loop`'s zero row is honest" in page  # only because its row is 0


# ---------------------------------------------------------------- check + determinism


def test_check_stale_then_fresh(tmp_path: Path) -> None:
    _seed(tmp_path)
    out = tmp_path / "page.md"
    site = tmp_path / "site.md"
    args = ["--data-dir", str(tmp_path), "--out", str(out), "--site-out", str(site)]
    assert obs.main([*args, "--check"]) == 1  # nothing committed yet -> stale
    assert obs.main(args) == 0  # write
    assert obs.main([*args, "--check"]) == 0  # fresh
    # A data change must flip --check to stale.
    _write(
        tmp_path,
        "modal-grid-fullscore2-20990101-000000.json",
        [_cell(total=427, passed=400, counts={"completed": 427})],
        run_id="fullscore2",
    )
    assert obs.main([*args, "--check"]) == 1
    assert obs.main(args) == 0
    assert obs.main([*args, "--check"]) == 0


def test_check_tolerates_only_the_date_line(tmp_path: Path) -> None:
    _seed(tmp_path)
    out = tmp_path / "page.md"
    site = tmp_path / "site.md"
    args = ["--data-dir", str(tmp_path), "--out", str(out), "--site-out", str(site)]
    assert obs.main(args) == 0
    # A different mtime-derived date (fresh clone) must NOT read as stale...
    for target in (out, site):
        text = target.read_text(encoding="utf-8")
        target.write_text(
            "\n".join(
                "*Data date: 1999-01-01 (newest receipt's mtime)*"
                if ln.startswith("*Data date: ")
                else ln
                for ln in text.splitlines()
            ),
            encoding="utf-8",
        )
    assert obs.main([*args, "--check"]) == 0
    # ...but a doctored number must.
    text = out.read_text(encoding="utf-8").replace("**99.1%**", "**100.0%**")
    out.write_text(text, encoding="utf-8")
    assert obs.main([*args, "--check"]) == 1


def test_output_deterministic_and_site_copy_byte_identical(tmp_path: Path) -> None:
    _seed(tmp_path)
    first = _render(tmp_path)
    second = _render(tmp_path)
    assert first == second
    out = tmp_path / "page.md"
    site = tmp_path / "site.md"
    assert obs.main(["--data-dir", str(tmp_path), "--out", str(out), "--site-out", str(site)]) == 0
    assert out.read_bytes() == site.read_bytes()
    assert out.read_text(encoding="utf-8").endswith("\n")


def test_zero_total_cell_renders_error_not_a_zero_score(tmp_path: Path) -> None:
    """A cell that ran no task has no score.

    Rendering ``0/0 (0.0%)`` would read as a *measured* zero — the false
    precision this page exists to prevent (an errored run is not a 0% result).
    """
    data = tmp_path / "data"
    _seed(data)
    _write(
        data,
        "modal-grid-observatory1-20990101-000000.json",
        [
            _cell(agent="react", bench="mbpp-sanitized", total=50, passed=50,
                  counts={"completed": 50}),
            # Real shape of an errored cell (observatory1's reflexion x
            # math500: a dataset-fetch 429): zeros with empty status_counts.
            _cell(agent="reflexion", bench="math500", total=0, passed=0,
                  status="error", counts={}, cost=0.0),
        ],
        run_id="observatory1",
    )
    page = obs.render(obs.load_inputs(data))
    depth = page.split("## 2.")[1].split("## 3.")[0]
    assert "0/0" not in depth
    assert "(0.0%)" not in depth
    assert "| error |" in depth or "error" in depth
    assert "reflexion × math500" in depth and "no result" in depth


def test_requested_but_absent_agent_is_reported_not_dropped(tmp_path: Path) -> None:
    """A row whose cells never landed must be named, not silently omitted.

    Otherwise a partial grid renders as if it were the whole requested run.
    """
    data = tmp_path / "data"
    _seed(data)
    _write(
        data,
        "modal-grid-observatory1-20990101-000000.json",
        [_cell(agent="react", bench="mbpp-sanitized", total=50, passed=50,
               counts={"completed": 50})],
        run_id="observatory1",
    )
    page = obs.render(obs.load_inputs(data))
    depth = page.split("## 2.")[1].split("## 3.")[0]
    assert "Incomplete run" in depth
    # every requested agent that produced nothing is named
    for absent in ("coding-agent", "plan-execute", "reflexion", "tree-of-thought"):
        assert absent in depth
