"""``chimera stoat sessions`` — list/show/cost over the eventlog plus share.

Every persisted stoat run journals its prompt, agent result, and event
trail to ``~/.chimera/eventlog/stoat-<utc>-<uuid>/``. This module exposes
that on-disk corpus to the CLI so users can ``sessions list`` for a
chronological table, ``sessions show <id>`` for the persisted summary +
events, ``sessions cost`` for cost rollups, and ``share <id>`` to package
a transcript for offline review.

Stdlib only at module level. Mirrors the weasel/shrew sessions layout
(``summary.json`` + ``event-NNNNNN-<id>.json``) and re-uses
:mod:`chimera.mink.cost` for the rollup so the JSON / CSV / text schema
stays identical across all six CLIs.

Trademark hygiene: no upstream brand names. ``stoat-`` is the on-disk
session prefix, paralleling ``mink-`` / ``otter-`` / ``ferret-`` /
``weasel-`` / ``shrew-``.
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
    "iter_run_records",
    "get_session",
    "default_eventlog_root",
    "default_shares_dir",
    "cmd_sessions_list",
    "cmd_sessions_show",
    "cmd_sessions_cost",
    "cmd_share",
    "dispatch_sessions",
    "dispatch_share",
    "render_share_json",
    "render_share_markdown",
    "write_share_file",
    "VALID_SHARE_SINKS",
    "VALID_SHARE_FORMATS",
]


_PREFIX = "stoat-"
_CLI_ORIGIN = "stoat"
"""On-disk prefix for stoat session directories. Mirrors the weasel/
shrew/ferret prefix conventions so a single ``ls ~/.chimera/eventlog/``
line tells you which CLI emitted each run."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class SessionRecord:
    """Compact summary of one persisted stoat session.

    Built from ``summary.json`` only — no event walk needed.
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
    cli_origin: str = ""

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
            "cli_origin": self.cli_origin,
        }


@dataclass
class SessionDetail:
    """Full detail for one stoat session: summary + persisted events."""

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


def default_shares_dir() -> Path:
    """Return ``~/.chimera/shares/`` (created lazily by :func:`write_share_file`)."""
    return Path.home() / ".chimera" / "shares"


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
    session_dir: Path,
    summary: dict[str, Any],
    *,
    cli_origin: str = _CLI_ORIGIN,
) -> SessionRecord:
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
        cli_origin=cli_origin,
    )


def iter_sessions(
    eventlog_root: Path | None = None,
    *,
    all_clis: bool = False,
) -> Iterator[SessionRecord]:
    """Yield one :class:`SessionRecord` per persisted stoat session, newest first.

    Args:
        eventlog_root: Override the eventlog root. Defaults to
            :func:`default_eventlog_root`.
        all_clis: When ``True``, yield records for every Chimera CLI's
            sessions (B9-W11). Default ``False`` preserves the historic
            ``stoat-`` only behavior.

    Yields:
        :class:`SessionRecord` instances ordered by directory name
        descending. ``stoat-<UTC>-<uuid>`` prefixes sort lexically
        identical to chronologically.
    """
    # WHY (B9-W11): late-binding sibling import.
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
            cli_origin=cross.cli_origin,
        )


def get_session(
    session_id: str, eventlog_root: Path | None = None,
) -> SessionDetail:
    """Load a session's summary + every ``event-*.json`` file.

    Args:
        session_id: The session directory name
            (e.g. ``stoat-20260430T101501-71032a5e``).
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
    """Truncate ``s`` to ``width`` columns, replacing newlines."""
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


