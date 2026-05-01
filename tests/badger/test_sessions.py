"""Tests for ``chimera.badger.sessions`` — session inspection / cost / share."""

from __future__ import annotations

import argparse
import json
import tarfile
from pathlib import Path

import pytest

from chimera.badger import sessions


def _make_session(
    root: Path,
    session_id: str,
    *,
    model: str = "claude-sonnet-4-6",
    cost: float = 0.01,
    success: bool = True,
    started_at: str = "2026-04-30T05:10:01Z",
) -> Path:
    """Create a fixture session under *root*."""
    session_dir = root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": "2026-04-30T05:11:01Z",
        "model": model,
        "prompt": "test prompt",
        "success": success,
        "cost_usd": cost,
        "steps": 3,
        "tool_calls_total": 5,
    }
    (session_dir / "summary.json").write_text(json.dumps(summary))
    (session_dir / "event-000001-user.json").write_text(json.dumps({
        "type": "user_message",
        "metadata": {"content": "hello"},
    }))
    return session_dir


def test_iter_sessions_yields_records(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True)
    _make_session(eventlog, "badger-20260430T051001-aaaa1111")
    _make_session(eventlog, "badger-20260430T061001-bbbb2222")
    # An unrelated dir must not be yielded.
    _make_session(eventlog, "ferret-20260430T061001-cccc3333")
    records = list(sessions.iter_sessions())
    assert len(records) == 2
    # Newest first.
    assert records[0].session_id.startswith("badger-20260430T0610")


def test_iter_sessions_empty_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert list(sessions.iter_sessions()) == []


def test_get_session_loads_events(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True)
    _make_session(eventlog, "badger-20260430T051001-aaaa1111")
    detail = sessions.get_session("badger-20260430T051001-aaaa1111")
    assert detail.session_id == "badger-20260430T051001-aaaa1111"
    assert len(detail.events) == 1
    assert detail.events[0]["type"] == "user_message"


def test_get_session_missing_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(FileNotFoundError):
        sessions.get_session("badger-nope")


def test_format_session_table_empty() -> None:
    text = sessions.format_session_table([])
    assert "no persisted badger sessions" in text


def test_format_session_table_renders_record(tmp_path: Path) -> None:
    rec = sessions.SessionRecord(
        session_id="badger-x",
        started_at="2026-04-30T05:10:01Z",
        ended_at="",
        model="claude",
        prompt="hi",
        success=True,
        cost_usd=0.0123,
        steps=4,
        tool_calls=7,
        path=tmp_path,
    )
    text = sessions.format_session_table([rec])
    assert "badger-x" in text
    assert "$0.0123" in text


def test_parse_since_relative() -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    cutoff = sessions.parse_since("7d", now=now)
    assert cutoff == now - timedelta(days=7)


def test_parse_since_iso() -> None:
    cutoff = sessions.parse_since("2026-04-01")
    assert cutoff.year == 2026 and cutoff.month == 4 and cutoff.day == 1


def test_parse_since_invalid_raises() -> None:
    with pytest.raises(ValueError):
        sessions.parse_since("nonsense")


# ---------------------------------------------------------------------------
# CLI dispatchers
# ---------------------------------------------------------------------------


def test_cmd_sessions_list_text(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True)
    _make_session(eventlog, "badger-20260430T051001-aaaa1111")
    args = argparse.Namespace(
        sessions_since=None, sessions_model=None, sessions_limit=10,
        sessions_json=False,
    )
    rc = sessions.cmd_sessions_list(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "badger-20260430T051001-aaaa1111" in out


def test_cmd_sessions_list_json(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True)
    _make_session(eventlog, "badger-20260430T051001-aaaa1111")
    args = argparse.Namespace(
        sessions_since=None, sessions_model=None, sessions_limit=10,
        sessions_json=True,
    )
    rc = sessions.cmd_sessions_list(args)
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert isinstance(rows, list) and len(rows) == 1


def test_cmd_session_cost_aggregates(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True)
    _make_session(eventlog, "badger-20260430T051001-aaaa1111", cost=0.10)
    _make_session(eventlog, "badger-20260430T061001-bbbb2222", cost=0.05)
    args = argparse.Namespace(
        sessions_since=None, sessions_model=None,
        sessions_json=True,
    )
    rc = sessions.cmd_session_cost(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["session_count"] == 2
    assert abs(payload["total_usd"] - 0.15) < 1e-9


def test_cmd_session_share_writes_tarball(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    eventlog = tmp_path / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True)
    _make_session(eventlog, "badger-share-test")
    output_path = tmp_path / "out.tar.gz"
    args = argparse.Namespace(
        sessions_target="badger-share-test",
        output=str(output_path),
    )
    rc = sessions.cmd_session_share(args)
    assert rc == 0
    assert output_path.exists()
    with tarfile.open(output_path) as tar:
        names = tar.getnames()
    assert any("summary.json" in n for n in names)


def test_dispatch_sessions_list(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    args = argparse.Namespace(
        sessions_command="sessions",
        sessions_action="list",
        sessions_since=None, sessions_model=None,
        sessions_limit=10, sessions_json=False,
    )
    rc = sessions.dispatch_sessions(args)
    assert rc == 0


def test_dispatch_sessions_unknown_action(capsys) -> None:
    args = argparse.Namespace(
        sessions_command="sessions",
        sessions_action="bogus",
    )
    rc = sessions.dispatch_sessions(args)
    assert rc == 2


def test_dispatch_sessions_returns_none_when_not_engaged() -> None:
    args = argparse.Namespace(sessions_command="other")
    rc = sessions.dispatch_sessions(args)
    assert rc is None
