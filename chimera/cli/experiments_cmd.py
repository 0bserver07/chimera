"""``chimera experiments`` — see what the toolkit has recorded.

Two read-only verbs over :mod:`chimera.experiments`:

``chimera experiments list``
    Every recorded run, oldest first, with its status and size. Runs that say
    ``running`` but have no live writer are shown as ``interrupted`` — those
    are the ones ``resume()`` picks up.

``chimera experiments show <name>[/<stamp>]``
    One run's manifest and result, including the git SHA and dirty flag that
    answer *which code produced this number*. A bare name shows the newest run.

Reclaiming old runs is deliberately **not** here: ``experiment-runs`` is a
registry store, so retention belongs to ``chimera gc`` and its dry-run-first
rules. A second pruning mechanism is exactly the drift this subsystem exists to
end.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from chimera.experiments import NoSuchRun, RunInfo, list_runs, load_run, runs_root

__all__ = ["register", "run"]

#: Cap on the per-file listing in ``show``. A ProgramBench-shaped run leaves a
#: workspace per task; printing all of them buries the manifest and the result,
#: which are what the command is for.
MAX_LISTED_FILES = 40


def _human_size(size: int) -> str:
    """Format a byte count as a short human-readable string."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}GB"


def _display_status(info: RunInfo) -> str:
    """Status as a human should read it, promoting a dead ``running`` run."""
    return "interrupted" if info.interrupted else info.status


def _summarize(info: RunInfo) -> dict[str, Any]:
    """One run as a JSON-serializable row."""
    result = info.result or {}
    cells = result.get("cells") or []
    return {
        "name": info.name,
        "stamp": info.stamp,
        "status": _display_status(info),
        "raw_status": info.status,
        "started_at": info.started_at,
        "dir": str(info.dir),
        "git": info.manifest.get("git"),
        "config": info.manifest.get("config") or {},
        "size_bytes": info.size_bytes(),
        "cells": cells,
    }


def _score(info: RunInfo) -> str:
    """A one-line score for the list view, or ``""`` when there is none."""
    cells = (info.result or {}).get("cells") or []
    if not cells:
        return ""
    passed = sum(int(c.get("passed", 0) or 0) for c in cells)
    total = sum(int(c.get("total", 0) or 0) for c in cells)
    cost = sum(float(c.get("cost_usd", 0.0) or 0.0) for c in cells)
    if not total:
        return f"${cost:.4f}"
    return f"{passed}/{total} ({100.0 * passed / total:.1f}%)  ${cost:.4f}"


def cmd_list(args: argparse.Namespace) -> int:
    """Print every recorded run.

    Args:
        args: Parsed arguments; ``name`` filters to one experiment and
            ``json`` switches to machine-readable output.

    Returns:
        Process exit code (always 0 — no runs is a fact, not a failure).
    """
    infos = list_runs(getattr(args, "name", None) or None)
    if getattr(args, "json", False):
        print(json.dumps([_summarize(i) for i in infos], indent=2))
        return 0
    if not infos:
        print(f"No experiment runs recorded under {runs_root()}")
        print("Start one with: chimera.experiments.start('<name>', config={...})")
        return 0
    width = max(len(f"{i.name}/{i.stamp}") for i in infos)
    print(f"{len(infos)} run(s) under {runs_root()}\n")
    for info in infos:
        ref = f"{info.name}/{info.stamp}".ljust(width)
        score = _score(info)
        tail = f"  {score}" if score else ""
        print(
            f"  {ref}  {_display_status(info):<11}"
            f"  {_human_size(info.size_bytes()):>8}{tail}"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Print one run's manifest, files, and result.

    Args:
        args: Parsed arguments; ``ref`` is ``<name>`` or ``<name>/<stamp>``
            and ``json`` switches to machine-readable output.

    Returns:
        Process exit code — 2 when the run does not exist.
    """
    try:
        info = load_run(args.ref)
    except (NoSuchRun, ValueError) as exc:
        print(f"chimera experiments show: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "json", False):
        payload = _summarize(info)
        payload["manifest"] = info.manifest
        payload["result"] = info.result
        print(json.dumps(payload, indent=2))
        return 0

    git = info.manifest.get("git") or {}
    sha = git.get("sha") or "(not a git checkout)"
    if git.get("dirty"):
        sha = f"{sha} +dirty"
    print(f"{info.name}/{info.stamp}")
    print(f"  status     {_display_status(info)}")
    print(f"  started    {info.started_at or '(unknown)'}")
    print(f"  git        {sha}")
    print(f"  cwd        {info.manifest.get('cwd') or '(unrecorded)'}")
    print(f"  dir        {info.dir}  ({_human_size(info.size_bytes())})")
    config = info.manifest.get("config") or {}
    if config:
        print("  config     " + json.dumps(config, sort_keys=True, default=str))
    argv = info.manifest.get("argv") or []
    if argv:
        print("  argv       " + " ".join(str(a) for a in argv))

    files = sorted(p for p in info.dir.rglob("*") if p.is_file())
    if files:
        print("\n  files")
        # A real sweep leaves thousands of per-task artifacts; the point of
        # this section is orientation, not an inventory. --json has them all.
        for path in files[:MAX_LISTED_FILES]:
            print(f"    {path.relative_to(info.dir)}  ({_human_size(path.stat().st_size)})")
        if len(files) > MAX_LISTED_FILES:
            print(f"    … and {len(files) - MAX_LISTED_FILES} more")

    if info.result is None:
        print(
            "\n  no result.json — this run never called finish()."
            + ("  Resume it with chimera.experiments.resume()." if info.interrupted else "")
        )
        return 0
    print("\n  result")
    for cell in info.result.get("cells") or []:
        print(
            f"    {cell.get('agent_id')} x {cell.get('benchmark')}: "
            f"{cell.get('passed')}/{cell.get('total')} "
            f"({100.0 * float(cell.get('pass_rate', 0.0)):.1f}%)  "
            f"${float(cell.get('cost_usd', 0.0)):.4f}  {cell.get('status')}"
        )
    print(f"\n  Receipt: {info.dir / 'result.json'}")
    print("  Copy it into data/ by hand if it is worth publishing — data/ is curated.")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Attach the ``experiments`` subcommand and its verbs to ``subparsers``."""
    parser = subparsers.add_parser(
        "experiments",
        help="List and inspect recorded experiment runs",
    )
    sub = parser.add_subparsers(dest="experiments_cmd", required=True)

    p_list = sub.add_parser("list", help="list recorded runs")
    p_list.add_argument("name", nargs="?", default=None, help="restrict to one experiment")
    p_list.add_argument("--json", action="store_true", help="machine-readable output")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="show one run's manifest and result")
    p_show.add_argument("ref", help="<name> (newest run) or <name>/<stamp>")
    p_show.add_argument("--json", action="store_true", help="machine-readable output")
    p_show.set_defaults(func=cmd_show)


def run(args: argparse.Namespace) -> int:
    """Dispatch to the handler argparse selected via ``set_defaults(func=...)``.

    Used by :func:`chimera.cli.main.main` when ``args.command == 'experiments'``.
    """
    func = getattr(args, "func", None)
    if func is None:
        print("Error: no experiments subcommand given", file=sys.stderr)
        return 2
    return int(func(args))
