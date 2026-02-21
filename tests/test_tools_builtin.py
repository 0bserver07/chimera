import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.read import ReadFileTool
from chimera.tools.write import WriteFileTool
from chimera.tools.bash import BashTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestReadFileTool:
    def test_read_existing_file(self, env):
        env.write_file("test.txt", "hello world")
        tool = ReadFileTool()
        result = tool.execute({"path": "test.txt"}, env)
        assert result.success
        assert result.output == "hello world"

    def test_read_nonexistent_file(self, env):
        tool = ReadFileTool()
        result = tool.execute({"path": "nope.txt"}, env)
        assert not result.success
        assert "not found" in result.error.lower()

    def test_schema(self):
        tool = ReadFileTool()
        schema = tool.to_anthropic_schema()
        assert schema["name"] == "read_file"


class TestWriteFileTool:
    def test_write_new_file(self, env):
        tool = WriteFileTool()
        result = tool.execute({"path": "out.txt", "content": "data"}, env)
        assert result.success
        assert env.read_file("out.txt") == "data"

    def test_write_creates_dirs(self, env):
        tool = WriteFileTool()
        result = tool.execute({"path": "a/b/c.txt", "content": "deep"}, env)
        assert result.success
        assert env.read_file("a/b/c.txt") == "deep"

    def test_schema(self):
        tool = WriteFileTool()
        assert tool.name == "write_file"


class TestBashTool:
    def test_run_simple_command(self, env):
        tool = BashTool()
        result = tool.execute({"command": "echo hello"}, env)
        assert result.success
        assert "hello" in result.output

    def test_run_failing_command(self, env):
        tool = BashTool()
        result = tool.execute({"command": "false"}, env)
        assert not result.success

    def test_schema(self):
        tool = BashTool()
        assert tool.name == "bash"
