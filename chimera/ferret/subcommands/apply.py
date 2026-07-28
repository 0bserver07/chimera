"""``chimera ferret apply [--last]`` — apply latest agent diff via ``git apply``.

This handler walks the ferret eventlog (``~/.chimera/eventlog/ferret-*``)
backwards from the most recent run, looking for a tool-call event that
emitted a unified diff. The most common producer is the
:class:`chimera.tools.apply_patch.ApplyPatchTool`, but we also accept
inline diffs surfaced via :class:`chimera.tools.write.WriteTool` so an
agent that hand-rolled a patch via ``apply_patch`` -> stdout still
applies cleanly.

The found diff is written to a temp file and shelled out to ``git apply``
so we inherit git's concurrency, conflict, and binary-file handling.
On a non-zero return from git we surface the patch path so the operator
can re-run with custom flags.

Flags
-----

* ``--last`` — restrict the search to the single most-recent ferret run
  (mirrors the codex behavior where the bare ``apply`` walks back
  through history while ``apply --last`` short-circuits on the newest).

Exit codes
----------

* ``0`` — patch found and ``git apply`` succeeded.
* ``1`` — patch found but ``git apply`` returned non-zero.
* ``2`` — no patch found, or eventlog root is missing.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator
from chimera.config.paths import store_path

__all__ = [
    "find_latest_diff",
    "extract_diff_from_event",
    "run_apply",
]


# ---------------------------------------------------------------------------
# Diff extraction helpers
# ---------------------------------------------------------------------------


def _looks_like_unified_diff(text: str) -> bool:
    """Return ``True`` when *text* opens with a unified-diff header.

    We accept both the ``diff --git`` header (preferred — it includes
    file paths even for adds/deletes) and the bare ``--- a/path``
    fallback some tools emit.
    """
    if not isinstance(text, str) or not text:
        return False
    head = text.lstrip()
    return head.startswith("diff --git ") or head.startswith("--- ")


def extract_diff_from_event(event: dict[str, Any]) -> str | None:
    """Return the unified-diff body found in *event*, or ``None``.

    Looks at three slots in priority order:

    1. ``event["arguments"]["patch"]`` — the apply_patch tool envelope.
    2. ``event["result"]["patch"]`` — handlers that return the patch
       on the result side instead of the args side.
    3. Any ``str`` value anywhere in the event whose head matches
       :func:`_looks_like_unified_diff`.

    The walk is shallow (one level deep) so a malformed event with
    deeply nested junk doesn't trigger an O(n²) scan.
    """
    if not isinstance(event, dict):
        return None

    # 1. apply_patch-style envelope on the args side.
    args = event.get("arguments")
    if isinstance(args, dict):
        patch = args.get("patch") or args.get("diff") or args.get("unified_diff")
        if isinstance(patch, str) and _looks_like_unified_diff(patch):
            return patch

    # 2. Mirror on the result side.
    result = event.get("result")
    if isinstance(result, dict):
        patch = result.get("patch") or result.get("diff")
        if isinstance(patch, str) and _looks_like_unified_diff(patch):
            return patch

    # 3. Last-resort: any top-level string value that opens with a
    # unified-diff header. This catches free-form tool output that
    # printed a diff to stdout without a structured envelope.
    for value in event.values():
        if isinstance(value, str) and _looks_like_unified_diff(value):
            return value

    return None


def _ferret_eventlog_root() -> Path:
    """Return ``~/.chimera/eventlog`` honoring ``Path.home()`` patches."""
    return store_path("eventlog")


def _iter_ferret_sessions(
    eventlog_root: Path | None = None,
    *,
    limit: int | None = None,
) -> Iterator[Path]:
    """Yield ferret session directories newest-first.

    We rely on the ``ferret-<UTC>-<uuid>`` naming convention so a lexical
    sort gives chronological order without parsing timestamps.
    """
    root = eventlog_root or _ferret_eventlog_root()
    if not root.exists() or not root.is_dir():
        return
    candidates = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name.startswith("ferret-")),
        reverse=True,
    )
    if limit is not None:
        candidates = candidates[:limit]
    yield from candidates


def find_latest_diff(
    eventlog_root: Path | None = None,
    *,
    only_last: bool = False,
) -> tuple[str, Path] | None:
    """Walk ferret sessions and return ``(patch_text, session_dir)`` or ``None``.

    Args:
        eventlog_root: Override the eventlog root (defaults to
            ``~/.chimera/eventlog``).
        only_last: When ``True``, search only the single most-recent
            ferret session; when ``False``, walk back through every
            session until a patch is found.

    Returns:
        A tuple of the unified-diff text and the directory it came from,
        or ``None`` when no patch can be located.
    """
    limit = 1 if only_last else None
    for session_dir in _iter_ferret_sessions(eventlog_root, limit=limit):
        # event-*.json files are written in numeric order; walk newest-first
        # so a later "patch" event in the same session wins over an earlier one.
        events = sorted(session_dir.glob("event-*.json"), reverse=True)
        for ev_path in events:
            try:
                data = json.loads(ev_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            patch = extract_diff_from_event(data)
            if patch:
                return patch, session_dir
    return None


# ---------------------------------------------------------------------------
# git apply wrapper
# ---------------------------------------------------------------------------


def _run_git_apply(
    patch_text: str,
    *,
    cwd: str,
    extra_args: list[str] | None = None,
    runner: Any = None,
) -> tuple[int, str, str]:
    """Run ``git apply`` against *patch_text*; return ``(rc, stdout, stderr)``.

    The patch is written to a NamedTemporaryFile so git sees a real path
    (some platforms reject ``--`` stdin patches in older git versions).

    Args:
        patch_text: Full unified diff body.
        cwd: Working directory ``git apply`` runs from.
        extra_args: Optional list of extra args appended after the patch
            path (e.g. ``["--reject"]``).
        runner: Optional ``subprocess.run``-compatible callable for tests
            to inject. Defaults to :func:`subprocess.run`.
    """
    runner = runner or subprocess.run
    with tempfile.NamedTemporaryFile(
        "w", suffix=".patch", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(patch_text)
        if not patch_text.endswith("\n"):
            fh.write("\n")
        patch_path = fh.name

    try:
        cmd = ["git", "apply", patch_path]
        if extra_args:
            cmd.extend(extra_args)
        completed = runner(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return (
            int(getattr(completed, "returncode", 1)),
            str(getattr(completed, "stdout", "") or ""),
            str(getattr(completed, "stderr", "") or ""),
        )
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_apply(args: argparse.Namespace) -> int:
    """Apply the latest ferret-emitted diff via ``git apply``.

    Reads ``args.last`` (the ``--last`` flag) and ``args.cwd`` (the
    target git tree). Prints a one-line summary to stderr on success
    and the git error body on failure.

    Args:
        args: Parsed ferret namespace from :mod:`chimera.ferret.cli`.

    Returns:
        Process exit code (see module docstring).
    """
    cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())
    only_last = bool(getattr(args, "last", False))
    found = find_latest_diff(only_last=only_last)
    if found is None:
        sys.stderr.write(
            "ferret apply: no agent diff found in the eventlog. "
            "Run ferret with -p to produce a patch, then retry.\n"
        )
        return 2
    patch_text, session_dir = found
    rc, stdout, stderr = _run_git_apply(patch_text, cwd=cwd)
    if rc == 0:
        sys.stderr.write(
            f"[ferret apply] applied patch from {session_dir.name}\n"
        )
        if stdout:
            sys.stderr.write(stdout)
        return 0
    sys.stderr.write(
        f"[ferret apply] git apply failed (rc={rc}) for patch from "
        f"{session_dir.name}.\n"
    )
    if stderr:
        sys.stderr.write(stderr)
    return 1
