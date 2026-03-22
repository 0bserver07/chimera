#!/usr/bin/env python3
"""MCP server for RAG / doc retrieval — grounds Claude's responses in project docs.

Indexes all ``.md``, ``.rst``, ``.txt`` files **and** Python docstrings in the
project, then exposes three MCP tools:

- ``chimera_doc_search(query, max_results)`` — TF-IDF search over docs
- ``chimera_api_lookup(symbol_name)`` — find docstring / signature for a symbol
- ``chimera_grounded_answer(question)`` — doc search + web search, answer with citations

Reuses:

- :class:`chimera.tools.codebase_index.CodebaseIndex` for TF-IDF indexing
- :class:`chimera.tools.definition_lookup.DefinitionFinder` for symbol lookup
- :class:`chimera.tools.grounded_search.GroundedSearchTool` for web grounding

Usage::

    python -m chimera.mcp_servers.rag_server
    # or
    python chimera/mcp_servers/rag_server.py

Configure in ``.mcp.json`` for Claude Code::

    {
      "mcpServers": {
        "chimera-rag": {
          "command": "python3",
          "args": ["chimera/mcp_servers/rag_server.py"]
        }
      }
    }
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from typing import Any

from chimera.tools.codebase_index import CodebaseIndex
from chimera.tools.definition_lookup import DefinitionFinder

# ── Server metadata ──────────────────────────────────────────────────

SERVER_NAME = "chimera-rag"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

# Extensions to index for documentation content
_DOC_EXTENSIONS = {".md", ".rst", ".txt"}

# Directories to skip when walking
_IGNORE_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "node_modules", ".venv", "venv", ".tox", ".eggs",
    "dist", "build", ".chimera_checkpoints",
})

# ── Tool definitions ─────────────────────────────────────────────────

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "chimera_doc_search",
        "description": (
            "Search project documentation (.md, .rst, .txt files and Python "
            "docstrings) for content related to a query. Returns ranked "
            "results with relevance scores and text snippets."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (natural language or keywords).",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "chimera_api_lookup",
        "description": (
            "Find the docstring and signature for a Python class or function "
            "in the project. Returns file path, line number, kind, and the "
            "full source / docstring."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_name": {
                    "type": "string",
                    "description": "Symbol name to look up (class or function name).",
                },
            },
            "required": ["symbol_name"],
        },
    },
    {
        "name": "chimera_grounded_answer",
        "description": (
            "Answer a question by searching project docs first, then falling "
            "back to a web search. Returns an answer composed from the most "
            "relevant passages, with source citations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to answer.",
                },
            },
            "required": ["question"],
        },
    },
]


# ── Docstring extraction ─────────────────────────────────────────────

def _extract_docstrings(source: str, file_path: str) -> list[dict[str, str]]:
    """Extract docstrings from a Python source file.

    Args:
        source: Python source code.
        file_path: Relative path of the file (used for labelling).

    Returns:
        List of dicts with keys ``symbol``, ``kind``, ``docstring``, ``file``.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    results: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            docstring = ast.get_docstring(node)
            if docstring:
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                results.append({
                    "symbol": node.name,
                    "kind": kind,
                    "docstring": docstring,
                    "file": file_path,
                })
    return results


# ── Snippet extraction ────────────────────────────────────────────────

