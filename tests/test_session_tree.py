"""Tests for chimera.sessions.tree."""
from chimera.sessions.tree import SessionTree
from chimera.types import Message, ToolCall


def test_create_empty_tree(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    assert tree.entry_count == 0
    assert tree.active_leaf is None


def test_add_message(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    entry_id = tree.add_message(Message.user("hello"))
    assert tree.entry_count == 1
    assert tree.active_leaf == entry_id


def test_get_messages(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    tree.add_message(Message.user("hello"))
    tree.add_message(Message.assistant("hi"))
    msgs = tree.get_messages()
    assert len(msgs) == 2
    assert msgs[0].content == "hello"
    assert msgs[1].content == "hi"


def test_persistence(tmp_path):
    path = tmp_path / "session.jsonl"
    tree1 = SessionTree(path)
    tree1.add_message(Message.user("hello"))
    tree1.add_message(Message.assistant("hi"))
    tree2 = SessionTree(path)
    assert tree2.entry_count == 2
    msgs = tree2.get_messages()
    assert msgs[0].content == "hello"
    assert msgs[1].content == "hi"


def test_fork_and_branch(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    tree.add_message(Message.user("hello"))
    id2 = tree.add_message(Message.assistant("hi"))
    tree.add_message(Message.user("continue"))
    tree.fork(id2)
    tree.add_message(Message.user("different path"))
    msgs = tree.get_messages()
    assert len(msgs) == 3
    assert msgs[2].content == "different path"


def test_get_leaves(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    id1 = tree.add_message(Message.user("hello"))
    id2 = tree.add_message(Message.assistant("hi"))
    tree.fork(id1)
    id3 = tree.add_message(Message.user("branch"))
    leaves = tree.get_leaves()
    assert len(leaves) == 2
    assert id2 in leaves
    assert id3 in leaves


def test_switch_branch(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    id1 = tree.add_message(Message.user("hello"))
    id2 = tree.add_message(Message.assistant("hi"))
    tree.fork(id1)
    id3 = tree.add_message(Message.user("branch"))
    tree.switch_branch(id2)
    msgs = tree.get_messages()
    assert msgs[-1].content == "hi"
    tree.switch_branch(id3)
    msgs = tree.get_messages()
    assert msgs[-1].content == "branch"


def test_tool_calls_preserved(tmp_path):
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    tc = ToolCall(id="tc1", name="read_file", arguments={"path": "a.py"})
    tree.add_message(Message.assistant("reading", tool_calls=[tc]))
    tree2 = SessionTree(path)
    msgs = tree2.get_messages()
    assert len(msgs[0].tool_calls) == 1
    assert msgs[0].tool_calls[0].name == "read_file"


def test_call_id_preserved(tmp_path):
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    tree.add_message(Message.tool("tc1", "file contents"))
    tree2 = SessionTree(path)
    msgs = tree2.get_messages()
    assert msgs[0].call_id == "tc1"


def test_compaction_entry(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    tree.add_message(Message.user("hello"))
    tree.add_compaction(
        summary="Did stuff", first_kept_id="abc",
        tokens_before=5000, read_files=["a.py"], modified_files=["b.py"],
    )
    msgs = tree.get_messages()
    assert any("Did stuff" in m.content for m in msgs)


def test_label_entry(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    id1 = tree.add_message(Message.user("hello"))
    tree.add_label(id1, "checkpoint-1")
    assert tree.entry_count == 2


def test_branch_points(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    id1 = tree.add_message(Message.user("hello"))
    tree.add_message(Message.assistant("hi"))
    tree.fork(id1)
    tree.add_message(Message.user("branch"))
    points = tree.get_branch_points()
    assert id1 in points


def test_corrupt_line_skipped(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text('{"type":"message","id":"a","parent_id":null,"timestamp":0,"message":{"role":"user","content":"hi"}}\n{CORRUPT\n{"type":"message","id":"b","parent_id":"a","timestamp":1,"message":{"role":"assistant","content":"hello"}}\n')
    tree = SessionTree(path)
    assert tree.entry_count == 2
    msgs = tree.get_messages()
    assert msgs[0].content == "hi"
    assert msgs[1].content == "hello"