def format_session_table(
    records: list[SessionRecord],
    *,
    show_origin: bool = False,
) -> str:
    """Render ``records`` as a fixed-width table for ``sessions list``.

    Args:
        records: The records to render.
        show_origin: When ``True`` (cross-CLI mode, B9-W11), render an
            extra ``ORIGIN`` column.
    """
    if not records:
        return "(no stoat sessions found)"
    rows: list[str] = []
    if show_origin:
        rows.append(
            f"{'STARTED':<16}  {'ID':<36}  {'ORIGIN':<7}  "
            f"{'OK':<3}  {'STEPS':>5}  {'PROMPT':<40}"
        )
        rows.append("-" * 118)
    else:
        rows.append(
            f"{'STARTED':<16}  {'ID':<36}  {'OK':<3}  {'STEPS':>5}  {'PROMPT':<40}"
        )
        rows.append("-" * 110)
    for r in records:
        ok = "yes" if r.success else "no"
        if show_origin:
            origin = (r.cli_origin or "?")[:7]
            rows.append(
                f"{_short_date(r.started_at):<16}  "
                f"{_short(r.session_id, 36):<36}  "
                f"{origin:<7}  "
                f"{ok:<3}  "
                f"{r.steps:>5}  "
                f"{_short(r.prompt, 40):<40}"
            )
        else:
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
    """Render a :class:`SessionDetail` as human-readable text."""
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
# CLI handlers — list / show
# ---------------------------------------------------------------------------


def cmd_sessions_list(
    *,
    eventlog_root: Path | None = None,
    json_output: bool = False,
    out: Any = None,
    all_clis: bool = False,
) -> int:
    """Implement ``chimera stoat sessions list``.

    Args:
        eventlog_root: Override the eventlog root (mainly for tests).
        json_output: Emit a JSON array instead of the table.
        out: Stream to write to. Defaults to :data:`sys.stdout`.
        all_clis: When ``True`` (``--all-clis``, B9-W11), include
            sessions created by every Chimera CLI; the table grows an
            ``ORIGIN`` column.

    Returns:
        Process exit code.
    """
    stream = out if out is not None else sys.stdout
    records = list(iter_sessions(eventlog_root, all_clis=all_clis))
    if json_output:
        stream.write(json.dumps([r.to_dict() for r in records], indent=2))
        stream.write("\n")
    else:
        stream.write(format_session_table(records, show_origin=all_clis))
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
    """Implement ``chimera stoat sessions show <id>``.

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
        err_stream.write("stoat sessions show: missing session id\n")
        return 2
    try:
        detail = get_session(session_id, eventlog_root=eventlog_root)
    except FileNotFoundError as exc:
        err_stream.write(f"stoat sessions show: {exc}\n")
        return 2
    if json_output:
        stream.write(json.dumps(detail.to_dict(), indent=2))
        stream.write("\n")
    else:
        stream.write(format_session_detail(detail))
        stream.write("\n")
    return 0


# ---------------------------------------------------------------------------
# Cost rollup — re-uses chimera.mink.cost.compute_summary
# ---------------------------------------------------------------------------


def iter_run_records(eventlog_root: Path | None = None) -> Iterator[Any]:
    """Yield :class:`chimera.mink.runs.RunRecord` for every persisted stoat session.

    Mirrors :func:`chimera.weasel.sessions.iter_run_records` so the same
    ``compute_summary`` rollup applies verbatim — no new cost/format/CSV
    code lives here.
    """
    from chimera.mink.runs import _read_summary as mink_read_summary
    from chimera.mink.runs import _summary_to_record as mink_summary_to_record

    root = eventlog_root or default_eventlog_root()
    if not root.exists():
        return
    candidates = [
        p for p in root.iterdir()
        if p.is_dir() and p.name.startswith(_PREFIX)
    ]
    candidates.sort(key=lambda p: p.name, reverse=True)
    for run_dir in candidates:
        summary = mink_read_summary(run_dir)
        if summary is None:
            continue
        yield mink_summary_to_record(run_dir, summary)


def cmd_sessions_cost(
    *,
    since: str | None = None,
    model: str | None = None,
    fmt: str = "text",
    limit: int | None = None,
    eventlog_root: Path | None = None,
    use_rich: bool = True,
    out: Any = None,
    err: Any = None,
) -> int:
    """Implement ``chimera stoat sessions cost``.

    Walks ``~/.chimera/eventlog/stoat-*/summary.json`` and aggregates
    cost via :func:`chimera.mink.cost.compute_summary`. The output schema
    is byte-identical to ``mink runs cost`` / ``weasel sessions cost``
    so downstream dashboards stay one-parser.
    """
    from chimera.mink.cost import (
        compute_summary,
        format_csv,
        format_json,
        format_text,
        parse_since,
    )

    stream = out if out is not None else sys.stdout
    err_stream = err if err is not None else sys.stderr

    fmt_norm = (fmt or "text").strip().lower()
    if fmt_norm not in {"text", "json", "csv"}:
        err_stream.write(
            f"stoat sessions cost: unknown --format {fmt!r} "
            "(supported: text, json, csv)\n"
        )
        return 2
    try:
        cutoff = parse_since(since)
    except ValueError as exc:
        err_stream.write(f"stoat sessions cost: {exc}\n")
        return 2

    summary = compute_summary(
        iter_run_records(eventlog_root),
        since=cutoff,
        since_label=since,
        model=model,
        limit=limit,
    )
    if fmt_norm == "json":
        body = format_json(summary)
    elif fmt_norm == "csv":
        body = format_csv(summary)
    else:
        body = format_text(summary, use_rich=use_rich)
    stream.write(body)
    if not body.endswith("\n"):
        stream.write("\n")
    return 0


# ---------------------------------------------------------------------------
# Share — render a session as JSON or markdown, write to file or stdout
# ---------------------------------------------------------------------------


VALID_SHARE_SINKS = ("file", "stdout")
"""Sinks supported by :func:`cmd_share`. HTTP / HTML are intentionally
omitted — stoat keeps the share surface small (mirrors weasel)."""

VALID_SHARE_FORMATS = ("json", "md")
"""Render formats supported by :func:`cmd_share`. ``json`` is the default
— it's lossless round-trip with ``sessions show --json``."""

