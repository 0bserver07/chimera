"""Tests for the live team-watch dashboard (``chimera.mink.team_watch``)."""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest

from chimera.cli.agent_teams import Team, TeamMailbox, create_team
from chimera.mink.team_watch import (
    ANSI_CLEAR,
    main,
    render_team_status,
    watch_team,
)


@pytest.fixture
def teams_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate ``teams_root()`` so tests never touch ``~/.chimera/teams``."""
    monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# render_team_status
# ---------------------------------------------------------------------------


def test_render_empty_team(teams_home: Path) -> None:
    """A freshly-created team with no tasks or mail renders without crashing."""
    create_team("alpha")

    team = Team("alpha", root=teams_home)
    out = render_team_status(team)

    assert isinstance(out, str)
    assert "team: alpha" in out
    assert "members (0): -" in out
    assert "tasks: open=0 claimed=0 completed=0 total=0" in out
    assert "(no tasks yet)" in out
    assert "(no messages yet)" in out


def test_render_with_tasks(teams_home: Path) -> None:
    """Task counts and recent rows include open / claimed / completed states."""
    create_team("beta")
    team = Team("beta", root=teams_home)
    team.add_member("alice")
    team.add_member("bob")

    open_id = team.add_task("review error messages", created_by="lead")
    claim_id = team.add_task("check test coverage", created_by="lead")
    done_id = team.add_task("review auth module", created_by="lead")

    assert team.claim_task(claim_id, "bob")
    assert team.claim_task(done_id, "alice")
    assert team.complete_task(done_id, "alice", result="looks ok")

    out = render_team_status(team)

    # Members list survives the round-trip.
    assert "members (2): alice, bob" in out

    # Counts.
    assert "tasks: open=1 claimed=1 completed=1 total=3" in out

    # Each status appears as a left-padded label and the descriptions
    # show up in the recent block. Width is 9 chars so "completed"
    # (already 9 wide) renders without trailing space.
    assert "[completed]" in out
    assert "[claimed  ]" in out
    assert "[open     ]" in out
    assert "review auth module" in out
    assert "check test coverage" in out
    assert "review error messages" in out

    # Completion result is appended after "->".
    assert "looks ok" in out

    # open_id should also be in the recent block (no result so no "->").
    assert open_id  # add_task always returns an id


def test_render_with_mailbox(teams_home: Path) -> None:
    """Mailbox files are read non-destructively and included in the block."""
    create_team("gamma")
    team = Team("gamma", root=teams_home)
    team.add_member("alice")
    team.add_member("bob")

    bob_inbox = TeamMailbox(team, "bob")
    alice_inbox = TeamMailbox(team, "alice")
    # Sleep just enough so timestamps are strictly ordered. Without this
    # the assertion on chronological order would be racy on fast disks.
    bob_inbox.send("alice", "the auth file is at chimera/auth.py")
    time.sleep(0.01)
    alice_inbox.send("codex-1", "claimed task 3")

    out = render_team_status(team)

    # Header for the mailbox section.
    assert "recent mailbox activity" in out
    # Both messages appear.
    assert "alice    -> bob" in out
    assert "codex-1  -> alice" in out
    assert "the auth file is at" in out
    assert "claimed task 3" in out
    # No "(no messages yet)" placeholder when real messages exist.
    assert "(no messages yet)" not in out

    # Non-destructive: messages must still be readable after rendering.
    leftover = TeamMailbox(team, "bob").recv()
    assert len(leftover) == 1
    assert leftover[0]["content"] == "the auth file is at chimera/auth.py"


def test_render_skips_partial_jsonl_lines(teams_home: Path) -> None:
    """A torn write (partial JSON) must not crash the renderer."""
    create_team("delta")
    team = Team("delta", root=teams_home)
    team.add_member("alice")

    inbox = team.mailbox_dir / "alice.jsonl"
    inbox.write_text(
        json.dumps({"from": "bob", "to": "alice", "content": "good", "ts": 1.0})
        + "\n"
        + '{"from": "bob", "to": "alice", "content": "tr'  # truncated line
        + "\n",
        encoding="utf-8",
    )

    out = render_team_status(team)
    assert "good" in out
    # The partial line is skipped silently — no exception, no garbage.
    assert "tr" not in out.split("recent mailbox activity")[1]


def test_render_handles_missing_team_directory(tmp_path: Path) -> None:
    """A Team pointing at a non-existent dir renders empty rather than raising."""
    team = Team("nope", root=tmp_path)
    out = render_team_status(team)
    assert "team: nope" in out
    assert "members (0): -" in out
    assert "(no tasks yet)" in out


# ---------------------------------------------------------------------------
# watch_team
# ---------------------------------------------------------------------------


def test_watch_team_stops_after_n_renders(teams_home: Path) -> None:
    """``stop_after_n_renders=3`` exits cleanly after exactly 3 frames."""
    create_team("epsilon")
    sink = io.StringIO()
    rc = watch_team(
        "epsilon",
        root=teams_home,
        interval=0.0,
        stdout=sink,
        stop_after_n_renders=3,
    )
    assert rc == 0
    output = sink.getvalue()
    assert output.count(ANSI_CLEAR) == 3
    assert output.count("team: epsilon") == 3


def test_watch_team_returns_one_when_team_missing(tmp_path: Path) -> None:
    """Watching a non-existent team prints to stderr and returns 1."""
    sink = io.StringIO()
    rc = watch_team(
        "ghost",
        root=tmp_path,
        interval=0.0,
        stdout=sink,
        stop_after_n_renders=1,
    )
    assert rc == 1
    # Nothing should have been written to the dashboard sink.
    assert sink.getvalue() == ""


def test_watch_team_handles_keyboard_interrupt(
    teams_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Ctrl-C during the sleep returns 0 (clean shutdown)."""
    create_team("zeta")

    def raise_kbd(_: float) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr("chimera.mink.team_watch.time.sleep", raise_kbd)
    sink = io.StringIO()
    rc = watch_team("zeta", root=teams_home, interval=0.1, stdout=sink)
    assert rc == 0
    # One frame should have rendered before the interrupt.
    assert "team: zeta" in sink.getvalue()


# ---------------------------------------------------------------------------
# main / argparse
# ---------------------------------------------------------------------------


def test_main_argparse_missing_team(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``main`` returns non-zero with a clear error when the team is missing."""
    rc = main([
        "--team", "x",
        "--teams-home", str(tmp_path),
        "--interval", "0.01",
    ])
    assert rc != 0
    captured = capsys.readouterr()
    assert "x" in captured.err
    assert "does not exist" in captured.err


def test_main_argparse_requires_team(capsys: pytest.CaptureFixture[str]) -> None:
    """``--team`` is required; argparse exits with code 2."""
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "--team" in captured.err
