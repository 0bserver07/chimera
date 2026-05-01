"""``chimera badger sessions`` — inspect persisted badger sessions.

Every ``chimera badger -p ...`` (and REPL turn) journals its prompt,
agent result, and tool calls to ``~/.chimera/eventlog/badger-<utc>-<uuid>/``.
This module exposes that on-disk corpus to the CLI so users can ``sessions
list``, ``sessions show <id>``, ``sessions cost``, and ``share <id>``
without reaching for ``cat`` and ``jq``.

The on-disk schema (``summary.json`` + ``event-NNNNNN-<id>.json``) is
shared with mink, otter, ferret, weasel, and shrew so operators familiar
with one CLI's session listing find a near-identical UX here.

Stdlib only. No external runtime dependency.
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
    "get_session",
    "format_session_table",
    "format_session_detail",
    "default_eventlog_root",
    "parse_since",
    "cmd_sessions_list",
    "cmd_sessions_show",
    "cmd_session_cost",
    "cmd_session_share",
    "dispatch_sessions",
]


_PREFIX = "badger-"


@dataclass
class SessionRecord:
    """Compact summary of one persisted badger session."""

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
    """Full detail for one badger session: summary + every persisted event."""

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


def default_eventlog_root() -> Path:
    """Return ``~/.chimera/eventlog/`` honoring the current ``Path.home()``."""
    return Path.home() / ".chimera" / "eventlog"


def _read_summary(session_dir: Path) -> dict[str, Any] | None:
    """Read and parse ``summary.json`` for ``session_dir``."""
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
    """Yield one :class:`SessionRecord` per persisted badger session, newest first.

    Args:
        eventlog_root: Override the eventlog root. Defaults to
            :func:`default_eventlog_root`.

    Yields:
        :class:`SessionRecord` instances ordered by ``session_id``
        descending (session ids are timestamp-sortable).
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
        session_id: The session directory name.
        eventlog_root: Override the eventlog root.

    Returns:
        A :class:`SessionDetail` with ``summary`` and ``events`` populated.

    Raises:
        FileNotFoundError: When ``session_id`` does not exist.
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
        session_id=session_id, summary=summary, events=events, path=session_dir,
    )


# ---------------------------------------------------------------------------
# `--since` parsing (shared shape with ferret)
# ---------------------------------------------------------------------------

_DURATION_UNITS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 86400 * 7,
}


def parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """Parse a ``--since`` argument into an aware UTC :class:`datetime`."""
    s = value.strip()
    if not s:
        raise ValueError("empty --since value")
    now = now or datetime.now(timezone.utc)
    if s[-1].lower() in _DURATION_UNITS and s[:-1].isdigit():
        seconds = int(s[:-1]) * _DURATION_UNITS[s[-1].lower()]
        return now - timedelta(seconds=seconds)
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
    """Best-effort parse of ``record.started_at`` into aware UTC datetime."""
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


def _short(s: str, width: int) -> str:
    """Truncate ``s`` to fit in ``width`` columns."""
    flat = s.replace("\n", " ").replace("\r", " ")
    if len(flat) <= width:
        return flat
    if width <= 1:
        return flat[:width]
    return flat[: width - 1] + "..."


def _short_date(iso: str) -> str:
    """Compact ``2026-04-30T05:10:01Z`` -> ``2026-04-30 05:10``."""
    if not iso:
        return ""
    iso = iso.replace("Z", "")
    head, _, _ = iso.partition(".")
    if "T" in head:
        date_part, _, time_part = head.partition("T")
        time_part = time_part[:5]
        return f"{date_part} {time_part}"
    return head


def format_session_table(
    records: Iterable[SessionRecord],
    *,
    limit: int = 20,
) -> str:
    """Render ``records`` as a fixed-column table.

    Args:
        records: Any iterable of :class:`SessionRecord`.
        limit: Maximum number of rows to show. Use ``<= 0`` for unlimited.

    Returns:
        A multi-line string. Empty input returns the header line plus a
        friendly "no sessions" footer.
    """
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
    header_line = "  ".join(name.ljust(width) for name, width in header_cols)
    if not rows:
        return f"{header_line}\n(no persisted badger sessions found)"

    lines = [header_line]
    for r in rows:
        ok_str = "yes" if r.success else "no "
        cost_str = f"${r.cost_usd:.4f}"
        line = "  ".join(
            [
                r.session_id.ljust(38),
                _short_date(r.started_at).ljust(16),
                _short(r.model, 18).ljust(18),
                str(r.steps).rjust(5),
                cost_str.rjust(8),
                ok_str,
                _short(r.prompt, 60),
            ]
        )
        lines.append(line)
    return "\n".join(lines)


