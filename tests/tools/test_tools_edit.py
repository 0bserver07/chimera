# tests/test_tools_edit.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.edit import EditFileTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestEditFileTool:
    def test_replace_exact_match(self, env):
        env.write_file("main.py", "def hello():\n    return 'hi'\n")
        tool = EditFileTool()
        result = tool.execute({
            "path": "main.py",
            "old_string": "return 'hi'",
            "new_string": "return 'hello'",
        }, env)
        assert result.success
        assert env.read_file("main.py") == "def hello():\n    return 'hello'\n"

    def test_replace_not_found(self, env):
        env.write_file("main.py", "def hello():\n    pass\n")
        tool = EditFileTool()
        result = tool.execute({
            "path": "main.py",
            "old_string": "NONEXISTENT",
            "new_string": "something",
        }, env)
        assert not result.success
        assert "not found" in result.error.lower()

    def test_replace_ambiguous(self, env):
        env.write_file("main.py", "x = 1\nx = 1\n")
        tool = EditFileTool()
        result = tool.execute({
            "path": "main.py",
            "old_string": "x = 1",
            "new_string": "x = 2",
        }, env)
        assert not result.success
        assert "ambiguous" in result.error.lower() or "multiple" in result.error.lower()

    def test_file_not_found(self, env):
        tool = EditFileTool()
        result = tool.execute({
            "path": "nope.py",
            "old_string": "a",
            "new_string": "b",
        }, env)
        assert not result.success

    def test_schema(self):
        tool = EditFileTool()
        assert tool.name == "edit_file"
        schema = tool.to_anthropic_schema()
        assert "old_string" in str(schema)
        assert "new_string" in str(schema)
