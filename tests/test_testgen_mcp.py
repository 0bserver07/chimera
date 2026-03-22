"""Tests for chimera.mcp_servers.testgen_server — test generation MCP server."""
from __future__ import annotations

from chimera.mcp_servers.testgen_server import TestgenMCPServer, find_coverage_gaps


SAMPLE_SOURCE = """\
class Calculator:
    def add(self, a, b):
        return a + b

    def subtract(self, a, b):
        return a - b

    def _internal(self):
        pass

def helper(x):
    return x * 2

def _private():
    pass
"""

SAMPLE_TEST = """\
def test_helper():
    assert helper(2) == 4

def test_Calculator_add():
    calc = Calculator()
    assert calc.add(1, 2) == 3
"""


class TestCoverageGaps:
    """Test coverage gap detection."""

    def test_finds_untested_functions(self) -> None:
        gaps = find_coverage_gaps(SAMPLE_SOURCE, SAMPLE_TEST, "calc.py")

        gap_names = [g["name"] for g in gaps]
        # subtract is not tested, should be a gap
        assert "Calculator.subtract" in gap_names
        # add and helper are tested, should not be gaps
        assert "Calculator.add" not in gap_names
        assert "helper" not in gap_names
        # Private functions should not appear
        assert "_internal" not in gap_names
        assert "_private" not in gap_names

    def test_no_test_file_reports_all(self) -> None:
        gaps = find_coverage_gaps(SAMPLE_SOURCE, None, "calc.py")

        gap_names = [g["name"] for g in gaps]
        assert "Calculator.add" in gap_names
        assert "Calculator.subtract" in gap_names
        assert "helper" in gap_names

    def test_syntax_error_returns_empty(self) -> None:
        gaps = find_coverage_gaps("def broken(:", None, "bad.py")
        assert gaps == []


class TestTestgenMCPServer:
    """Test the MCP server message handling."""

    def test_initialize_and_list_tools(self) -> None:
        server = TestgenMCPServer()

        resp = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        assert resp is not None
        assert resp["result"]["serverInfo"]["name"] == "chimera-testgen"

        resp = server.handle_message({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        tools = resp["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "chimera_testgen" in tool_names
        assert "chimera_coverage_gaps" in tool_names

    def test_testgen_with_source_string(self) -> None:
        """Test testgen via analyze_source (avoids filesystem dependency)."""
        from chimera.testgen.generator import TestGenerator

        gen = TestGenerator()
        cases = gen.analyze_source(SAMPLE_SOURCE, "calc.py")

        # Should generate tests for public functions/methods
        names = [c.name for c in cases]
        assert any("add" in n for n in names)
        assert any("helper" in n for n in names)
        # Should not generate tests for private methods
        assert not any("_internal" in n for n in names)
        assert not any("_private" in n for n in names)

    def test_unknown_tool_returns_error(self) -> None:
        server = TestgenMCPServer()

        resp = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "nonexistent_tool",
                "arguments": {},
            },
        })
        assert resp is not None
        assert resp["result"]["isError"] is True
