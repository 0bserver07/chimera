"""Audit H-3: ``chimera mink runs list`` and ``runs show <id>``.

Pins the user-visible behavior that persisted ``~/.chimera/eventlog/mink-*/``
directories surface as a real CLI feature instead of requiring users to
``cat`` JSON by hand.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from chimera.mink import cli as mink_cli
from chimera.mink import runs as runs_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_eventlog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``~/.chimera/eventlog`` and ``Path.home()`` at ``tmp_path``."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    eventlog = home / ".chimera" / "eventlog"
    eventlog.mkdir(parents=True)
    return eventlog


def _make_run(
    root: Path,
    run_id: str,
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
) -> Path:
    """Create one fake mink run dir with a summary.json (and optional events)."""
    run_dir = root / run_id
    run_dir.mkdir()
    summary = {
        "run_id": run_id,
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
        "total_tokens": 0,
        "error": error,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if with_events:
        (run_dir / "event-000000-aaaaaaaa.json").write_text(
            json.dumps(
                {
                    "idx": 0,
                    "event_id": "aaaaaaaa",
                    "type": "user_message",
                    "timestamp": 1.0,
                    "metadata": {"content": prompt, "event_id": "aaaaaaaa"},
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "event-000001-bbbbbbbb.json").write_text(
            json.dumps(
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
                }
            ),
            encoding="utf-8",
        )
    return run_dir


# ---------------------------------------------------------------------------
# runs.iter_runs / get_run direct exercise
# ---------------------------------------------------------------------------


def test_iter_runs_yields_newest_first(fake_eventlog: Path):
    _make_run(fake_eventlog, "mink-20260424T010000-aaaa1111", started_at="2026-04-24T01:00:00Z")
    _make_run(fake_eventlog, "mink-20260424T020000-bbbb2222", started_at="2026-04-24T02:00:00Z")
    _make_run(fake_eventlog, "mink-20260424T030000-cccc3333", started_at="2026-04-24T03:00:00Z")

    ids = [r.run_id for r in runs_mod.iter_runs()]
    assert ids == [
        "mink-20260424T030000-cccc3333",
        "mink-20260424T020000-bbbb2222",
        "mink-20260424T010000-aaaa1111",
    ]


def test_iter_runs_skips_dirs_without_summary(fake_eventlog: Path):
    """An aborted run with no summary.json must be skipped, never raise."""
    (fake_eventlog / "mink-empty-dir-xxxxxxxx").mkdir()
    _make_run(fake_eventlog, "mink-20260424T010000-aaaa1111", started_at="2026-04-24T01:00:00Z")

    records = list(runs_mod.iter_runs())
    assert len(records) == 1
    assert records[0].run_id == "mink-20260424T010000-aaaa1111"


def test_get_run_loads_summary_and_events(fake_eventlog: Path):
    _make_run(
        fake_eventlog,
        "mink-20260424T010000-aaaa1111",
        started_at="2026-04-24T01:00:00Z",
        prompt="test prompt",
    )

    detail = runs_mod.get_run("mink-20260424T010000-aaaa1111")
    assert detail.summary["prompt"] == "test prompt"
    assert len(detail.events) == 2
    assert detail.events[0]["type"] == "user_message"
    assert detail.events[1]["type"] == "agent_result"


def test_get_run_missing_id_raises(fake_eventlog: Path):
    with pytest.raises(FileNotFoundError):
        runs_mod.get_run("mink-does-not-exist")


# ---------------------------------------------------------------------------
# format_run_table / format_run_detail
# ---------------------------------------------------------------------------


def test_format_run_table_lists_all_records(fake_eventlog: Path):
    _make_run(fake_eventlog, "mink-20260424T010000-aaaa1111", started_at="2026-04-24T01:00:00Z", prompt="alpha")
    _make_run(fake_eventlog, "mink-20260424T020000-bbbb2222", started_at="2026-04-24T02:00:00Z", prompt="beta")
    _make_run(fake_eventlog, "mink-20260424T030000-cccc3333", started_at="2026-04-24T03:00:00Z", prompt="gamma")

    out = runs_mod.format_run_table(runs_mod.iter_runs(), color=False)
    assert "RUN_ID" in out
    assert "alpha" in out and "beta" in out and "gamma" in out


def test_format_run_table_empty_returns_friendly_message():
    out = runs_mod.format_run_table([], color=False)
    assert "no persisted runs" in out.lower()


def test_format_run_detail_includes_prompt_and_events(fake_eventlog: Path):
    _make_run(
        fake_eventlog,
        "mink-detail-test",
        started_at="2026-04-24T05:00:00Z",
        prompt="detail prompt",
    )
    detail = runs_mod.get_run("mink-detail-test")
    out = runs_mod.format_run_detail(detail, color=False)
    assert "mink-detail-test" in out
    assert "detail prompt" in out
    assert "user_message" in out or "[user]" in out
    assert "agent_result" in out or "[agent]" in out


# ---------------------------------------------------------------------------
# CLI dispatch via _dispatch_runs
# ---------------------------------------------------------------------------


def _runs_args(
    *,
    runs_command: str | None = "runs",
    runs_action: str | None = "list",
    runs_target: str | None = None,
    full: bool = False,
    runs_limit: int = 20,
    runs_filter_model: str | None = None,
    runs_success_only: bool = False,
    runs_failed_only: bool = False,
    runs_show_events: bool = True,
    no_color: bool = True,
    no_rich: bool = False,
) -> argparse.Namespace:
    """Build a Namespace mirroring what argparse would produce for ``runs``."""
    return argparse.Namespace(
        runs_command=runs_command,
        runs_action=runs_action,
        runs_target=runs_target,
        full=full,
        runs_limit=runs_limit,
        runs_filter_model=runs_filter_model,
        runs_success_only=runs_success_only,
        runs_failed_only=runs_failed_only,
        runs_show_events=runs_show_events,
        no_color=no_color,
        no_rich=no_rich,
    )


def test_runs_list_table(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
):
    """``mink runs list`` prints all 3 ids in newest-first order."""
    _make_run(fake_eventlog, "mink-20260424T010000-aaaa1111", started_at="2026-04-24T01:00:00Z")
    _make_run(fake_eventlog, "mink-20260424T020000-bbbb2222", started_at="2026-04-24T02:00:00Z")
    _make_run(fake_eventlog, "mink-20260424T030000-cccc3333", started_at="2026-04-24T03:00:00Z")

    rc = mink_cli._dispatch_runs(_runs_args(runs_action="list"))
    assert rc == 0
    out = capsys.readouterr().out
    # All three ids present.
    assert "mink-20260424T030000-cccc3333" in out
    assert "mink-20260424T020000-bbbb2222" in out
    assert "mink-20260424T010000-aaaa1111" in out
    # Newest first: T03 before T01 in linear stdout.
    pos_03 = out.index("mink-20260424T030000-cccc3333")
    pos_01 = out.index("mink-20260424T010000-aaaa1111")
    assert pos_03 < pos_01, f"newest-first order broken:\n{out}"


def test_runs_show_renders_detail(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
):
    """``mink runs show <id>`` prints prompt, model, cost, and events."""
    _make_run(
        fake_eventlog,
        "mink-show-test",
        started_at="2026-04-24T05:00:00Z",
        prompt="my custom prompt",
        model="my-model",
        cost_usd=0.0123,
    )
    rc = mink_cli._dispatch_runs(
        _runs_args(runs_action="show", runs_target="mink-show-test"),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "my custom prompt" in out
    assert "my-model" in out
    assert "0.012300" in out or "$0.012" in out  # cost rendered with 6 decimals
    assert "user_message" in out or "[user]" in out


def test_runs_list_filter_failed(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
):
    """``runs list --failed-only`` shows only failed runs."""
    _make_run(fake_eventlog, "mink-success-1", started_at="2026-04-24T01:00:00Z", success=True)
    _make_run(fake_eventlog, "mink-success-2", started_at="2026-04-24T02:00:00Z", success=True)
    _make_run(
        fake_eventlog, "mink-failed-1", started_at="2026-04-24T03:00:00Z",
        success=False, error="boom",
    )

    rc = mink_cli._dispatch_runs(
        _runs_args(runs_action="list", runs_failed_only=True),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "mink-failed-1" in out
    assert "mink-success-1" not in out
    assert "mink-success-2" not in out


def test_runs_list_filter_success_only(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
):
    """``runs list --success-only`` drops failed runs."""
    _make_run(fake_eventlog, "mink-ok-1", started_at="2026-04-24T01:00:00Z", success=True)
    _make_run(fake_eventlog, "mink-fail-1", started_at="2026-04-24T02:00:00Z", success=False)

    rc = mink_cli._dispatch_runs(
        _runs_args(runs_action="list", runs_success_only=True),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "mink-ok-1" in out
    assert "mink-fail-1" not in out


def test_runs_list_filter_by_model(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
):
    """``runs list --runs-model glm-5`` shows only runs that used glm-5."""
    _make_run(fake_eventlog, "mink-glm-1", started_at="2026-04-24T01:00:00Z", model="glm-5.1:cloud")
    _make_run(fake_eventlog, "mink-kimi-1", started_at="2026-04-24T02:00:00Z", model="kimi-k2.6:cloud")

    rc = mink_cli._dispatch_runs(
        _runs_args(runs_action="list", runs_filter_model="glm-5.1:cloud"),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "mink-glm-1" in out
    assert "mink-kimi-1" not in out


def test_runs_show_unknown_id_exits_2(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
):
    """Nonexistent run id → exit 2 + helpful stderr pointing at runs list."""
    rc = mink_cli._dispatch_runs(
        _runs_args(runs_action="show", runs_target="mink-does-not-exist"),
    )
    captured = capsys.readouterr()
    assert rc == 2, f"expected exit 2, got {rc}; stderr={captured.err!r}"
    err = captured.err
    assert "not found" in err.lower() or "no summary" in err.lower()
    # The hint must point users at runs list so they can recover quickly.
    assert "runs list" in err


def test_runs_show_missing_id_exits_2(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
):
    """``mink runs show`` with no id arg → exit 2 + usage message."""
    rc = mink_cli._dispatch_runs(
        _runs_args(runs_action="show", runs_target=None),
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "RUN_ID" in err or "requires" in err


def test_runs_no_action_defaults_to_list(
    fake_eventlog: Path, capsys: pytest.CaptureFixture[str],
):
    """``mink runs`` (with no list/show) acts as ``mink runs list``."""
    _make_run(fake_eventlog, "mink-bare-1", started_at="2026-04-24T01:00:00Z")
    rc = mink_cli._dispatch_runs(_runs_args(runs_action=None))
    assert rc == 0
    assert "mink-bare-1" in capsys.readouterr().out


def test_dispatch_runs_returns_none_when_not_runs():
    """Without ``runs_command='runs'`` the dispatcher must defer to the loop."""
    assert mink_cli._dispatch_runs(_runs_args(runs_command=None)) is None


# ---------------------------------------------------------------------------
# argparse wiring smoke
# ---------------------------------------------------------------------------


def test_argparse_accepts_runs_list_with_filters():
    """The new flags must round-trip through ``add_arguments``."""
    parser = argparse.ArgumentParser()
    mink_cli.add_arguments(parser)
    args = parser.parse_args(
        [
            "runs", "list",
            "--limit", "5",
            "--runs-model", "glm-5",
            "--success-only",
        ]
    )
    assert args.runs_command == "runs"
    assert args.runs_action == "list"
    assert args.runs_limit == 5
    assert args.runs_filter_model == "glm-5"
    assert args.runs_success_only is True


def test_argparse_accepts_runs_show_with_no_events():
    parser = argparse.ArgumentParser()
    mink_cli.add_arguments(parser)
    args = parser.parse_args(["runs", "show", "mink-some-id", "--no-events"])
    assert args.runs_command == "runs"
    assert args.runs_action == "show"
    assert args.runs_target == "mink-some-id"
    assert args.runs_show_events is False


# ---------------------------------------------------------------------------
# End-to-end via subprocess (smoke; verifies wiring + exit code)
# ---------------------------------------------------------------------------


def test_runs_list_subprocess_runs_and_exits_zero(fake_eventlog: Path):
    """Run the real CLI binary; should exit 0 even on an empty eventlog."""
    proc = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", "mink", "runs", "list"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**__import__("os").environ, "HOME": str(fake_eventlog.parent.parent)},
    )
    assert proc.returncode == 0, (
        f"runs list exit {proc.returncode}: stderr={proc.stderr!r}"
    )
