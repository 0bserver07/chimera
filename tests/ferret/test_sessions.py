"""``chimera ferret sessions list/show`` — fixture-driven regression tests.

Mirrors ``tests/otter/test_sessions.py``. We materialize fake session
directories under ``tmp_path`` (via a ``Path.home`` monkeypatch so the
production helper :func:`default_eventlog_root` resolves to the
fixture), then exercise the public ``cmd_sessions_*`` and
``dispatch_sessions`` entry points.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from chimera.ferret import sessions as sessions_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_eventlog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` so ``~/.chimera/eventlog`` resolves under tmp."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    eventlog = home / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True)
    return eventlog


def _make_session(
    root: Path,
    session_id: str,
    *,
    started_at: str,
    model: str = "stub-model",
    prompt: str = "do a thing",
    success: bool = True,
    cost_usd: float = 0.001,
    steps: int = 1,
    tool_calls: int = 0,
    error: str | None = None,
    with_events: bool = True,
    extra_events: list[dict[str, Any]] | None = None,
) -> Path:
    """Materialize one fake ferret session dir (summary.json + events)."""
    session_dir = root / session_id
    session_dir.mkdir()
    summary = {
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": started_at,
        "model": model,
        "prompt": prompt,
        "cwd": "/tmp",
        "sandbox": "read-only",
        "approval": "read-only",
        "steps": steps,
        "tool_calls_total": tool_calls,
        "success": success,
        "cost_usd": cost_usd,
        "total_tokens": 0,
        "error": error,
    }
    (session_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8",
    )
    if with_events:
        events: list[dict[str, Any]] = extra_events or [
            {
                "idx": 0,
                "event_id": "aaaaaaaa",
                "type": "user_message",
                "timestamp": 1.0,
                "metadata": {"content": prompt, "event_id": "aaaaaaaa"},
            },
            {
                "idx": 1,
                "event_id": "bbbbbbbb",
                "type": "agent_result",
                "timestamp": 2.0,
                "metadata": {
                    "output": "all done",
                    "steps": steps,
                    "success": success,
                    "cost": cost_usd,
                },
            },
        ]
        for i, ev in enumerate(events):
            event_id = ev.get("event_id") or f"{i:08x}"
            (session_dir / f"event-{i:06d}-{event_id}.json").write_text(
                json.dumps(ev), encoding="utf-8",
            )
    return session_dir


def _list_args(
    *,
    sessions_command: str | None = "sessions",
    sessions_action: str | None = "list",
    sessions_target: str | None = None,
    sessions_limit: int = 20,
    sessions_model: str | None = None,
    sessions_since: str | None = None,
    sessions_json: bool = False,
    full: bool = True,
    no_color: bool = True,
) -> argparse.Namespace:
    """Build a Namespace mirroring what argparse would produce."""
    return argparse.Namespace(
        sessions_command=sessions_command,
        sessions_action=sessions_action,
        sessions_target=sessions_target,
        sessions_limit=sessions_limit,
        sessions_model=sessions_model,
        sessions_since=sessions_since,
        sessions_json=sessions_json,
        full=full,
        no_color=no_color,
    )


# ---------------------------------------------------------------------------
# iter_sessions / get_session direct exercise
# ---------------------------------------------------------------------------


def test_iter_sessions_yields_newest_first(fake_eventlog: Path) -> None:
    _make_session(
        fake_eventlog,
        "ferret-20260430T010000-aaaa1111",
        started_at="2026-04-30T01:00:00Z",
    )
    _make_session(
        fake_eventlog,
        "ferret-20260430T020000-bbbb2222",
        started_at="2026-04-30T02:00:00Z",
    )
    _make_session(
        fake_eventlog,
        "ferret-20260430T030000-cccc3333",
        started_at="2026-04-30T03:00:00Z",
    )

    ids = [r.session_id for r in sessions_mod.iter_sessions()]
    assert ids == [
        "ferret-20260430T030000-cccc3333",
        "ferret-20260430T020000-bbbb2222",
        "ferret-20260430T010000-aaaa1111",
    ]


def test_iter_sessions_skips_dirs_without_summary(fake_eventlog: Path) -> None:
    """An aborted session with no summary.json must be skipped, not raise."""
    (fake_eventlog / "ferret-empty-dir-xxxxxxxx").mkdir()
    _make_session(
        fake_eventlog,
        "ferret-20260430T010000-aaaa1111",
        started_at="2026-04-30T01:00:00Z",
    )

    records = list(sessions_mod.iter_sessions())
    assert len(records) == 1
    assert records[0].session_id == "ferret-20260430T010000-aaaa1111"


def test_iter_sessions_skips_non_ferret_prefix(fake_eventlog: Path) -> None:
    """Otter dirs in the same eventlog root are not picked up by ferret."""
    _make_session(
        fake_eventlog,
        "ferret-20260430T020000-bbbb2222",
        started_at="2026-04-30T02:00:00Z",
    )
    _make_session(
        fake_eventlog,
        "otter-20260430T010000-aaaa1111",
        started_at="2026-04-30T01:00:00Z",
    )

    ids = [r.session_id for r in sessions_mod.iter_sessions()]
    assert ids == ["ferret-20260430T020000-bbbb2222"]


def test_iter_sessions_missing_root_returns_empty(tmp_path: Path) -> None:
    """When ``~/.chimera/eventlog`` does not exist, return zero sessions."""
    missing = tmp_path / "absent-eventlog"
    assert not missing.exists()
    assert list(sessions_mod.iter_sessions(missing)) == []


def test_get_session_loads_summary_and_events(fake_eventlog: Path) -> None:
    _make_session(
        fake_eventlog,
        "ferret-20260430T010000-aaaa1111",
        started_at="2026-04-30T01:00:00Z",
        prompt="test prompt",
    )

    detail = sessions_mod.get_session("ferret-20260430T010000-aaaa1111")
    assert detail.summary["prompt"] == "test prompt"
    assert len(detail.events) == 2
    assert detail.events[0]["type"] == "user_message"
    assert detail.events[1]["type"] == "agent_result"


def test_get_session_missing_id_raises(fake_eventlog: Path) -> None:
    with pytest.raises(FileNotFoundError):
        sessions_mod.get_session("ferret-does-not-exist")


# ---------------------------------------------------------------------------
# parse_since
# ---------------------------------------------------------------------------


def test_parse_since_relative_days() -> None:
    now = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
    cutoff = sessions_mod.parse_since("7d", now=now)
    assert cutoff == now - timedelta(days=7)


def test_parse_since_relative_hours_minutes() -> None:
    now = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
    assert sessions_mod.parse_since("3h", now=now) == now - timedelta(hours=3)
    assert sessions_mod.parse_since("90m", now=now) == now - timedelta(minutes=90)


def test_parse_since_iso_date_assumes_utc() -> None:
    cutoff = sessions_mod.parse_since("2026-04-01")
    assert cutoff.tzinfo is not None
    assert cutoff.year == 2026 and cutoff.month == 4 and cutoff.day == 1


def test_parse_since_invalid_raises() -> None:
    with pytest.raises(ValueError):
        sessions_mod.parse_since("nonsense")


# ---------------------------------------------------------------------------
# format_session_table / format_session_detail
# ---------------------------------------------------------------------------


def test_format_session_table_lists_all_records(fake_eventlog: Path) -> None:
    _make_session(
        fake_eventlog,
        "ferret-20260430T010000-aaaa1111",
        started_at="2026-04-30T01:00:00Z",
        prompt="alpha",
    )
    _make_session(
        fake_eventlog,
        "ferret-20260430T020000-bbbb2222",
        started_at="2026-04-30T02:00:00Z",
        prompt="beta",
    )
    _make_session(
        fake_eventlog,
        "ferret-20260430T030000-cccc3333",
        started_at="2026-04-30T03:00:00Z",
        prompt="gamma",
    )

    out = sessions_mod.format_session_table(
        sessions_mod.iter_sessions(), color=False,
    )
    assert "SESSION_ID" in out
    assert "alpha" in out and "beta" in out and "gamma" in out


def test_format_session_table_empty_returns_friendly_message() -> None:
    out = sessions_mod.format_session_table([], color=False)
    assert "no persisted sessions" in out.lower()


def test_format_session_detail_includes_prompt_and_events(
    fake_eventlog: Path,
) -> None:
    _make_session(
        fake_eventlog,
        "ferret-detail-test",
        started_at="2026-04-30T05:00:00Z",
        prompt="detail prompt",
    )
    detail = sessions_mod.get_session("ferret-detail-test")
    out = sessions_mod.format_session_detail(detail, color=False)
    assert "ferret-detail-test" in out
    assert "detail prompt" in out
    assert "user_message" in out or "[user]" in out
    assert "agent_result" in out or "[agent]" in out


# ---------------------------------------------------------------------------
# cmd_sessions_list / cmd_sessions_show / dispatch_sessions
# ---------------------------------------------------------------------------


def test_sessions_list_table(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """``ferret sessions list`` prints all 3 ids in newest-first order."""
    _make_session(
        fake_eventlog,
        "ferret-20260430T010000-aaaa1111",
        started_at="2026-04-30T01:00:00Z",
    )
    _make_session(
        fake_eventlog,
        "ferret-20260430T020000-bbbb2222",
        started_at="2026-04-30T02:00:00Z",
    )
    _make_session(
        fake_eventlog,
        "ferret-20260430T030000-cccc3333",
        started_at="2026-04-30T03:00:00Z",
    )

    rc = sessions_mod.dispatch_sessions(_list_args(sessions_action="list"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "ferret-20260430T030000-cccc3333" in out
    assert "ferret-20260430T020000-bbbb2222" in out
    assert "ferret-20260430T010000-aaaa1111" in out
    pos_03 = out.index("ferret-20260430T030000-cccc3333")
    pos_01 = out.index("ferret-20260430T010000-aaaa1111")
    assert pos_03 < pos_01, f"newest-first order broken:\n{out}"


def test_sessions_list_empty_eventlog(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = sessions_mod.cmd_sessions_list(_list_args(sessions_action="list"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "no persisted sessions" in out.lower()


def test_sessions_list_filter_by_model(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(
        fake_eventlog,
        "ferret-gpt5-1",
        started_at="2026-04-30T01:00:00Z",
        model="gpt-5",
    )
    _make_session(
        fake_eventlog,
        "ferret-gpt4o-1",
        started_at="2026-04-30T02:00:00Z",
        model="gpt-4o",
    )

    rc = sessions_mod.dispatch_sessions(_list_args(sessions_model="gpt-5"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "ferret-gpt5-1" in out
    assert "ferret-gpt4o-1" not in out


def test_sessions_list_filter_by_since(
    fake_eventlog: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--since 2d`` drops sessions older than the cutoff."""
    now = datetime.now(timezone.utc)
    five_days_ago = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    one_day_ago = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _make_session(fake_eventlog, "ferret-old-session", started_at=five_days_ago)
    _make_session(fake_eventlog, "ferret-new-session", started_at=one_day_ago)

    rc = sessions_mod.dispatch_sessions(_list_args(sessions_since="2d"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "ferret-new-session" in out
    assert "ferret-old-session" not in out


def test_sessions_list_invalid_since_exits_2(
    fake_eventlog: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = sessions_mod.dispatch_sessions(_list_args(sessions_since="garbage"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "since" in err.lower() or "invalid" in err.lower()


def test_sessions_list_json_emits_array(
    fake_eventlog: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(
        fake_eventlog,
        "ferret-json-1",
        started_at="2026-04-30T01:00:00Z",
        prompt="alpha",
    )
    _make_session(
        fake_eventlog,
        "ferret-json-2",
        started_at="2026-04-30T02:00:00Z",
        prompt="beta",
    )

    rc = sessions_mod.dispatch_sessions(_list_args(sessions_json=True))
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) == 2
    ids = [item["session_id"] for item in payload]
    assert "ferret-json-2" in ids
    assert "ferret-json-1" in ids


def test_sessions_show_renders_detail(
    fake_eventlog: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(
        fake_eventlog,
        "ferret-show-test",
        started_at="2026-04-30T05:00:00Z",
        prompt="my custom prompt",
        model="my-model",
        cost_usd=0.0123,
    )
    rc = sessions_mod.dispatch_sessions(
        _list_args(sessions_action="show", sessions_target="ferret-show-test"),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "my custom prompt" in out
    assert "my-model" in out
    assert "0.012300" in out or "$0.012" in out


def test_sessions_show_unknown_id_exits_2(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = sessions_mod.dispatch_sessions(
        _list_args(
            sessions_action="show",
            sessions_target="ferret-does-not-exist",
        ),
    )
    captured = capsys.readouterr()
    assert rc == 2
    err = captured.err
    assert "not found" in err.lower() or "no summary" in err.lower()
    assert "sessions list" in err


def test_sessions_show_missing_id_exits_2(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = sessions_mod.dispatch_sessions(
        _list_args(sessions_action="show", sessions_target=None),
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "session_id" in err.lower() or "requires" in err.lower()


def test_dispatch_sessions_returns_none_when_not_engaged() -> None:
    """``dispatch_sessions`` is a no-op when ``sessions_command`` isn't set."""
    args = argparse.Namespace(sessions_command=None)
    assert sessions_mod.dispatch_sessions(args) is None
