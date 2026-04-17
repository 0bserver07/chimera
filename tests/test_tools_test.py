# tests/test_tools_test.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.test import TestTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="python -m pytest")
        e.setup()
        yield e
        e.cleanup()


class TestTestTool:
    def test_run_all_tests(self, env):
        env.write_file("test_hello.py", "def test_pass():\n    assert True\n")
        tool = TestTool()
        result = tool.execute({}, env)
        assert result.success
        assert "1 passed" in result.output

    def test_run_specific_file(self, env):
        env.write_file("test_a.py", "def test_a():\n    assert True\n")
        env.write_file("test_b.py", "def test_b():\n    assert False\n")
        tool = TestTool()
        result = tool.execute({"path": "test_a.py"}, env)
        assert result.success
        assert "1 passed" in result.output

    def test_run_failing_test(self, env):
        env.write_file("test_fail.py", "def test_fail():\n    assert False\n")
        tool = TestTool()
        result = tool.execute({}, env)
        assert "failed" in result.output.lower()

    def test_specific_file_failure_sets_error(self, env):
        """When a targeted test file fails, ToolResult must signal error."""
        env.write_file("test_fail.py", "def test_fail():\n    assert False\n")
        tool = TestTool()
        result = tool.execute({"path": "test_fail.py"}, env)
        assert result.error is not None
        assert "failed" in result.error.lower() or "exit" in result.error.lower()
        # Output still contains the pytest report.
        assert "FAILED" in result.output or "failed" in result.output.lower()

    def test_specific_file_success_no_error(self, env):
        env.write_file("test_ok.py", "def test_ok():\n    assert True\n")
        tool = TestTool()
        result = tool.execute({"path": "test_ok.py"}, env)
        assert result.error is None
        assert result.success

    def test_schema(self):
        tool = TestTool()
        assert tool.name == "test"
