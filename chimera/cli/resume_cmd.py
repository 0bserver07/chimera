"""Top-level ``chimera resume`` dispatcher.

Wave 9 (C1) gave every per-CLI agent a uniform ``--resume <id>`` /
``--continue`` flag pair driven by
:mod:`chimera.sessions.eventlog.resume_helpers`. That covered the case
where you already know which CLI minted the session — ``chimera otter
--resume otter-...`` just works.

This module closes the remaining gap: ``chimera resume <id>`` (or the
bare ``chimera resume`` for the most recent run across *all* CLIs),
which auto-detects the originating codename from the run-id prefix and
delegates to the right per-CLI ``--resume`` flag via subprocess. The
goal is "I don't remember which CLI I was using, just continue" —
useful in mixed-CLI workflows or shell histories where the originating
command has scrolled off.

Pass-through semantics: any extra args after the run id are forwarded
verbatim, so ``chimera resume <id> -p "next prompt"`` ≡ ``chimera <cli>
--resume <id> -p "next prompt"``.

Stdlib only. No provider imports — argparse + subprocess + the
read-only ``find_latest_run`` helper.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from chimera.sessions.eventlog.resume_helpers import (
    default_eventlog_root,
    find_latest_run,
)

__all__ = [
    "KNOWN_CODENAMES",
    "add_arguments",
    "detect_codename",
    "find_latest_across_all",
    "run",
]


# WHY: fixed list of CLI codenames mirrors ``chimera/cli/main.py`` — kept
# explicit (not introspected from main.py) so the resume dispatcher
# stays light and circular-import-free.
KNOWN_CODENAMES: tuple[str, ...] = (
    "mink",
    "otter",
    "ferret",
    "weasel",
    "shrew",
    "stoat",
    "badger",
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``chimera resume`` arguments on ``parser``.

    The subcommand intentionally swallows unknown trailing arguments via
    ``argparse.REMAINDER`` so callers can pass ``-p "next"`` /
    ``--print "next"`` / any other per-CLI flag through to the
    delegate without us having to mirror each CLI's full schema here.

    Args:
        parser: The argparse subparser created by
            :func:`chimera.cli.main.build_parser`.
    """
    parser.add_argument(
        "run_id",
        nargs="?",
        default=None,
        help=(
            "Run id to resume (e.g. 'otter-20260430T101501-71032a5e'). "
            "When omitted, resumes the most recent run across all CLIs."
        ),
    )
    parser.add_argument(
        "extra",
        nargs=argparse.REMAINDER,
        help=(
            "Pass-through args forwarded verbatim to the underlying CLI "
            "(e.g. '-p \"next prompt\"' or '--workdir .')."
        ),
    )


def detect_codename(run_id: str) -> str | None:
    """Return the CLI codename embedded in ``run_id``.

    Run ids minted by every CLI follow the shape
    ``<codename>-<utc-timestamp>-<uuid8>`` (see
    :func:`chimera.sessions.eventlog.resume_helpers.find_latest_run`),
    so the codename is always the segment before the first ``-``.

    Args:
        run_id: A directory name under ``~/.chimera/eventlog/``.

    Returns:
        The matching codename when the prefix corresponds to one of the
        seven known CLIs. ``None`` when the prefix is unrecognised — the
        caller surfaces this as a user-facing error rather than guessing.
    """
    if not run_id:
        return None
    head, _, _ = run_id.partition("-")
    if not head:
        return None
    if head in KNOWN_CODENAMES:
        return head
    return None


