"""Tests for ``chimera.weasel.sessions`` (agent W1).

Covers:

* :func:`iter_sessions` walks ``~/.chimera/eventlog/weasel-*`` and skips
  non-weasel sessions.
* :func:`get_session` raises :class:`FileNotFoundError` for unknown ids
  and loads ``summary.json`` + ``event-*.json`` files when present.
* :func:`format_session_table` and :func:`format_session_detail` produce
  stable human-readable output.
* :func:`dispatch_sessions` routes ``list`` / ``show`` / unknown actions
  via the W1 placeholder parser.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import pytest

from chimera.weasel import sessions as weasel_sessions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_session(
    root: Path,
    name: str,
    *,
    summary: dict | None = None,
    events: list[dict] | None = None,
) -> Path:
    """Create a session directory under ``root`` with summary + events."""
    session_dir = root / name
    session_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": name,
        "started_at": "2026-04-30T10:00:00Z",
        "ended_at": "2026-04-30T10:01:00Z",
        "model": "claude-sonnet-4-6",
        "prompt": "do something",
        "success": True,
        "cost_usd": 0.01,
        "steps": 3,
        "tool_calls_total": 2,
    }
    if summary:
        payload.update(summary)
    (session_dir / "summary.json").write_text(json.dumps(payload))
    for i, ev in enumerate(events or []):
        (session_dir / f"event-{i:06d}-x.json").write_text(json.dumps(ev))
    return session_dir


# ---------------------------------------------------------------------------
# iter_sessions
# ---------------------------------------------------------------------------


def test_iter_sessions_skips_non_weasel(tmp_path) -> None:
    """``iter_sessions`` only emits ``weasel-*`` directories."""
    _write_session(tmp_path, "weasel-20260430T100000-aaaaaaaa")
    _write_session(tmp_path, "otter-20260430T100000-bbbbbbbb")
    _write_session(tmp_path, "weasel-20260430T110000-cccccccc")
    records = list(weasel_sessions.iter_sessions(tmp_path))
    ids = [r.session_id for r in records]
    assert ids == [
        "weasel-20260430T110000-cccccccc",
        "weasel-20260430T100000-aaaaaaaa",
    ]


def test_iter_sessions_handles_missing_root(tmp_path) -> None:
    """A non-existent eventlog root yields zero records (no crash)."""
    missing = tmp_path / "no-such-dir"
    assert list(weasel_sessions.iter_sessions(missing)) == []


def test_iter_sessions_skips_missing_summary(tmp_path) -> None:
    """Sessions without summary.json are skipped."""
    bad = tmp_path / "weasel-20260430T100000-zzzzzzzz"
    bad.mkdir()
    _write_session(tmp_path, "weasel-20260430T110000-cccccccc")
    records = list(weasel_sessions.iter_sessions(tmp_path))
    assert len(records) == 1
    assert records[0].session_id == "weasel-20260430T110000-cccccccc"


def test_iter_sessions_skips_invalid_summary_json(tmp_path) -> None:
    """Sessions with malformed summary.json are skipped silently."""
    bad = tmp_path / "weasel-20260430T100000-aaaaaaaa"
    bad.mkdir()
    (bad / "summary.json").write_text("not json {{")
    records = list(weasel_sessions.iter_sessions(tmp_path))
    assert records == []


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------


def test_get_session_loads_summary_and_events(tmp_path) -> None:
    """``get_session`` returns the parsed summary plus event files."""
    name = "weasel-20260430T100000-aaaaaaaa"
    _write_session(
        tmp_path,
        name,
        events=[{"type": "user_message"}, {"type": "agent_result"}],
    )
    detail = weasel_sessions.get_session(name, eventlog_root=tmp_path)
    assert detail.session_id == name
    assert detail.summary["model"] == "claude-sonnet-4-6"
    assert len(detail.events) == 2
    assert detail.events[0]["type"] == "user_message"


def test_get_session_unknown_id_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        weasel_sessions.get_session("weasel-missing", eventlog_root=tmp_path)


def test_get_session_missing_summary_raises(tmp_path) -> None:
    """A directory without summary.json raises FileNotFoundError."""
    bad = tmp_path / "weasel-20260430T100000-aaaaaaaa"
    bad.mkdir()
    with pytest.raises(FileNotFoundError):
        weasel_sessions.get_session(bad.name, eventlog_root=tmp_path)


def test_get_session_skips_invalid_event_files(tmp_path) -> None:
    """Bad event JSON is skipped without crashing the loader."""
    name = "weasel-20260430T100000-aaaaaaaa"
    session_dir = _write_session(tmp_path, name)
    (session_dir / "event-000000-x.json").write_text("garbage")
    (session_dir / "event-000001-y.json").write_text(json.dumps({"ok": True}))
    detail = weasel_sessions.get_session(name, eventlog_root=tmp_path)
    assert len(detail.events) == 1
    assert detail.events[0] == {"ok": True}


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def test_format_session_table_empty() -> None:
    assert weasel_sessions.format_session_table([]) == "(no weasel sessions found)"


def test_format_session_table_renders_rows(tmp_path) -> None:
    name = "weasel-20260430T100000-aaaaaaaa"
    _write_session(tmp_path, name)
    records = list(weasel_sessions.iter_sessions(tmp_path))
    out = weasel_sessions.format_session_table(records)
    assert "STARTED" in out
    assert name[:36] in out
    assert "1 session" in out


def test_format_session_detail_includes_summary_fields(tmp_path) -> None:
    name = "weasel-20260430T100000-aaaaaaaa"
    _write_session(tmp_path, name)
    detail = weasel_sessions.get_session(name, eventlog_root=tmp_path)
    text = weasel_sessions.format_session_detail(detail)
    assert name in text
    assert "claude-sonnet-4-6" in text
    assert "do something" in text


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------


def test_cmd_sessions_list_text(tmp_path) -> None:
    _write_session(tmp_path, "weasel-20260430T100000-aaaaaaaa")
    buf = io.StringIO()
    rc = weasel_sessions.cmd_sessions_list(eventlog_root=tmp_path, out=buf)
    assert rc == 0
    assert "STARTED" in buf.getvalue()


def test_cmd_sessions_list_json(tmp_path) -> None:
    _write_session(tmp_path, "weasel-20260430T100000-aaaaaaaa")
    buf = io.StringIO()
    rc = weasel_sessions.cmd_sessions_list(
        eventlog_root=tmp_path, json_output=True, out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert isinstance(payload, list)
    assert payload[0]["session_id"] == "weasel-20260430T100000-aaaaaaaa"


def test_cmd_sessions_show_missing_id_is_usage_error() -> None:
    err = io.StringIO()
    rc = weasel_sessions.cmd_sessions_show(None, err=err)
    assert rc == 2
    assert "missing session id" in err.getvalue()


def test_cmd_sessions_show_unknown_id(tmp_path) -> None:
    err = io.StringIO()
    rc = weasel_sessions.cmd_sessions_show(
        "weasel-nope", eventlog_root=tmp_path, err=err,
    )
    assert rc == 2
    assert "session not found" in err.getvalue()


def test_cmd_sessions_show_text(tmp_path) -> None:
    name = "weasel-20260430T100000-aaaaaaaa"
    _write_session(tmp_path, name)
    buf = io.StringIO()
    rc = weasel_sessions.cmd_sessions_show(
        name, eventlog_root=tmp_path, out=buf,
    )
    assert rc == 0
    assert name in buf.getvalue()


def test_cmd_sessions_show_json(tmp_path) -> None:
    name = "weasel-20260430T100000-aaaaaaaa"
    _write_session(tmp_path, name)
    buf = io.StringIO()
    rc = weasel_sessions.cmd_sessions_show(
        name, eventlog_root=tmp_path, json_output=True, out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["session_id"] == name


# ---------------------------------------------------------------------------
# dispatch_sessions
# ---------------------------------------------------------------------------


def test_dispatch_sessions_list_default(monkeypatch) -> None:
    """No sub_action defaults to 'list'."""
    captured: dict[str, object] = {}

    def _fake_list(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(weasel_sessions, "cmd_sessions_list", _fake_list)
    args = argparse.Namespace(
        sub_action=None,
        sub_target=None,
        json_output=False,
    )
    rc = weasel_sessions.dispatch_sessions(args)
    assert rc == 0
    assert captured.get("json_output") is False


def test_dispatch_sessions_show_routes_target(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_show(session_id, **kwargs):
        captured["session_id"] = session_id
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(weasel_sessions, "cmd_sessions_show", _fake_show)
    args = argparse.Namespace(
        sub_action="show",
        sub_target="weasel-abc",
        json_output=True,
    )
    rc = weasel_sessions.dispatch_sessions(args)
    assert rc == 0
    assert captured["session_id"] == "weasel-abc"
    assert captured["json_output"] is True


def test_dispatch_sessions_unknown_action_is_usage_error(capsys) -> None:
    args = argparse.Namespace(
        sub_action="bogus",
        sub_target=None,
        json_output=False,
    )
    rc = weasel_sessions.dispatch_sessions(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "unknown action" in captured.err


# ---------------------------------------------------------------------------
# default_eventlog_root
# ---------------------------------------------------------------------------


def test_default_eventlog_root_is_under_home() -> None:
    root = weasel_sessions.default_eventlog_root()
    assert root.parts[-2:] == (".chimera", "eventlog")
