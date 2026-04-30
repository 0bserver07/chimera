"""``chimera ferret sessions`` — inspect persisted ferret sessions.

Every ``chimera ferret -p ...`` (and server-driven turn) journals its
prompt, agent result, and tool calls to
``~/.chimera/eventlog/ferret-<utc>-<uuid>/``. This module exposes that
on-disk corpus to the CLI so users can ``sessions list`` for a table
view and ``sessions show <id>`` for a transcript without reaching for
``cat`` and ``jq``.

This is the ferret twin of :mod:`chimera.otter.sessions`; the on-disk
schema (``summary.json`` + ``event-NNNNNN-<id>.json``) is shared with
otter and mink so that operators familiar with ``otter sessions`` find
a near-identical UX under ``ferret sessions``.

Stdlib only. No external runtime dependency — the format is the same
JSON-on-disk layout written by ``run_ferret_print`` and the REPL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

__all__ = [
    "SessionRecord",
    "SessionDetail",
    "iter_sessions",
    "get_session",
    "format_session_table",
    "format_session_detail",
    "default_eventlog_root",
    "parse_since",
    "cmd_sessions_list",
    "cmd_sessions_show",
    "dispatch_sessions",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SessionRecord:
    """Compact summary of one persisted ferret session.

    Built from ``summary.json`` only — never reads individual event
    files. Mirrors :class:`chimera.otter.sessions.SessionRecord` so the
    two CLIs share rendering helpers.

    Attributes:
        session_id: The directory name (e.g.
            ``ferret-20260430T051001-71032a5e``).
        started_at: ISO-8601 UTC start timestamp from ``summary.json``.
        ended_at: ISO-8601 UTC end timestamp from ``summary.json``.
        model: Provider model name actually used (post-fallback).
        prompt: The user prompt that drove this session.
        success: Whether the loop reported ``success=True``.
        cost_usd: Total session cost in USD (zero when unknown).
        steps: Number of ReAct steps executed.
        tool_calls: Total number of tool calls dispatched.
        path: Absolute path to the session's eventlog directory.
        error: Optional error string from ``summary.json`` (None on success).
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
        """Render as a JSON-serializable dict for ``--json`` output."""
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
    """Full detail for one ferret session: summary + every persisted event."""

    session_id: str
    summary: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    path: Path = field(default_factory=lambda: Path("."))

    def to_dict(self) -> dict[str, Any]:
        """Render as a JSON-serializable dict for ``--json`` output."""
        return {
            "session_id": self.session_id,
            "path": str(self.path),
            "summary": self.summary,
            "events": self.events,
        }


# ---------------------------------------------------------------------------
# Disk walks
# ---------------------------------------------------------------------------


_PREFIX = "ferret-"


def default_eventlog_root() -> Path:
    """Return ``~/.chimera/eventlog/`` honoring the current ``Path.home()``."""
    return Path.home() / ".chimera" / "eventlog"


