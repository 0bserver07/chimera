"""Smoke tests for the experimental agent-teams subsystem.

Run with::

    CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1 uv run pytest tests/test_agent_teams_smoke.py -x -v
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from chimera.cli.agent_teams import (
    ENV_FLAG,
    Team,
    TeamMailbox,
    create_team,
    is_enabled,
    join_team,
)
from chimera.tools.send_message import SendMessageTool


@pytest.fixture(autouse=True)
def _isolated_teams_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the teams root to a per-test temp dir."""
    monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def teams_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FLAG, "1")


def test_team_create_and_join(teams_enabled: None) -> None:
    team = create_team("alpha")
    assert team.exists()
    join_team("alpha", "agent-A")
    join_team("alpha", "agent-B")

    cfg = Team("alpha").load_config()
    assert "agent-A" in cfg["members"]
    assert "agent-B" in cfg["members"]
    assert len(cfg["members"]) == 2
    # idempotent re-join
    join_team("alpha", "agent-A")
    assert len(Team("alpha").load_config()["members"]) == 2


def test_task_claim_lock(teams_enabled: None) -> None:
    team = create_team("beta")
    join_team("beta", "agent-1")
    join_team("beta", "agent-2")
    task_id = team.add_task("write the README")

    results: dict[str, bool] = {}
    barrier = threading.Barrier(2)

    def race(agent_id: str) -> None:
        barrier.wait()
        results[agent_id] = team.claim_task(task_id, agent_id)

    threads = [
        threading.Thread(target=race, args=("agent-1",)),
        threading.Thread(target=race, args=("agent-2",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), "thread hung — likely deadlock in flock"

    winners = [aid for aid, won in results.items() if won]
    assert len(winners) == 1, f"expected exactly one winner, got {results}"

    tasks = team.list_tasks()
    rec = next(r for r in tasks if r["id"] == task_id)
    assert rec["status"] == "claimed"
    assert rec["claimed_by"] == winners[0]


def test_send_message_delivery(teams_enabled: None) -> None:
    create_team("gamma")
    join_team("gamma", "alice")
    join_team("gamma", "bob")

    tool = SendMessageTool(team_name="gamma", sender_id="alice")
    result = tool.execute({"to": "bob", "content": "ping"}, env=None)
    assert result.success, result.error
    assert "bob" in result.output

    bob_inbox = TeamMailbox(Team("gamma"), "bob")
    msgs = bob_inbox.recv()
    assert len(msgs) == 1
    assert msgs[0]["from"] == "alice"
    assert msgs[0]["to"] == "bob"
    assert msgs[0]["content"] == "ping"
    # inbox drained
    assert bob_inbox.recv() == []


def test_disabled_without_env_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure flag is unset for this test even if shell exported it.
    monkeypatch.delenv(ENV_FLAG, raising=False)
    assert not is_enabled()
    create_team("delta")
    tool = SendMessageTool(team_name="delta", sender_id="alice")
    result = tool.execute({"to": "bob", "content": "ping"}, env=None)
    assert not result.success
    assert ENV_FLAG in (result.error or "")


def test_complete_task_marks_completed(teams_enabled: None) -> None:
    team = create_team("epsilon")
    join_team("epsilon", "worker")
    tid = team.add_task("do the thing")
    assert team.claim_task(tid, "worker")
    assert team.complete_task(tid, "worker", result="done")

    rec = next(r for r in team.list_tasks() if r["id"] == tid)
    assert rec["status"] == "completed"
    assert rec["result"] == "done"


def test_state_layout_on_disk(teams_enabled: None, _isolated_teams_root: Path) -> None:
    create_team("zeta")
    join_team("zeta", "a1")
    Team("zeta").add_task("task one")

    base = _isolated_teams_root / "zeta"
    assert (base / "config.json").is_file()
    assert (base / "task_list.jsonl").is_file()
    assert (base / "mailbox").is_dir()
    assert (base / "mailbox" / "a1.jsonl").is_file()


def test_send_message_unknown_team(teams_enabled: None) -> None:
    tool = SendMessageTool(team_name="nonesuch", sender_id="x")
    result = tool.execute({"to": "y", "content": "hi"}, env=None)
    assert not result.success
    assert "nonesuch" in (result.error or "")
