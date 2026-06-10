"""Tests for the interactive plan-approval loop (``chimera team approvals``)."""
from __future__ import annotations

import time

import pytest

from chimera.cli.agent_teams import Team
from chimera.mink.team_approvals import run_approvals

POLL = 0.0123  # sentinel poll interval so only the loop's own sleep is intercepted


@pytest.fixture(autouse=True)
def _teams_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))


def _team_with_pending_plan() -> tuple[Team, str]:
    team = Team("alpha")
    team.init()
    team.add_member("worker")
    tid = team.add_task("Refactor the parser", requires_plan=True)
    assert team.claim_task(tid, "worker")
    assert team.propose_plan(tid, "worker", "1. read 2. patch 3. test")
    return team, tid


def _stop_loop_after_first_poll(monkeypatch) -> None:
    real_sleep = time.sleep

    def _sleep(secs: float) -> None:
        if secs == POLL:
            raise KeyboardInterrupt
        real_sleep(secs)

    monkeypatch.setattr(time, "sleep", _sleep)


def test_run_approvals_missing_team_returns_1() -> None:
    assert run_approvals("does-not-exist", poll_interval=POLL) == 1


def test_run_approvals_approve_flow(monkeypatch, capsys) -> None:
    team, tid = _team_with_pending_plan()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "a")
    _stop_loop_after_first_poll(monkeypatch)

    assert run_approvals("alpha", poll_interval=POLL) == 0

    rec = next(t for t in team.list_tasks(status_filter="all") if t["id"] == tid)
    assert rec["plan_status"] == "approved"
    out = capsys.readouterr().out
    assert "PENDING PLAN" in out
    assert "approved" in out


def test_run_approvals_reject_flow_records_feedback(monkeypatch) -> None:
    team, tid = _team_with_pending_plan()
    answers = iter(["r", "needs a test step"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    _stop_loop_after_first_poll(monkeypatch)

    assert run_approvals("alpha", poll_interval=POLL) == 0

    rec = next(t for t in team.list_tasks(status_filter="all") if t["id"] == tid)
    assert rec["plan_status"] == "rejected"
    assert rec["plan_feedback"] == "needs a test step"
    # a rejected task cannot complete until a fresh plan is approved
    assert team.complete_task(tid, "worker") is False


def test_propose_plan_never_clobbers_an_approved_plan() -> None:
    team, tid = _team_with_pending_plan()
    assert team.approve_plan(tid, "approve")

    assert team.propose_plan(tid, "worker", "sneaky rewrite") is False

    rec = next(t for t in team.list_tasks(status_filter="all") if t["id"] == tid)
    assert rec["plan_status"] == "approved"
    assert rec["proposed_plan"] == "1. read 2. patch 3. test"