_SHARE_FORMAT_EXTENSIONS: dict[str, str] = {
    "json": ".json",
    "md": ".md",
}


def render_share_json(detail: SessionDetail) -> str:
    """Render ``detail`` as JSON, schema-compatible with ``sessions show --json``."""
    return json.dumps(detail.to_dict(), indent=2, default=str) + "\n"


def render_share_markdown(detail: SessionDetail) -> str:
    """Render ``detail`` as a GitHub-flavored Markdown transcript.

    Mirrors :func:`chimera.weasel.sessions.render_share_markdown` adapted
    to the stoat banner — same heading layout, same code-fence per event.
    """
    s = detail.summary
    lines: list[str] = []
    lines.append(f"# Stoat session `{detail.session_id}`")
    lines.append("")
    lines.append(f"- model: `{s.get('model', '')}`")
    lines.append(f"- started: {s.get('started_at', '')}")
    lines.append(f"- ended: {s.get('ended_at', '')}")
    lines.append(f"- steps: {s.get('steps', 0)}")
    lines.append(f"- tool calls: {s.get('tool_calls_total', 0)}")
    cost = float(s.get("cost_usd", 0.0) or 0.0)
    lines.append(f"- cost (USD): {cost:.6f}")
    lines.append(f"- success: {bool(s.get('success', False))}")
    if s.get("error"):
        lines.append(f"- error: {s['error']}")
    lines.append("")
    lines.append("## Prompt")
    lines.append("")
    lines.append("```")
    lines.append(str(s.get("prompt", "")))
    lines.append("```")
    lines.append("")
    lines.append(f"## Events ({len(detail.events)})")
    if not detail.events:
        lines.append("")
        lines.append("_(no events recorded)_")
        return "\n".join(lines) + "\n"
    for ev in detail.events:
        ev_type = str(ev.get("type") or "?")
        meta = ev.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {"raw": meta}
        lines.append("")
        lines.append(f"### `{ev_type}`")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(meta, indent=2, default=str))
        lines.append("```")
    return "\n".join(lines) + "\n"


def _render_share(detail: SessionDetail, fmt: str) -> str:
    """Dispatch to the per-format renderer."""
    if fmt == "json":
        return render_share_json(detail)
    return render_share_markdown(detail)


def write_share_file(
    session_id: str,
    body: str,
    fmt: str,
    *,
    shares_dir: Path | None = None,
) -> Path:
    """Write ``body`` to ``<shares_dir>/<session_id>.<ext>`` and return the path.

    The session id already starts with ``stoat-`` for any session that
    came from this CLI, so we don't re-prefix. Shares dir is created on
    demand with parents.
    """
    out_dir = shares_dir or default_shares_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = _SHARE_FORMAT_EXTENSIONS[fmt]
    name = session_id if session_id.startswith(_PREFIX) else f"{_PREFIX}{session_id}"
    path = out_dir / f"{name}{ext}"
    path.write_text(body, encoding="utf-8")
    return path.resolve()


