"""``chimera weasel sessions`` — minimal list/show over the eventlog.

Every persisted weasel run journals its prompt, agent result, and event
trail to ``~/.chimera/eventlog/weasel-<utc>-<uuid>/``. This module exposes
that on-disk corpus to the CLI so users can ``sessions list`` for a
chronological table and ``sessions show <id>`` for the persisted summary
+ events.

Stdlib only. Mirrors the otter sessions on-disk schema (``summary.json``
+ ``event-NNNNNN-<id>.json``) so operators familiar with
``chimera otter sessions`` find a near-identical UX under
``chimera weasel sessions``.

Trademark hygiene: no upstream brand names. ``weasel-`` is the on-disk
session prefix, paralleling the ``otter-`` and ``mink-`` prefixes.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

__all__ = [
    "SessionRecord",
    "SessionDetail",
    "iter_sessions",
    "get_session",
    "default_eventlog_root",
    "cmd_sessions_list",
    "cmd_sessions_show",
    "dispatch_sessions",
]


_PREFIX = "weasel-"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class SessionRecord:
    """Compact summary of one persisted weasel session.

    Built from ``summary.json`` only.
    """

    session_id: str
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

    def to_dict(self) -> dict[str, Any]:
        """Render as a JSON-serializable dict."""
        return {
            "session_id": self.session_id,
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
        }


@dataclass
class SessionDetail:
    """Full detail for one weasel session: summary + persisted events."""

    session_id: str
    summary: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    path: Path = field(default_factory=lambda: Path("."))

    def to_dict(self) -> dict[str, Any]:
        """Render as a JSON-serializable dict."""
        return {
            "session_id": self.session_id,
            "path": str(self.path),
            "summary": self.summary,
            "events": self.events,
        }


# ---------------------------------------------------------------------------
# Disk walks
# ---------------------------------------------------------------------------


def default_eventlog_root() -> Path:
    """Return ``~/.chimera/eventlog/`` honoring the current ``Path.home()``."""
    return Path.home() / ".chimera" / "eventlog"


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


def _summary_to_record(session_dir: Path, summary: dict[str, Any]) -> SessionRecord:
    """Convert a ``summary.json`` dict into a :class:`SessionRecord`."""
    return SessionRecord(
        session_id=str(
            summary.get("session_id") or summary.get("run_id") or session_dir.name
        ),
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
    )


def iter_sessions(eventlog_root: Path | None = None) -> Iterator[SessionRecord]:
    """Yield one :class:`SessionRecord` per persisted weasel session, newest first.

    Args:
        eventlog_root: Override the eventlog root. Defaults to
            :func:`default_eventlog_root`.

    Yields:
        :class:`SessionRecord` instances ordered by ``session_id``
        descending. ``weasel-<UTC>-<uuid>`` prefixes sort lexically
        identical to chronologically.
    """
    root = eventlog_root or default_eventlog_root()
    if not root.exists():
        return
    candidates = [
        p for p in root.iterdir()
        if p.is_dir() and p.name.startswith(_PREFIX)
    ]
    candidates.sort(key=lambda p: p.name, reverse=True)
    for session_dir in candidates:
        summary = _read_summary(session_dir)
        if summary is None:
            continue
        yield _summary_to_record(session_dir, summary)


def get_session(
    session_id: str, eventlog_root: Path | None = None,
) -> SessionDetail:
    """Load a session's summary + every ``event-*.json`` file.

    Args:
        session_id: The session directory name
            (e.g. ``weasel-20260430T101501-71032a5e``).
        eventlog_root: Override the eventlog root.

    Returns:
        A :class:`SessionDetail` with ``summary`` and ``events`` populated.

    Raises:
        FileNotFoundError: When ``session_id`` does not exist or has no
            summary.
    """
    root = eventlog_root or default_eventlog_root()
    session_dir = root / session_id
    if not session_dir.exists() or not session_dir.is_dir():
        raise FileNotFoundError(f"session not found: {session_id}")
    summary = _read_summary(session_dir)
    if summary is None:
        raise FileNotFoundError(
            f"session {session_id!r} has no summary.json"
        )
    events: list[dict[str, Any]] = []
    for ev_path in sorted(session_dir.glob("event-*.json")):
        try:
            data = json.loads(ev_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            events.append(data)
    return SessionDetail(
        session_id=session_id,
        summary=summary,
        events=events,
        path=session_dir,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _short(s: str, width: int) -> str:
    """Truncate ``s`` to fit ``width`` columns (replacing newlines)."""
    flat = s.replace("\n", " ").replace("\r", " ")
    if len(flat) <= width:
        return flat
    if width <= 1:
        return flat[:width]
    return flat[: width - 1] + "…"


def _short_date(iso: str) -> str:
    """``2026-04-24T05:10:01Z`` -> ``2026-04-24 05:10``."""
    if not iso:
        return ""
    iso = iso.replace("Z", "")
    head, _, _ = iso.partition(".")
    if "T" in head:
        date_part, _, time_part = head.partition("T")
        return f"{date_part} {time_part[:5]}"
    return head


def format_session_table(records: list[SessionRecord]) -> str:
    """Render ``records`` as a fixed-width table for ``sessions list``.

    Returns:
        The fully rendered table (header + rows + footer summary). Empty
        ``records`` returns ``"(no weasel sessions found)"``.
    """
    if not records:
        return "(no weasel sessions found)"
    rows: list[str] = []
    rows.append(
        f"{'STARTED':<16}  {'ID':<36}  {'OK':<3}  {'STEPS':>5}  {'PROMPT':<40}"
    )
    rows.append("-" * 110)
    for r in records:
        ok = "yes" if r.success else "no"
        rows.append(
            f"{_short_date(r.started_at):<16}  "
            f"{_short(r.session_id, 36):<36}  "
            f"{ok:<3}  "
            f"{r.steps:>5}  "
            f"{_short(r.prompt, 40):<40}"
        )
    rows.append("")
    rows.append(f"{len(records)} session(s)")
    return "\n".join(rows)


def format_session_detail(detail: SessionDetail) -> str:
    """Render a :class:`SessionDetail` as a human-readable transcript."""
    lines: list[str] = []
    summary = detail.summary
    lines.append(f"session  {detail.session_id}")
    lines.append(f"path     {detail.path}")
    lines.append(f"started  {summary.get('started_at', '')}")
    lines.append(f"ended    {summary.get('ended_at', '')}")
    lines.append(f"model    {summary.get('model', '')}")
    lines.append(f"success  {summary.get('success', False)}")
    lines.append(f"steps    {summary.get('steps', 0)}")
    lines.append("")
    lines.append("prompt:")
    lines.append(str(summary.get("prompt", "")))
    if detail.events:
        lines.append("")
        lines.append(f"events: {len(detail.events)} record(s)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------


def cmd_sessions_list(
    *,
    eventlog_root: Path | None = None,
    json_output: bool = False,
    out: Any = None,
) -> int:
    """Implement ``chimera weasel sessions list``.

    Args:
        eventlog_root: Override the eventlog root (mainly for tests).
        json_output: Emit a JSON array instead of the table.
        out: Stream to write to. Defaults to :data:`sys.stdout`.

    Returns:
        Process exit code.
    """
    stream = out if out is not None else sys.stdout
    records = list(iter_sessions(eventlog_root))
    if json_output:
        stream.write(json.dumps([r.to_dict() for r in records], indent=2))
        stream.write("\n")
    else:
        stream.write(format_session_table(records))
        stream.write("\n")
    return 0


def cmd_sessions_show(
    session_id: str | None,
    *,
    eventlog_root: Path | None = None,
    json_output: bool = False,
    out: Any = None,
    err: Any = None,
) -> int:
    """Implement ``chimera weasel sessions show <id>``.

    Args:
        session_id: The session directory name.
        eventlog_root: Override the eventlog root.
        json_output: Emit JSON instead of human-readable text.
        out: Stream to write to. Defaults to :data:`sys.stdout`.
        err: Error stream. Defaults to :data:`sys.stderr`.

    Returns:
        Process exit code.
    """
    stream = out if out is not None else sys.stdout
    err_stream = err if err is not None else sys.stderr
    if not session_id:
        err_stream.write(
            "weasel sessions show: missing session id\n"
        )
        return 2
    try:
        detail = get_session(session_id, eventlog_root=eventlog_root)
    except FileNotFoundError as exc:
        err_stream.write(f"weasel sessions show: {exc}\n")
        return 2
    if json_output:
        stream.write(json.dumps(detail.to_dict(), indent=2))
        stream.write("\n")
    else:
        stream.write(format_session_detail(detail))
        stream.write("\n")
    return 0


def dispatch_sessions(args: argparse.Namespace) -> int:
    """Dispatch ``chimera weasel sessions [list|show <id>]``.

    The W1 scaffold parser puts the action under ``args.sub_action`` and
    the optional id under ``args.sub_target``. ``--json`` is honored when
    set on the namespace.

    Args:
        args: Parsed weasel CLI namespace.

    Returns:
        Process exit code.
    """
    action = getattr(args, "sub_action", None) or "list"
    target = getattr(args, "sub_target", None)
    json_output = bool(getattr(args, "json_output", False))
    if action == "list":
        return cmd_sessions_list(json_output=json_output)
    if action == "show":
        return cmd_sessions_show(target, json_output=json_output)
    print(
        f"weasel sessions: unknown action {action!r} "
        "(supported: list, show)",
        file=sys.stderr,
    )
    return 2
