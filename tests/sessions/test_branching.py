"""Tests for SessionTree branching."""


from chimera.sessions.branching import SessionTree
from chimera.types import Message


class TestSessionTree:
    def test_initial_main_branch(self):
        tree = SessionTree()
        assert tree.active_branch_id == "main"
        assert "main" in tree.branches
        assert tree.active_branch.name == "main"
        assert tree.messages == []

    def test_add_message_to_active(self):
        tree = SessionTree()
        msg = Message.user("hello")
        tree.add_message(msg)
        assert len(tree.messages) == 1
        assert tree.messages[0].content == "hello"

    def test_fork_creates_new_branch(self):
        tree = SessionTree()
        tree.add_message(Message.user("hello"))
        tree.add_message(Message.assistant("hi"))

        branch_id = tree.fork(name="experiment")
        assert branch_id in tree.branches
        assert tree.active_branch_id == branch_id
        assert tree.branches[branch_id].parent_branch_id == "main"
        assert tree.branches[branch_id].name == "experiment"

    def test_fork_inherits_parent_messages(self):
        tree = SessionTree()
        tree.add_message(Message.user("hello"))
        tree.add_message(Message.assistant("hi"))

        tree.fork(name="child")
        # Should inherit the two parent messages
        assert len(tree.messages) == 2
        assert tree.messages[0].content == "hello"
        assert tree.messages[1].content == "hi"

        # Adding a message to the child branch
        tree.add_message(Message.user("new question"))
        assert len(tree.messages) == 3
        assert tree.messages[2].content == "new question"

    def test_switch_branch(self):
        tree = SessionTree()
        tree.add_message(Message.user("hello"))
        branch_id = tree.fork(name="alt")
        tree.add_message(Message.user("alt message"))

        # Switch back to main
        assert tree.switch("main") is True
        assert tree.active_branch_id == "main"
        assert len(tree.messages) == 1

        # Switch to alt
        assert tree.switch(branch_id) is True
        assert tree.active_branch_id == branch_id
        assert len(tree.messages) == 2  # 1 inherited + 1 own

        # Switch to nonexistent
        assert tree.switch("does-not-exist") is False

    def test_list_branches(self):
        tree = SessionTree()
        tree.add_message(Message.user("hello"))
        tree.add_message(Message.assistant("hi"))
        branch_id = tree.fork(name="experiment")
        tree.add_message(Message.user("branch msg"))

        branches = tree.list_branches()
        assert len(branches) == 2

        main_info = next(b for b in branches if b["id"] == "main")
        assert main_info["name"] == "main"
        assert main_info["message_count"] == 2
        assert main_info["active"] is False

        child_info = next(b for b in branches if b["id"] == branch_id)
        assert child_info["name"] == "experiment"
        assert child_info["parent"] == "main"
        assert child_info["message_count"] == 3  # 2 inherited + 1 own
        assert child_info["active"] is True

    def test_tree_view(self):
        tree = SessionTree()
        tree.add_message(Message.user("hello"))
        tree.fork(name="child-a")
        tree.switch("main")
        tree.fork(name="child-b")

        view = tree.tree_view()
        assert "main" in view
        assert "child-a" in view
        assert "child-b" in view
        # Active branch should have asterisk marker
        assert "*" in view

    def test_fork_at_specific_message(self):
        tree = SessionTree()
        tree.add_message(Message.user("msg0"))
        tree.add_message(Message.assistant("msg1"))
        tree.add_message(Message.user("msg2"))

        # Fork at message index 1 (only inherit msg0)
        branch_id = tree.fork(at_message=1, name="early-fork")
        assert tree.branches[branch_id].fork_point == 1
        # Should only inherit 1 message from parent
        assert len(tree.messages) == 1
        assert tree.messages[0].content == "msg0"

        # Add a message and verify
        tree.add_message(Message.user("diverged"))
        assert len(tree.messages) == 2
        assert tree.messages[1].content == "diverged"
