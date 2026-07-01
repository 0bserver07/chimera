"""Tests for resumable per-lane sessions (spec §13.2)."""
import shutil
import subprocess
from types import SimpleNamespace

import pytest

pytest.importorskip("rich")

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.tui.cohort import Cohort  # noqa: E402
from chimera.tui.history_io import (  # noqa: E402
    deserialize_history,
    serialize_history,
)
from chimera.tui.lane import Lane, LaneConfig  # noqa: E402
from chimera.tui.workspace import apply_diff, provision_workspaces  # noqa: E402
from chimera.types import Message, ToolCall  # noqa: E402

_HAS_GIT = shutil.which("git") is not None
requires_git = pytest.mark.skipif(not _HAS_GIT, reason="git not installed")


# -- history codec ------------------------------------------------------
def test_history_codec_roundtrips_tool_calls_and_images():
    hist = [
        Message.user("fix the bug"),
        Message.assistant("reading", tool_calls=[ToolCall(id="t1", name="read_file", arguments={"path": "a.py"})]),
        Message.tool("t1", "file contents"),
        Message.user_with_image("look", "BASE64", "image/png"),
        Message.assistant("done"),
    ]
    back = deserialize_history(serialize_history(hist))
    assert [m.role for m in back] == ["user", "assistant", "tool", "user", "assistant"]
    assert back[1].tool_calls[0].id == "t1"
    assert back[1].tool_calls[0].name == "read_file"
    assert back[1].tool_calls[0].arguments == {"path": "a.py"}
    assert back[2].call_id == "t1"
    assert back[3].has_images  # image content block survives


def test_serialize_history_is_json_safe():
    import json

    rows = serialize_history([Message.user("hi"), Message.assistant("yo")])
    assert json.loads(json.dumps(rows)) == rows


# -- cohort persistence & resume loading --------------------------------
def _lane_with_history(lid, model, hist):
    return Lane(LaneConfig(lid, lid, model), driver=SimpleNamespace(history=hist), workspace=None)


def test_persist_writes_history_and_load_saved_reads_it(tmp_path):
    hist = [
        Message.user("do it"),
        Message.assistant("ok", tool_calls=[ToolCall(id="1", name="bash", arguments={"cmd": "ls"})]),
        Message.tool("1", "a.py b.py"),
    ]
    a = _lane_with_history("A", "glm-5.2", hist)
    # give A some telemetry so restore has something to carry
    a.on_turn_begin()
    a.record(LoopEvent(LoopEventType.result, SimpleNamespace(
        reason="completed", turn_count=3, cost_usd=0.004,
        usage={"input_tokens": 200, "output_tokens": 60}, messages=[]), 0))
    a.on_turn_end(order=1)

    co = Cohort([a], task="do it", source="/repo", isolation="worktree")
    out = co.persist(root=tmp_path / "cohorts")
    assert (out / "lane-A.history.json").exists()

    loaded = Cohort.load_saved(co.cohort_id, root=tmp_path / "cohorts")
    assert loaded["manifest"]["task"] == "do it"
    lane0 = loaded["lanes"][0]
    assert lane0["lane_id"] == "A"
    assert lane0["telemetry"]["cost"] == 0.004
    restored = deserialize_history(lane0["history"])
    assert restored[1].tool_calls[0].name == "bash"


def test_list_saved_orders_and_summarizes(tmp_path):
    root = tmp_path / "cohorts"
    for task in ("first", "second"):
        Cohort([_lane_with_history("A", "glm-5.2", [Message.user(task)])],
               task=task, isolation="worktree").persist(root=root)
    rows = Cohort.list_saved(root=root)
    assert len(rows) == 2
    assert all("cohort_id" in r and "task" in r and "lanes" in r for r in rows)


def test_load_saved_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Cohort.load_saved("nope-0000", root=tmp_path / "cohorts")


# -- workspace restore (the hard part) ----------------------------------
def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "x.txt").write_text("base\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                          capture_output=True, text=True).stdout.strip()


@requires_git
def test_resume_restores_workspace_from_base_and_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    head = _init_repo(repo)

    # first run: produce changes, capture the diff, tear down
    ws1 = provision_workspaces(repo, ["A"], "worktree")
    (ws1[0].path / "x.txt").write_text("CHANGED\n")
    (ws1[0].path / "new.py").write_text("print(1)\n")
    diff = ws1[0].diff()
    ws1.cleanup_all()
    assert "CHANGED" in diff and "new.py" in diff

    # resume: fresh worktree from the SAME base commit + re-apply the diff
    ws2 = provision_workspaces(repo, ["A"], "worktree", base_commit=head)
    try:
        assert (ws2[0].path / "x.txt").read_text() == "base\n"  # clean base
        assert apply_diff(ws2[0].path, diff) is True
        assert (ws2[0].path / "x.txt").read_text() == "CHANGED\n"   # modified restored
        assert (ws2[0].path / "new.py").exists()                    # new file restored
    finally:
        ws2.cleanup_all()


def test_apply_diff_empty_is_noop(tmp_path):
    assert apply_diff(tmp_path, "") is True
    assert apply_diff(tmp_path, "   \n") is True


# -- driver seeding -----------------------------------------------------
def test_driver_load_history_seeds_conversation(tmp_path):
    try:
        from chimera.assembly.driver import AgentDriver
        driver = AgentDriver(model="glm-5.2", project_dir=str(tmp_path))
    except Exception:  # noqa: BLE001 - construction needs a provider; skip if unavailable
        pytest.skip("AgentDriver could not be constructed in this environment")
    hist = [Message.user("earlier task"), Message.assistant("earlier answer")]
    driver.load_history(hist)
    assert driver.history == hist
