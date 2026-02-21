# tests/test_tools_list_files.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.list_files import ListFilesTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestListFilesTool:
    def test_list_all_files(self, env):
        env.write_file("a.py", "x")
        env.write_file("b.txt", "y")
        tool = ListFilesTool()
        result = tool.execute({"path": "."}, env)
        assert result.success
        assert "a.py" in result.output
        assert "b.txt" in result.output

    def test_list_with_glob(self, env):
        env.write_file("a.py", "x")
        env.write_file("b.txt", "y")
        tool = ListFilesTool()
        result = tool.execute({"path": ".", "glob": "*.py"}, env)
        assert result.success
        assert "a.py" in result.output
        assert "b.txt" not in result.output

    def test_list_subdirectory(self, env):
        env.write_file("src/main.py", "x")
        env.write_file("tests/test.py", "y")
        tool = ListFilesTool()
        result = tool.execute({"path": "src"}, env)
        assert result.success
        assert "main.py" in result.output

    def test_empty_directory(self, env):
        tool = ListFilesTool()
        result = tool.execute({"path": "."}, env)
        assert result.success

    def test_schema(self):
        tool = ListFilesTool()
        assert tool.name == "list_files"
