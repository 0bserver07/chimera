"""Chunk large files into small-model-friendly segments.

Small local models choke on multi-thousand-line file dumps — they
either ignore the tail or hallucinate symbols that aren't present.
The shrew read-tool wraps :func:`chunk_text` so that any read of a
file larger than the chunk size returns one segment at a time, with
metadata that lets the agent ask for the next one.

Public surface:

* :func:`chunk_text` — split a string into <=``max_bytes`` chunks
  while preserving line boundaries when possible.
* :func:`format_chunk_header` — rendering helper for the agent's
  observation channel (e.g. ``"file.py [chunk 2/5, lines 91–180]"``).

Stdlib-only. Pure functions. Returns lists of ``Chunk`` records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "Chunk",
    "DEFAULT_MAX_BYTES",
    "chunk_text",
    "format_chunk_header",
]


#: Default chunk byte budget. <2 KB matches the spec target — a
#: small model can hold multiple chunks in working memory and still
#: reason about each.
DEFAULT_MAX_BYTES: Final[int] = 2_000


@dataclass(frozen=True)
class Chunk:
    """A slice of a chunked file.

    Attributes:
        index: 0-based chunk index.
        total: Total number of chunks in the source file.
        start_line: 1-based line number of the first line in this chunk.
        end_line: 1-based line number of the last line in this chunk
            (inclusive).
        text: The chunk body, including a trailing newline when the
            source had one.
    """

    index: int
    total: int
    start_line: int
    end_line: int
    text: str


def chunk_text(text: str, max_bytes: int = DEFAULT_MAX_BYTES) -> list[Chunk]:
    """Split ``text`` into <=``max_bytes`` chunks at line boundaries.

    Args:
        text: Source content. May contain any line endings; we split
            on ``\\n`` and re-join.
        max_bytes: Soft per-chunk byte budget. Values <= 0 fall back
            to :data:`DEFAULT_MAX_BYTES`. A single line longer than
            ``max_bytes`` becomes its own chunk (we don't break
            inside a line — small models do worse with mid-line
            cuts than with one oversized chunk).

    Returns:
        A list of :class:`Chunk` records covering the whole input.
        Empty input returns one empty chunk so callers always get a
        well-formed result.
    """
    if max_bytes <= 0:
        max_bytes = DEFAULT_MAX_BYTES
    if not text:
        return [Chunk(index=0, total=1, start_line=1, end_line=1, text="")]

    lines = text.splitlines(keepends=True)
    chunks: list[tuple[int, int, str]] = []  # (start_line, end_line, body)
    current: list[str] = []
    current_size = 0
    current_start = 1
    line_no = 0

    for line in lines:
        line_no += 1
        line_size = len(line.encode("utf-8"))
        if current and current_size + line_size > max_bytes:
            chunks.append((current_start, line_no - 1, "".join(current)))
            current = [line]
            current_size = line_size
            current_start = line_no
        else:
            current.append(line)
            current_size += line_size

    if current:
        chunks.append((current_start, line_no, "".join(current)))

    total = len(chunks)
    return [
        Chunk(
            index=i,
            total=total,
            start_line=start,
            end_line=end,
            text=body,
        )
        for i, (start, end, body) in enumerate(chunks)
    ]


def format_chunk_header(chunk: Chunk, filename: str = "") -> str:
    """Render a one-line header describing ``chunk``.

    Args:
        chunk: A :class:`Chunk` from :func:`chunk_text`.
        filename: Optional filename to prefix.

    Returns:
        A string like ``"file.py [chunk 2/5, lines 91-180]"``.
    """
    prefix = f"{filename} " if filename else ""
    return (
        f"{prefix}[chunk {chunk.index + 1}/{chunk.total}, "
        f"lines {chunk.start_line}-{chunk.end_line}]"
    )
