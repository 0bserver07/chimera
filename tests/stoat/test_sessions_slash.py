"""Tests for the ``/sessions`` slash command (W14-3, item 2).

Covers:

* ``/sessions`` is in :data:`SLASH_COMMANDS` and shows up in ``/help``.
* ``/sessions`` and ``/sessions list`` render an empty-state line when
  ``~/.chimera/eventlog/`` has no stoat sessions.
* ``/sessions list`` renders a row per session with the short date, the
  id, and a truncated prompt.
* ``/sessions list <n>`` honours an integer limit; non-integer values
  surface a friendly error.
* ``/sessions show <id>`` renders the summary block; missing ids
  surface a friendly error.
* ``/sessions show`` without an id surfaces a friendly error.
* Unknown actions (``/sessions wat``) surface a friendly error.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.stoat.shell_mode import ShellModeManager
from chimera.stoat.slash import (
    SLASH_COMMANDS,
    build_default_palette,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def palette():  # type: ignore[no-untyped-def]
    """Fresh slash palette over a fresh shell-mode manager."""
    return build_default_palette(
        shell_mode=ShellModeManager(),
        model="kimi-k2.6",
    )


def _make_session(
    eventlog_root: Path,
    session_id: str,
    *,
    prompt: str = "do the thing",
    started_at: str = "2026-05-07T10:01:00",
    ended_at: str = "2026-05-07T10:02:00",
    success: bool = True,
    model: str = "kimi-k2.6",
) -> Path:
    """Materialise a stoat session directory with a minimal summary.json."""
    session_dir = eventlog_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "model": model,
        "prompt": prompt,
        "success": success,
        "cost_usd": 0.012,
        "steps": 3,
        "tool_calls_total": 5,
        "cwd": str(eventlog_root),
        "cli_origin": "stoat",
    }
    (session_dir / "summary.json").write_text(json.dumps(summary))
    return session_dir


@pytest.fixture()
def fake_eventlog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the eventlog walk to a fresh tmp dir and yield it."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog_root = tmp_path / ".chimera" / "eventlog"
    eventlog_root.mkdir(parents=True, exist_ok=True)
    return eventlog_root


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_sessions_in_canonical_command_list() -> None:
    """``/sessions`` is part of the documented slash palette."""
    assert "/sessions" in SLASH_COMMANDS


def test_help_text_advertises_sessions(palette) -> None:  # type: ignore[no-untyped-def]
    """``/help`` mentions ``/sessions`` so users discover it."""
    result = palette.dispatch("/help")
    assert result.text is not None
    assert "/sessions" in result.text


# ---------------------------------------------------------------------------
# /sessions list
# ---------------------------------------------------------------------------


def test_sessions_empty_state(palette, fake_eventlog: Path) -> None:  # type: ignore[no-untyped-def]
    """No sessions on disk -> friendly empty-state text, never raises."""
    result = palette.dispatch("/sessions")
    assert result.handled is True
    assert result.text is not None
    assert "no stoat sessions" in result.text.lower()


def test_sessions_list_renders_rows(palette, fake_eventlog: Path) -> None:  # type: ignore[no-untyped-def]
    """A populated eventlog yields one row per session, newest first."""
    _make_session(
        fake_eventlog,
        "stoat-20260507T100100-aaaaaaaa",
        prompt="add CI",
    )
    _make_session(
        fake_eventlog,
        "stoat-20260507T093000-bbbbbbbb",
        prompt="fix flake",
    )
    result = palette.dispatch("/sessions list")
    assert result.text is not None
    assert "stoat-20260507T100100-aaaaaaaa" in result.text
    assert "stoat-20260507T093000-bbbbbbbb" in result.text
    assert "add CI" in result.text
    assert "fix flake" in result.text
    # Newer session sorts above the older one.
    idx_new = result.text.index("aaaaaaaa")
    idx_old = result.text.index("bbbbbbbb")
    assert idx_new < idx_old


def test_sessions_list_limit_respected(  # type: ignore[no-untyped-def]
    palette, fake_eventlog: Path,
) -> None:
    """``/sessions list <n>`` caps the number of rows rendered."""
    for i in range(5):
        _make_session(
            fake_eventlog,
            f"stoat-20260507T1001{i:02d}-{'c' * 8}",
            prompt=f"prompt {i}",
        )
    result = palette.dispatch("/sessions list 2")
    assert result.text is not None
    assert "2 session(s)" in result.text


def test_sessions_list_non_integer_limit_friendly_error(  # type: ignore[no-untyped-def]
    palette,
) -> None:
    """Non-integer limit -> human-readable error rather than a traceback."""
    result = palette.dispatch("/sessions list abc")
    assert result.handled is True
    assert result.text is not None
    assert "expected an integer" in result.text


def test_sessions_list_zero_limit_returns_empty(  # type: ignore[no-untyped-def]
    palette, fake_eventlog: Path,
) -> None:
    """A zero or negative limit short-circuits to ``(no sessions)``."""
    _make_session(
        fake_eventlog,
        "stoat-20260507T100100-aaaaaaaa",
        prompt="add CI",
    )
    result = palette.dispatch("/sessions list 0")
    assert result.text == "(no sessions)"


# ---------------------------------------------------------------------------
# /sessions show
# ---------------------------------------------------------------------------


def test_sessions_show_renders_summary(  # type: ignore[no-untyped-def]
    palette, fake_eventlog: Path,
) -> None:
    """``/sessions show <id>`` prints the summary block."""
    _make_session(
        fake_eventlog,
        "stoat-20260507T100100-aaaaaaaa",
        prompt="add CI",
        model="kimi-k2.6",
    )
    result = palette.dispatch("/sessions show stoat-20260507T100100-aaaaaaaa")
    assert result.text is not None
    assert "stoat-20260507T100100-aaaaaaaa" in result.text
    assert "kimi-k2.6" in result.text
    assert "add CI" in result.text


def test_sessions_show_missing_id(palette) -> None:  # type: ignore[no-untyped-def]
    """``/sessions show`` without an id -> friendly error."""
    result = palette.dispatch("/sessions show")
    assert result.text is not None
    assert "missing session id" in result.text


def test_sessions_show_not_found(  # type: ignore[no-untyped-def]
    palette, fake_eventlog: Path,
) -> None:
    """``/sessions show <bogus>`` -> friendly not-found, never raises."""
    result = palette.dispatch("/sessions show stoat-does-not-exist")
    assert result.text is not None
    assert "not found" in result.text or "show:" in result.text


# ---------------------------------------------------------------------------
# Unknown actions
# ---------------------------------------------------------------------------


def test_sessions_unknown_action(palette) -> None:  # type: ignore[no-untyped-def]
    """Unknown actions surface a friendly error rather than raising."""
    result = palette.dispatch("/sessions wat")
    assert result.text is not None
    assert "unknown action" in result.text


def test_sessions_keep_going(palette) -> None:  # type: ignore[no-untyped-def]
    """``/sessions`` always keeps the REPL looping (never exits)."""
    for line in ("/sessions", "/sessions list", "/sessions show foo"):
        result = palette.dispatch(line)
        assert result.keep_going is True
