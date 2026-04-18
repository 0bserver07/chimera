"""Tests for chimera.mcp_servers.rag_server — RAG / doc retrieval MCP server."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from chimera.mcp_servers.rag_server import (
    RAGServer,
    _extract_docstrings,
    _extract_snippet,
)


# ── Helpers ───────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Create a small temporary project with docs and Python files."""
    # Documentation file
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "guide.md").write_text(
        "# Getting Started\n\n"
        "Chimera uses a provider-based architecture with 8 layers.\n"
        "Install with `pip install chimera`.\n"
    )
    (docs_dir / "api.md").write_text(
        "# API Reference\n\n"
        "## Agent\n\n"
        "The Agent class is the main entry point for running tasks.\n"
    )
    # README
    (tmp_path / "README.md").write_text(
        "# My Project\n\nA sample project for testing the RAG server.\n"
    )
    # Python source with docstrings
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "calculator.py").write_text(textwrap.dedent('''\
        """Calculator module."""

        class Calculator:
            """A simple calculator that supports basic arithmetic."""

            def add(self, a: int, b: int) -> int:
                """Add two numbers together.

                Args:
                    a: First operand.
                    b: Second operand.

                Returns:
                    The sum of a and b.
                """
                return a + b

            def multiply(self, a: int, b: int) -> int:
                """Multiply two numbers."""
                return a * b
    '''))
    return tmp_path


@pytest.fixture()
def server(tmp_project: Path) -> RAGServer:
    """Create a RAGServer pointed at the temporary project."""
    return RAGServer(workdir=str(tmp_project))


# ── Unit tests ────────────────────────────────────────────────────────

class TestExtractDocstrings:
    def test_extracts_class_and_function_docstrings(self) -> None:
        source = textwrap.dedent('''\
            class Foo:
                """Foo class."""
                def bar(self):
                    """Bar method."""
                    pass
        ''')
        results = _extract_docstrings(source, "test.py")
        assert len(results) == 2
        symbols = {r["symbol"] for r in results}
        assert "Foo" in symbols
        assert "bar" in symbols

    def test_handles_syntax_error(self) -> None:
        results = _extract_docstrings("def broken(:", "bad.py")
        assert results == []


class TestExtractSnippet:
    def test_finds_relevant_passage(self) -> None:
        content = "alpha beta gamma " * 50 + "the answer is 42 " + "delta epsilon " * 50
        snippet = _extract_snippet(content, ["answer", "42"])
        assert "answer" in snippet or "42" in snippet

    def test_returns_start_when_no_match(self) -> None:
        content = "hello world this is a test " * 20
        snippet = _extract_snippet(content, ["zzzznotfound"])
        assert snippet.startswith("hello")


class TestRAGServerDocSearch:
    def test_doc_search_returns_results(self, server: RAGServer) -> None:
        server.index_workspace()
        result = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "chimera_doc_search",
                "arguments": {"query": "provider architecture layers"},
            },
        })
        assert result is not None
        assert "error" not in result
        text = result["result"]["content"][0]["text"]
        assert "result(s)" in text or "documentation" in text.lower()

    def test_doc_search_empty_query(self, server: RAGServer) -> None:
        server.index_workspace()
        result = server.handle_message({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "chimera_doc_search",
                "arguments": {"query": ""},
            },
        })
        assert result is not None
        assert result["result"]["isError"] is True


class TestRAGServerAPILookup:
    def test_api_lookup_finds_symbol(self, server: RAGServer) -> None:
        server.index_workspace()
        result = server.handle_message({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "chimera_api_lookup",
                "arguments": {"symbol_name": "Calculator"},
            },
        })
        assert result is not None
        text = result["result"]["content"][0]["text"]
        assert "Calculator" in text
        # Should find the docstring
        assert "arithmetic" in text.lower() or "calculator" in text.lower()

    def test_api_lookup_missing_symbol(self, server: RAGServer) -> None:
        server.index_workspace()
        result = server.handle_message({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "chimera_api_lookup",
                "arguments": {"symbol_name": "NonExistentWidget"},
            },
        })
        assert result is not None
        text = result["result"]["content"][0]["text"]
        assert "No" in text or "not found" in text.lower()


class TestRAGServerProtocol:
    def test_initialize_returns_server_info(self, server: RAGServer) -> None:
        result = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        assert result is not None
        assert result["result"]["serverInfo"]["name"] == "chimera-rag"

    def test_tools_list_returns_three_tools(self, server: RAGServer) -> None:
        result = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        })
        assert result is not None
        tools = result["result"]["tools"]
        assert len(tools) == 3
        names = {t["name"] for t in tools}
        assert names == {"chimera_doc_search", "chimera_api_lookup", "chimera_grounded_answer"}

    def test_unknown_method_returns_error(self, server: RAGServer) -> None:
        result = server.handle_message({
            "jsonrpc": "2.0",
            "id": 99,
            "method": "unknown/method",
            "params": {},
        })
        assert result is not None
        assert "error" in result
        assert result["error"]["code"] == -32601

    def test_notification_returns_none(self, server: RAGServer) -> None:
        result = server.handle_message({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        assert result is None
