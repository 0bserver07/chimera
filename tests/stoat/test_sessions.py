"""Tests for ``chimera stoat sessions`` and ``chimera stoat share``.

Tests construct fixture session directories under a temp eventlog root
to verify list / show / cost / share end-to-end without touching live
state under ``~/.chimera/eventlog/``.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from chimera.stoat import sessions as stoat_sessions


def _fixture_session(
    root: Path,
    session_id: str,
    *,
    success: bool = True,
    cost_usd: float = 0.123,
    steps: int = 4,
    started_at: str = "2026-04-30T10:15:01Z",
    model: str = "kimi-k2.6",
    prompt: str = "ping",
) -> Path:
    """Materialise a minimal ``summary.json`` for ``session_id`` under ``root``."""
    session_dir = root / session_id
    session_dir.mkdir(parents=True)
    summary = {
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": "2026-04-30T10:15:30Z",
        "model": model,
        "prompt": prompt,
        "success": success,
        "cost_usd": cost_usd,
        "steps": steps,
        "tool_calls_total": 2,
    }
    (session_dir / "summary.json").write_text(json.dumps(summary))
    # One placeholder event so the share-md path exercises the loop.
    event = {"type": "tool_call", "metadata": {"name": "Read"}}
    (session_dir / "event-000001-tool_call.json").write_text(json.dumps(event))
    return session_dir


# ---------------------------------------------------------------------------
# iter_sessions / get_session
# ---------------------------------------------------------------------------


def test_iter_sessions_empty_root(tmp_path) -> None:
    """An empty eventlog root yields nothing without crashing."""
    assert list(stoat_sessions.iter_sessions(tmp_path)) == []


def test_iter_sessions_filters_to_stoat_prefix(tmp_path) -> None:
    """Only ``stoat-`` prefixed dirs are surfaced."""
    _fixture_session(tmp_path, "stoat-20260430T100000-aaa")
    _fixture_session(tmp_path, "weasel-20260430T100000-bbb")
    records = list(stoat_sessions.iter_sessions(tmp_path))
    assert len(records) == 1
    assert records[0].session_id == "stoat-20260430T100000-aaa"


def test_iter_sessions_orders_newest_first(tmp_path) -> None:
    """Records sort newest-first by directory name."""
    _fixture_session(tmp_path, "stoat-20260430T100000-old")
    _fixture_session(tmp_path, "stoat-20260430T200000-new")
    records = list(stoat_sessions.iter_sessions(tmp_path))
    ids = [r.session_id for r in records]
    assert ids[0].endswith("new")
    assert ids[1].endswith("old")


def test_get_session_loads_events(tmp_path) -> None:
    """``get_session`` returns the summary plus all ``event-*.json``."""
    _fixture_session(tmp_path, "stoat-20260430T100000-evt")
    detail = stoat_sessions.get_session(
        "stoat-20260430T100000-evt", eventlog_root=tmp_path,
    )
    assert detail.summary["model"] == "kimi-k2.6"
    assert len(detail.events) == 1
    assert detail.events[0]["type"] == "tool_call"


def test_get_session_missing_raises(tmp_path) -> None:
    """A missing session id raises ``FileNotFoundError``."""
    with pytest.raises(FileNotFoundError):
        stoat_sessions.get_session("nope", eventlog_root=tmp_path)


# ---------------------------------------------------------------------------
# cmd_sessions_list
# ---------------------------------------------------------------------------


def test_cmd_sessions_list_text(tmp_path) -> None:
    """``sessions list`` renders a fixed-width table."""
    _fixture_session(tmp_path, "stoat-20260430T100000-tbl")
    out = io.StringIO()
    rc = stoat_sessions.cmd_sessions_list(eventlog_root=tmp_path, out=out)
    assert rc == 0
    text = out.getvalue()
    assert "STARTED" in text
    assert "stoat-20260430T100000-tbl" in text


def test_cmd_sessions_list_json(tmp_path) -> None:
    """``sessions list --json`` emits a JSON array."""
    _fixture_session(tmp_path, "stoat-20260430T100000-jsn")
    out = io.StringIO()
    rc = stoat_sessions.cmd_sessions_list(
        eventlog_root=tmp_path, json_output=True, out=out,
    )
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert isinstance(payload, list)
    assert payload[0]["session_id"] == "stoat-20260430T100000-jsn"


def test_cmd_sessions_list_empty(tmp_path) -> None:
    """An empty root yields the placeholder line."""
    out = io.StringIO()
    rc = stoat_sessions.cmd_sessions_list(eventlog_root=tmp_path, out=out)
    assert rc == 0
    assert "no stoat sessions found" in out.getvalue()


# ---------------------------------------------------------------------------
# cmd_sessions_show
# ---------------------------------------------------------------------------


def test_cmd_sessions_show_text(tmp_path) -> None:
    """``sessions show <id>`` renders a human-readable transcript."""
    _fixture_session(tmp_path, "stoat-20260430T100000-shw")
    out = io.StringIO()
    rc = stoat_sessions.cmd_sessions_show(
        "stoat-20260430T100000-shw",
        eventlog_root=tmp_path,
        out=out,
    )
    assert rc == 0
    text = out.getvalue()
    assert "stoat-20260430T100000-shw" in text
    assert "model    kimi-k2.6" in text


def test_cmd_sessions_show_missing_id() -> None:
    """An empty session id is a usage error (rc=2)."""
    err = io.StringIO()
    rc = stoat_sessions.cmd_sessions_show(None, err=err)
    assert rc == 2
    assert "missing session id" in err.getvalue()


def test_cmd_sessions_show_json(tmp_path) -> None:
    """``--json`` round-trips through ``json.loads``."""
    _fixture_session(tmp_path, "stoat-20260430T100000-jsh")
    out = io.StringIO()
    rc = stoat_sessions.cmd_sessions_show(
        "stoat-20260430T100000-jsh",
        eventlog_root=tmp_path,
        json_output=True,
        out=out,
    )
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["session_id"] == "stoat-20260430T100000-jsh"
    assert payload["summary"]["model"] == "kimi-k2.6"


# ---------------------------------------------------------------------------
# cmd_sessions_cost
# ---------------------------------------------------------------------------


def test_cmd_sessions_cost_text(tmp_path) -> None:
    """``sessions cost`` produces a non-empty rollup."""
    _fixture_session(tmp_path, "stoat-20260430T100000-cst", cost_usd=0.5)
    out = io.StringIO()
    rc = stoat_sessions.cmd_sessions_cost(
        eventlog_root=tmp_path, out=out, use_rich=False,
    )
    assert rc == 0
    body = out.getvalue()
    assert body  # rollup body present


def test_cmd_sessions_cost_json(tmp_path) -> None:
    """``--cost-format json`` emits a JSON document."""
    _fixture_session(tmp_path, "stoat-20260430T100000-cj1", cost_usd=0.5)
    _fixture_session(tmp_path, "stoat-20260430T200000-cj2", cost_usd=1.25)
    out = io.StringIO()
    rc = stoat_sessions.cmd_sessions_cost(
        eventlog_root=tmp_path, fmt="json", out=out,
    )
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert isinstance(payload, dict)


def test_cmd_sessions_cost_unknown_format(tmp_path) -> None:
    """An unknown ``fmt`` is a usage error."""
    err = io.StringIO()
    out = io.StringIO()
    rc = stoat_sessions.cmd_sessions_cost(
        eventlog_root=tmp_path, fmt="bogus", out=out, err=err,
    )
    assert rc == 2
    assert "unknown --format" in err.getvalue()


# ---------------------------------------------------------------------------
# share
# ---------------------------------------------------------------------------


def test_cmd_share_writes_json_file(tmp_path) -> None:
    """``share <id>`` writes the JSON transcript to the shares dir."""
    eventlog = tmp_path / "eventlog"
    eventlog.mkdir()
    _fixture_session(eventlog, "stoat-20260430T100000-shr")
    shares = tmp_path / "shares"
    out = io.StringIO()
    rc = stoat_sessions.cmd_share(
        "stoat-20260430T100000-shr",
        eventlog_root=eventlog,
        shares_dir=shares,
        sink="file",
        fmt="json",
        out=out,
    )
    assert rc == 0
    written_path = Path(out.getvalue().strip())
    assert written_path.exists()
    payload = json.loads(written_path.read_text())
    assert payload["session_id"] == "stoat-20260430T100000-shr"


def test_cmd_share_stdout_md(tmp_path) -> None:
    """``share --sink stdout --format md`` emits markdown."""
    _fixture_session(tmp_path, "stoat-20260430T100000-mdt")
    out = io.StringIO()
    rc = stoat_sessions.cmd_share(
        "stoat-20260430T100000-mdt",
        eventlog_root=tmp_path,
        sink="stdout",
        fmt="md",
        out=out,
    )
    assert rc == 0
    text = out.getvalue()
    assert "# Stoat session `stoat-20260430T100000-mdt`" in text
    assert "## Prompt" in text


def test_cmd_share_missing_id() -> None:
    """An empty session id is a usage error."""
    err = io.StringIO()
    rc = stoat_sessions.cmd_share(None, err=err)
    assert rc == 2
    assert "missing session id" in err.getvalue()


def test_cmd_share_unknown_sink() -> None:
    """An unknown sink is a usage error."""
    err = io.StringIO()
    rc = stoat_sessions.cmd_share(
        "stoat-x", sink="http", err=err,
    )
    assert rc == 2
    assert "unknown --sink" in err.getvalue()


def test_dispatch_sessions_routes_list(tmp_path, monkeypatch) -> None:
    """``dispatch_sessions`` with action='list' calls ``cmd_sessions_list``."""
    import argparse

    monkeypatch.setattr(
        stoat_sessions, "default_eventlog_root", lambda: tmp_path,
    )
    args = argparse.Namespace(sub_action="list", sub_target=None)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = stoat_sessions.dispatch_sessions(args)
    assert rc == 0


def test_dispatch_sessions_unknown_action() -> None:
    """An unknown action surfaces a usage error."""
    import argparse

    args = argparse.Namespace(sub_action="bogus", sub_target=None)
    rc = stoat_sessions.dispatch_sessions(args)
    assert rc == 2