def _extract_snippet(content: str, query_words: list[str], window: int = 300) -> str:
    """Find the most relevant passage in *content* based on *query_words*.

    Args:
        content: Full text content.
        query_words: Lowercased query tokens.
        window: Character window size for the snippet.

    Returns:
        The most relevant passage, or the start of the content if no match.
    """
    lower = content.lower()
    best_pos = 0
    best_score = 0

    step = max(1, window // 6)
    for i in range(0, max(1, len(lower) - window), step):
        chunk = lower[i : i + window]
        score = sum(1 for w in query_words if w in chunk)
        if score > best_score:
            best_score = score
            best_pos = i

    start = max(0, best_pos - 50)
    end = min(len(content), best_pos + window + 50)
    return content[start:end].strip()


# ── RAG MCP Server ───────────────────────────────────────────────────

class RAGServer:
    """MCP server that grounds Claude's answers in project documentation.

    On startup (or on first ``initialize``), indexes all documentation files
    and Python docstrings under *workdir*. Exposes three tools via the MCP
    stdio protocol.

    Args:
        workdir: Project root to index. Defaults to ``os.getcwd()``.
        index: Optional pre-built :class:`CodebaseIndex` (useful for testing).
        finder: Optional pre-built :class:`DefinitionFinder` (useful for testing).
    """

    def __init__(
        self,
        workdir: str | None = None,
        index: CodebaseIndex | None = None,
        finder: DefinitionFinder | None = None,
    ) -> None:
        self._workdir = workdir or os.getcwd()
        self._index = index or CodebaseIndex()
        self._finder = finder or DefinitionFinder(self._workdir)
        self._initialized = False
        self._indexed = index is not None
        # Stores file content for snippet extraction
        self._doc_contents: dict[str, str] = {}
        # Stores extracted docstrings keyed by symbol name (lowercase)
        self._docstrings: dict[str, list[dict[str, str]]] = {}

    # ── Indexing ──────────────────────────────────────────────────────

    def index_workspace(self) -> int:
        """Index documentation files and Python docstrings.

        Returns:
            Number of documentation items indexed.
        """
        if self._indexed:
            return self._index.file_count

        root = Path(self._workdir)
        count = 0

        for path in root.rglob("*"):
            if not path.is_file():
                continue
            # Skip ignored directories
            parts = path.relative_to(root).parts
            if any(p in _IGNORE_DIRS or p.startswith(".") for p in parts):
                continue

            rel_path = str(path.relative_to(root))

            # Index documentation files
            if path.suffix in _DOC_EXTENSIONS:
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if not content.strip():
                    continue
                self._index.index_file(rel_path, content)
                self._doc_contents[rel_path] = content
                count += 1

            # Extract docstrings from Python files
            elif path.suffix == ".py":
                try:
                    source = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                docstrings = _extract_docstrings(source, rel_path)
                for ds in docstrings:
                    # Index docstrings as if they were documents
                    doc_key = f"{rel_path}::{ds['symbol']}"
                    text = f"{ds['symbol']} ({ds['kind']}): {ds['docstring']}"
                    self._index.index_file(doc_key, text)
                    self._doc_contents[doc_key] = text
                    # Also store for API lookup
                    sym_lower = ds["symbol"].lower()
                    self._docstrings.setdefault(sym_lower, []).append(ds)
                    count += 1

        self._indexed = True
        return count

    # ── JSON-RPC dispatch ─────────────────────────────────────────────

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single JSON-RPC message.

        Args:
            message: Parsed JSON-RPC request or notification.

        Returns:
            JSON-RPC response dict, or ``None`` for notifications.
        """
        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params", {})

        # Notifications (no id) — no response required
        if msg_id is None:
            return None

        handler = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "ping": self._handle_ping,
        }.get(method)

        if handler is None:
            return _error_response(msg_id, -32601, f"Method not found: {method}")

        try:
            result = handler(params)
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as e:
            return _error_response(msg_id, -32603, str(e))

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle the initialize request."""
        self._initialized = True
        if not self._indexed:
            self.index_workspace()
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/list request."""
        return {"tools": TOOL_DEFINITIONS}

    def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/call request by dispatching to the right tool."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        dispatch = {
            "chimera_doc_search": self._call_doc_search,
            "chimera_api_lookup": self._call_api_lookup,
            "chimera_grounded_answer": self._call_grounded_answer,
        }

        handler = dispatch.get(tool_name)
        if handler is None:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True,
            }
        return handler(arguments)

    def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle ping request."""
        return {}

    # ── Tool implementations ──────────────────────────────────────────

    def _call_doc_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Search project documentation.

        Args:
            arguments: Must contain ``query``; may contain ``max_results``.

        Returns:
            MCP content response with ranked results and snippets.
        """
        query = arguments.get("query", "")
        max_results = arguments.get("max_results", 5)

        if not query:
            return {
                "content": [{"type": "text", "text": "Error: query is required"}],
                "isError": True,
            }

        if not self._indexed:
            self.index_workspace()

        results = self._index.search(query, max_results=max_results)

        if not results:
            return {
                "content": [{"type": "text", "text": f"No documentation found matching: {query}"}],
            }

        query_words = [w.lower() for w in query.split() if len(w) > 2]
        lines: list[str] = [f"Found {len(results)} result(s) for '{query}':\n"]
        for r in results:
            content = self._doc_contents.get(r.path, "")
            snippet = _extract_snippet(content, query_words) if content else ""
            lines.append(f"  [{r.score:.3f}] {r.path}")
            if snippet:
                lines.append(f"    {snippet[:200]}")
            lines.append("")

        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
        }

    def _call_api_lookup(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Look up a symbol's docstring and signature.

        First checks extracted docstrings (fast, has docstring text).
        Falls back to DefinitionFinder for full source.

        Args:
            arguments: Must contain ``symbol_name``.

        Returns:
            MCP content response with symbol information.
        """
        symbol_name = arguments.get("symbol_name", "")
        if not symbol_name:
            return {
                "content": [{"type": "text", "text": "Error: symbol_name is required"}],
                "isError": True,
            }

        if not self._indexed:
            self.index_workspace()

        # Check extracted docstrings first
        sym_lower = symbol_name.lower()
        docstring_entries = self._docstrings.get(sym_lower, [])

        if docstring_entries:
            lines: list[str] = [f"API documentation for '{symbol_name}':\n"]
            for ds in docstring_entries:
                lines.append(f"  {ds['file']} ({ds['kind']})")
                lines.append(f"    {ds['docstring'][:500]}")
                lines.append("")
            return {
                "content": [{"type": "text", "text": "\n".join(lines)}],
            }

        # Fall back to DefinitionFinder
        definitions = self._finder.find(symbol_name)
        if not definitions:
            return {
                "content": [{"type": "text", "text": f"No API documentation found for '{symbol_name}'"}],
            }

        lines = [f"Definitions for '{symbol_name}':\n"]
        for d in definitions[:10]:
            lines.append(f"  {d.file}:{d.line} ({d.kind})")
            lines.append(f"    {d.source[:300]}")
            lines.append("")
        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
        }

    def _call_grounded_answer(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Answer a question using docs first, web search as fallback.

        Args:
            arguments: Must contain ``question``.

        Returns:
            MCP content response with answer and citations.
        """
        question = arguments.get("question", "")
        if not question:
            return {
                "content": [{"type": "text", "text": "Error: question is required"}],
                "isError": True,
            }

        if not self._indexed:
            self.index_workspace()

        # Step 1: Search project docs
        doc_results = self._index.search(question, max_results=5)
        query_words = [w.lower() for w in question.split() if len(w) > 2]

        citations: list[dict[str, str]] = []
        for r in doc_results:
            content = self._doc_contents.get(r.path, "")
            if content:
                snippet = _extract_snippet(content, query_words, window=400)
                citations.append({
                    "source": r.path,
                    "type": "project_doc",
                    "passage": snippet,
                    "score": f"{r.score:.3f}",
                })

        # Step 2: If insufficient doc results, try web search (best effort)
        web_citations: list[dict[str, str]] = []
        if len(citations) < 2:
            web_citations = self._web_search_fallback(question, query_words)

        all_citations = citations + web_citations

        if not all_citations:
            return {
                "content": [{"type": "text", "text": f"No information found for: {question}"}],
            }

        # Step 3: Format grounded answer
        lines: list[str] = [f"Grounded answer for: {question}\n"]

        if citations:
            lines.append("From project documentation:")
            for i, c in enumerate(citations, 1):
                lines.append(f"  [{i}] {c['source']} (score: {c['score']})")
                lines.append(f"      {c['passage'][:300]}")
                lines.append("")

        if web_citations:
            lines.append("From web sources:")
            offset = len(citations)
            for i, c in enumerate(web_citations, offset + 1):
                lines.append(f"  [{i}] {c['source']}")
                lines.append(f"      {c['passage'][:300]}")
                lines.append("")

        lines.append("Sources:")
        for i, c in enumerate(all_citations, 1):
            lines.append(f"  [{i}] {c['source']}")

        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
        }

    def _web_search_fallback(
        self,
        question: str,
        query_words: list[str],
    ) -> list[dict[str, str]]:
        """Attempt a web search as a fallback for grounded answers.

        Args:
            question: The original question.
            query_words: Lowercased query tokens.

        Returns:
            List of citation dicts from web results, or empty list on failure.
        """
        try:
            from chimera.tools.grounded_search import GroundedSearchTool
            tool = GroundedSearchTool()
            result = tool.execute({"query": question, "num_sources": 2}, env=None)
            if result.error or not result.metadata:
                return []
            web_cites = result.metadata.get("citations", [])
            return [
                {
                    "source": c.get("url", "web"),
                    "type": "web",
                    "passage": c.get("passage", "")[:300],
                    "score": "web",
                }
                for c in web_cites
            ]
        except Exception:
            return []

    # ── Stdio loop ────────────────────────────────────────────────────

    def run(self) -> None:
        """Run the MCP server, reading from stdin and writing to stdout.

        Reads newline-delimited JSON messages from stdin and writes
        responses as newline-delimited JSON to stdout.  Runs until
        stdin is closed.
        """
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                error = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
                sys.stdout.write(json.dumps(error) + "\n")
                sys.stdout.flush()
                continue

            response = self.handle_message(message)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()


# ── Helpers ───────────────────────────────────────────────────────────

def _error_response(msg_id: int | str, code: int, message: str) -> dict[str, Any]:
    """Build a JSON-RPC error response."""
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def main() -> None:
    """Entry point for the MCP RAG server."""
    server = RAGServer()
    server.run()


if __name__ == "__main__":
    main()
