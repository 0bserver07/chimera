"""Embedding-based semantic search for codebases.

Uses an LLM provider to generate embeddings and cosine similarity for search.
Falls back to TF-IDF (CodebaseIndex) when embeddings aren't available.

Optional dependency: numpy (for efficient cosine similarity).
Without numpy, uses pure-Python dot product.

Inspired by Cursor's codebase indexing.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from chimera.tools.codebase_index import CodebaseIndex, SearchResult

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

# Type for an embedding function: text → float vector
EmbedFn = Callable[[str], list[float]]


def _cosine_similarity_python(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity, using numpy if available."""
    if _HAS_NUMPY:
        va = np.array(a)
        vb = np.array(b)
        dot = np.dot(va, vb)
        norm = np.linalg.norm(va) * np.linalg.norm(vb)
        return float(dot / norm) if norm > 0 else 0.0
    return _cosine_similarity_python(a, b)


@dataclass
class EmbeddingEntry:
    """A file with its embedding vector."""

    path: str
    embedding: list[float]
    content_hash: str = ""


class EmbeddingIndex:
    """Embedding-based codebase search with optional TF-IDF fallback.

    Example::

        def embed(text):
            return provider.embed(text)  # Your embedding function

        index = EmbeddingIndex(embed_fn=embed)
        index.embed_file("auth.py", "def login(user): ...")
        results = index.search("authentication")

    When no embed_fn is provided, falls back to TF-IDF search.
    """

    def __init__(self, embed_fn: EmbedFn | None = None) -> None:
        self._embed_fn = embed_fn
        self._entries: dict[str, EmbeddingEntry] = {}
        self._fallback = CodebaseIndex()

    @property
    def has_embeddings(self) -> bool:
        """Whether embedding search is available."""
        return self._embed_fn is not None

    @property
    def file_count(self) -> int:
        """Number of indexed files."""
        if self._embed_fn:
            return len(self._entries)
        return self._fallback.file_count

    def embed_file(self, path: str, content: str) -> None:
        """Generate and store an embedding for a file.

        Falls back to TF-IDF indexing if no embed_fn is configured.
        """
        if self._embed_fn:
            embedding = self._embed_fn(content)
            self._entries[path] = EmbeddingEntry(
                path=path,
                embedding=embedding,
            )
        else:
            self._fallback.index_file(path, content)

    def embed_directory(self, directory: str | Path, **kwargs: Any) -> int:
        """Index all files in a directory."""
        if not self._embed_fn:
            return self._fallback.index_directory(directory, **kwargs)

        root = Path(directory)
        count = 0
        from chimera.tools.codebase_index import _CODE_EXTENSIONS
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in _CODE_EXTENSIONS:
                continue
            parts = path.relative_to(root).parts
            if any(p.startswith(".") or p in ("node_modules", "__pycache__") for p in parts):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                self.embed_file(str(path.relative_to(root)), content)
                count += 1
            except Exception:
                continue
        return count

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        """Search for files similar to the query.

        Uses embedding similarity if available, falls back to TF-IDF.
        """
        if not self._embed_fn:
            return self._fallback.search(query, max_results=max_results)

        query_embedding = self._embed_fn(query)
        scored: list[tuple[str, float]] = []

        for path, entry in self._entries.items():
            score = _cosine_similarity(query_embedding, entry.embedding)
            if score > 0.01:
                scored.append((path, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [SearchResult(path=p, score=s) for p, s in scored[:max_results]]

    def save(self, path: str | Path) -> None:
        """Save embeddings to a JSON file."""
        data = {
            p: {"embedding": e.embedding, "content_hash": e.content_hash}
            for p, e in self._entries.items()
        }
        Path(path).write_text(json.dumps(data))

    def load(self, path: str | Path) -> None:
        """Load embeddings from a JSON file."""
        data = json.loads(Path(path).read_text())
        for p, info in data.items():
            self._entries[p] = EmbeddingEntry(
                path=p,
                embedding=info["embedding"],
                content_hash=info.get("content_hash", ""),
            )

    def remove_file(self, path: str) -> None:
        """Remove a file from the index."""
        self._entries.pop(path, None)
        self._fallback.remove_file(path)
