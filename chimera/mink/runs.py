"""``chimera mink runs`` — inspect persisted one-shot mink runs.

Every ``chimera mink -p`` invocation journals its prompt, agent result,
and tool calls to ``~/.chimera/eventlog/mink-<utc>-<uuid>/``. This module
exposes the on-disk corpus to the CLI so users can ``runs list`` for a
table view and ``runs show <id>`` for a transcript without reaching for
``cat`` and ``jq``.

Audit H-3 (``research/mink/AUDIT.md``).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

__all__ = [
    "RunRecord",
    "RunDetail",
    "iter_runs",
    "get_run",
    "format_run_table",
    "format_run_detail",
    "default_eventlog_root",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RunRecord:
    """Compact summary of one persisted mink run.

    Built from ``summary.json`` only — never reads individual event files.

    Attributes:
        run_id: The directory name (e.g. ``mink-20260424T051001-71032a5e``).
        started_at: ISO-8601 UTC start timestamp from ``summary.json``.
        ended_at: ISO-8601 UTC end timestamp from ``summary.json``.
        model: Provider model name actually used (post-fallback).
        prompt: The user prompt that drove this run.
        success: Whether the loop reported ``success=True``.
        cost_usd: Total run cost in USD (zero when unknown).
        steps: Number of ReAct steps executed.
        tool_calls: Total number of tool calls dispatched.
        path: Absolute path to the run's eventlog directory.
        error: Optional error string from ``summary.json`` (None on success).
    """

    run_id: str
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


@dataclass
class RunDetail:
    """Full detail for one mink run: summary + every persisted event."""

    run_id: str
    summary: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    path: Path = field(default_factory=lambda: Path("."))


# ---------------------------------------------------------------------------
# Disk walks
# ---------------------------------------------------------------------------


def default_eventlog_root() -> Path:
    """Return ``~/.chimera/eventlog/`` honoring the current ``Path.home()``."""
    return Path.home() / ".chimera" / "eventlog"


def _read_summary(run_dir: Path) -> dict[str, Any] | None:
    """Read and parse ``summary.json`` for ``run_dir``; ``None`` on any error."""
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        return None
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _summary_to_record(run_dir: Path, summary: dict[str, Any]) -> RunRecord:
    """Convert a ``summary.json`` dict into a :class:`RunRecord`."""
    return RunRecord(
        run_id=str(summary.get("run_id") or run_dir.name),
        started_at=str(summary.get("started_at") or ""),
        ended_at=str(summary.get("ended_at") or ""),
        model=str(summary.get("model") or ""),
        prompt=str(summary.get("prompt") or ""),
        success=bool(summary.get("success", False)),
        cost_usd=float(summary.get("cost_usd", 0.0) or 0.0),
        steps=int(summary.get("steps", 0) or 0),
        tool_calls=int(summary.get("tool_calls_total", 0) or 0),
        path=run_dir,
        error=summary.get("error"),
    )


def iter_runs(eventlog_root: Path | None = None) -> Iterator[RunRecord]:
    """Yield one :class:`RunRecord` per persisted run, newest first.

    Args:
        eventlog_root: Override the eventlog root. Defaults to
            :func:`default_eventlog_root`.

    Yields:
        :class:`RunRecord` instances ordered by ``run_id`` descending
        (run ids start with ``mink-<UTC>-<uuid>``, so lexical ordering
        equals chronological ordering).
    """
    root = eventlog_root or default_eventlog_root()
    if not root.exists():
        return
    candidates = [
        p for p in root.iterdir()
        if p.is_dir() and p.name.startswith("mink-")
    ]
    candidates.sort(key=lambda p: p.name, reverse=True)
    for run_dir in candidates:
        summary = _read_summary(run_dir)
        if summary is None:
            continue
        yield _summary_to_record(run_dir, summary)


def get_run(run_id: str, eventlog_root: Path | None = None) -> RunDetail:
    """Load one run's summary + every ``event-*.json`` file.

    Args:
        run_id: The run directory name (e.g. ``mink-20260424T051001-71032a5e``).
        eventlog_root: Override the eventlog root.

    Returns:
        A :class:`RunDetail` with ``summary`` and ``events`` populated.

    Raises:
        FileNotFoundError: When ``run_id`` does not exist or has no summary.
    """
    root = eventlog_root or default_eventlog_root()
    run_dir = root / run_id
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"run not found: {run_id}")
    summary = _read_summary(run_dir)
    if summary is None:
        raise FileNotFoundError(
            f"run {run_id!r} has no summary.json (did the run abort early?)"
        )
    events: list[dict[str, Any]] = []
    for ev_path in sorted(run_dir.glob("event-*.json")):
        try:
            data = json.loads(ev_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            events.append(data)
    return RunDetail(run_id=run_id, summary=summary, events=events, path=run_dir)


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
    """Compact ``2026-04-24T05:10:01Z`` → ``2026-04-24 05:10`` for the table."""
    if not iso:
        return ""
    iso = iso.replace("Z", "")
    head, _, _ = iso.partition(".")
    if "T" in head:
        date_part, _, time_part = head.partition("T")
        time_part = time_part[:5]  # HH:MM
        return f"{date_part} {time_part}"
    return head


def format_run_table(
    records: Iterable[RunRecord],
    *,
    limit: int = 20,
    color: bool | None = None,
) -> str:
    """Render ``records`` as a fixed-column table.

    Args:
        records: Any iterable of :class:`RunRecord`.
        limit: Maximum number of rows to show. Use ``<= 0`` for unlimited.
        color: When True, emit ANSI color. When None, auto-detect via
            ``sys.stdout.isatty()`` and the ``NO_COLOR`` env var.

    Returns:
        A multi-line string. Empty input returns the header line plus
        a friendly "no runs" footer.
    """
    if color is None:
        import os

        color = bool(getattr(sys.stdout, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")

    rows = list(records)
    if limit > 0:
        rows = rows[:limit]

    header_cols = (
        ("RUN_ID", 36),
        ("DATE", 16),
        ("MODEL", 18),
        ("STEPS", 5),
        ("COST", 8),
        ("OK", 3),
        ("PROMPT", 60),
    )
    header_line = "  ".join(
        _color(name.ljust(width), _BOLD, enable=color)
        for name, width in header_cols
    )
    if not rows:
        empty = _color("(no persisted runs found)", _DIM, enable=color)
        return f"{header_line}\n{empty}"

    lines = [header_line]
    for r in rows:
        ok_str = "yes" if r.success else "no "
        ok_styled = _color(ok_str, _GREEN if r.success else _RED, enable=color)
        cost_str = f"${r.cost_usd:.4f}"
        line = "  ".join(
            [
                r.run_id.ljust(36),
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


def format_run_detail(
    detail: RunDetail,
    *,
    color: bool | None = None,
    include_events: bool = True,
) -> str:
    """Pretty-print a single run: summary header + transcript.

    Args:
        detail: The loaded :class:`RunDetail`.
        color: Enable ANSI color (auto-detect when None).
        include_events: When False, skip the transcript and print only the
            summary block. Useful when the user only wants metadata.
    """
    if color is None:
        import os

        color = bool(getattr(sys.stdout, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")

    s = detail.summary
    out: list[str] = []
    out.append(_color(f"Run: {detail.run_id}", _BOLD, enable=color))
    out.append(f"  path:        {detail.path}")
    out.append(f"  model:       {s.get('model', '')}")
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
        enable=color,
    )
    out.append(f"  success:     {ok_label}")
    if s.get("error"):
        out.append(_color(f"  error:       {s['error']}", _RED, enable=color))
    out.append("")
    out.append(_color("Prompt:", _BOLD, enable=color))
    prompt = str(s.get("prompt", ""))
    for line in prompt.splitlines() or [""]:
        out.append(f"  {line}")
    if not include_events:
        return "\n".join(out)

    out.append("")
    out.append(_color(f"Events ({len(detail.events)}):", _BOLD, enable=color))
    if not detail.events:
        out.append(_color("  (no events recorded)", _DIM, enable=color))
        return "\n".join(out)
    for ev in detail.events:
        ev_type = str(ev.get("type") or "?")
        meta = ev.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {"raw": meta}
        if ev_type == "user_message":
            content = _short(str(meta.get("content", "")), 200)
            out.append(_color(f"  [user] {content}", _DIM, enable=color))
        elif ev_type == "agent_result":
            output = _short(str(meta.get("output", "")), 200)
            steps = meta.get("steps", 0)
            ok = "yes" if meta.get("success") else "no"
            ok_styled = _color(ok, _GREEN if meta.get("success") else _RED, enable=color)
            out.append(f"  [agent] steps={steps} ok={ok_styled} output={output}")
        else:
            # Generic fallback for any other event type so future schemas
            # don't simply disappear from the transcript.
            preview = _short(json.dumps(meta, default=str), 200)
            out.append(f"  [{ev_type}] {preview}")
    return "\n".join(out)
