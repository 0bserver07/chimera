"""``chimera gc`` — reclaim declared storage, dry-run first (spec M2).

The command is deliberately hard to make dangerous:

* **Dry run is the default.** Bare ``chimera gc`` prints what *would* go and
  the rule that selected each entry, and changes nothing. ``--apply`` is the
  only destructive spelling, and ``--archive DIR`` relocates instead of
  deleting — the owner's standing rule is archive/relocate, never delete by
  default.
* **Retention is opt-in.** Only stores that are declared ``prunable`` *and*
  carry a ``[storage.<name>]`` table are considered. Everything else is listed
  in the report with the reason it was skipped, so the output accounts for the
  whole registry rather than quietly showing a subset. ``gc --apply`` on a
  machine with no retention configured is a no-op by construction.
* **It cannot name a path the registry does not declare.** Candidates come
  from :func:`chimera.config.storage.plan_gc`, which iterates the registry and
  resolves roots through ``store_path``; and :func:`~chimera.config.storage
  .apply_prune` revalidates every candidate against the registry before the
  first deletion. ``datasets``, ``function_synthesis`` and everything else
  flagged ``prunable=False`` are structurally unreachable, as is any directory
  nobody declared — including anything under ``data/``.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # imported lazily at runtime so `chimera --help` stays light
    from chimera.config.storage import GcPlan

__all__ = ["add_arguments", "format_json", "format_text", "register", "run"]


def _plan(args: argparse.Namespace) -> GcPlan:
    """Build the reclaim plan described by *args*."""
    from chimera.config.storage import plan_gc

    raw_project = getattr(args, "project", None)
    stores = getattr(args, "store", None) or None
    return plan_gc(
        project=Path(raw_project) if raw_project else None,
        stores=stores,
    )


def format_text(plan: GcPlan, *, applied: bool, archive_to: Path | None) -> str:
    """Render the plan as a text report.

    Args:
        plan: The :class:`~chimera.config.storage.GcPlan`.
        applied: Whether the candidates were acted on.
        archive_to: Archive destination, when relocating rather than deleting.

    Returns:
        The rendered report.
    """
    from chimera.config.storage import (
        SKIP_NESTED_PREFIX,
        SKIP_REASONS,
        format_size,
        gc_skips,
    )

    lines: list[str] = []
    if applied:
        verb = f"archived to {archive_to}" if archive_to else "removed"
        lines.append(f"chimera gc — {verb}:")
    else:
        lines.append(
            "chimera gc (dry run — nothing changed; pass --apply to act):"
        )
    lines.append("")

    if not plan.candidates:
        lines.append("  no candidates: nothing selected by any configured retention.")
    for store in plan.stores:
        rows = [c for c in plan.candidates if c.store == store]
        root = rows[0].root
        lines.append(f"  {store}  {root}")
        width = max(len(c.entry.id) for c in rows)
        for candidate in rows:
            lines.append(
                f"    {candidate.entry.id:<{width}}  "
                f"{format_size(candidate.size_bytes):>10}  {candidate.rule}"
            )
        subtotal = sum(c.size_bytes for c in rows)
        lines.append(
            f"    -> {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}, "
            f"{format_size(subtotal)}"
        )
        lines.append("")

    for reason in SKIP_REASONS:
        names = gc_skips(plan, reason)
        if names:
            lines.append(f"  {reason}: {', '.join(names)}")
    nested = [s for s in plan.skips if s.reason.startswith(SKIP_NESTED_PREFIX)]
    for skip in nested:
        lines.append(f"  {skip.store}: skipped — {skip.reason}")

    lines.append("")
    lines.append(
        f"  total: {len(plan.candidates)} candidate(s), "
        f"{format_size(plan.total_bytes)} across {len(plan.stores)} store(s)"
    )
    if not applied and plan.candidates:
        lines.append("  run again with --apply to act, or --archive DIR to relocate.")
    return "\n".join(lines)


def format_json(plan: GcPlan, *, applied: bool, archive_to: Path | None) -> str:
    """Render the plan as JSON for scripting."""
    payload = {
        "applied": applied,
        "archive_to": str(archive_to) if archive_to else None,
        "candidates": [c.to_dict() for c in plan.candidates],
        "skipped": [s.to_dict() for s in plan.skips],
        "total_bytes": plan.total_bytes,
        "stores": plan.stores,
    }
    return json.dumps(payload, indent=2)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``chimera gc`` flags."""
    parser.add_argument(
        "--store",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "Limit to this registry store (repeatable). Default: every store "
            "with retention configured."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Act on the candidates. Without it, gc only reports.",
    )
    parser.add_argument(
        "--archive",
        default=None,
        metavar="DIR",
        help="With --apply, move candidates into DIR instead of deleting them.",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Project root for project-scope stores (default: cwd).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of text.",
    )


def run(args: argparse.Namespace) -> int:
    """Plan, print, and — only under ``--apply`` — act.

    Returns:
        ``0`` on success (including "nothing to do"), ``2`` when a store name
        is not declared or a candidate fails registry validation.
    """
    from chimera.config.paths import UnknownStore
    from chimera.config.storage import apply_prune

    try:
        plan = _plan(args)
    except UnknownStore as exc:
        print(f"chimera gc: {exc}")
        return 2

    archive_raw = getattr(args, "archive", None)
    archive_to = Path(archive_raw).expanduser() if archive_raw else None
    applied = bool(getattr(args, "apply", False))

    # Guarded on `plan.candidates` so that an empty plan never even creates the
    # archive directory: `gc --apply` with nothing configured touches no disk.
    if applied and plan.candidates:
        try:
            done = apply_prune(plan.candidates, archive_to=archive_to)
        except (UnknownStore, ValueError) as exc:
            # Validation runs before the first deletion, so nothing was
            # touched. Say so explicitly — a destructive command that fails
            # mid-way must never leave the reader guessing.
            print(f"chimera gc: refused, nothing was changed — {exc}")
            return 2
        plan.candidates = done

    if getattr(args, "json", False):
        print(format_json(plan, applied=applied, archive_to=archive_to))
    else:
        print(format_text(plan, applied=applied, archive_to=archive_to))
    return 0


def register(subparsers: Any) -> None:
    """Attach ``chimera gc`` to the top-level subparsers."""
    parser = subparsers.add_parser(
        "gc",
        help="Report (and with --apply, reclaim) declared storage.",
    )
    add_arguments(parser)
    parser.set_defaults(func=run)
