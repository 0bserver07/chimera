"""Tests for ``chimera shrew sessions cost`` (agent G7).

Mirrors :mod:`tests.weasel.test_sessions_cost` adapted to shrew.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

from chimera.shrew import sessions as shrew_sessions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_session(
    root: Path,
    name: str,
    *,
    summary: dict | None = None,
) -> Path:
    """Create a session directory under ``root`` with a ``summary.json``."""
    session_dir = root / name
    session_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": name,
        "started_at": "2026-04-30T10:00:00Z",
        "ended_at": "2026-04-30T10:01:00Z",
        "model": "qwen3.6-35b-a3b",
        "prompt": "do something",
        "success": True,
        "cost_usd": 0.01,
        "steps": 3,
        "tool_calls_total": 2,
        "total_tokens": 1000,
    }
    if summary:
        payload.update(summary)
    (session_dir / "summary.json").write_text(json.dumps(payload))
    return session_dir


# ---------------------------------------------------------------------------
# iter_run_records — adapter that feeds compute_summary
# ---------------------------------------------------------------------------


def test_iter_run_records_yields_only_shrew_dirs(tmp_path) -> None:
    """``iter_run_records`` skips otter/mink/weasel directories."""
    _write_session(tmp_path, "shrew-20260430T100000-aaaaaaaa")
    _write_session(tmp_path, "otter-20260430T100000-bbbbbbbb")
    _write_session(tmp_path, "weasel-20260430T100000-cccccccc")
    _write_session(tmp_path, "mink-20260430T100000-dddddddd")
    records = list(shrew_sessions.iter_run_records(tmp_path))
    ids = [r.run_id for r in records]
    assert ids == ["shrew-20260430T100000-aaaaaaaa"]


def test_iter_run_records_handles_missing_root(tmp_path) -> None:
    missing = tmp_path / "no-such-dir"
    assert list(shrew_sessions.iter_run_records(missing)) == []


def test_iter_run_records_skips_invalid_summary(tmp_path) -> None:
    bad = tmp_path / "shrew-20260430T100000-aaaaaaaa"
    bad.mkdir()
    (bad / "summary.json").write_text("not json {{")
    _write_session(tmp_path, "shrew-20260430T110000-bbbbbbbb")
    records = list(shrew_sessions.iter_run_records(tmp_path))
    assert len(records) == 1
    assert records[0].run_id == "shrew-20260430T110000-bbbbbbbb"


def test_iter_run_records_orders_newest_first(tmp_path) -> None:
    _write_session(tmp_path, "shrew-20260430T100000-aaaaaaaa")
    _write_session(tmp_path, "shrew-20260430T120000-cccccccc")
    _write_session(tmp_path, "shrew-20260430T110000-bbbbbbbb")
    records = list(shrew_sessions.iter_run_records(tmp_path))
    assert [r.run_id for r in records] == [
        "shrew-20260430T120000-cccccccc",
        "shrew-20260430T110000-bbbbbbbb",
        "shrew-20260430T100000-aaaaaaaa",
    ]


# ---------------------------------------------------------------------------
# cmd_sessions_cost — text/json/csv format dispatch
# ---------------------------------------------------------------------------


def test_cmd_sessions_cost_text(tmp_path) -> None:
    _write_session(
        tmp_path,
        "shrew-20260430T100000-aaaaaaaa",
        summary={"cost_usd": 0.05, "total_tokens": 2000},
    )
    out = io.StringIO()
    rc = shrew_sessions.cmd_sessions_cost(
        eventlog_root=tmp_path, fmt="text", use_rich=False, out=out,
    )
    assert rc == 0
    text = out.getvalue()
    assert "runs:" in text
    assert "$0.0500" in text
    assert "2000" in text


def test_cmd_sessions_cost_json(tmp_path) -> None:
    _write_session(
        tmp_path,
        "shrew-20260430T100000-aaaaaaaa",
        summary={"cost_usd": 0.05, "total_tokens": 2000},
    )
    out = io.StringIO()
    rc = shrew_sessions.cmd_sessions_cost(
        eventlog_root=tmp_path, fmt="json", out=out,
    )
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["totals"]["runs"] == 1
    assert payload["totals"]["cost_usd"] == 0.05
    assert payload["rows"][0]["run_id"] == "shrew-20260430T100000-aaaaaaaa"


def test_cmd_sessions_cost_csv(tmp_path) -> None:
    _write_session(
        tmp_path,
        "shrew-20260430T100000-aaaaaaaa",
        summary={"cost_usd": 0.05},
    )
    out = io.StringIO()
    rc = shrew_sessions.cmd_sessions_cost(
        eventlog_root=tmp_path, fmt="csv", out=out,
    )
    assert rc == 0
    body = out.getvalue()
    assert body.splitlines()[0].startswith("run_id,started_at,model")
    assert "shrew-20260430T100000-aaaaaaaa" in body


def test_cmd_sessions_cost_unknown_format_is_usage_error(tmp_path) -> None:
    err = io.StringIO()
    rc = shrew_sessions.cmd_sessions_cost(
        eventlog_root=tmp_path, fmt="yaml", err=err,
    )
    assert rc == 2
    assert "unknown --format" in err.getvalue()


def test_cmd_sessions_cost_invalid_since_is_usage_error(tmp_path) -> None:
    err = io.StringIO()
    rc = shrew_sessions.cmd_sessions_cost(
        eventlog_root=tmp_path, since="not-a-date", err=err,
    )
    assert rc == 2
    assert "not-a-date" in err.getvalue() or "since" in err.getvalue().lower()


def test_cmd_sessions_cost_empty_root_returns_zero(tmp_path) -> None:
    out = io.StringIO()
    rc = shrew_sessions.cmd_sessions_cost(
        eventlog_root=tmp_path, fmt="json", out=out,
    )
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["totals"]["runs"] == 0
    assert payload["totals"]["cost_usd"] == 0.0


def test_cmd_sessions_cost_model_filter(tmp_path) -> None:
    _write_session(
        tmp_path,
        "shrew-20260430T100000-aaaaaaaa",
        summary={"model": "qwen3.6-35b-a3b", "cost_usd": 0.02},
    )
    _write_session(
        tmp_path,
        "shrew-20260430T110000-bbbbbbbb",
        summary={"model": "claude-haiku-4-5", "cost_usd": 0.03},
    )
    out = io.StringIO()
    rc = shrew_sessions.cmd_sessions_cost(
        eventlog_root=tmp_path, model="qwen", fmt="json", out=out,
    )
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["totals"]["runs"] == 1
    assert payload["filters"]["model"] == "qwen"
    assert payload["totals"]["cost_usd"] == 0.02


def test_cmd_sessions_cost_limit_caps_rows(tmp_path) -> None:
    for i in range(3):
        _write_session(
            tmp_path,
            f"shrew-20260430T1{i}0000-{'a' * 8}",
            summary={"cost_usd": 0.01 * (i + 1)},
        )
    out = io.StringIO()
    rc = shrew_sessions.cmd_sessions_cost(
        eventlog_root=tmp_path, limit=2, fmt="json", out=out,
    )
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["totals"]["runs"] == 2


# ---------------------------------------------------------------------------
# dispatch_sessions("cost") — argparse plumbing
# ---------------------------------------------------------------------------


def test_dispatch_sessions_cost_default_format_text(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(shrew_sessions, "cmd_sessions_cost", _fake)
    args = argparse.Namespace(
        sub_action="cost",
        sub_target=None,
        json_output=False,
        cost_since=None,
        cost_model=None,
        cost_format=None,
        cost_limit=None,
    )
    rc = shrew_sessions.dispatch_sessions(args)
    assert rc == 0
    assert captured["fmt"] == "text"


def test_dispatch_sessions_cost_json_flag_promotes_format(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(shrew_sessions, "cmd_sessions_cost", _fake)
    args = argparse.Namespace(
        sub_action="cost",
        sub_target=None,
        json_output=True,
        cost_since=None,
        cost_model=None,
        cost_format=None,
        cost_limit=None,
    )
    rc = shrew_sessions.dispatch_sessions(args)
    assert rc == 0
    assert captured["fmt"] == "json"


def test_dispatch_sessions_cost_explicit_format_wins(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(shrew_sessions, "cmd_sessions_cost", _fake)
    args = argparse.Namespace(
        sub_action="cost",
        sub_target=None,
        json_output=True,
        cost_since="7d",
        cost_model="qwen",
        cost_format="csv",
        cost_limit=10,
    )
    rc = shrew_sessions.dispatch_sessions(args)
    assert rc == 0
    assert captured["fmt"] == "csv"
    assert captured["since"] == "7d"
    assert captured["model"] == "qwen"
    assert captured["limit"] == 10


def test_dispatch_sessions_unknown_action_message_lists_cost(capsys) -> None:
    args = argparse.Namespace(
        sub_action="bogus",
        sub_target=None,
        json_output=False,
    )
    rc = shrew_sessions.dispatch_sessions(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "list, show, cost" in captured.err
