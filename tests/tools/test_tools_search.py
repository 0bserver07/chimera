# tests/test_tools_search.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.search import SearchTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestSearchTool:
    def test_search_finds_match(self, env):
        env.write_file("main.py", "def hello():\n    return 'hi'\n")
        tool = SearchTool()
        result = tool.execute({"pattern": "hello", "path": "."}, env)
        assert result.success
        assert "main.py" in result.output

    def test_search_no_match(self, env):
        env.write_file("main.py", "def hello():\n    pass\n")
        tool = SearchTool()
        result = tool.execute({"pattern": "NONEXISTENT", "path": "."}, env)
        assert result.success
        assert result.output.strip() == "" or "no matches" in result.output.lower()

    def test_search_specific_file(self, env):
        env.write_file("a.py", "foo = 1\n")
        env.write_file("b.py", "bar = 2\n")
        tool = SearchTool()
        result = tool.execute({"pattern": "foo", "path": "a.py"}, env)
        assert result.success
        assert "a.py" in result.output

    def test_search_glob_filter(self, env):
        env.write_file("main.py", "hello\n")
        env.write_file("main.txt", "hello\n")
        tool = SearchTool()
        result = tool.execute({"pattern": "hello", "path": ".", "glob": "*.py"}, env)
        assert result.success
        assert "main.py" in result.output

    def test_schema(self):
        tool = SearchTool()
        assert tool.name == "search"
        schema = tool.to_anthropic_schema()
        assert "pattern" in str(schema)
