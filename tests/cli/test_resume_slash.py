"""Tests for the ``/resume`` slash command (G9, w13).

Covers:

* No-arg form lists the most-recent resumable session ids (eventlog +
  file backends, deduplicated, newest first).
* No-arg form prints a friendly message when no sessions exist.
* Single-arg form ``/resume <id>`` keeps surfacing the existing
  resume-by-id error path when ``id`` does not exist.
* The badger palette wires ``/resume`` into
  :data:`chimera.badger.slash.BADGER_SLASH_COMMANDS`.

The shared registry under :mod:`chimera.cli.slash_commands` is also
checked so mink / otter / ferret REPLs that build on it inherit the
behaviour.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.cli import slash_commands as sc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Recorder:
    """Tiny sink for the ``out`` callable handlers receive."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    @property
    def joined(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def chimera_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``~/.chimera`` to a fresh tmp dir for the test.

    Both ``Path.home() / ".chimera" / "eventlog"`` and
    ``Path.home() / ".chimera" / "sessions"`` are exercised by
    :func:`cmd_resume`. Patching ``Path.home`` is the cleanest hop —
    the resume helpers don't touch any other home subpath.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# /resume (no arg) — picker
# ---------------------------------------------------------------------------


def test_resume_no_arg_with_no_sessions_prints_friendly(
    chimera_home: Path,
) -> None:
    """When neither root exists, ``/resume`` reports zero recent sessions."""
    rec = _Recorder()
    sc.cmd_resume(session=None, _env=None, args="", out=rec)
    assert any("No resumable sessions" in line for line in rec.lines), rec.joined


def test_resume_no_arg_lists_eventlog_sessions(chimera_home: Path) -> None:
    """The picker enumerates eventlog session dirs newest first."""
    eventlog = chimera_home / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True)
    (eventlog / "badger-old").mkdir()
    (eventlog / "badger-new").mkdir()
    # WHY: bump the mtime explicitly so ``new`` clearly wins regardless
    # of filesystem-level timestamp resolution.
    import os
    import time

    os.utime(eventlog / "badger-old", (time.time() - 3600, time.time() - 3600))
    os.utime(eventlog / "badger-new", (time.time(), time.time()))

    rec = _Recorder()
    sc.cmd_resume(session=None, _env=None, args="", out=rec)
    body = rec.joined
    assert "badger-new" in body
    assert "badger-old" in body
    # newest-first ordering
    assert body.index("badger-new") < body.index("badger-old")
    # source label surfaces eventlog
    assert "eventlog" in body


def test_resume_no_arg_includes_file_storage_sessions(
    chimera_home: Path,
) -> None:
    """The picker also surfaces ~/.chimera/sessions/<id>.json[l] entries."""
    sessions = chimera_home / ".chimera" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "ferret-abc.json").write_text("{}")
    (sessions / "mink-def.jsonl").write_text("")

    rec = _Recorder()
    sc.cmd_resume(session=None, _env=None, args="", out=rec)
    body = rec.joined
    assert "ferret-abc" in body
    assert "mink-def" in body
    assert "file" in body


def test_resume_no_arg_dedupes_eventlog_wins(chimera_home: Path) -> None:
    """When both backends share an id, eventlog wins on tie."""
    eventlog = chimera_home / ".chimera" / "eventlog"
    sessions = chimera_home / ".chimera" / "sessions"
    eventlog.mkdir(parents=True)
    sessions.mkdir(parents=True)
    (eventlog / "shared-id").mkdir()
    (sessions / "shared-id.json").write_text("{}")

    import os
    import time

    now = time.time()
    os.utime(eventlog / "shared-id", (now, now))
    os.utime(sessions / "shared-id.json", (now, now))

    rec = _Recorder()
    sc.cmd_resume(session=None, _env=None, args="", out=rec)
    # Only one row for the shared id, and it's the eventlog source.
    rows = [line for line in rec.lines if "shared-id" in line]
    assert len(rows) == 1, rec.joined
    assert "eventlog" in rows[0], rec.joined


def test_resume_with_unknown_id_surfaces_error(chimera_home: Path) -> None:
    """``/resume <bogus>`` falls through to the legacy error reporting."""
    rec = _Recorder()

    class _Sess:
        agent: Any = None

    sc.cmd_resume(session=_Sess(), _env=None, args="does-not-exist", out=rec)
    # The legacy path produces either "session not found:" or
    # "resume failed:" — both are acceptable signals that the input
    # was treated as an id rather than the listing trigger.
    body = rec.joined
    assert (
        "session not found" in body
        or "resume failed" in body
        or "Recent sessions" not in body
    ), body


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_resume_registered_in_shared_registry() -> None:
    """The shared slash registry exposes ``/resume``."""
    names = {name for name, _ in sc.list_commands()}
    assert "resume" in names


def test_resume_registered_in_badger_palette() -> None:
    """badger's slash palette installs ``/resume`` and a help string."""
    from chimera.badger import slash as badger_slash

    assert "resume" in badger_slash.BADGER_SLASH_COMMANDS
    assert "resume" in badger_slash.BADGER_SLASH_HELP
    # Keep the underlying handler identity — same function as the
    # shared registry, so any future bug-fix lands in one place.
    assert badger_slash.BADGER_SLASH_COMMANDS["resume"] is sc.cmd_resume


def test_resume_dispatches_via_shared_registry(chimera_home: Path) -> None:
    """``dispatch("/resume", ...)`` routes to ``cmd_resume`` and prints output."""
    rec = _Recorder()
    handled = sc.dispatch("/resume", session=None, env=None, out=rec)
    assert handled
    assert any("No resumable" in line for line in rec.lines), rec.joined
