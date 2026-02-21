# tests/test_tools_replace.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.replace_in_file import ReplaceInFileTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestReplaceInFileTool:
    def test_replace_all_occurrences(self, env):
        env.write_file("main.py", "x = 1\ny = 1\nz = 1\n")
        tool = ReplaceInFileTool()
        result = tool.execute({
            "path": "main.py",
            "pattern": "= 1",
            "replacement": "= 2",
        }, env)
        assert result.success
        content = env.read_file("main.py")
        assert content.count("= 2") == 3
        assert "= 1" not in content

    def test_regex_replace(self, env):
        env.write_file("main.py", "foo_bar = 1\nfoo_baz = 2\n")
        tool = ReplaceInFileTool()
        result = tool.execute({
            "path": "main.py",
            "pattern": r"foo_(\w+)",
            "replacement": r"bar_\1",
        }, env)
        assert result.success
        content = env.read_file("main.py")
        assert "bar_bar" in content
        assert "bar_baz" in content

    def test_no_match(self, env):
        env.write_file("main.py", "hello\n")
        tool = ReplaceInFileTool()
        result = tool.execute({
            "path": "main.py",
            "pattern": "NOPE",
            "replacement": "yes",
        }, env)
        assert result.success
        assert "0" in result.output or "no" in result.output.lower()

    def test_file_not_found(self, env):
        tool = ReplaceInFileTool()
        result = tool.execute({
            "path": "nope.py",
            "pattern": "a",
            "replacement": "b",
        }, env)
        assert not result.success

    def test_schema(self):
        tool = ReplaceInFileTool()
        assert tool.name == "replace_in_file"
