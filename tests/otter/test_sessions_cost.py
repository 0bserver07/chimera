"""``chimera otter sessions cost`` — fixture-driven tests.

Mirrors ``tests/mink/test_runs_cost.py`` but exercises the otter twin
that aggregates ``~/.chimera/eventlog/otter-*/summary.json`` files
through :func:`chimera.otter.sessions.cmd_sessions_cost`.

Test plan:

1. ``iter_session_run_records`` — yields RunRecord-shaped objects from
   otter session dirs only, skips ``mink-*`` siblings, tolerates
   missing ``summary.json``.
2. ``cmd_sessions_cost`` text/json/csv outputs sum costs correctly.
3. ``--since`` shorthand and ISO filter older sessions.
4. ``--model`` substring filter narrows the corpus.
5. ``--limit`` caps rows considered.
6. Invalid ``--since`` → exit 2 with stderr message.
7. Invalid ``--format`` → exit 2 with stderr message.
8. Empty eventlog returns a friendly zero-row summary.
9. ``dispatch_sessions(action='cost')`` routes to the cost handler.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chimera.otter import sessions as sessions_mod


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
    total_tokens: int = 0,
    error: str | None = None,
) -> Path:
    """Materialize one fake otter session dir (summary.json only)."""
    session_dir = root / session_id
    session_dir.mkdir()
    summary = {
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": started_at,
        "model": model,
        "prompt": prompt,
        "cwd": "/tmp",
        "permission_mode": "default",
        "steps": steps,
        "tool_calls_total": tool_calls,
        "success": success,
        "cost_usd": cost_usd,
        "total_tokens": total_tokens,
        "error": error,
    }
    (session_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8",
    )
    return session_dir


def _cost_args(
    *,
    sessions_command: str | None = "sessions",
    sessions_action: str | None = "cost",
    sessions_target: str | None = None,
    sessions_limit: int | None = None,
    sessions_model: str | None = None,
    sessions_since: str | None = None,
    sessions_format: str = "text",
    no_color: bool = True,
) -> argparse.Namespace:
    """Build a Namespace mirroring what argparse would produce for cost."""
    return argparse.Namespace(
        sessions_command=sessions_command,
        sessions_action=sessions_action,
        sessions_target=sessions_target,
        sessions_limit=sessions_limit,
        sessions_model=sessions_model,
        sessions_since=sessions_since,
        sessions_format=sessions_format,
        no_color=no_color,
    )


# ---------------------------------------------------------------------------
# iter_session_run_records — adapter back to mink.runs.RunRecord
# ---------------------------------------------------------------------------


def test_iter_session_run_records_yields_otter_only(fake_eventlog: Path) -> None:
    """Only ``otter-*`` dirs are walked; ``mink-*`` siblings are skipped."""
    _make_session(
        fake_eventlog, "otter-20260424T030000-cccc3333",
        started_at="2026-04-24T03:00:00Z", cost_usd=0.10,
    )
    # A mink session in the same root must be ignored entirely.
    mink_dir = fake_eventlog / "mink-20260424T010000-aaaa1111"
    mink_dir.mkdir()
    (mink_dir / "summary.json").write_text(
        json.dumps({
            "run_id": "mink-20260424T010000-aaaa1111",
            "started_at": "2026-04-24T01:00:00Z",
            "ended_at": "2026-04-24T01:00:00Z",
            "model": "mink-model",
            "prompt": "from mink",
            "success": True,
            "cost_usd": 99.0,
            "steps": 7,
            "tool_calls_total": 0,
            "total_tokens": 0,
        }),
        encoding="utf-8",
    )
    records = list(sessions_mod.iter_session_run_records())
    assert len(records) == 1
    assert records[0].run_id == "otter-20260424T030000-cccc3333"
    assert abs(records[0].cost_usd - 0.10) < 1e-9


def test_iter_session_run_records_newest_first(fake_eventlog: Path) -> None:
    _make_session(
        fake_eventlog, "otter-20260424T010000-aaaa1111",
        started_at="2026-04-24T01:00:00Z",
    )
    _make_session(
        fake_eventlog, "otter-20260424T030000-cccc3333",
        started_at="2026-04-24T03:00:00Z",
    )
    _make_session(
        fake_eventlog, "otter-20260424T020000-bbbb2222",
        started_at="2026-04-24T02:00:00Z",
    )
    ids = [r.run_id for r in sessions_mod.iter_session_run_records()]
    assert ids == [
        "otter-20260424T030000-cccc3333",
        "otter-20260424T020000-bbbb2222",
        "otter-20260424T010000-aaaa1111",
    ]


def test_iter_session_run_records_skips_dirs_without_summary(
    fake_eventlog: Path,
) -> None:
    (fake_eventlog / "otter-empty-dir-xxxxxxxx").mkdir()
    _make_session(
        fake_eventlog, "otter-20260424T010000-aaaa1111",
        started_at="2026-04-24T01:00:00Z",
    )
    records = list(sessions_mod.iter_session_run_records())
    assert len(records) == 1


def test_iter_session_run_records_missing_root_returns_empty(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "absent-eventlog"
    assert not missing.exists()
    assert list(sessions_mod.iter_session_run_records(missing)) == []


# ---------------------------------------------------------------------------
# cmd_sessions_cost — text/json/csv outputs
# ---------------------------------------------------------------------------


def test_sessions_cost_text_sums_three_sessions(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(
        fake_eventlog, "otter-20260424T010000-aaaa1111",
        started_at="2026-04-24T01:00:00Z", cost_usd=0.10, total_tokens=100,
    )
    _make_session(
        fake_eventlog, "otter-20260424T020000-bbbb2222",
        started_at="2026-04-24T02:00:00Z", cost_usd=0.20, total_tokens=200,
    )
    _make_session(
        fake_eventlog, "otter-20260424T030000-cccc3333",
        started_at="2026-04-24T03:00:00Z", cost_usd=0.05, total_tokens=50,
    )
    rc = sessions_mod.cmd_sessions_cost(_cost_args(sessions_format="text"))
    assert rc == 0
    out = capsys.readouterr().out
    # Total: 0.35 (text format quotes 4-decimal precision)
    assert "$0.3500" in out
    assert "runs" in out


def test_sessions_cost_json_emits_totals_by_model_and_rows(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(
        fake_eventlog, "otter-20260424T010000-aaaa1111",
        started_at="2026-04-24T01:00:00Z", model="glm-5.1", cost_usd=0.10,
        total_tokens=100,
    )
    _make_session(
        fake_eventlog, "otter-20260424T020000-bbbb2222",
        started_at="2026-04-24T02:00:00Z", model="glm-5.1", cost_usd=0.30,
        total_tokens=300,
    )
    _make_session(
        fake_eventlog, "otter-20260424T030000-cccc3333",
        started_at="2026-04-24T03:00:00Z", model="kimi-k2", cost_usd=0.20,
        total_tokens=200,
    )
    rc = sessions_mod.cmd_sessions_cost(_cost_args(sessions_format="json"))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["runs"] == 3
    assert abs(payload["totals"]["cost_usd"] - 0.60) < 1e-9
    assert payload["totals"]["tokens"] == 600
    assert "glm-5.1" in payload["by_model"]
    assert payload["by_model"]["glm-5.1"]["runs"] == 2
    assert abs(payload["by_model"]["glm-5.1"]["cost_usd"] - 0.40) < 1e-9
    assert "kimi-k2" in payload["by_model"]
    # Per-session rows surface with otter prefix.
    row_ids = [r["run_id"] for r in payload["rows"]]
    assert all(rid.startswith("otter-") for rid in row_ids)


def test_sessions_cost_csv_emits_one_header_plus_rows(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(
        fake_eventlog, "otter-csv-1",
        started_at="2026-04-24T01:00:00Z", cost_usd=0.05,
    )
    _make_session(
        fake_eventlog, "otter-csv-2",
        started_at="2026-04-24T02:00:00Z", cost_usd=0.07,
    )
    rc = sessions_mod.cmd_sessions_cost(_cost_args(sessions_format="csv"))
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0].startswith("run_id,started_at,model,cost_usd")
    assert len(out) == 3  # 1 header + 2 sessions
    # Rows preserve newest-first ordering.
    assert out[1].startswith("otter-csv-2,")
    assert out[2].startswith("otter-csv-1,")


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_sessions_cost_since_drops_old_sessions(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime.now(timezone.utc)
    five_days = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    one_day = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _make_session(
        fake_eventlog, "otter-old-session",
        started_at=five_days, cost_usd=10.0,
    )
    _make_session(
        fake_eventlog, "otter-new-session",
        started_at=one_day, cost_usd=0.50,
    )
    rc = sessions_mod.cmd_sessions_cost(
        _cost_args(sessions_since="2d", sessions_format="json"),
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["runs"] == 1
    assert abs(payload["totals"]["cost_usd"] - 0.50) < 1e-9
    assert payload["filters"]["since"] == "2d"


def test_sessions_cost_model_substring_filter(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(
        fake_eventlog, "otter-glm-1",
        started_at="2026-04-24T01:00:00Z", model="glm-5.1:cloud", cost_usd=0.10,
    )
    _make_session(
        fake_eventlog, "otter-glm-2",
        started_at="2026-04-24T02:00:00Z", model="glm-5.1:local", cost_usd=0.20,
    )
    _make_session(
        fake_eventlog, "otter-kimi-1",
        started_at="2026-04-24T03:00:00Z", model="kimi-k2.6:cloud", cost_usd=10.0,
    )
    rc = sessions_mod.cmd_sessions_cost(
        _cost_args(sessions_model="glm", sessions_format="json"),
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["runs"] == 2
    assert abs(payload["totals"]["cost_usd"] - 0.30) < 1e-9
    assert payload["filters"]["model"] == "glm"


def test_sessions_cost_limit_caps_rows(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    for i in range(5):
        _make_session(
            fake_eventlog,
            f"otter-2026042{i}T000000-id{i:06d}",
            started_at=f"2026-04-2{i}T00:00:00Z",
            cost_usd=1.0,
        )
    rc = sessions_mod.cmd_sessions_cost(
        _cost_args(sessions_limit=2, sessions_format="json"),
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    # With limit=2, only the two newest are aggregated.
    assert payload["totals"]["runs"] == 2
    assert abs(payload["totals"]["cost_usd"] - 2.0) < 1e-9


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_sessions_cost_invalid_since_exits_2(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = sessions_mod.cmd_sessions_cost(_cost_args(sessions_since="garbage"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "since" in err.lower() or "iso" in err.lower()


def test_sessions_cost_invalid_format_exits_2(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = sessions_mod.cmd_sessions_cost(_cost_args(sessions_format="yaml"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "format" in err.lower()


# ---------------------------------------------------------------------------
# Empty corpus
# ---------------------------------------------------------------------------


def test_sessions_cost_empty_eventlog_returns_zero_summary(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = sessions_mod.cmd_sessions_cost(_cost_args(sessions_format="json"))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["runs"] == 0
    assert payload["totals"]["cost_usd"] == 0.0
    assert payload["by_model"] == {}
    assert payload["rows"] == []


def test_sessions_cost_text_empty_renders_zero_block(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = sessions_mod.cmd_sessions_cost(_cost_args(sessions_format="text"))
    assert rc == 0
    out = capsys.readouterr().out
    # Plain renderer always shows the totals block, even when empty.
    assert "runs" in out
    assert "$0.0000" in out


# ---------------------------------------------------------------------------
# Dispatcher routing
# ---------------------------------------------------------------------------


def test_dispatch_sessions_routes_cost_action(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _make_session(
        fake_eventlog, "otter-disp-cost-1",
        started_at="2026-04-24T01:00:00Z", cost_usd=0.42,
    )
    rc = sessions_mod.dispatch_sessions(_cost_args(sessions_format="json"))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["runs"] == 1
    assert abs(payload["totals"]["cost_usd"] - 0.42) < 1e-9


def test_dispatch_sessions_unknown_action_lists_cost_in_help(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown-action error message lists ``cost`` alongside list/show."""
    rc = sessions_mod.dispatch_sessions(_cost_args(sessions_action="bogus"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "cost" in err.lower()


def test_iter_session_run_records_in___all__() -> None:
    """The new public helper is reachable through the module's __all__."""
    assert "iter_session_run_records" in sessions_mod.__all__
    assert "cmd_sessions_cost" in sessions_mod.__all__
