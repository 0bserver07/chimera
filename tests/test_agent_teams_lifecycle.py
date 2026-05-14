"""Lifecycle helpers for the experimental agent-teams subsystem.

Covers ``Team.destroy``, the module-level ``destroy_team`` wrapper, and
``list_teams`` directory enumeration.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.cli.agent_teams import (
    Team,
    create_team,
    destroy_team,
    join_team,
    list_teams,
    teams_root,
)


@pytest.fixture(autouse=True)
def _isolated_teams_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the teams root to a per-test temp dir."""
    monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))
    return tmp_path


# ---- Team.destroy ---------------------------------------------------------

def test_destroy_empty_team(_isolated_teams_root: Path) -> None:
    team = create_team("alpha")
    assert team.dir.is_dir()

    deleted = team.destroy()

    assert deleted == team.dir
    assert not team.dir.exists()
    assert not team.exists()


def test_destroy_refuses_with_claimed(_isolated_teams_root: Path) -> None:
    team = create_team("beta")
    join_team("beta", "worker")
    tid = team.add_task("do a thing")
    assert team.claim_task(tid, "worker")

    with pytest.raises(ValueError, match="claimed-but-not-completed"):
        team.destroy()

    # Directory must still exist; nothing was deleted.
    assert team.dir.is_dir()
    assert team.config_path.is_file()


def test_destroy_forced_with_claimed(_isolated_teams_root: Path) -> None:
    team = create_team("gamma")
    join_team("gamma", "worker")
    tid = team.add_task("do another thing")
    assert team.claim_task(tid, "worker")

    deleted = team.destroy(force=True)

    assert deleted == team.dir
    assert not team.dir.exists()


def test_destroy_after_complete_succeeds(_isolated_teams_root: Path) -> None:
    team = create_team("delta")
    join_team("delta", "worker")
    tid = team.add_task("finish this")
    assert team.claim_task(tid, "worker")
    assert team.complete_task(tid, "worker", result="ok")

    deleted = team.destroy()

    assert deleted == team.dir
    assert not team.dir.exists()


def test_destroy_via_module_wrapper(_isolated_teams_root: Path) -> None:
    team = create_team("epsilon")
    join_team("epsilon", "worker")
    assert team.dir.is_dir()

    deleted = destroy_team("epsilon")

    assert deleted == team.dir
    assert not team.dir.exists()


def test_destroy_via_module_wrapper_refuses(_isolated_teams_root: Path) -> None:
    team = create_team("zeta")
    join_team("zeta", "worker")
    tid = team.add_task("hold this")
    assert team.claim_task(tid, "worker")

    with pytest.raises(ValueError, match="claimed-but-not-completed"):
        destroy_team("zeta")

    assert team.dir.is_dir()

    # Forced wrapper should clean up.
    assert destroy_team("zeta", force=True) == team.dir
    assert not team.dir.exists()


def test_destroy_nonexistent_is_safe(_isolated_teams_root: Path) -> None:
    """rmtree(ignore_errors=True) means destroying a never-created team is a no-op."""
    target = Team("ghost")
    assert not target.exists()
    deleted = target.destroy()
    assert deleted == target.dir
    assert not target.dir.exists()


# ---- list_teams -----------------------------------------------------------

def test_list_teams_empty(_isolated_teams_root: Path) -> None:
    # tmp_path exists but is empty.
    assert list_teams() == []


def test_list_teams_missing_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(missing))
    assert not missing.exists()
    assert list_teams() == []


def test_list_teams_with_two(_isolated_teams_root: Path) -> None:
    create_team("alpha")
    create_team("beta")
    join_team("alpha", "a1")
    join_team("alpha", "a2")
    join_team("beta", "b1")

    rows = list_teams()

    assert [r["name"] for r in rows] == ["alpha", "beta"]
    assert rows[0]["members"] == ["a1", "a2"]
    assert rows[1]["members"] == ["b1"]
    for r in rows:
        assert r["tasks_total"] == 0
        assert r["tasks_open"] == 0
        assert r["tasks_claimed"] == 0
        assert r["tasks_completed"] == 0
        assert Path(r["dir"]).is_dir()


def test_list_teams_counts_correct(_isolated_teams_root: Path) -> None:
    team = create_team("project")
    join_team("project", "alice")
    join_team("project", "bob")

    open_id = team.add_task("open task")  # noqa: F841 — left open intentionally
    claimed_id = team.add_task("claimed task")
    done_id = team.add_task("done task")

    assert team.claim_task(claimed_id, "alice")
    assert team.claim_task(done_id, "bob")
    assert team.complete_task(done_id, "bob", result="finished")

    rows = list_teams()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "project"
    assert set(row["members"]) == {"alice", "bob"}
    assert row["tasks_total"] == 3
    assert row["tasks_open"] == 1
    assert row["tasks_claimed"] == 1
    assert row["tasks_completed"] == 1


def test_list_teams_ignores_non_team_dirs(_isolated_teams_root: Path) -> None:
    create_team("real")
    # Stray dir with no config.json must be skipped.
    (_isolated_teams_root / "stray").mkdir()
    (_isolated_teams_root / "stray" / "notes.txt").write_text("hi")
    # Stray file at the root must also be skipped.
    (_isolated_teams_root / "loose.txt").write_text("hello")

    rows = list_teams()
    assert [r["name"] for r in rows] == ["real"]


def test_list_teams_uses_default_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without an explicit ``root`` arg the function falls back to ``teams_root()``."""
    monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))
    assert teams_root() == tmp_path
    create_team("solo")
    rows = list_teams()  # no root override — should still find "solo"
    assert [r["name"] for r in rows] == ["solo"]


def test_list_teams_explicit_root_override(tmp_path: Path) -> None:
    """An explicit ``root`` arg overrides the env-var default."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    Team("here", root=other).init()
    rows = list_teams(root=other)
    assert [r["name"] for r in rows] == ["here"]
