"""``chimera otter sessions`` — inspect persisted otter sessions.

Every ``chimera otter -p ...`` (and server-driven turn) journals its
prompt, agent result, and tool calls to
``~/.chimera/eventlog/otter-<utc>-<uuid>/``. This module exposes that
on-disk corpus to the CLI so users can ``sessions list`` for a table
view and ``sessions show <id>`` for a transcript without reaching for
``cat`` and ``jq``.

This is the otter twin of :mod:`chimera.mink.runs`; the on-disk schema
(``summary.json`` + ``event-NNNNNN-<id>.json``) is shared with mink so
that operators familiar with ``mink runs`` find a near-identical UX
under ``otter sessions``.

Stdlib only. The only external runtime dependency is
:mod:`chimera.mink.runs` for shared formatting helpers, which is itself
stdlib-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

__all__ = [
    "SessionRecord",
    "SessionDetail",
    "iter_sessions",
    "iter_session_run_records",
    "get_session",
    "format_session_table",
    "format_session_detail",
    "default_eventlog_root",
    "parse_since",
    "rename_session",
    "cmd_sessions_list",
    "cmd_sessions_show",
    "cmd_sessions_cost",
    "cmd_sessions_rename",
    "dispatch_sessions",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SessionRecord:
    """Compact summary of one persisted otter session.

    Built from ``summary.json`` only — never reads individual event
    files. Mirrors :class:`chimera.mink.runs.RunRecord` so the two CLIs
    share rendering helpers.

    Attributes:
        session_id: The directory name (e.g. ``otter-20260424T051001-71032a5e``).
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
        title: Optional hand-authored label from ``--title`` /
            ``sessions rename``. ``None`` falls back to the prompt for
            display purposes.
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
    title: str | None = None
    cli_origin: str = ""

    def display_title(self) -> str:
        """Return the user-facing title.

        Returns ``title`` when set (via ``--title`` or
        ``sessions rename``); otherwise falls back to ``prompt`` so the
        existing truncated-prompt heuristic continues to work.
        """
        if self.title and self.title.strip():
            return self.title
        return self.prompt

    def to_dict(self) -> dict[str, Any]:
        """Render as a JSON-serializable dict for ``--json`` output."""
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "model": self.model,
            "prompt": self.prompt,
            "title": self.title,
            "success": self.success,
            "cost_usd": self.cost_usd,
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "path": str(self.path),
            "error": self.error,
            "cli_origin": self.cli_origin,
        }


@dataclass
class SessionDetail:
    """Full detail for one otter session: summary + every persisted event."""

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


_PREFIX = "otter-"
_CLI_ORIGIN = "otter"


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


def _summary_to_record(
    session_dir: Path,
    summary: dict[str, Any],
    *,
    cli_origin: str = _CLI_ORIGIN,
) -> SessionRecord:
    """Convert a ``summary.json`` dict into a :class:`SessionRecord`."""
    raw_title = summary.get("title")
    title = str(raw_title) if isinstance(raw_title, str) and raw_title.strip() else None
    return SessionRecord(
        session_id=str(summary.get("session_id") or summary.get("run_id") or session_dir.name),
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
        cli_origin=cli_origin,
    )


def iter_session_run_records(eventlog_root: Path | None = None) -> Iterator[Any]:
    """Yield :class:`chimera.mink.runs.RunRecord` for every persisted otter session.

    Otter and mink share the on-disk ``summary.json`` schema, so we can
    reuse the mink-side aggregation (:func:`chimera.mink.cost.compute_summary`)
    by rebuilding :class:`RunRecord` instances from each otter session dir.
    The ``run_id`` field is set to the otter session id so cost rollups
    show ``otter-...`` prefixes verbatim.

    Args:
        eventlog_root: Override the eventlog root. Defaults to
            :func:`default_eventlog_root`.

    Yields:
        :class:`RunRecord` instances ordered by directory name descending
        (newest first).
    """
    from chimera.mink.runs import _summary_to_record

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
        # WHY: ``chimera.mink.runs._summary_to_record`` keys off
        # ``run_id`` first, then falls back to the directory name. Otter
        # summaries write ``session_id`` instead of ``run_id``, so we
        # bridge the field here so the mink-side helper sees a stable
        # id across both flavors.
        if "run_id" not in summary and "session_id" in summary:
            summary = dict(summary)
            summary["run_id"] = summary["session_id"]
        yield _summary_to_record(session_dir, summary)


