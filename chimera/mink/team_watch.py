"""Live TTY dashboard for a Chimera agent team.

Reads team state non-destructively from ``~/.chimera/teams/<name>/`` (or a
custom root) and renders a refreshing status block on a TTY. The render
function is pure — it only reads disk state and returns a string — so it
can be unit-tested by handing it a :class:`~chimera.cli.agent_teams.Team`
backed by a tmp directory.

CLI::

    chimera-team-watch --team alpha [--interval 1.0] [--max-recent 10]
                       [--teams-home /path/to/teams]

Exit codes:
    0 on clean shutdown (Ctrl-C or ``stop_after_n_renders`` exhausted).
    1 if the requested team does not exist.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from chimera.cli.agent_teams import Team

__all__ = [
    "ANSI_CLEAR",
    "main",
    "render_team_status",
    "watch_team",
]

# WHY: \x1b[2J clears the screen, \x1b[H homes the cursor. Together these
# give a flicker-free refresh on any VT100-compatible terminal without
# pulling in a curses dep.
ANSI_CLEAR = "\x1b[2J\x1b[H"

# Banner padding width — keep the divider line visually consistent across
# renders even when the team name length varies.
_DIVIDER_WIDTH = 60


def _format_ts(ts: float) -> str:
    """Format a Unix timestamp as ``HH:MM:SS`` in local time."""
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "--:--:--"


def _read_mailbox_messages(team: Team) -> list[dict[str, Any]]:
    """Read every mailbox/*.jsonl file non-destructively.

    Returns a flat list of mailbox records, each guaranteed to carry
    ``from``, ``to``, ``content``, and ``ts`` keys (missing fields are
    coerced to safe defaults so the renderer never crashes on a partial
    or hand-edited file).

    Catches :class:`json.JSONDecodeError` so a torn write (mid-flush) does
    not poison the dashboard — the bad line is silently skipped.
    """
    mailbox_dir = team.mailbox_dir
    if not mailbox_dir.exists():
        return []

    messages: list[dict[str, Any]] = []
    for mailbox_path in sorted(mailbox_dir.glob("*.jsonl")):
        # Skip the sidecar .lock files that the team _flock helper creates.
        if mailbox_path.suffix != ".jsonl":
            continue
        try:
            raw = mailbox_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # Partial write — agent may be in the middle of appending.
                continue
            if not isinstance(rec, dict):
                continue
            messages.append({
                "from": str(rec.get("from", "?")),
                "to": str(rec.get("to", mailbox_path.stem)),
                "content": str(rec.get("content", "")),
                "ts": float(rec.get("ts", 0.0) or 0.0),
            })
    return messages


def _truncate(text: str, width: int) -> str:
    """Single-line, width-bounded display string."""
    flat = text.replace("\n", " ").replace("\r", " ").strip()
    if len(flat) <= width:
        return flat
    if width <= 1:
        return flat[:width]
    return flat[: width - 1] + "..."


def render_team_status(team: Team, max_recent_tasks: int = 10) -> str:
    """Render a multi-line status block for the given team.

    Args:
        team: A :class:`~chimera.cli.agent_teams.Team` instance. Its
            backing directory may or may not yet exist — missing files
            render as empty sections rather than raising.
        max_recent_tasks: How many of the most-recent task records to show
            in the ``recent:`` section.

    Returns:
        A newline-joined status block. The block always starts with the
        team banner and is safe to print verbatim to a TTY.
    """
    lines: list[str] = []

    # ---- banner --------------------------------------------------------
    banner_title = f" team: {team.name} "
    dashes = max(0, _DIVIDER_WIDTH - len(banner_title) - 6)
    lines.append("━━━" + banner_title + "━" * dashes)

    # ---- config / members ---------------------------------------------
    try:
        cfg = team.load_config() if team.exists() else {}
    except (OSError, json.JSONDecodeError):
        cfg = {}
    members = list(cfg.get("members", []) or [])
    if members:
        lines.append(f"members ({len(members)}): " + ", ".join(members))
    else:
        lines.append("members (0): -")

    # ---- tasks ---------------------------------------------------------
    try:
        tasks = team.list_tasks() if team.task_path.exists() else []
    except (OSError, json.JSONDecodeError):
        tasks = []

    open_n = sum(1 for t in tasks if t.get("status") == "open")
    claimed_n = sum(1 for t in tasks if t.get("status") == "claimed")
    completed_n = sum(1 for t in tasks if t.get("status") == "completed")
    total_n = len(tasks)
    lines.append(
        f"tasks: open={open_n} claimed={claimed_n} "
        f"completed={completed_n} total={total_n}"
    )

    # Recent tasks: most-recently-touched first. We approximate "touched"
    # by max(completed_at, claimed_at, created_at) so completed tasks
    # bubble up alongside fresh ones rather than always sinking to the
    # bottom.
    def _recency(rec: dict[str, Any]) -> float:
        for key in ("completed_at", "claimed_at", "created_at"):
            val = rec.get(key)
            if isinstance(val, (int, float)):
                return float(val)
        return 0.0

    recent_tasks = sorted(tasks, key=_recency, reverse=True)[:max_recent_tasks]
    lines.append("recent:")
    if not recent_tasks:
        lines.append("  (no tasks yet)")
    else:
        for rec in recent_tasks:
            status = str(rec.get("status", "open"))
            claimed_by = rec.get("claimed_by") or "-"
            desc = _truncate(str(rec.get("description", "")), 40)
            result = rec.get("result")
            tail = ""
            if status == "completed" and result:
                tail = " -> " + _truncate(str(result), 30)
            lines.append(
                f"  [{status:<9}] {claimed_by:<8} | {desc:<40}{tail}"
            )

    # ---- mailbox -------------------------------------------------------
    messages = _read_mailbox_messages(team)
    messages.sort(key=lambda m: m["ts"], reverse=True)
    recent_msgs = messages[:5]
    lines.append("recent mailbox activity (last 5 across all agents):")
    if not recent_msgs:
        lines.append("  (no messages yet)")
    else:
        # Re-sort chronologically (oldest first) so the eye scans the
        # rendered block top-to-bottom.
        for msg in reversed(recent_msgs):
            ts_str = _format_ts(msg["ts"])
            sender = _truncate(msg["from"], 8)
            recipient = _truncate(msg["to"], 8)
            content = _truncate(msg["content"], 40)
            lines.append(
                f"  {ts_str}  {sender:<8} -> {recipient:<8}  : {content}"
            )

    return "\n".join(lines)


def watch_team(
    team_name: str,
    root: Path | None = None,
    interval: float = 1.0,
    stdout: TextIO = sys.stdout,
    stop_after_n_renders: int | None = None,
) -> int:
    """Loop: clear the screen and re-render team status every ``interval``s.

    Args:
        team_name: Name of the team to watch (must already exist on disk).
        root: Optional override for the teams root directory. ``None``
            defers to :func:`~chimera.cli.agent_teams.teams_root`.
        interval: Seconds to sleep between renders. Ignored on the final
            iteration so tests don't pay an extra ``sleep``.
        stdout: Where to write the rendered frames. Defaults to
            ``sys.stdout`` — tests can hand in an :class:`io.StringIO`.
        stop_after_n_renders: If set, render exactly N frames and return
            0. ``None`` (the default) loops until Ctrl-C.

    Returns:
        ``0`` on clean shutdown (Ctrl-C or N-render cap hit), ``1`` if
        the team does not exist on disk.
    """
    team = Team(team_name, root=root)
    if not team.exists():
        print(
            f"team '{team_name}' does not exist under {team.dir.parent}",
            file=sys.stderr,
        )
        return 1

    renders = 0
    try:
        while True:
            stdout.write(ANSI_CLEAR)
            stdout.write(render_team_status(team))
            stdout.write("\n")
            try:
                stdout.flush()
            except (OSError, ValueError):
                # Closed stream — bail out cleanly.
                return 0
            renders += 1
            if stop_after_n_renders is not None and renders >= stop_after_n_renders:
                return 0
            # Avoid sleeping in the test path: if the next iteration is
            # the bounded cap, just spin straight through.
            if stop_after_n_renders is not None and renders + 1 > stop_after_n_renders:
                continue
            time.sleep(max(0.0, interval))
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    """``chimera-team-watch`` entry point.

    Args:
        argv: Optional argv slice (without the program name). ``None``
            defers to :data:`sys.argv`.

    Returns:
        Process exit code suitable for ``sys.exit``.
    """
    parser = argparse.ArgumentParser(
        prog="chimera-team-watch",
        description="Live dashboard for an agent team's tasks and mailbox.",
    )
    parser.add_argument("--team", required=True, help="Team name to watch.")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between refreshes (default: 1.0).",
    )
    parser.add_argument(
        "--teams-home",
        type=str,
        default=None,
        help="Override the teams root directory (default: ~/.chimera/teams).",
    )
    parser.add_argument(
        "--max-recent",
        type=int,
        default=10,
        help="Recent task rows to render (default: 10).",
    )
    args = parser.parse_args(argv)

    root = Path(args.teams_home).expanduser() if args.teams_home else None
    # max-recent is plumbed through to render_team_status via a thin
    # wrapper so watch_team's signature stays stable.
    team = Team(args.team, root=root)
    if not team.exists():
        print(
            f"team '{args.team}' does not exist under {team.dir.parent}",
            file=sys.stderr,
        )
        return 1

    try:
        while True:
            sys.stdout.write(ANSI_CLEAR)
            sys.stdout.write(render_team_status(team, max_recent_tasks=args.max_recent))
            sys.stdout.write("\n")
            sys.stdout.flush()
            time.sleep(max(0.0, args.interval))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover - manual invocation
    sys.exit(main())
