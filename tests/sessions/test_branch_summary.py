"""Tests for SessionTree.summarize_branch (branch summarization)."""
from chimera.sessions.tree import CompactionEntry, SessionTree
from chimera.types import Message


def _fake_summarizer(messages: list[Message]) -> str:
    """A provider-agnostic fake: encodes the count so tests can assert on it."""
    return f"summary of {len(messages)} messages"


def test_summarize_active_branch_stores_and_returns_id(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    tree.add_message(Message.user("hello"))
    tree.add_message(Message.assistant("hi"))
    tree.add_message(Message.user("more"))

    new_id = tree.summarize_branch(None, _fake_summarizer)

    # The returned id resolves within the tree.
    assert new_id in {e.id for e in tree.get_branch(new_id)}
    branch = tree.get_branch(new_id)
    assert branch[-1].id == new_id
    last = branch[-1]
    assert isinstance(last, CompactionEntry)
    assert last.summary == "summary of 3 messages"


def test_summary_is_retrievable_via_get_messages(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    tree.add_message(Message.user("hello"))
    tree.add_message(Message.assistant("hi"))

    tree.summarize_branch(None, lambda msgs: "the branch did stuff")

    msgs = tree.get_messages()
    assert any("the branch did stuff" in m.content for m in msgs)


def test_summarize_advances_active_leaf(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    tree.add_message(Message.user("hello"))

    new_id = tree.summarize_branch(None, _fake_summarizer)

    assert tree.active_leaf == new_id


def test_summary_persists_across_reload(tmp_path):
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    tree.add_message(Message.user("hello"))
    tree.add_message(Message.assistant("hi"))
    new_id = tree.summarize_branch(None, _fake_summarizer)

    reloaded = SessionTree(path)
    branch = reloaded.get_branch(new_id)
    assert branch[-1].id == new_id
    last = branch[-1]
    assert isinstance(last, CompactionEntry)
    assert last.summary == "summary of 2 messages"


def test_summarize_specific_non_active_branch(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    root = tree.add_message(Message.user("root"))
    tree.add_message(Message.assistant("main-a"))
    main_leaf = tree.add_message(Message.user("main-b"))
    # Fork a second branch off the root.
    tree.fork(root)
    alt_leaf = tree.add_message(Message.assistant("alt-a"))

    # Active leaf is currently the alt branch; summarize the *main* branch.
    assert tree.active_leaf == alt_leaf
    new_id = tree.summarize_branch(main_leaf, _fake_summarizer)

    # Summary attaches to the summarized (main) branch: main had 3 messages.
    branch = tree.get_branch(new_id)
    last = branch[-1]
    assert isinstance(last, CompactionEntry)
    assert last.summary == "summary of 3 messages"
    assert last.parent_id == main_leaf
    # The alt branch is untouched — its messages don't include the summary.
    alt_msgs = tree.get_messages(alt_leaf)
    assert [m.content for m in alt_msgs] == ["root", "alt-a"]


def test_summarize_empty_tree_is_graceful(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")

    new_id = tree.summarize_branch(None, _fake_summarizer)

    # Summarizer was called with an empty list; the summary is still stored.
    branch = tree.get_branch(new_id)
    assert branch[-1].id == new_id
    last = branch[-1]
    assert isinstance(last, CompactionEntry)
    assert last.summary == "summary of 0 messages"


def test_summarize_unknown_leaf_raises(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    tree.add_message(Message.user("hello"))

    try:
        tree.summarize_branch("does-not-exist", _fake_summarizer)
    except ValueError as exc:
        assert "not found" in str(exc)
    else:  # pragma: no cover - failure path
        raise AssertionError("expected ValueError for unknown leaf id")


def test_summarizer_error_leaves_tree_unchanged(tmp_path):
    tree = SessionTree(tmp_path / "session.jsonl")
    tree.add_message(Message.user("hello"))
    leaf = tree.add_message(Message.assistant("hi"))
    count_before = tree.entry_count

    def boom(_messages: list[Message]) -> str:
        raise RuntimeError("summarizer failed")

    try:
        tree.summarize_branch(None, boom)
    except RuntimeError:
        pass

    # No compaction entry was appended and the active leaf did not move.
    assert tree.entry_count == count_before
    assert tree.active_leaf == leaf


def test_existing_tree_behavior_still_works_after_summary(tmp_path):
    """Sanity: summarizing does not break normal add/branch/get flow."""
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    tree.add_message(Message.user("hello"))
    tree.add_message(Message.assistant("hi"))
    tree.summarize_branch(None, _fake_summarizer)

    # Continue the conversation after the summary.
    tree.add_message(Message.user("what next?"))
    msgs = tree.get_messages()
    assert msgs[-1].content == "what next?"
    assert any("summary of 2 messages" in m.content for m in msgs)

    # Persistence round-trips the whole branch including the summary.
    reloaded = SessionTree(path)
    reloaded_msgs = reloaded.get_messages()
    assert reloaded_msgs[-1].content == "what next?"
    assert any("summary of 2 messages" in m.content for m in reloaded_msgs)