def cmd_share(
    session_id: str | None,
    *,
    sink: str = "file",
    fmt: str = "json",
    eventlog_root: Path | None = None,
    shares_dir: Path | None = None,
    out: Any = None,
    err: Any = None,
) -> int:
    """Implement ``chimera stoat share <session-id>``.

    Mirrors :func:`chimera.weasel.sessions.cmd_share` — render + dispatch
    to one of the supported sinks. Skips HTTP / HTML to keep the stoat
    surface small.
    """
    stream = out if out is not None else sys.stdout
    err_stream = err if err is not None else sys.stderr

    if not session_id:
        err_stream.write(
            "stoat share: missing session id "
            "(see 'stoat sessions list' for available ids)\n"
        )
        return 2
    sink_norm = (sink or "file").strip().lower()
    fmt_norm = (fmt or "json").strip().lower()
    if sink_norm not in VALID_SHARE_SINKS:
        err_stream.write(
            f"stoat share: unknown --sink {sink!r} "
            f"(supported: {', '.join(VALID_SHARE_SINKS)})\n"
        )
        return 2
    if fmt_norm not in VALID_SHARE_FORMATS:
        err_stream.write(
            f"stoat share: unknown --format {fmt!r} "
            f"(supported: {', '.join(VALID_SHARE_FORMATS)})\n"
        )
        return 2

    try:
        detail = get_session(session_id, eventlog_root=eventlog_root)
    except FileNotFoundError as exc:
        err_stream.write(f"stoat share: {exc}\n")
        return 2

    body = _render_share(detail, fmt_norm)

    if sink_norm == "stdout":
        stream.write(body)
        if not body.endswith("\n"):
            stream.write("\n")
        return 0

    # sink == "file"
    try:
        path = write_share_file(
            session_id, body, fmt_norm, shares_dir=shares_dir,
        )
    except OSError as exc:
        err_stream.write(f"stoat share: failed to write share file: {exc}\n")
        return 1
    stream.write(f"{path}\n")
    return 0


# ---------------------------------------------------------------------------
# Top-level dispatchers
# ---------------------------------------------------------------------------


def dispatch_sessions(args: argparse.Namespace) -> int:
    """Dispatch ``chimera stoat sessions [list|show <id>|cost]``.

    The CLI parser puts the action under ``args.sub_action`` and the
    optional id under ``args.sub_target``. ``--json`` is honored when
    set on the namespace. The ``cost`` action additionally reads
    ``args.cost_since`` / ``args.cost_model`` / ``args.cost_format`` /
    ``args.cost_limit`` (all optional; sane defaults applied here).
    """
    action = getattr(args, "sub_action", None) or "list"
    target = getattr(args, "sub_target", None)
    json_output = bool(getattr(args, "json_output", False))
    all_clis = bool(getattr(args, "sessions_all_clis", False))
    if action == "list":
        return cmd_sessions_list(json_output=json_output, all_clis=all_clis)
    if action == "show":
        return cmd_sessions_show(target, json_output=json_output)
    if action == "cost":
        fmt_default = "json" if json_output else "text"
        return cmd_sessions_cost(
            since=getattr(args, "cost_since", None),
            model=getattr(args, "cost_model", None),
            fmt=getattr(args, "cost_format", None) or fmt_default,
            limit=getattr(args, "cost_limit", None),
        )
    print(
        f"stoat sessions: unknown action {action!r} "
        "(supported: list, show, cost)",
        file=sys.stderr,
    )
    return 2


def dispatch_share(args: argparse.Namespace) -> int:
    """Dispatch ``chimera stoat share <session-id>``.

    The CLI parser stores the session id as ``args.sub_action`` when
    ``args.subcommand == "share"`` (positional slot 2), or
    ``args.sub_target`` when the action slot is also occupied.
    """
    target = (
        getattr(args, "sub_target", None)
        or getattr(args, "sub_action", None)
    )
    sink = getattr(args, "share_sink", None) or "file"
    fmt = getattr(args, "share_format", None) or "json"
    return cmd_share(target, sink=sink, fmt=fmt)
