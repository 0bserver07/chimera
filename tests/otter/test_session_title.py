"""``chimera otter sessions`` — title flag + rename subcommand regression tests.

Covers the O4-W9 surface:

* ``chimera otter -p PROMPT --title "..."`` persists ``title`` into
  ``summary.json`` (verified via :func:`chimera.otter.cli._write_run_summary`).
* :func:`chimera.otter.sessions.format_session_table` surfaces the title
  in the listing (new TITLE column).
* :func:`chimera.otter.sessions.format_session_detail` prints a
  ``title:`` line when present.
* :func:`chimera.otter.sessions.rename_session` /
  :func:`chimera.otter.sessions.cmd_sessions_rename` round-trip the
  title through the eventlog.
* When ``--title`` is unset, the prompt remains the de-facto title
  (back-compat with existing fixtures).

Stdlib only; no network, no provider stub. Each test materializes one
or more fake otter session directories under ``tmp_path`` via a
``Path.home`` monkeypatch so the production helper
:func:`chimera.otter.sessions.default_eventlog_root` resolves there.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from chimera.otter import cli as otter_cli
from chimera.otter import sessions as sessions_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_eventlog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` so ``~/.chimera/eventlog`` resolves under tmp."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    eventlog = home / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True)
    return eventlog


@dataclasses.dataclass
class _FakeResult:
    """Minimal AgentResult-like stand-in for ``_write_run_summary``."""

    output: str = "ok"
    steps: int = 1
    tool_calls_total: int = 0
    cost: float = 0.0
    success: bool = True
    error: str | None = None


def _make_session_dir(
    root: Path,
    session_id: str,
    *,
    title: str | None = None,
    prompt: str = "do a thing",
    started_at: str = "2026-04-30T12:00:00Z",
) -> Path:
    """Materialize one fake session dir whose ``summary.json`` may carry a title."""
    session_dir = root / session_id
    session_dir.mkdir()
    summary: dict[str, Any] = {
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": started_at,
        "model": "stub-model",
        "prompt": prompt,
        "cwd": "/tmp",
        "permission_mode": "default",
        "steps": 1,
        "tool_calls_total": 0,
        "success": True,
        "cost_usd": 0.0,
        "total_tokens": 0,
        "error": None,
    }
    if title is not None:
        summary["title"] = title
    (session_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8",
    )
    return session_dir


# ---------------------------------------------------------------------------
# _write_run_summary persists the title (mirrors what -p --title does)
# ---------------------------------------------------------------------------


def test_write_run_summary_persists_title(tmp_path: Path) -> None:
    """``--title`` value lands in summary.json under the ``title`` key."""
    run_dir = tmp_path / "otter-20260430T120000-aabbccdd"
    run_dir.mkdir()

    summary_path = otter_cli._write_run_summary(
        run_dir,
        run_id=run_dir.name,
        started_at="2026-04-30T12:00:00Z",
        ended_at="2026-04-30T12:00:01Z",
        model="stub-model",
        prompt="explain the bug",
        result=_FakeResult(),
        cwd="/tmp",
        title="Investigate flaky test",
    )

    assert summary_path == run_dir / "summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["title"] == "Investigate flaky test"
    # Heuristic field still preserved alongside the explicit title.
    assert payload["prompt"] == "explain the bug"


def test_write_run_summary_omits_title_when_unset(tmp_path: Path) -> None:
    """When ``title=None`` the field stays out of summary.json (back-compat)."""
    run_dir = tmp_path / "otter-20260430T120000-eeeeeeee"
    run_dir.mkdir()

    otter_cli._write_run_summary(
        run_dir,
        run_id=run_dir.name,
        started_at="2026-04-30T12:00:00Z",
        ended_at="2026-04-30T12:00:01Z",
        model="stub-model",
        prompt="ship it",
        result=_FakeResult(),
        cwd="/tmp",
        title=None,
    )
    payload = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert "title" not in payload


def test_write_run_summary_strips_blank_title(tmp_path: Path) -> None:
    """Whitespace-only ``--title`` is treated as "unset" so prompt remains the title."""
    run_dir = tmp_path / "otter-20260430T120000-ffffffff"
    run_dir.mkdir()

    otter_cli._write_run_summary(
        run_dir,
        run_id=run_dir.name,
        started_at="2026-04-30T12:00:00Z",
        ended_at="2026-04-30T12:00:01Z",
        model="stub-model",
        prompt="ship it",
        result=_FakeResult(),
        cwd="/tmp",
        title="   ",
    )
    payload = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert "title" not in payload


# ---------------------------------------------------------------------------
# argparse surface — ``-p --title "..."`` lands on Namespace.session_title
# ---------------------------------------------------------------------------


def test_argparse_registers_title_flag() -> None:
    """``--title FOO`` flows through ``add_arguments`` onto ``args.session_title``."""
    parser = argparse.ArgumentParser()
    otter_cli.add_arguments(parser)
    args = parser.parse_args(["-p", "do thing", "--title", "Refactor X"])
    assert args.print_mode == "do thing"
    assert args.session_title == "Refactor X"


def test_argparse_title_defaults_to_none() -> None:
    """No ``--title`` flag => ``session_title`` is None on the namespace."""
    parser = argparse.ArgumentParser()
    otter_cli.add_arguments(parser)
    args = parser.parse_args(["-p", "do thing"])
    assert args.session_title is None


# ---------------------------------------------------------------------------
# format_session_table surfaces the title
# ---------------------------------------------------------------------------


def test_format_session_table_shows_title_when_present(fake_eventlog: Path) -> None:
    """Records with a ``title`` field render it in the TITLE column."""
    _make_session_dir(
        fake_eventlog,
        "otter-20260430T120000-titled01",
        title="Investigate flaky test",
        prompt="some long internal prompt",
    )
    records = list(sessions_mod.iter_sessions())
    assert len(records) == 1
    assert records[0].title == "Investigate flaky test"
    assert records[0].display_title() == "Investigate flaky test"

    out = sessions_mod.format_session_table(records, color=False)
    assert "TITLE" in out
    assert "Investigate flaky test" in out


def test_format_session_table_falls_back_to_prompt_without_title(
    fake_eventlog: Path,
) -> None:
    """Records lacking ``title`` fall back to the truncated prompt heuristic."""
    _make_session_dir(
        fake_eventlog,
        "otter-20260430T120000-prompt01",
        title=None,
        prompt="run the tests please",
    )
    records = list(sessions_mod.iter_sessions())
    assert records[0].title is None
    assert records[0].display_title() == "run the tests please"

    out = sessions_mod.format_session_table(records, color=False)
    assert "run the tests please" in out


def test_format_session_detail_includes_title_line(fake_eventlog: Path) -> None:
    """``sessions show`` prints a ``title:`` line when set."""
    _make_session_dir(
        fake_eventlog,
        "otter-20260430T120000-detail01",
        title="Investigate flaky test",
    )
    detail = sessions_mod.get_session("otter-20260430T120000-detail01")
    rendered = sessions_mod.format_session_detail(detail, color=False)
    assert "title:" in rendered
    assert "Investigate flaky test" in rendered


# ---------------------------------------------------------------------------
# rename_session round-trips through summary.json
# ---------------------------------------------------------------------------


def test_rename_session_writes_title(fake_eventlog: Path) -> None:
    """First rename installs a title key; iter_sessions reads it back."""
    _make_session_dir(
        fake_eventlog, "otter-20260430T120000-rename01",
    )
    path = sessions_mod.rename_session(
        "otter-20260430T120000-rename01", "Hot path optimization",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["title"] == "Hot path optimization"

    [record] = list(sessions_mod.iter_sessions())
    assert record.title == "Hot path optimization"
    assert record.display_title() == "Hot path optimization"


def test_rename_session_overwrites_existing_title(fake_eventlog: Path) -> None:
    """A rename on a session with an existing title replaces it."""
    _make_session_dir(
        fake_eventlog, "otter-20260430T120000-rename02", title="Old title",
    )
    sessions_mod.rename_session(
        "otter-20260430T120000-rename02", "New title",
    )
    [record] = list(sessions_mod.iter_sessions())
    assert record.title == "New title"


def test_rename_session_clears_title_when_blank(fake_eventlog: Path) -> None:
    """Empty / whitespace-only title removes the key (round-trips with --title=None)."""
    _make_session_dir(
        fake_eventlog, "otter-20260430T120000-rename03", title="To be cleared",
    )
    sessions_mod.rename_session(
        "otter-20260430T120000-rename03", "   ",
    )
    payload = json.loads(
        (fake_eventlog / "otter-20260430T120000-rename03" / "summary.json")
        .read_text(encoding="utf-8")
    )
    assert "title" not in payload


def test_rename_session_unknown_raises(fake_eventlog: Path) -> None:
    """Renaming a missing session raises FileNotFoundError, not silent success."""
    with pytest.raises(FileNotFoundError):
        sessions_mod.rename_session(
            "otter-20260430T120000-missing0", "anything",
        )


def test_rename_session_no_summary_raises(fake_eventlog: Path) -> None:
    """A session dir without summary.json raises FileNotFoundError."""
    (fake_eventlog / "otter-20260430T120000-nosum01").mkdir()
    with pytest.raises(FileNotFoundError):
        sessions_mod.rename_session(
            "otter-20260430T120000-nosum01", "anything",
        )


# ---------------------------------------------------------------------------
# cmd_sessions_rename + dispatch_sessions("rename") — CLI surface
# ---------------------------------------------------------------------------


def _rename_args(
    *,
    sessions_target: str | None,
    sessions_title: Any,
) -> argparse.Namespace:
    """Build a Namespace mirroring what _dispatch_sessions would forward."""
    return argparse.Namespace(
        sessions_command="sessions",
        sessions_action="rename",
        sessions_target=sessions_target,
        sessions_title=sessions_title,
    )


def test_cmd_sessions_rename_round_trips_via_dispatch(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """``dispatch_sessions(action="rename")`` writes the title and confirms on stdout."""
    _make_session_dir(
        fake_eventlog, "otter-20260430T120000-cmd01",
    )
    args = _rename_args(
        sessions_target="otter-20260430T120000-cmd01",
        sessions_title=["Refactor", "the", "loop"],
    )
    rc = sessions_mod.dispatch_sessions(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "renamed otter-20260430T120000-cmd01" in captured.out
    assert "Refactor the loop" in captured.out

    # Round-trip: list sees the new title.
    table = sessions_mod.format_session_table(
        list(sessions_mod.iter_sessions()), color=False,
    )
    assert "Refactor the loop" in table


def test_cmd_sessions_rename_string_title_works(fake_eventlog: Path) -> None:
    """``sessions_title`` may be a plain str (not just a list)."""
    _make_session_dir(
        fake_eventlog, "otter-20260430T120000-cmd02",
    )
    args = _rename_args(
        sessions_target="otter-20260430T120000-cmd02",
        sessions_title="Plain string title",
    )
    rc = sessions_mod.cmd_sessions_rename(args)
    assert rc == 0
    [record] = list(sessions_mod.iter_sessions())
    assert record.title == "Plain string title"


def test_cmd_sessions_rename_missing_target_returns_error(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """No SESSION_ID -> usage error to stderr + exit code 2."""
    args = _rename_args(sessions_target=None, sessions_title=["x"])
    rc = sessions_mod.cmd_sessions_rename(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "SESSION_ID" in err


def test_cmd_sessions_rename_missing_title_returns_error(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing title arg (None) -> usage error to stderr + exit code 2."""
    _make_session_dir(
        fake_eventlog, "otter-20260430T120000-cmd03",
    )
    args = _rename_args(
        sessions_target="otter-20260430T120000-cmd03",
        sessions_title=None,
    )
    rc = sessions_mod.cmd_sessions_rename(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "TITLE" in err


def test_cmd_sessions_rename_unknown_id_returns_error(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Renaming a missing session prints an error + hint and exits 2."""
    args = _rename_args(
        sessions_target="otter-20260430T120000-ghost000",
        sessions_title=["x"],
    )
    rc = sessions_mod.cmd_sessions_rename(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "session not found" in err
    assert "sessions list" in err


def test_dispatch_sessions_unknown_action_lists_rename(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The "supported actions" hint mentions ``rename`` so the new verb is discoverable."""
    args = argparse.Namespace(
        sessions_command="sessions",
        sessions_action="bogus",
    )
    rc = sessions_mod.dispatch_sessions(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "rename" in err