def _read_summary(session_dir: Path) -> dict[str, Any] | None:
    """Read and parse ``summary.json`` for ``session_dir``; ``None`` on any error."""
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
            summary.get("session_id")
            or summary.get("run_id")
            or session_dir.name
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
    """Yield one :class:`SessionRecord` per persisted ferret session, newest first.

    Args:
        eventlog_root: Override the eventlog root. Defaults to
            :func:`default_eventlog_root`.

    Yields:
        :class:`SessionRecord` instances ordered by ``session_id``
        descending (session ids start with ``ferret-<UTC>-<uuid>``, so
        lexical ordering equals chronological ordering).
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
    """Load one session's summary + every ``event-*.json`` file.

    Args:
        session_id: The session directory name
            (e.g. ``ferret-20260430T051001-71032a5e``).
        eventlog_root: Override the eventlog root.

    Returns:
        A :class:`SessionDetail` with ``summary`` and ``events`` populated.

    Raises:
        FileNotFoundError: When ``session_id`` does not exist or has no summary.
    """
    root = eventlog_root or default_eventlog_root()
    session_dir = root / session_id
    if not session_dir.exists() or not session_dir.is_dir():
        raise FileNotFoundError(f"session not found: {session_id}")
    summary = _read_summary(session_dir)
    if summary is None:
        raise FileNotFoundError(
            f"session {session_id!r} has no summary.json "
            "(did the session abort early?)"
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
        session_id=session_id, summary=summary, events=events, path=session_dir,
    )


# ---------------------------------------------------------------------------
# `--since` parsing
# ---------------------------------------------------------------------------


_DURATION_UNITS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 86400 * 7,
}


def parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """Parse a ``--since`` argument into an aware UTC :class:`datetime`.

    Accepts two forms:

    1. **Relative duration** like ``7d``, ``24h``, ``30m``, ``2w``.
       Resolved against ``now`` (default: current UTC) so older
       sessions are filtered.
    2. **Absolute ISO-8601** like ``2026-04-01`` or
       ``2026-04-01T12:00:00Z``.

    Args:
        value: The user-provided string. Whitespace is stripped.
        now: Reference time for relative durations. Defaults to
            ``datetime.now(timezone.utc)``. Overridable for tests.

    Returns:
        An aware UTC :class:`datetime` representing the cutoff.

    Raises:
        ValueError: When ``value`` cannot be interpreted.
    """
    s = value.strip()
    if not s:
        raise ValueError("empty --since value")
    now = now or datetime.now(timezone.utc)
    # Relative: <int><unit>, e.g. 7d, 24h.
    if s[-1].lower() in _DURATION_UNITS and s[:-1].isdigit():
        seconds = int(s[:-1]) * _DURATION_UNITS[s[-1].lower()]
        return now - timedelta(seconds=seconds)
    # Absolute ISO-8601. Accept both ``Z`` suffix and explicit offsets.
    iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(
            f"invalid --since {value!r}: expected '<N>{{s,m,h,d,w}}' "
            "or ISO-8601 timestamp"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _record_started_at(record: SessionRecord) -> datetime | None:
    """Best-effort parse of ``record.started_at`` into an aware UTC datetime."""
    raw = record.started_at
    if not raw:
        return None
    iso = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"


def _color(text: str, code: str, *, enable: bool) -> str:
    """Wrap ``text`` in ANSI ``code`` when ``enable`` is True."""
    return f"{code}{text}{_RESET}" if enable else text


def _short(s: str, width: int) -> str:
    """Truncate ``s`` to fit in ``width`` columns (replacing newlines)."""
    flat = s.replace("\n", " ").replace("\r", " ")
    if len(flat) <= width:
        return flat
    if width <= 1:
        return flat[:width]
    return flat[: width - 1] + "…"


def _short_date(iso: str) -> str:
    """Compact ``2026-04-30T05:10:01Z`` -> ``2026-04-30 05:10`` for the table."""
    if not iso:
        return ""
    iso = iso.replace("Z", "")
    head, _, _ = iso.partition(".")
    if "T" in head:
        date_part, _, time_part = head.partition("T")
        time_part = time_part[:5]  # HH:MM
        return f"{date_part} {time_part}"
    return head


def _resolve_color(color: bool | None) -> bool:
    """Resolve a tri-state ``color`` flag against TTY + ``NO_COLOR`` env."""
    if color is not None:
        return color
    isatty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    return isatty and not os.environ.get("NO_COLOR")


def format_session_table(
    records: Iterable[SessionRecord],
    *,
    limit: int = 20,
    color: bool | None = None,
) -> str:
    """Render ``records`` as a fixed-column table.

    Args:
        records: Any iterable of :class:`SessionRecord`.
        limit: Maximum number of rows to show. Use ``<= 0`` for unlimited.
        color: When True, emit ANSI color. When None, auto-detect via
            ``sys.stdout.isatty()`` and the ``NO_COLOR`` env var.

    Returns:
        A multi-line string. Empty input returns the header line plus a
        friendly "no sessions" footer.
    """
    enable = _resolve_color(color)
    rows = list(records)
    if limit > 0:
        rows = rows[:limit]

    header_cols = (
        ("SESSION_ID", 38),
        ("DATE", 16),
        ("MODEL", 18),
        ("STEPS", 5),
        ("COST", 8),
        ("OK", 3),
        ("PROMPT", 60),
    )
    header_line = "  ".join(
        _color(name.ljust(width), _BOLD, enable=enable)
        for name, width in header_cols
    )
    if not rows:
        empty = _color(
            "(no persisted sessions found)", _DIM, enable=enable,
        )
        return f"{header_line}\n{empty}"

    lines = [header_line]
    for r in rows:
        ok_str = "yes" if r.success else "no "
        ok_styled = _color(
            ok_str, _GREEN if r.success else _RED, enable=enable,
        )
        cost_str = f"${r.cost_usd:.4f}"
        line = "  ".join(
            [
                r.session_id.ljust(38),
                _short_date(r.started_at).ljust(16),
                _short(r.model, 18).ljust(18),
                str(r.steps).rjust(5),
                cost_str.rjust(8),
                ok_styled,
                _short(r.prompt, 60),
            ]
        )
        lines.append(line)
    return "\n".join(lines)


def format_session_detail(
    detail: SessionDetail,
    *,
    color: bool | None = None,
    include_events: bool = True,
) -> str:
    """Pretty-print a single session: summary header + transcript.

    Args:
        detail: The loaded :class:`SessionDetail`.
        color: Enable ANSI color (auto-detect when None).
        include_events: When False, skip the transcript and print only
            the summary block. Useful when the user only wants metadata.

    Returns:
        Multi-line printable string.
    """
    enable = _resolve_color(color)
    s = detail.summary
    out: list[str] = []
    out.append(_color(f"Session: {detail.session_id}", _BOLD, enable=enable))
    out.append(f"  path:        {detail.path}")
    out.append(f"  model:       {s.get('model', '')}")
    out.append(f"  started:     {s.get('started_at', '')}")
    out.append(f"  ended:       {s.get('ended_at', '')}")
    out.append(f"  cwd:         {s.get('cwd', '')}")
    out.append(f"  sandbox:     {s.get('sandbox', '')}")
    out.append(f"  approval:    {s.get('approval', '')}")
    out.append(f"  steps:       {s.get('steps', 0)}")
    out.append(f"  tool calls:  {s.get('tool_calls_total', 0)}")
    cost = float(s.get("cost_usd", 0.0) or 0.0)
    out.append(f"  cost:        ${cost:.6f}")
    success = bool(s.get("success", False))
    ok_label = _color(
        "yes" if success else "no",
        _GREEN if success else _RED,
        enable=enable,
    )
    out.append(f"  success:     {ok_label}")
    if s.get("error"):
        out.append(_color(f"  error:       {s['error']}", _RED, enable=enable))
    out.append("")
    out.append(_color("Prompt:", _BOLD, enable=enable))
    prompt = str(s.get("prompt", ""))
    for line in prompt.splitlines() or [""]:
        out.append(f"  {line}")
    if not include_events:
        return "\n".join(out)

    out.append("")
    out.append(
        _color(f"Events ({len(detail.events)}):", _BOLD, enable=enable),
    )
    if not detail.events:
        out.append(_color("  (no events recorded)", _DIM, enable=enable))
        return "\n".join(out)
    for ev in detail.events:
        ev_type = str(ev.get("type") or "?")
        meta = ev.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {"raw": meta}
        if ev_type == "user_message":
            content = _short(str(meta.get("content", "")), 200)
            out.append(_color(f"  [user] {content}", _DIM, enable=enable))
        elif ev_type == "agent_result":
            output = _short(str(meta.get("output", "")), 200)
            steps = meta.get("steps", 0)
            ok = "yes" if meta.get("success") else "no"
            ok_styled = _color(
                ok, _GREEN if meta.get("success") else _RED, enable=enable,
            )
            out.append(
                f"  [agent] steps={steps} ok={ok_styled} output={output}",
            )
        else:
            preview = _short(json.dumps(meta, default=str), 200)
            out.append(f"  [{ev_type}] {preview}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def cmd_sessions_list(args: argparse.Namespace) -> int:
    """Implement ``chimera ferret sessions list``.

    Filter flags:

    * ``--since 7d`` / ``--since 2026-04-01`` — drop sessions older than
      the cutoff.
    * ``--model gpt-5`` — keep only sessions whose ``model`` matches
      exactly.
    * ``--limit 50`` — cap the number of rows after filtering. ``<= 0``
      means "no cap".
    * ``--json`` — print one JSON array of records to stdout instead of
      the human-readable table.

    Args:
        args: Parsed argparse namespace. Recognized attributes:
            ``sessions_since``, ``sessions_model``, ``sessions_limit``,
            ``sessions_json``, ``no_color``.

    Returns:
        Exit code: ``0`` on success (including empty result set),
        ``2`` when ``--since`` cannot be parsed.
    """
    records = list(iter_sessions())

    since_raw = getattr(args, "sessions_since", None)
    if since_raw:
        try:
            cutoff = parse_since(str(since_raw))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        records = [
            r for r in records
            if (dt := _record_started_at(r)) is not None and dt >= cutoff
        ]

    model_filter = getattr(args, "sessions_model", None)
    if model_filter:
        records = [r for r in records if r.model == model_filter]

    limit = int(getattr(args, "sessions_limit", 20) or 20)

    if bool(getattr(args, "sessions_json", False)):
        rows = records if limit <= 0 else records[:limit]
        print(json.dumps([r.to_dict() for r in rows], indent=2))
        return 0

    no_color = bool(getattr(args, "no_color", False))
    color: bool | None = False if no_color else None
    print(format_session_table(records, limit=limit, color=color))
    return 0


def cmd_sessions_show(args: argparse.Namespace) -> int:
    """Implement ``chimera ferret sessions show <id>``.

    Args:
        args: Parsed argparse namespace. Recognized attributes:
            ``sessions_target`` (the SESSION_ID, may be ``None``),
            ``full`` (include events; default True via the dispatcher),
            ``sessions_json`` (machine output), ``no_color``.

    Returns:
        Exit code: ``0`` on success, ``2`` when the session id is
        missing or unknown.
    """
    session_id = getattr(args, "sessions_target", None)
    if not session_id:
        print(
            "error: 'ferret sessions show' requires a SESSION_ID argument "
            "(see 'ferret sessions list' for available ids).",
            file=sys.stderr,
        )
        return 2

    try:
        detail = get_session(str(session_id))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "hint: list available sessions with "
            "'chimera ferret sessions list' "
            f"(eventlog root: {default_eventlog_root()})",
            file=sys.stderr,
        )
        return 2

    full = bool(getattr(args, "full", False))
    if bool(getattr(args, "sessions_json", False)):
        payload: dict[str, Any] = {
            "session_id": detail.session_id,
            "path": str(detail.path),
            "summary": detail.summary,
        }
        if full:
            payload["events"] = detail.events
        print(json.dumps(payload, indent=2))
        return 0

    no_color = bool(getattr(args, "no_color", False))
    color: bool | None = False if no_color else None
    print(
        format_session_detail(
            detail, color=color, include_events=full,
        )
    )
    return 0


def dispatch_sessions(args: argparse.Namespace) -> int | None:
    """Top-level entry called by FF1's CLI.

    Dispatches ``chimera ferret sessions ...`` based on the parsed args.
    Returning ``None`` means "no sessions subcommand asked for; caller
    proceeds with the normal ferret dispatch path".

    Args:
        args: Parsed argparse namespace. Looks at
            ``args.sessions_command`` (must equal ``"sessions"`` to engage)
            and ``args.sessions_action`` (``"list"``, ``"show"``, or
            ``None`` -> default to ``list``).

    Returns:
        Exit code, or ``None`` when this dispatcher does not apply.
    """
    if getattr(args, "sessions_command", None) != "sessions":
        return None
    action = getattr(args, "sessions_action", None)
    if action == "list" or action is None:
        return cmd_sessions_list(args)
    if action == "show":
        return cmd_sessions_show(args)
    print(
        f"error: unknown 'sessions' action: {action!r} "
        "(supported: list, show)",
        file=sys.stderr,
    )
    return 2
