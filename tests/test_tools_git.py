# tests/test_tools_git.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.git import GitTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        # Initialize a git repo
        e.run_command("git init")
        e.run_command("git config user.email 'test@test.com'")
        e.run_command("git config user.name 'Test'")
        yield e
        e.cleanup()


class TestGitTool:
    def test_git_status(self, env):
        tool = GitTool()
        result = tool.execute({"command": "status"}, env)
        assert result.success

    def test_git_add_and_commit(self, env):
        env.write_file("test.txt", "hello")
        tool = GitTool()
        result = tool.execute({"command": "add test.txt"}, env)
        assert result.success
        result = tool.execute({"command": "commit -m 'initial'"}, env)
        assert result.success

    def test_git_log(self, env):
        env.write_file("test.txt", "hello")
        tool = GitTool()
        tool.execute({"command": "add test.txt"}, env)
        tool.execute({"command": "commit -m 'initial'"}, env)
        result = tool.execute({"command": "log --oneline"}, env)
        assert result.success
        assert "initial" in result.output

    def test_git_diff(self, env):
        env.write_file("test.txt", "hello")
        tool = GitTool()
        tool.execute({"command": "add test.txt"}, env)
        tool.execute({"command": "commit -m 'initial'"}, env)
        env.write_file("test.txt", "hello world")
        result = tool.execute({"command": "diff"}, env)
        assert result.success

    def test_blocked_commands(self, env):
        tool = GitTool()
        result = tool.execute({"command": "push --force"}, env)
        assert not result.success
        assert "blocked" in result.error.lower() or "not allowed" in result.error.lower()

    def test_schema(self):
        tool = GitTool()
        assert tool.name == "git"