def iter_sessions(
    eventlog_root: Path | None = None,
    *,
    all_clis: bool = False,
) -> Iterator[SessionRecord]:
    """Yield one :class:`SessionRecord` per persisted otter session, newest first.

    Args:
        eventlog_root: Override the eventlog root. Defaults to
            :func:`default_eventlog_root`.
        all_clis: When ``True``, also yield records for sessions
            created by other Chimera CLIs. Default ``False`` preserves
            the historic ``otter-`` only behavior (B9-W11).

    Yields:
        :class:`SessionRecord` instances ordered by ``session_id``
        descending (session ids start with ``otter-<UTC>-<uuid>``, so
        lexical ordering equals chronological ordering).
    """
    # WHY (B9-W11): late-binding sibling import to avoid hard-coupling
    # otter sessions to the cross-CLI module's import order.
    from chimera.sessions.eventlog.cross_cli import (
        iter_all_sessions as _iter_all,
        iter_sessions_for_cli as _iter_for,
    )

    source = (
        _iter_all(eventlog_root)
        if all_clis
        else _iter_for(_CLI_ORIGIN, eventlog_root)
    )
    for cross in source:
        # WHY: rebuild as the otter-flavored ``SessionRecord`` so the
        # extra ``title`` field (otter-only) and the existing
        # :meth:`display_title` semantics keep working downstream.
        raw_title = cross.summary.get("title") if cross.summary else None
        title = (
            str(raw_title)
            if isinstance(raw_title, str) and raw_title.strip()
            else None
        )
        yield SessionRecord(
            session_id=cross.session_id,
            started_at=cross.started_at,
            ended_at=cross.ended_at,
            model=cross.model,
            prompt=cross.prompt,
            success=cross.success,
            cost_usd=cross.cost_usd,
            steps=cross.steps,
            tool_calls=cross.tool_calls,
            path=cross.path,
            error=cross.error,
            title=title,
            cli_origin=cross.cli_origin,
        )


def get_session(
    session_id: str, eventlog_root: Path | None = None,
) -> SessionDetail:
    """Load one session's summary + every ``event-*.json`` file.

    Args:
        session_id: The session directory name
            (e.g. ``otter-20260424T051001-71032a5e``).
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

    1. **Relative duration** like ``7d``, ``24h``, ``30m``, ``2w``. Resolved
       against ``now`` (default: current UTC) so older sessions are filtered.
    2. **Absolute ISO-8601** like ``2026-04-01`` or ``2026-04-01T12:00:00Z``.

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
# Formatting helpers (delegate to mink.runs ANSI color helpers)
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
    """Compact ``2026-04-24T05:10:01Z`` -> ``2026-04-24 05:10`` for the table."""
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
    import os

    isatty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    return isatty and not os.environ.get("NO_COLOR")


def format_session_table(
    records: Iterable[SessionRecord],
    *,
    limit: int = 20,
    color: bool | None = None,
    show_origin: bool = False,
) -> str:
    """Render ``records`` as a fixed-column table.

    Args:
        records: Any iterable of :class:`SessionRecord`.
        limit: Maximum number of rows to show. Use ``<= 0`` for unlimited.
        color: When True, emit ANSI color. When None, auto-detect via
            ``sys.stdout.isatty()`` and the ``NO_COLOR`` env var.
        show_origin: When ``True`` (cross-CLI mode), render an extra
            ``ORIGIN`` column right after ``SESSION_ID`` (B9-W11).

    Returns:
        A multi-line string. Empty input returns the header line plus a
        friendly "no sessions" footer.
    """
    enable = _resolve_color(color)
    rows = list(records)
    if limit > 0:
        rows = rows[:limit]

    # WHY (O4-W9): TITLE column surfaces a hand-authored label written
    # by ``chimera otter -p --title "..."`` (see
    # :func:`chimera.otter.cli._write_run_summary`) or set after-the-fact
    # via ``sessions rename``. When ``title`` is unset on a record we
    # fall back to the prompt for back-compat with existing fixtures —
    # both routes go through :meth:`SessionRecord.display_title`.
    base_cols: list[tuple[str, int]] = [("SESSION_ID", 38)]
    if show_origin:
        base_cols.append(("ORIGIN", 7))
    base_cols.extend([
        ("DATE", 16),
        ("MODEL", 18),
        ("STEPS", 5),
        ("COST", 8),
        ("OK", 3),
        ("TITLE", 40),
        ("PROMPT", 60),
    ])
    header_line = "  ".join(
        _color(name.ljust(width), _BOLD, enable=enable)
        for name, width in base_cols
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
        title_cell = _short(r.display_title(), 40)
        cells: list[str] = [r.session_id.ljust(38)]
        if show_origin:
            cells.append((r.cli_origin or "?").ljust(7))
        cells.extend([
            _short_date(r.started_at).ljust(16),
            _short(r.model, 18).ljust(18),
            str(r.steps).rjust(5),
            cost_str.rjust(8),
            ok_styled,
            title_cell.ljust(40),
            _short(r.prompt, 60),
        ])
        lines.append("  ".join(cells))
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
    title_val = s.get("title")
    if isinstance(title_val, str) and title_val.strip():
        out.append(f"  title:       {title_val}")
    out.append(f"  started:     {s.get('started_at', '')}")
    out.append(f"  ended:       {s.get('ended_at', '')}")
    out.append(f"  cwd:         {s.get('cwd', '')}")
    out.append(f"  perm-mode:   {s.get('permission_mode', '')}")
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
    """Implement ``chimera otter sessions list``.

    Filter flags:

    * ``--since 7d`` / ``--since 2026-04-01`` — drop sessions older than
      the cutoff.
    * ``--model glm-5.1:cloud`` — keep only sessions whose ``model``
      matches exactly.
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
    all_clis = bool(getattr(args, "sessions_all_clis", False))
    records = list(iter_sessions(all_clis=all_clis))

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
    print(format_session_table(
        records, limit=limit, color=color, show_origin=all_clis,
    ))
    return 0


