"""Cross-CLI eventlog walker — see every persisted session regardless of prefix.

Every Chimera CLI (``chimera otter``, ``chimera ferret``, ``chimera weasel``,
``chimera shrew``, ``chimera stoat``, ``chimera badger``, plus ``chimera mink``
under its ``runs`` alias) writes ``summary.json`` + ``event-NNNNNN-*.json``
files into ``~/.chimera/eventlog/<cli>-<utc>-<uuid>/``. Historically each
CLI's ``sessions list`` filtered to its own ``<cli>-`` prefix, so a session
created by ``chimera otter`` was invisible from ``chimera ferret sessions
list`` or ``chimera badger sessions show <id>``.

This module exposes a single shared walker that yields *every* session,
regardless of which CLI created it. Each yielded record carries a
``cli_origin`` field parsed from the on-disk directory prefix so callers
can render a "who created this?" column.

The on-disk schema is a strict subset of what every per-CLI sessions
module already writes — no migration is required. Existing per-CLI
``SessionRecord`` classes simply gain an additional ``cli_origin`` field
populated from this walker.

Stdlib only. Late-binding imports inside functions keep the import graph
acyclic between :mod:`chimera.sessions.eventlog` and the per-CLI modules
that re-export from here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from chimera.config.paths import store_path

__all__ = [
    "SessionRecord",
    "iter_all_sessions",
    "iter_sessions_for_cli",
    "find_session_dir",
    "default_eventlog_root",
    "parse_cli_origin",
    "KNOWN_CLI_ORIGINS",
]


# ---------------------------------------------------------------------------
# Known CLI origins — kept narrow on purpose so an unrelated directory
# (e.g. ``backup-2026-04-30``) never shows up as a session.
# ---------------------------------------------------------------------------


KNOWN_CLI_ORIGINS: frozenset[str] = frozenset({
    "mink",
    "otter",
    "ferret",
    "weasel",
    "shrew",
    "stoat",
    "badger",
})


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class SessionRecord:
    """Compact summary of one persisted session, regardless of origin CLI.

    Built from ``summary.json`` only — never reads individual event
    files. The shape is the union of fields each per-CLI ``SessionRecord``
    populates today (``otter`` carries an extra ``title``; all others
    leave it ``None``), plus a new ``cli_origin`` parsed from the
    directory prefix.

    Attributes:
        session_id: The directory name (e.g. ``otter-20260430T051001-71032a5e``).
        cli_origin: The CLI that created this session — ``"otter"``,
            ``"ferret"``, ``"weasel"``, ``"shrew"``, ``"stoat"``,
            ``"badger"``, ``"mink"``, or ``""`` when the prefix is
            unrecognized.
        started_at: ISO-8601 UTC start timestamp from ``summary.json``.
        ended_at: ISO-8601 UTC end timestamp from ``summary.json``.
        model: Provider model name actually used.
        prompt: The user prompt that drove this session.
        success: Whether the loop reported ``success=True``.
        cost_usd: Total session cost in USD (zero when unknown).
        steps: Number of ReAct steps executed.
        tool_calls: Total number of tool calls dispatched.
        path: Absolute path to the session's eventlog directory.
        error: Optional error string from ``summary.json`` (None on success).
        title: Optional hand-authored label (otter-only today; ``None``
            elsewhere).
        summary: The raw ``summary.json`` dict so callers can inspect
            CLI-specific fields without re-reading from disk.
    """

    session_id: str
    cli_origin: str
    started_at: str
    ended_at: str
    model: str
    prompt: str
    success: bool
    cost_usd: float
    steps: int
    tool_calls: int
    path: Path
    error: str | None = None
    title: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Render as a JSON-serializable dict."""
        return {
            "session_id": self.session_id,
            "cli_origin": self.cli_origin,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "model": self.model,
            "prompt": self.prompt,
            "success": self.success,
            "cost_usd": self.cost_usd,
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "path": str(self.path),
            "error": self.error,
            "title": self.title,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def default_eventlog_root() -> Path:
    """Return ``~/.chimera/eventlog/`` honoring the current ``Path.home()``."""
    return store_path("eventlog")


def parse_cli_origin(dir_name: str) -> str:
    """Extract the CLI origin from an eventlog directory name.

    Args:
        dir_name: The directory basename
            (e.g. ``otter-20260430T051001-71032a5e``).

    Returns:
        The CLI origin string when the prefix is one of
        :data:`KNOWN_CLI_ORIGINS`; ``""`` otherwise.
    """
    head, _, _ = dir_name.partition("-")
    if head in KNOWN_CLI_ORIGINS:
        return head
    return ""


def _read_summary(session_dir: Path) -> dict[str, Any] | None:
    """Read and parse ``summary.json`` for ``session_dir``; ``None`` on error."""
    summary_path = session_dir / "summary.json"
    if not summary_path.exists():
        return None
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _summary_to_record(
    session_dir: Path, summary: dict[str, Any], cli_origin: str,
) -> SessionRecord:
    """Convert a ``summary.json`` dict into a :class:`SessionRecord`."""
    raw_title = summary.get("title")
    title = (
        str(raw_title)
        if isinstance(raw_title, str) and raw_title.strip()
        else None
    )
    return SessionRecord(
        session_id=str(
            summary.get("session_id")
            or summary.get("run_id")
            or session_dir.name
        ),
        cli_origin=cli_origin,
        started_at=str(summary.get("started_at") or ""),
        ended_at=str(summary.get("ended_at") or ""),
        model=str(summary.get("model") or ""),
        prompt=str(summary.get("prompt") or ""),
        success=bool(summary.get("success", False)),
        cost_usd=float(summary.get("cost_usd", 0.0) or 0.0),
        steps=int(summary.get("steps", 0) or 0),
        tool_calls=int(summary.get("tool_calls_total", 0) or 0),
        path=session_dir,
        error=summary.get("error"),
        title=title,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Walkers
# ---------------------------------------------------------------------------


def iter_all_sessions(
    eventlog_root: Path | None = None,
) -> Iterator[SessionRecord]:
    """Yield one :class:`SessionRecord` per persisted session, every CLI.

    Walks ``eventlog_root`` and yields a record for every subdirectory
    whose prefix is one of :data:`KNOWN_CLI_ORIGINS`. Directories whose
    prefix is unrecognized (or that lack a parseable ``summary.json``)
    are skipped silently — a future CLI rev that ships a new prefix
    only needs to add its name to ``KNOWN_CLI_ORIGINS``.

    Args:
        eventlog_root: Override the eventlog root. Defaults to
            :func:`default_eventlog_root`.

    Yields:
        :class:`SessionRecord` instances ordered by directory name
        descending (newest first; session ids are timestamp-sortable).
    """
    root = eventlog_root or default_eventlog_root()
    if not root.exists():
        return
    candidates: list[tuple[Path, str]] = []
    for p in root.iterdir():
        if not p.is_dir():
            continue
        origin = parse_cli_origin(p.name)
        if not origin:
            continue
        candidates.append((p, origin))
    candidates.sort(key=lambda pair: pair[0].name, reverse=True)
    for session_dir, origin in candidates:
        summary = _read_summary(session_dir)
        if summary is None:
            continue
        yield _summary_to_record(session_dir, summary, origin)


def iter_sessions_for_cli(
    cli: str, eventlog_root: Path | None = None,
) -> Iterator[SessionRecord]:
    """Yield :class:`SessionRecord` instances for one CLI's sessions only.

    A thin filter over :func:`iter_all_sessions`; preserves the historic
    per-CLI behavior where ``chimera otter sessions list`` saw only
    ``otter-`` prefixed directories.

    Args:
        cli: CLI origin name (e.g. ``"otter"``, ``"ferret"``).
        eventlog_root: Override the eventlog root.

    Yields:
        :class:`SessionRecord` instances whose ``cli_origin`` matches
        ``cli``, ordered newest first.
    """
    for rec in iter_all_sessions(eventlog_root):
        if rec.cli_origin == cli:
            yield rec


def find_session_dir(
    session_id: str, eventlog_root: Path | None = None,
) -> Path | None:
    """Locate a session directory by id, regardless of CLI origin.

    Used by per-CLI ``sessions show`` so users can pull up a session
    created by a different CLI without having to remember which
    flavor's eventlog to look in.

    Args:
        session_id: The session directory name
            (e.g. ``otter-20260430T051001-71032a5e``).
        eventlog_root: Override the eventlog root.

    Returns:
        Absolute path to the session directory when it exists and has a
        readable ``summary.json``; ``None`` otherwise.
    """
    root = eventlog_root or default_eventlog_root()
    candidate = root / session_id
    if candidate.is_dir() and (candidate / "summary.json").exists():
        return candidate
    return None
