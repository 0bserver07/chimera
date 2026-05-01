"""Tests for ``chimera shrew share`` (agent G7).

Mirrors :mod:`tests.weasel.test_sessions_share` adapted to shrew.
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
    events: list[dict] | None = None,
) -> Path:
    session_dir = root / name
    session_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": name,
        "started_at": "2026-04-30T10:00:00Z",
        "ended_at": "2026-04-30T10:01:00Z",
        "model": "qwen3.6-35b-a3b",
        "prompt": "do a thing",
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
# render helpers
# ---------------------------------------------------------------------------


def test_render_share_json_round_trips(tmp_path) -> None:
    name = "shrew-20260430T100000-aaaaaaaa"
    _write_session(
        tmp_path,
        name,
        events=[{"type": "user_message", "metadata": {"content": "hi"}}],
    )
    detail = shrew_sessions.get_session(name, eventlog_root=tmp_path)
    body = shrew_sessions.render_share_json(detail)
    payload = json.loads(body)
    assert payload["session_id"] == name
    assert payload["summary"]["prompt"] == "do a thing"
    assert payload["events"][0]["type"] == "user_message"


def test_render_share_markdown_includes_summary_and_events(tmp_path) -> None:
    name = "shrew-20260430T100000-aaaaaaaa"
    _write_session(
        tmp_path,
        name,
        events=[
            {"type": "user_message", "metadata": {"content": "hi"}},
            {"type": "agent_result", "metadata": {"output": "done"}},
        ],
    )
    detail = shrew_sessions.get_session(name, eventlog_root=tmp_path)
    body = shrew_sessions.render_share_markdown(detail)
    assert body.startswith("# Shrew session ")
    assert "## Prompt" in body
    assert "do a thing" in body
    assert "## Events (2)" in body
    assert "### `user_message`" in body
    assert "### `agent_result`" in body


def test_render_share_markdown_handles_no_events(tmp_path) -> None:
    name = "shrew-20260430T100000-aaaaaaaa"
    _write_session(tmp_path, name)
    detail = shrew_sessions.get_session(name, eventlog_root=tmp_path)
    body = shrew_sessions.render_share_markdown(detail)
    assert "_(no events recorded)_" in body


def test_render_share_markdown_includes_error_when_present(tmp_path) -> None:
    name = "shrew-20260430T100000-aaaaaaaa"
    _write_session(tmp_path, name, summary={"success": False, "error": "boom"})
    detail = shrew_sessions.get_session(name, eventlog_root=tmp_path)
    body = shrew_sessions.render_share_markdown(detail)
    assert "- error: boom" in body


# ---------------------------------------------------------------------------
# write_share_file
# ---------------------------------------------------------------------------


def test_write_share_file_creates_dir_and_returns_path(tmp_path) -> None:
    shares = tmp_path / "shares"
    path = shrew_sessions.write_share_file(
        "shrew-20260430T100000-aaaaaaaa",
        '{"hello": "world"}\n',
        "json",
        shares_dir=shares,
    )
    assert path.exists()
    assert path.suffix == ".json"
    assert path.read_text(encoding="utf-8") == '{"hello": "world"}\n'


def test_write_share_file_prefixes_bare_id(tmp_path) -> None:
    """Ids without ``shrew-`` get the prefix prepended on disk."""
    path = shrew_sessions.write_share_file(
        "abc123", "body", "md", shares_dir=tmp_path,
    )
    assert path.name == "shrew-abc123.md"


def test_default_shares_dir_under_home() -> None:
    p = shrew_sessions.default_shares_dir()
    assert p.parts[-2:] == (".chimera", "shares")


# ---------------------------------------------------------------------------
# cmd_share — sink + format dispatch
# ---------------------------------------------------------------------------


def test_cmd_share_missing_id_is_usage_error() -> None:
    err = io.StringIO()
    rc = shrew_sessions.cmd_share(None, err=err)
    assert rc == 2
    assert "missing session id" in err.getvalue()


def test_cmd_share_unknown_id_is_usage_error(tmp_path) -> None:
    err = io.StringIO()
    rc = shrew_sessions.cmd_share(
        "shrew-nope", eventlog_root=tmp_path, err=err,
    )
    assert rc == 2
    assert "session not found" in err.getvalue()


def test_cmd_share_unknown_sink_is_usage_error(tmp_path) -> None:
    name = "shrew-20260430T100000-aaaaaaaa"
    _write_session(tmp_path, name)
    err = io.StringIO()
    rc = shrew_sessions.cmd_share(
        name, sink="http", eventlog_root=tmp_path, err=err,
    )
    assert rc == 2
    assert "unknown --sink" in err.getvalue()


def test_cmd_share_unknown_format_is_usage_error(tmp_path) -> None:
    name = "shrew-20260430T100000-aaaaaaaa"
    _write_session(tmp_path, name)
    err = io.StringIO()
    rc = shrew_sessions.cmd_share(
        name, fmt="html", eventlog_root=tmp_path, err=err,
    )
    assert rc == 2
    assert "unknown --format" in err.getvalue()


def test_cmd_share_stdout_json(tmp_path) -> None:
    name = "shrew-20260430T100000-aaaaaaaa"
    _write_session(tmp_path, name, events=[{"type": "user_message"}])
    out = io.StringIO()
    rc = shrew_sessions.cmd_share(
        name,
        sink="stdout",
        fmt="json",
        eventlog_root=tmp_path,
        out=out,
    )
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["session_id"] == name


def test_cmd_share_stdout_markdown(tmp_path) -> None:
    name = "shrew-20260430T100000-aaaaaaaa"
    _write_session(tmp_path, name)
    out = io.StringIO()
    rc = shrew_sessions.cmd_share(
        name,
        sink="stdout",
        fmt="md",
        eventlog_root=tmp_path,
        out=out,
    )
    assert rc == 0
    body = out.getvalue()
    assert body.startswith("# Shrew session ")


def test_cmd_share_file_sink_writes_disk(tmp_path) -> None:
    name = "shrew-20260430T100000-aaaaaaaa"
    _write_session(tmp_path, name)
    shares = tmp_path / "shares"
    out = io.StringIO()
    rc = shrew_sessions.cmd_share(
        name,
        sink="file",
        fmt="json",
        eventlog_root=tmp_path,
        shares_dir=shares,
        out=out,
    )
    assert rc == 0
    written_path = Path(out.getvalue().strip())
    assert written_path.exists()
    payload = json.loads(written_path.read_text(encoding="utf-8"))
    assert payload["session_id"] == name


def test_cmd_share_file_sink_md_extension(tmp_path) -> None:
    name = "shrew-20260430T100000-aaaaaaaa"
    _write_session(tmp_path, name)
    shares = tmp_path / "shares"
    out = io.StringIO()
    rc = shrew_sessions.cmd_share(
        name,
        sink="file",
        fmt="md",
        eventlog_root=tmp_path,
        shares_dir=shares,
        out=out,
    )
    assert rc == 0
    written_path = Path(out.getvalue().strip())
    assert written_path.suffix == ".md"


# ---------------------------------------------------------------------------
# dispatch_share
# ---------------------------------------------------------------------------


def test_dispatch_share_reads_id_from_sub_action(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake(session_id, **kwargs):
        captured["session_id"] = session_id
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(shrew_sessions, "cmd_share", _fake)
    args = argparse.Namespace(
        sub_action="shrew-abc",
        sub_target=None,
        share_sink=None,
        share_format=None,
    )
    rc = shrew_sessions.dispatch_share(args)
    assert rc == 0
    assert captured["session_id"] == "shrew-abc"
    assert captured["sink"] == "file"
    assert captured["fmt"] == "json"


def test_dispatch_share_passes_flags(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake(session_id, **kwargs):
        captured["session_id"] = session_id
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(shrew_sessions, "cmd_share", _fake)
    args = argparse.Namespace(
        sub_action="shrew-abc",
        sub_target=None,
        share_sink="stdout",
        share_format="md",
    )
    rc = shrew_sessions.dispatch_share(args)
    assert rc == 0
    assert captured["sink"] == "stdout"
    assert captured["fmt"] == "md"


def test_dispatch_share_falls_back_to_sub_target(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake(session_id, **kwargs):
        captured["session_id"] = session_id
        return 0

    monkeypatch.setattr(shrew_sessions, "cmd_share", _fake)
    args = argparse.Namespace(
        sub_action=None,
        sub_target="shrew-fallback",
        share_sink=None,
        share_format=None,
    )
    rc = shrew_sessions.dispatch_share(args)
    assert rc == 0
    assert captured["session_id"] == "shrew-fallback"
