"""Tests for export_jsonl / replay_from_jsonl round-trip."""
from __future__ import annotations

import json
from pathlib import Path

from chimera.events.sourcing import (
    SqliteEventStore,
    ToolCalledEvent,
    ToolCompletedEvent,
    UserMessageEvent,
    export_jsonl,
    replay_from_jsonl,
)


def _seed(store: SqliteEventStore) -> None:
    store.append("s1", UserMessageEvent(session_id="s1", content="hi"))
    store.append("s1", ToolCalledEvent(
        session_id="s1", call_id="c1", tool_name="bash", arguments={"cmd": "ls"},
    ))
    store.append("s1", ToolCompletedEvent(
        session_id="s1", call_id="c1", tool_name="bash",
        success=True, output="a\nb\n",
    ))


def test_export_writes_one_line_per_event(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "src.db")
    _seed(store)
    out = tmp_path / "log.jsonl"
    n = export_jsonl(store, "s1", out)
    assert n == 3
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["wire_id"] == "user.message.1"
    assert first["seq"] == 1
    assert first["payload"]["content"] == "hi"


def test_round_trip_preserves_events(tmp_path: Path) -> None:
    src = SqliteEventStore(tmp_path / "src.db")
    _seed(src)
    out = tmp_path / "log.jsonl"
    export_jsonl(src, "s1", out)

    dst = SqliteEventStore(tmp_path / "dst.db")
    n = replay_from_jsonl(out, dst)
    assert n == 3

    src_events = list(src.read_since("s1"))
    dst_events = list(dst.read_since("s1"))
    assert len(src_events) == len(dst_events)
    for s, d in zip(src_events, dst_events):
        assert s.name == d.name
        assert s.version == d.version
        assert type(s.payload) is type(d.payload)
        # Compare by serializing — payloads are dataclasses.
        from dataclasses import asdict
        assert asdict(s.payload) == asdict(d.payload)


def test_replay_idempotent_skip(tmp_path: Path) -> None:
    src = SqliteEventStore(tmp_path / "src.db")
    _seed(src)
    out = tmp_path / "log.jsonl"
    export_jsonl(src, "s1", out)

    dst = SqliteEventStore(tmp_path / "dst.db")
    replay_from_jsonl(out, dst)
    # Importing again should append zero new events.
    n2 = replay_from_jsonl(out, dst)
    assert n2 == 0
    assert dst.last_seq("s1") == 3


def test_export_from_offset(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "src.db")
    _seed(store)
    out = tmp_path / "tail.jsonl"
    n = export_jsonl(store, "s1", out, from_seq=1)
    assert n == 2
    seqs = [json.loads(l)["seq"] for l in out.read_text().splitlines()]
    assert seqs == [2, 3]