def find_latest_across_all(
    eventlog_root: Path | None = None,
    *,
    cwd: str | None = None,
) -> str | None:
    """Return the newest run id across every known CLI.

    Iterates over :data:`KNOWN_CODENAMES`, calls
    :func:`find_latest_run` for each, then returns the run whose
    ``<utc-timestamp>`` segment is greatest. We compare on the
    timestamp rather than the full string because a raw lexical sort
    would let alphabetically-late codenames (``weasel`` > ``mink``)
    shadow chronologically-newer runs from earlier codenames.

    Args:
        eventlog_root: Optional override for the eventlog root.
            Defaults to :func:`default_eventlog_root`.
        cwd: Optional cwd filter. When set, only runs whose persisted
            ``summary.json`` ``cwd`` matches this absolute path are
            considered.

    Returns:
        The newest matching run id, or ``None`` when no run is present.
    """
    root = eventlog_root or default_eventlog_root()
    candidates: list[tuple[str, str]] = []
    for codename in KNOWN_CODENAMES:
        latest = find_latest_run(f"{codename}-", root, cwd=cwd)
        if latest:
            # Run ids are ``<codename>-<utc>-<uuid8>``; key on the
            # ``<utc>-<uuid8>`` tail so timestamps drive ordering.
            _, _, tail = latest.partition("-")
            candidates.append((tail, latest))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def _build_subprocess_argv(
    codename: str,
    run_id: str,
    extra: list[str],
) -> list[str]:
    """Construct the argv for the delegated ``chimera <cli> --resume`` call.

    The dispatcher always re-invokes the *current* Python's ``chimera``
    entrypoint via ``python -m chimera.cli.main`` so behaviour is
    identical inside venvs, editable installs, and pinned wheels — no
    PATH lookup gambles.

    Args:
        codename: Detected CLI codename (one of :data:`KNOWN_CODENAMES`).
        run_id: The run id to resume.
        extra: Pass-through args forwarded after ``--resume <id>``.

    Returns:
        A fully-formed argv list suitable for :func:`subprocess.run`.
    """
    # ``argparse.REMAINDER`` may sneak in a leading ``--`` separator —
    # strip a single one to keep the delegated argv clean.
    cleaned: list[str] = list(extra)
    if cleaned and cleaned[0] == "--":
        cleaned = cleaned[1:]
    return [
        sys.executable,
        "-m",
        "chimera.cli.main",
        codename,
        "--resume",
        run_id,
        *cleaned,
    ]


def run(args: argparse.Namespace) -> int:
    """Execute ``chimera resume``.

    Resolves a target run id (explicit or "most recent across all"),
    detects the originating CLI from the prefix, then dispatches to
    ``chimera <cli> --resume <id>`` via :func:`subprocess.run`,
    forwarding any pass-through args.

    Args:
        args: Parsed argparse namespace from
            :func:`chimera.cli.main.build_parser`.

    Returns:
        Exit code: ``0`` on successful dispatch (mirrors the delegated
        CLI's exit code), ``1`` when the run id can't be resolved or
        the prefix is unrecognised, ``2`` when the underlying CLI fails
        to launch.
    """
    run_id: str | None = getattr(args, "run_id", None)
    extra: list[str] = list(getattr(args, "extra", None) or [])

    if run_id is None:
        resolved = find_latest_across_all()
        if resolved is None:
            print(
                "chimera resume: no prior runs found under "
                f"{default_eventlog_root()}.",
                file=sys.stderr,
            )
            return 1
        run_id = resolved
        print(
            f"chimera resume: resuming most recent run {run_id!r}.",
            file=sys.stderr,
        )

    codename = detect_codename(run_id)
    if codename is None:
        print(
            f"chimera resume: cannot detect originating CLI for "
            f"run id {run_id!r}. Expected prefix to be one of: "
            f"{', '.join(KNOWN_CODENAMES)}.",
            file=sys.stderr,
        )
        return 1

    argv = _build_subprocess_argv(codename, run_id, extra)
    try:
        completed = subprocess.run(argv, check=False)
    except OSError as exc:
        print(
            f"chimera resume: failed to dispatch to '{codename}' "
            f"({exc}).",
            file=sys.stderr,
        )
        return 2
    return int(completed.returncode)