def cmd_sessions_show(args: argparse.Namespace) -> int:
    """Implement ``chimera otter sessions show <id>``.

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
            "error: 'otter sessions show' requires a SESSION_ID argument "
            "(see 'otter sessions list' for available ids).",
            file=sys.stderr,
        )
        return 2

    try:
        detail = get_session(str(session_id))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "hint: list available sessions with "
            "'chimera otter sessions list' "
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


def cmd_sessions_cost(args: argparse.Namespace) -> int:
    """Implement ``chimera otter sessions cost``.

    Aggregates ``cost_usd`` / ``total_tokens`` across persisted otter
    sessions under ``~/.chimera/eventlog/otter-*/`` and emits a rollup
    in human-readable, JSON, or CSV form. Reuses
    :func:`chimera.mink.cost.compute_summary` so the on-the-wire JSON
    shape is a strict superset of ``chimera mink runs cost --format json``
    (``totals``, ``by_model``, ``rows``) — i.e. parity with M4 and the
    ``GET /runs/cost`` HTTP route.

    Recognized ``args`` attributes:

    * ``sessions_since`` — shorthand (``7d`` / ``24h`` / ``30m``) or
      ISO-8601 cutoff. ``None`` / empty disables the filter.
    * ``sessions_model`` — case-insensitive substring filter on the
      model name. ``"all"`` and ``None`` mean "every model".
    * ``sessions_format`` — ``"text"`` (default) / ``"json"`` / ``"csv"``.
    * ``sessions_limit`` — cap on the number of rows considered (newest
      first). ``None`` / ``0`` means "no cap".
    * ``no_color`` — when truthy, force the plain ASCII renderer (no
      rich, no ANSI).

    Args:
        args: Parsed argparse namespace.

    Returns:
        Exit code: ``0`` on success (including empty result set),
        ``2`` when ``--since`` / ``--format`` cannot be parsed.
    """
    from chimera.mink.cost import (
        compute_summary,
        format_csv,
        format_json,
        format_text,
        parse_since as _parse_cost_since,
    )

    since_raw = getattr(args, "sessions_since", None)
    try:
        cutoff = _parse_cost_since(since_raw)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    fmt = str(getattr(args, "sessions_format", "text") or "text").lower()
    if fmt not in {"text", "json", "csv"}:
        print(
            f"error: unknown --format {fmt!r} (supported: text, json, csv)",
            file=sys.stderr,
        )
        return 2

    limit_raw = getattr(args, "sessions_limit", None)
    try:
        limit = int(limit_raw) if limit_raw is not None else None
    except (TypeError, ValueError):
        limit = None

    summary = compute_summary(
        iter_session_run_records(),
        since=cutoff,
        since_label=since_raw,
        model=getattr(args, "sessions_model", None),
        limit=limit,
    )

    if fmt == "json":
        print(format_json(summary))
        return 0
    if fmt == "csv":
        print(format_csv(summary))
        return 0
    no_color = bool(getattr(args, "no_color", False))
    print(format_text(summary, use_rich=not no_color))
    return 0


def rename_session(
    session_id: str,
    title: str,
    *,
    eventlog_root: Path | None = None,
) -> Path:
    """Update the ``title`` field of one session's ``summary.json``.

    Used by :func:`cmd_sessions_rename` to back the
    ``chimera otter sessions rename <id> <title>`` subcommand. The write
    is in-place: the existing ``summary.json`` is loaded, the ``title``
    key is upserted (an empty / whitespace-only ``title`` *removes* the
    key — round-trips with the ``--title`` heuristic), and the file is
    re-serialized.

    Args:
        session_id: Session directory name
            (e.g. ``otter-20260424T051001-71032a5e``).
        title: New title. Empty / whitespace-only clears the field.
        eventlog_root: Override ``~/.chimera/eventlog/`` for tests.

    Returns:
        Absolute path to the rewritten ``summary.json``.

    Raises:
        FileNotFoundError: When the session directory or its
            ``summary.json`` does not exist.
        ValueError: When ``summary.json`` is malformed (not a JSON object).
    """
    root = eventlog_root or default_eventlog_root()
    session_dir = root / session_id
    if not session_dir.exists() or not session_dir.is_dir():
        raise FileNotFoundError(f"session not found: {session_id}")
    summary_path = session_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(
            f"session {session_id!r} has no summary.json "
            "(did the session abort early?)"
        )
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"session {session_id!r} summary.json is malformed: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"session {session_id!r} summary.json is not a JSON object"
        )
    cleaned = title.strip() if isinstance(title, str) else ""
    if cleaned:
        data["title"] = cleaned
    else:
        data.pop("title", None)
    summary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return summary_path


def cmd_sessions_rename(args: argparse.Namespace) -> int:
    """Implement ``chimera otter sessions rename <id> <title>``.

    Args:
        args: Parsed argparse namespace. Recognized attributes:
            ``sessions_target`` (the SESSION_ID positional),
            ``sessions_title`` (the new title positional, joined with
            spaces if argparse supplied a list).

    Returns:
        Exit code: ``0`` on success, ``2`` on missing args / unknown id /
        malformed summary.
    """
    session_id = getattr(args, "sessions_target", None)
    if not session_id:
        print(
            "error: 'otter sessions rename' requires SESSION_ID and TITLE "
            "(usage: 'chimera otter sessions rename <id> <title>').",
            file=sys.stderr,
        )
        return 2
    raw_title = getattr(args, "sessions_title", None)
    if raw_title is None:
        print(
            "error: 'otter sessions rename' requires a TITLE argument "
            "(pass an empty string '' to clear an existing title).",
            file=sys.stderr,
        )
        return 2
    if isinstance(raw_title, list):
        title = " ".join(str(p) for p in raw_title)
    else:
        title = str(raw_title)
    try:
        path = rename_session(str(session_id), title)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "hint: list available sessions with "
            "'chimera otter sessions list' "
            f"(eventlog root: {default_eventlog_root()})",
            file=sys.stderr,
        )
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    cleaned = title.strip()
    if cleaned:
        print(f"renamed {session_id} -> {cleaned!r} ({path})")
    else:
        print(f"cleared title for {session_id} ({path})")
    return 0


def dispatch_sessions(args: argparse.Namespace) -> int | None:
    """Top-level entry called by O1's CLI.

    Dispatches ``chimera otter sessions ...`` based on the parsed args.
    Returning ``None`` means "no sessions subcommand asked for; caller
    proceeds with the normal otter dispatch path".

    Args:
        args: Parsed argparse namespace. Looks at
            ``args.sessions_command`` (must equal ``"sessions"`` to engage)
            and ``args.sessions_action`` (``"list"``, ``"show"``,
            ``"cost"``, ``"rename"``, or ``None`` -> default to ``list``).

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
    if action == "cost":
        return cmd_sessions_cost(args)
    if action == "rename":
        return cmd_sessions_rename(args)
    print(
        f"error: unknown 'sessions' action: {action!r} "
        "(supported: list, show, cost, rename)",
        file=sys.stderr,
    )
    return 2
