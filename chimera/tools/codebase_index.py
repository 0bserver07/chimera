"""Codebase indexing with TF-IDF for semantic search.

Provides a CodebaseIndex that indexes files by content and supports
keyword-based semantic search. Uses stdlib only (no numpy/sklearn).

For full embedding-based search, install the 'embeddings' extra.

Inspired by Cursor's codebase indexing and Aider's repo map.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.config.ignore import is_not_source
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

# File extensions to index
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".c", ".cpp",
    ".h", ".hpp", ".rb", ".php", ".swift", ".kt", ".scala", ".sh", ".bash",
    ".yml", ".yaml", ".json", ".toml", ".md", ".txt", ".sql", ".html", ".css",
}


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens, including sub-word splits on underscores."""
    # Extract identifier-like tokens
    raw = re.findall(r"[a-z_][a-z0-9_]*", text.lower())
    # Also split on underscores to get sub-words
    tokens: list[str] = []
    for tok in raw:
        tokens.append(tok)
        parts = tok.split("_")
        if len(parts) > 1:
            tokens.extend(p for p in parts if p)
    return tokens


@dataclass
class IndexEntry:
    """A single indexed file."""

    path: str
    tokens: list[str] = field(default_factory=list)
    line_count: int = 0
    size_bytes: int = 0


@dataclass
class SearchResult:
    """A search result with relevance score."""

    path: str
    score: float
    snippet: str = ""


class CodebaseIndex:
    """TF-IDF based codebase index for semantic search.

    Example::

        index = CodebaseIndex()
        index.index_directory("/path/to/project")
        results = index.search("authentication login handler")
        for r in results:
            print(f"{r.path}: {r.score:.3f}")
    """

    def __init__(self) -> None:
        self._entries: dict[str, IndexEntry] = {}
        self._idf: dict[str, float] = {}
        self._tfidf: dict[str, dict[str, float]] = {}  # path → {token → score}

    @property
    def file_count(self) -> int:
        """Number of indexed files."""
        return len(self._entries)

    def index_directory(
        self,
        directory: str | Path,
        extensions: set[str] | None = None,
        max_file_size: int = 500_000,
    ) -> int:
        """Index all code files in a directory.

        Args:
            directory: Root directory to scan.
            extensions: File extensions to include (default: common code files).
            max_file_size: Skip files larger than this (bytes).

        Returns:
            Number of files indexed.
        """
        exts = extensions or _CODE_EXTENSIONS
        root = Path(directory)
        count = 0

        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in exts:
                continue
            if path.stat().st_size > max_file_size:
                continue
            # Skip hidden dirs and the shared non-source set.
            parts = path.relative_to(root).parts
            if any(p.startswith(".") or is_not_source(p) for p in parts):
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            rel_path = str(path.relative_to(root))
            tokens = _tokenize(content)
            self._entries[rel_path] = IndexEntry(
                path=rel_path,
                tokens=tokens,
                line_count=content.count("\n") + 1,
                size_bytes=len(content.encode()),
            )
            count += 1

        self._build_tfidf()
        return count

    def index_file(self, path: str, content: str) -> None:
        """Index a single file by path and content."""
        tokens = _tokenize(content)
        self._entries[path] = IndexEntry(
            path=path,
            tokens=tokens,
            line_count=content.count("\n") + 1,
            size_bytes=len(content.encode()),
        )
        self._build_tfidf()

    def _build_tfidf(self) -> None:
        """Compute TF-IDF scores for all indexed files."""
        n = len(self._entries)
        if n == 0:
            return

        # Document frequency
        df: Counter[str] = Counter()
        for entry in self._entries.values():
            unique_tokens = set(entry.tokens)
            for token in unique_tokens:
                df[token] += 1

        # IDF — use log(n/count) but floor at a small positive value
        # so single-document corpora still produce non-zero scores
        self._idf = {
            token: max(math.log((n + 1) / (count + 1)) + 1, 0.1)
            for token, count in df.items()
        }

        # TF-IDF per document
        self._tfidf = {}
        for path, entry in self._entries.items():
            tf: Counter[str] = Counter(entry.tokens)
            total = len(entry.tokens) or 1
            self._tfidf[path] = {
                token: (count / total) * self._idf.get(token, 0)
                for token, count in tf.items()
            }

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        """Search the index for files matching a query.

        Args:
            query: Natural language search query.
            max_results: Maximum number of results.

        Returns:
            Ranked list of SearchResult.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores: list[tuple[str, float]] = []
        for path, tfidf in self._tfidf.items():
            score = sum(tfidf.get(token, 0) for token in query_tokens)
            if score > 0:
                scores.append((path, score))

        scores.sort(key=lambda x: x[1], reverse=True)

        results: list[SearchResult] = []
        for path, score in scores[:max_results]:
            results.append(SearchResult(path=path, score=score))
        return results

    def update_file(self, path: str, content: str) -> None:
        """Re-index a single file (incremental update)."""
        self.index_file(path, content)

    def remove_file(self, path: str) -> None:
        """Remove a file from the index."""
        self._entries.pop(path, None)
        self._tfidf.pop(path, None)
        self._build_tfidf()


class SemanticSearchTool(BaseTool):
    """Tool that searches the codebase using the index."""

    name = "semantic_search"
    description = (
        "Search the codebase for files related to a concept or keyword. "
        "Returns ranked file paths with relevance scores."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (natural language or keywords)",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results to return (default: 10)",
            },
        },
        "required": ["query"],
    }

    def __init__(self, index: CodebaseIndex | None = None) -> None:
        self._index = index or CodebaseIndex()

    @property
    def index(self) -> CodebaseIndex:
        """The underlying codebase index."""
        return self._index

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        query = args["query"]
        max_results = args.get("max_results", 10)

        if self._index.file_count == 0:
            # Try to auto-index from environment
            if env is not None:
                workdir = getattr(env, "_workdir", None) or getattr(env, "workdir", ".")
                if workdir:
                    self._index.index_directory(workdir)

        results = self._index.search(query, max_results=max_results)

        if not results:
            return ToolResult(output=f"No files found matching: {query}")

        lines: list[str] = []
        for r in results:
            lines.append(f"  {r.score:.3f}  {r.path}")

        return ToolResult(
            output="\n".join(lines),
            metadata={"query": query, "result_count": len(results)},
        )