def format_session_detail(
    detail: SessionDetail,
    *,
    include_events: bool = True,
) -> str:
    """Pretty-print a single session: summary header + transcript."""
    s = detail.summary
    out: list[str] = []
    out.append(f"Session: {detail.session_id}")
    out.append(f"  path:        {detail.path}")
    out.append(f"  model:       {s.get('model', '')}")
    out.append(f"  started:     {s.get('started_at', '')}")
    out.append(f"  ended:       {s.get('ended_at', '')}")
    out.append(f"  steps:       {s.get('steps', 0)}")
    out.append(f"  tool calls:  {s.get('tool_calls_total', 0)}")
    cost = float(s.get("cost_usd", 0.0) or 0.0)
    out.append(f"  cost:        ${cost:.6f}")
    success = bool(s.get("success", False))
    out.append(f"  success:     {'yes' if success else 'no'}")
    if s.get("error"):
        out.append(f"  error:       {s['error']}")
    out.append("")
    out.append("Prompt:")
    prompt = str(s.get("prompt", ""))
    for line in prompt.splitlines() or [""]:
        out.append(f"  {line}")
    if not include_events:
        return "\n".join(out)

    out.append("")
    out.append(f"Events ({len(detail.events)}):")
    if not detail.events:
        out.append("  (no events recorded)")
        return "\n".join(out)
    for ev in detail.events:
        ev_type = str(ev.get("type") or "?")
        meta = ev.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {"raw": meta}
        if ev_type == "user_message":
            content = _short(str(meta.get("content", "")), 200)
            out.append(f"  [user] {content}")
        elif ev_type == "agent_result":
            output = _short(str(meta.get("output", "")), 200)
            steps = meta.get("steps", 0)
            ok = "yes" if meta.get("success") else "no"
            out.append(f"  [agent] steps={steps} ok={ok} output={output}")
        else:
            preview = _short(json.dumps(meta, default=str), 200)
            out.append(f"  [{ev_type}] {preview}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def cmd_sessions_list(args: argparse.Namespace) -> int:
    """Implement ``chimera badger sessions list``."""
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

    print(format_session_table(records, limit=limit))
    return 0


def cmd_sessions_show(args: argparse.Namespace) -> int:
    """Implement ``chimera badger sessions show <id>``."""
    session_id = getattr(args, "sessions_target", None)
    if not session_id:
        print(
            "error: 'badger sessions show' requires a SESSION_ID argument",
            file=sys.stderr,
        )
        return 2

    try:
        detail = get_session(str(session_id))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
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

    print(format_session_detail(detail, include_events=full))
    return 0


def cmd_session_cost(args: argparse.Namespace) -> int:
    """Implement ``chimera badger sessions cost`` — aggregate spend.

    Sums ``cost_usd`` across all persisted badger sessions, with optional
    ``--since`` cutoff and per-model filter. Pure stdlib.
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

    total = sum(r.cost_usd for r in records)
    by_model: dict[str, float] = {}
    for r in records:
        by_model[r.model] = by_model.get(r.model, 0.0) + r.cost_usd

    if bool(getattr(args, "sessions_json", False)):
        print(json.dumps({
            "total_usd": total,
            "session_count": len(records),
            "by_model": by_model,
        }, indent=2))
        return 0

    print(f"Total cost across {len(records)} session(s): ${total:.6f}")
    if by_model:
        print("By model:")
        for model, spend in sorted(by_model.items(), key=lambda kv: -kv[1]):
            print(f"  {model:<28} ${spend:.6f}")
    return 0


def cmd_session_share(args: argparse.Namespace) -> int:
    """Implement ``chimera badger share <session>`` — export a session.

    Writes a tarball of the session directory to a path next to the
    session itself (or to ``--output`` when given). Pure stdlib via
    :mod:`tarfile`.
    """
    session_id = getattr(args, "sessions_target", None)
    if not session_id:
        print(
            "error: 'badger share' requires a SESSION_ID argument",
            file=sys.stderr,
        )
        return 2
    try:
        detail = get_session(str(session_id))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    import tarfile

    output = getattr(args, "output", None)
    if output:
        tar_path = Path(output)
    else:
        tar_path = detail.path.parent / f"{detail.session_id}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(detail.path, arcname=detail.session_id)
    print(f"badger share: wrote {tar_path}")
    return 0


def dispatch_sessions(args: argparse.Namespace) -> int | None:
    """Top-level dispatcher for ``chimera badger sessions ...``.

    Args:
        args: Parsed argparse namespace with ``sessions_command`` and
            ``sessions_action`` set by the CLI dispatcher.

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
        return cmd_session_cost(args)
    if action == "share":
        return cmd_session_share(args)
    print(
        f"error: unknown 'sessions' action: {action!r} "
        "(supported: list, show, cost, share)",
        file=sys.stderr,
    )
    return 2
