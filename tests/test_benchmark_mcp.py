"""Tests for chimera.mcp_servers.benchmark_server — benchmark MCP server."""
from __future__ import annotations

from chimera.mcp_servers.benchmark_server import (
    BenchmarkMCPServer,
    get_humaneval_problem,
    run_eval,
)


class TestRunEval:
    """Test code evaluation in subprocess."""

    def test_passing_code(self) -> None:
        code = "def add(a, b): return a + b"
        test_code = "assert add(1, 2) == 3\nassert add(0, 0) == 0\nprint('ok')"

        result = run_eval(code, test_code, timeout=10)

        assert result.passed is True
        assert "ok" in result.output

    def test_failing_code(self) -> None:
        code = "def add(a, b): return a - b"  # intentional bug
        test_code = "assert add(1, 2) == 3"

        result = run_eval(code, test_code, timeout=10)

        assert result.passed is False
        assert result.returncode != 0

    def test_timeout(self) -> None:
        code = "import time\ndef slow(): time.sleep(100)"
        test_code = "slow()"

        result = run_eval(code, test_code, timeout=1)

        assert result.passed is False
        assert "timed out" in result.error.lower()


class TestHumanEvalProblems:
    """Test HumanEval problem retrieval."""

    def test_get_problem_by_id(self) -> None:
        problem = get_humaneval_problem("0")
        assert problem is not None
        assert "prompt" in problem
        assert "test" in problem
        assert "has_close_elements" in problem["prompt"]

    def test_get_problem_with_prefix(self) -> None:
        problem = get_humaneval_problem("HumanEval/0")
        assert problem is not None
        assert "has_close_elements" in problem["prompt"]

    def test_missing_problem_returns_none(self) -> None:
        problem = get_humaneval_problem("999")
        assert problem is None


class TestBenchmarkMCPServer:
    """Test the MCP server message handling."""

    def test_initialize_and_list_tools(self) -> None:
        server = BenchmarkMCPServer()

        resp = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        assert resp is not None
        assert resp["result"]["serverInfo"]["name"] == "chimera-benchmark"

        resp = server.handle_message({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        tools = resp["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "chimera_eval" in tool_names
        assert "chimera_humaneval" in tool_names

    def test_eval_tool_call(self) -> None:
        server = BenchmarkMCPServer()

        resp = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "chimera_eval",
                "arguments": {
                    "code": "def add(a, b): return a + b",
                    "test_code": "assert add(1, 2) == 3",
                },
            },
        })
        assert resp is not None
        content = resp["result"]["content"]
        assert "PASSED" in content[0]["text"]

    def test_humaneval_tool_call(self) -> None:
        server = BenchmarkMCPServer()

        resp = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "chimera_humaneval",
                "arguments": {"problem_id": "0"},
            },
        })
        assert resp is not None
        content = resp["result"]["content"]
        assert "has_close_elements" in content[0]["text"]
