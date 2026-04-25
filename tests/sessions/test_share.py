"""Round-trip tests for :mod:`chimera.sessions.share` (issue #129).

Covers the file and base64 sinks end-to-end (export → import → identical
events) plus argument validation. Also pins the ``chimera mink runs
share <id> --sink file`` CLI surface so the dispatcher contract holds.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from chimera.sessions import share as share_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_eventlog(root: Path, run_id: str) -> Path:
    """Create a 3-event session dir under ``root``; return its path."""
    session_dir = root / run_id
    session_dir.mkdir(parents=True)
    summary = {
        "run_id": run_id,
        "started_at": "2026-04-23T12:00:00Z",
        "ended_at": "2026-04-23T12:00:05Z",
        "model": "glm-5.1:cloud",
        "prompt": "share me",
        "cwd": "/tmp",
        "permission_mode": "default",
        "steps": 1,
        "tool_calls_total": 1,
        "success": True,
        "cost_usd": 0.0042,
        "total_tokens": 123,
        "error": None,
    }
    (session_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8",
    )
    events = [
        {
            "idx": 0,
            "event_id": "aaaaaaaa",
            "type": "user_message",
            "timestamp": 1.0,
            "metadata": {"content": "share me", "event_id": "aaaaaaaa"},
        },
        {
            "idx": 1,
            "event_id": "bbbbbbbb",
            "type": "tool_call",
            "timestamp": 2.0,
            "metadata": {"tool": "read", "args": {"path": "/etc/hosts"}},
        },
        {
            "idx": 2,
            "event_id": "cccccccc",
            "type": "agent_result",
            "timestamp": 3.0,
            "metadata": {"output": "ok", "steps": 1, "success": True},
        },
    ]
    for ev in events:
        fname = f"event-{int(ev['idx']):06d}-{ev['event_id']}.json"
        (session_dir / fname).write_text(json.dumps(ev), encoding="utf-8")
    return session_dir


def _read_session(session_dir: Path) -> tuple[dict, list[dict]]:
    """Return ``(summary, events_sorted_by_idx)`` from a session dir."""
    summary = json.loads((session_dir / "summary.json").read_text(encoding="utf-8"))
    events: list[dict] = []
    for ev_path in sorted(session_dir.glob("event-*.json")):
        events.append(json.loads(ev_path.read_text(encoding="utf-8")))
    events.sort(key=lambda e: e.get("idx", 0))
    return summary, events


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


def test_round_trip_file_sink(tmp_path: Path) -> None:
    """File sink: export to disk, import to a fresh root, all bytes match."""
    src_root = tmp_path / "src-eventlog"
    src_root.mkdir()
    run_id = "mink-roundtrip-file-aaaa1111"
    src_dir = _seed_eventlog(src_root, run_id)
    src_summary, src_events = _read_session(src_dir)

    # Override the file-sink output dir at ~/.chimera/exports/ via HOME.
    home = tmp_path / "home"
    home.mkdir()
    monkey_home = home
    # WHY: we don't have monkeypatch as a fixture arg here, so use the
    # module's helper directly by pointing _default_export_dir at HOME.
    # We patch by temporarily overriding Path.home via the share module.
    original_default_export = share_mod._default_export_dir
    original_default_eventlog = share_mod._default_eventlog_root
    share_mod._default_export_dir = lambda: monkey_home / ".chimera" / "exports"
    share_mod._default_eventlog_root = lambda: src_root
    try:
        path = share_mod.export_to_url(run_id, sink="file")
    finally:
        share_mod._default_export_dir = original_default_export
        share_mod._default_eventlog_root = original_default_eventlog

    out_path = Path(path)
    assert out_path.is_file(), f"expected tarball at {out_path}"
    assert out_path.suffix == ".gz"
    assert out_path.name == f"{run_id}.tar.gz"

    target_root = tmp_path / "target-eventlog"
    imported_id = share_mod.import_from_url(out_path, target_eventlog_root=target_root)
    assert imported_id == run_id

    dst_summary, dst_events = _read_session(target_root / run_id)
    assert dst_summary == src_summary
    assert dst_events == src_events


def test_round_trip_base64_sink(tmp_path: Path) -> None:
    """Base64 sink: data URI parses + import recovers identical events."""
    src_root = tmp_path / "src-eventlog"
    src_root.mkdir()
    run_id = "mink-roundtrip-b64-bbbb2222"
    src_dir = _seed_eventlog(src_root, run_id)
    src_summary, src_events = _read_session(src_dir)

    uri = share_mod.export_to_url(run_id, sink="base64", eventlog_root=src_root)
    assert uri.startswith(share_mod.DATA_URI_PREFIX)
    # Body must be valid base64 (no whitespace, finite length).
    body = uri[len(share_mod.DATA_URI_PREFIX):]
    assert body and "\n" not in body

    target_root = tmp_path / "target-eventlog"
    imported_id = share_mod.import_from_url(uri, target_eventlog_root=target_root)
    assert imported_id == run_id

    dst_summary, dst_events = _read_session(target_root / run_id)
    assert dst_summary == src_summary
    assert dst_events == src_events


def test_unknown_sink_raises(tmp_path: Path) -> None:
    """Sink validation rejects anything outside the allow-list."""
    src_root = tmp_path / "src-eventlog"
    src_root.mkdir()
    run_id = "mink-validate-sink-cccc3333"
    _seed_eventlog(src_root, run_id)

    with pytest.raises(ValueError, match="unknown sink"):
        share_mod.export_to_url(run_id, sink="rocketship", eventlog_root=src_root)


def test_share_subcommand_writes_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``mink runs share <id> --sink file`` prints the absolute tarball path.

    Drives ``_dispatch_runs`` directly with a synthetic Namespace so we
    don't need to invoke argparse end-to-end (covered separately).
    """
    pytest.importorskip("rich")  # mink CLI imports rich at module import.
    from chimera.mink import cli as mink_cli

    home = tmp_path / "home"
    home.mkdir()
    eventlog_root = home / ".chimera" / "eventlog"
    eventlog_root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    run_id = "mink-cli-share-dddd4444"
    _seed_eventlog(eventlog_root, run_id)

    args = argparse.Namespace(
        runs_command="runs",
        runs_action="share",
        runs_target=run_id,
        runs_share_sink="file",
        full=False,
        runs_limit=20,
        runs_filter_model=None,
        runs_success_only=False,
        runs_failed_only=False,
        runs_show_events=True,
        no_color=True,
        no_rich=False,
    )
    rc = mink_cli._dispatch_runs(args)
    assert rc == 0, capsys.readouterr().err
    out = capsys.readouterr().out.strip()
    expected = home / ".chimera" / "exports" / f"{run_id}.tar.gz"
    assert out == str(expected.resolve()), f"unexpected stdout: {out!r}"
    assert expected.is_file()
