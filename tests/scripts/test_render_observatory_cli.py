"""CLI-flag tests for scripts/render_observatory.py (--inputs override)."""

from __future__ import annotations

from pathlib import Path

from tests.scripts.test_render_observatory import _cell, _write, obs


def test_inputs_override_restricts_receipt_set(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "modal-grid-fullscore2-20990101-000000.json",
        [_cell(total=10, passed=9, counts={"completed": 10})],
        run_id="fullscore2",
    )
    _write(
        tmp_path,
        "matrix-full-glm52.json",
        [_cell(agent="react", bench="human-eval", total=1, passed=1, cost=0.01)],
        model="glm-5.2[1m]",
    )
    out = tmp_path / "page.md"
    site = tmp_path / "site.md"
    code = obs.main(
        [
            "--data-dir",
            str(tmp_path),
            "--out",
            str(out),
            "--site-out",
            str(site),
            "--inputs",
            "modal-grid-fullscore2-*.json",
        ]
    )
    assert code == 0
    page = out.read_text(encoding="utf-8")
    assert "modal-grid-fullscore2-20990101-000000.json" in page
    assert "matrix-full-glm52.json" not in page  # excluded by the override
