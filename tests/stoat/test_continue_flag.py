"""Tests for ``--continue`` / ``-c`` / ``--session`` flags (W14-3, item 3).

Covers:

* Argparse exposes ``continue_latest`` and ``resume_session`` slots when
  ``-c`` / ``--continue`` / ``--session <id>`` are passed.
* ``resolve_resume_session_id`` returns ``None`` when no flag is set.
* ``resolve_resume_session_id`` honours the explicit id from
  ``--session`` even when ``--continue`` is also set.
* ``resolve_resume_session_id`` resolves ``--continue`` against the
  newest session whose summary's ``cwd`` matches.
* ``resolve_resume_session_id`` returns ``None`` for ``--continue`` when
  no candidate session exists.
* :func:`chimera.stoat.repl.run` writes a ``[resumed session ...]``
  banner when resume succeeds and a ``stoat: --session resume failed``
  hint when the id is bogus.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import pytest

from chimera.stoat import cli as stoat_cli
from chimera.stoat import repl as stoat_repl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stoat")
    stoat_cli.add_arguments(parser)
    return parser


def _make_session_dir(
    eventlog_root: Path,
    session_id: str,
    *,
    cwd: str,
    prompt: str = "do x",
    model: str = "kimi-k2.6",
) -> Path:
    session_dir = eventlog_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "session_id": session_id,
        "started_at": "2026-05-07T10:01:00",
        "ended_at": "2026-05-07T10:02:00",
        "model": model,
        "prompt": prompt,
        "success": True,
        "cwd": cwd,
        "cli_origin": "stoat",
    }
    (session_dir / "summary.json").write_text(json.dumps(summary))
    return session_dir


# ---------------------------------------------------------------------------
# Argparse — flag wiring
# ---------------------------------------------------------------------------


def test_continue_flag_sets_continue_latest() -> None:
    """``-c`` and ``--continue`` both flip ``args.continue_latest`` on."""
    parser = _build_parser()
    args = parser.parse_args(["-c"])
    assert args.continue_latest is True
    args = parser.parse_args(["--continue"])
    assert args.continue_latest is True


def test_continue_flag_default_false() -> None:
    """No flag means ``continue_latest`` defaults to ``False``."""
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.continue_latest is False


def test_session_flag_captures_id() -> None:
    """``--session <id>`` puts the value on ``args.resume_session``."""
    parser = _build_parser()
    args = parser.parse_args(["--session", "stoat-20260507T100100-aaaaaaaa"])
    assert args.resume_session == "stoat-20260507T100100-aaaaaaaa"


def test_session_flag_default_none() -> None:
    """``--session`` defaults to ``None`` when not passed."""
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.resume_session is None


# ---------------------------------------------------------------------------
# resolve_resume_session_id
# ---------------------------------------------------------------------------


def test_resolve_returns_none_when_no_flag() -> None:
    """No ``--continue`` / ``--session`` -> resolver returns ``None``."""
    ns = argparse.Namespace(
        continue_latest=False, resume_session=None, cwd=None,
    )
    assert stoat_repl.resolve_resume_session_id(ns) is None


def test_resolve_explicit_session_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit ``--session`` always wins over ``--continue``."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True, exist_ok=True)
    _make_session_dir(eventlog, "stoat-20260507T093000-bbbbbbbb", cwd=str(tmp_path))
    ns = argparse.Namespace(
        continue_latest=True,
        resume_session="stoat-20260507T100100-aaaaaaaa",
        cwd=str(tmp_path),
    )
    assert (
        stoat_repl.resolve_resume_session_id(ns)
        == "stoat-20260507T100100-aaaaaaaa"
    )


def test_resolve_continue_picks_newest_for_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--continue`` resolves to the newest session whose cwd matches."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True, exist_ok=True)
    _make_session_dir(
        eventlog, "stoat-20260507T093000-bbbbbbbb", cwd=str(tmp_path),
    )
    _make_session_dir(
        eventlog, "stoat-20260507T100100-aaaaaaaa", cwd=str(tmp_path),
    )
    # Foreign cwd — must be skipped.
    _make_session_dir(
        eventlog, "stoat-20260507T110000-cccccccc", cwd="/elsewhere",
    )
    ns = argparse.Namespace(
        continue_latest=True,
        resume_session=None,
        cwd=str(tmp_path),
    )
    resolved = stoat_repl.resolve_resume_session_id(ns)
    assert resolved == "stoat-20260507T100100-aaaaaaaa"


def test_resolve_continue_no_candidate_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No matching session -> ``--continue`` returns ``None``."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True, exist_ok=True)
    ns = argparse.Namespace(
        continue_latest=True,
        resume_session=None,
        cwd=str(tmp_path),
    )
    assert stoat_repl.resolve_resume_session_id(ns) is None


# ---------------------------------------------------------------------------
# repl.run() — banner rendering for resume
# ---------------------------------------------------------------------------


class _FakeRepl:
    """Stand-in :class:`StoatRepl` we patch into ``run`` for test isolation."""

    instances: list["_FakeRepl"] = []

    def __init__(self, *_a: object, **kw: object) -> None:
        self.kwargs = kw
        self.out = io.StringIO()
        _FakeRepl.instances.append(self)

    def run(self) -> int:  # pragma: no cover — mock impl
        return 0


def test_run_with_continue_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful resume writes a ``[resumed session ...]`` banner."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True, exist_ok=True)
    _make_session_dir(
        eventlog,
        "stoat-20260507T100100-aaaaaaaa",
        cwd=str(tmp_path),
        prompt="add CI",
    )
    monkeypatch.setattr(stoat_repl, "StoatRepl", _FakeRepl)
    _FakeRepl.instances = []

    ns = argparse.Namespace(
        continue_latest=True,
        resume_session=None,
        cwd=str(tmp_path),
        model="kimi-k2.6",
        max_steps=50,
        shell_mode=False,
        plan_mode=False,
    )
    stoat_repl.run(ns)
    assert _FakeRepl.instances, "expected a StoatRepl instance to be built"
    inst = _FakeRepl.instances[-1]
    rendered = inst.out.getvalue()
    assert "[resumed session stoat-20260507T100100-aaaaaaaa]" in rendered
    assert "add CI" in rendered
    assert inst.kwargs["session_id"] == "stoat-20260507T100100-aaaaaaaa"


def test_run_with_bogus_session_writes_stderr_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bogus ``--session`` id surfaces a hint and clears ``session_id``."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(stoat_repl, "StoatRepl", _FakeRepl)
    _FakeRepl.instances = []

    ns = argparse.Namespace(
        continue_latest=False,
        resume_session="stoat-no-such-thing",
        cwd=str(tmp_path),
        model="kimi-k2.6",
        max_steps=50,
        shell_mode=False,
        plan_mode=False,
    )
    stoat_repl.run(ns)
    err = capsys.readouterr().err
    assert "stoat: --session resume failed" in err
    assert _FakeRepl.instances[-1].kwargs["session_id"] == ""


def test_run_without_resume_no_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No resume flags -> no banner is written before the REPL boots."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(stoat_repl, "StoatRepl", _FakeRepl)
    _FakeRepl.instances = []
    ns = argparse.Namespace(
        continue_latest=False,
        resume_session=None,
        cwd=str(tmp_path),
        model="kimi-k2.6",
        max_steps=50,
        shell_mode=False,
        plan_mode=False,
    )
    stoat_repl.run(ns)
    assert _FakeRepl.instances[-1].out.getvalue() == ""
