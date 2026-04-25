"""Regression tests for AUDIT.md H-3 — `mink runs list` / `mink runs show <id>`.

Persisted run dirs at ``~/.chimera/eventlog/mink-*/`` accumulate during
normal use; before this fix users had to ``cat summary.json`` by hand.
The ``runs`` subcommand surfaces them via a small read-only CLI.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_eventlog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect ``~/.chimera/eventlog`` at ``Path.home()`` to a tmp dir.

    Also ensures the production helper :func:`_eventlog_root` resolves
    to ``<tmp_path>/.chimera/eventlog`` so list/show act on a controlled
    fixture instead of the user's real disk.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # type: ignore[arg-type]
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True)
    return eventlog


def _make_fake_run(
    eventlog: Path,
    run_id: str,
    *,
    started_at: str,
    model: str = "glm-5.1:cloud",
    success: bool = True,
    steps: int = 2,
    cost_usd: float = 0.0123,
    prompt: str = "do the thing",
    events: list[dict[str, Any]] | None = None,
) -> Path:
    """Materialize a run dir with summary.json + optional event files."""
    run_dir = eventlog / run_id
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "started_at": started_at,
                "ended_at": started_at,
                "model": model,
                "prompt": prompt,
                "cwd": "/tmp/proj",
                "permission_mode": "default",
                "steps": steps,
                "tool_calls_total": 1,
                "success": success,
                "cost_usd": cost_usd,
                "total_tokens": 0,
                "error": None,
            },
        ),
    )
    for i, ev in enumerate(events or []):
        (run_dir / f"event-{i:06d}-deadbeef.json").write_text(json.dumps(ev))
    return run_dir


# ---------------------------------------------------------------------------
# 1. list with empty dir
# ---------------------------------------------------------------------------


def test_runs_list_empty(fake_eventlog: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``mink runs list`` on an empty directory still exits 0 with a hint."""
    from chimera.mink.cli import _run_runs_list

    rc = _run_runs_list()
    out = capsys.readouterr().out
    assert rc == 0
    # The exact wording lives in chimera.mink.runs.format_run_table; assert
    # only that the empty-state message is present and not blank.
    assert "no persisted runs" in out.lower()


# ---------------------------------------------------------------------------
# 2. list with three fake summaries (sorted desc)
# ---------------------------------------------------------------------------


def test_runs_list_three_summaries_sorted(
    fake_eventlog: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Three runs render in started_at descending order."""
    from chimera.mink.cli import _run_runs_list

    _make_fake_run(fake_eventlog, "mink-20260101T000000-aaaaaaaa", started_at="2026-01-01T00:00:00Z")
    _make_fake_run(fake_eventlog, "mink-20260201T000000-bbbbbbbb", started_at="2026-02-01T00:00:00Z")
    _make_fake_run(fake_eventlog, "mink-20260301T000000-cccccccc", started_at="2026-03-01T00:00:00Z")

    rc = _run_runs_list()
    out = capsys.readouterr().out
    assert rc == 0

    pos_a = out.index("mink-20260101")
    pos_b = out.index("mink-20260201")
    pos_c = out.index("mink-20260301")
    # Desc by started_at: c (newest) before b before a
    assert pos_c < pos_b < pos_a, (
        f"expected descending order, got positions a={pos_a} b={pos_b} c={pos_c}"
    )
    # WHY: a typical user runs `mink runs list | head` so the most-recent
    # row must hit stdout first, not last.


# ---------------------------------------------------------------------------
# 3. show with valid id
# ---------------------------------------------------------------------------


def test_runs_show_valid_id(
    fake_eventlog: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``runs show <id>`` prints summary + event listing."""
    from chimera.mink.cli import _run_runs_show

    run_id = "mink-20260101T000000-aaaaaaaa"
    _make_fake_run(
        fake_eventlog,
        run_id,
        started_at="2026-01-01T00:00:00Z",
        events=[
            {"type": "user_message", "metadata": {"content": "hi"}},
            {"type": "agent_result", "metadata": {"output": "done"}},
        ],
    )

    rc = _run_runs_show(run_id, full=False)
    out = capsys.readouterr().out
    assert rc == 0
    # Header / summary fields
    assert run_id in out
    assert "glm-5.1:cloud" in out
    # Event count (the new format prints "Events (N):" rather than the
    # raw filenames).
    assert "Events (2)" in out


def test_runs_show_full_dumps_conversation(
    fake_eventlog: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``runs show <id> --full`` includes event content for each entry."""
    from chimera.mink.cli import _run_runs_show

    run_id = "mink-20260101T000000-aaaaaaaa"
    _make_fake_run(
        fake_eventlog,
        run_id,
        started_at="2026-01-01T00:00:00Z",
        events=[
            {"type": "user_message", "metadata": {"content": "the prompt"}},
            {"type": "agent_result", "metadata": {"output": "the result"}},
        ],
    )

    rc = _run_runs_show(run_id, full=True)
    out = capsys.readouterr().out
    assert rc == 0
    # The transcript renderer (chimera.mink.runs.format_run_detail) embeds
    # both event payloads; the exact framing differs but the bodies must
    # appear so users can read what the agent said and did.
    assert "the prompt" in out
    assert "the result" in out


# ---------------------------------------------------------------------------
# 4. show with missing id (exit 1, friendly error)
# ---------------------------------------------------------------------------


def test_runs_show_missing_id_exits_nonzero(
    fake_eventlog: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown run id reports a clean error with a non-zero exit code.

    The exact code is implementation-defined (1 or 2 depending on the
    refactor); assert non-zero so ``not found`` style failures stay
    detectable by shell scripts without coupling to the literal value.
    """
    from chimera.mink.cli import _run_runs_show

    rc = _run_runs_show("mink-does-not-exist", full=False)
    err = capsys.readouterr().err
    assert rc != 0
    assert "not found" in err
    assert "mink-does-not-exist" in err


def test_runs_show_missing_id_argument(
    fake_eventlog: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Calling show without a run id prints usage and exits non-zero."""
    from chimera.mink.cli import _run_runs_show

    rc = _run_runs_show(None, full=False)
    err = capsys.readouterr().err
    assert rc != 0
    assert "RUN_ID" in err
