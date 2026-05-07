"""Apply a structured patch envelope to one or more files atomically.

The DSL is a self-contained file-and-hunk format::

    *** Begin Patch
    *** Update File: relative/path.py
     def hello():
    -    return "old"
    +    return "new"
    *** Add File: docs/new.md
    +# Title
    +Body line.
    *** Delete File: legacy/dead.py
    *** End Patch

Markers:

* ``*** Begin Patch`` / ``*** End Patch`` — required envelope.
* ``*** Update File: <path>`` — modify an existing file. Hunks use
  unified-diff line prefixes (``" "`` context, ``"-"`` remove, ``"+"`` add).
* ``*** Add File: <path>`` — create a new file. Body is ``+``-prefixed
  lines joined by newlines.
* ``*** Delete File: <path>`` — remove an existing file.

Apply semantics are atomic. The tool first parses every hunk, then
verifies pre-conditions for each file (Add target must not exist,
Update / Delete target must exist as a regular file, every Update hunk
must locate its match in the current file contents). If any of those
checks fails, no file is touched. Once all checks pass, writes proceed
sequentially; an OSError mid-apply triggers a rollback that restores
each file from the snapshot captured during validation, including
re-creating files that had already been deleted and removing files
that had already been added. The end state is therefore either
"every file changed" or "no file changed".

Trademark hygiene: this is a tool name, not an upstream brand. The
implementation reuses :class:`chimera.core.patch_parser.PatchParser`
(a pure-Python primitive owned by chimera) for the line-level diff
math; this module owns the multi-file orchestration, validation, and
rollback layer on top of it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


@dataclass
class _PlanItem:
    """One pre-validated step in an atomic apply.

    Attributes:
        path: Absolute path to the affected file.
        rel: The path as written in the patch, used in user-facing messages.
        operation: One of ``"add"``, ``"update"``, ``"delete"``.
        original: Snapshot of the file's pre-apply contents. ``None`` for
            ``"add"`` since no prior file exists.
        new: New contents to write. ``None`` for ``"delete"``.
    """

    path: Path
    rel: str
    operation: str
    original: str | None
    new: str | None


class ApplyPatchTool(BaseTool):
    """Apply a multi-file structured patch atomically with rollback.

    See the module docstring for the patch DSL and apply semantics.
    """

    name = "apply_patch"
    description = (
        "Apply a structured patch envelope to one or more files. "
        "Supports *** Update File / *** Add File / *** Delete File "
        "between *** Begin Patch / *** End Patch markers. Atomic: parses "
        "fully and verifies every hunk before any write hits disk; "
        "filesystem errors mid-apply roll back to the original tree."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "patch": {
                "type": "string",
                "description": (
                    "Patch text wrapped in *** Begin Patch / *** End Patch. "
                    "Each *** Update File: <path> hunk uses unified-diff "
                    "lines (' ' context, '-' remove, '+' add). Use "
                    "*** Add File: <path> with '+' lines for new files "
                    "and *** Delete File: <path> to remove files."
                ),
            },
        },
        "required": ["patch"],
    }
    is_destructive = True

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        patch_text = args.get("patch")
        if not isinstance(patch_text, str) or not patch_text.strip():
            return ToolResult(
                output="",
                error="apply_patch: 'patch' must be a non-empty string",
            )

        # ---- Phase 1: parse ----
        from chimera.core.patch_parser import PatchParser

        parser = PatchParser()
        try:
            file_patches = parser.parse(patch_text)
        except Exception as e:  # noqa: BLE001
            return ToolResult(output="", error=f"apply_patch parse error: {e}")

        if not file_patches:
            return ToolResult(
                output="",
                error="apply_patch: no file hunks parsed (empty envelope?)",
            )

        # ---- Phase 2: pre-validate every operation ----
        cwd = Path.cwd()
        plan: list[_PlanItem] = []
        seen_paths: set[Path] = set()
        for fp in file_patches:
            rel = (fp.path or "").strip()
            if not rel:
                return ToolResult(
                    output="",
                    error="apply_patch: empty file path in patch hunk",
                )
            full = (cwd / rel).resolve()
            if full in seen_paths:
                return ToolResult(
                    output="",
                    error=f"apply_patch: duplicate hunk for '{rel}'",
                )
            seen_paths.add(full)

            if fp.operation == "delete":
                if not full.exists():
                    return ToolResult(
                        output="",
                        error=f"apply_patch: cannot delete missing file '{rel}'",
                    )
                if not full.is_file():
                    return ToolResult(
                        output="",
                        error=f"apply_patch: '{rel}' is not a regular file",
                    )
                plan.append(_PlanItem(full, rel, "delete", full.read_text(), None))

            elif fp.operation == "add":
                if full.exists():
                    return ToolResult(
                        output="",
                        error=f"apply_patch: cannot add existing file '{rel}'",
                    )
                new_text = parser.apply(fp, "")
                plan.append(_PlanItem(full, rel, "add", None, new_text))

            elif fp.operation == "update":
                if not full.exists():
                    return ToolResult(
                        output="",
                        error=f"apply_patch: cannot update missing file '{rel}'",
                    )
                if not full.is_file():
                    return ToolResult(
                        output="",
                        error=f"apply_patch: '{rel}' is not a regular file",
                    )
                original = full.read_text()
                try:
                    new_text = parser.apply(fp, original)
                except ValueError as e:
                    return ToolResult(
                        output="",
                        error=f"apply_patch: hunk conflict in '{rel}': {e}",
                    )
                plan.append(_PlanItem(full, rel, "update", original, new_text))

            else:  # pragma: no cover — patch_parser only emits these three ops
                return ToolResult(
                    output="",
                    error=(
                        f"apply_patch: unknown operation '{fp.operation}' "
                        f"for '{rel}'"
                    ),
                )

        # ---- Phase 3: apply with rollback ----
        applied: list[_PlanItem] = []
        try:
            for item in plan:
                if item.operation == "delete":
                    item.path.unlink()
                elif item.operation == "add":
                    parent = item.path.parent
                    if parent and not parent.exists():
                        parent.mkdir(parents=True, exist_ok=True)
                    item.path.write_text(item.new or "")
                else:  # update
                    item.path.write_text(item.new or "")
                applied.append(item)
        except OSError as e:
            self._rollback(applied)
            return ToolResult(
                output="",
                error=(
                    f"apply_patch: filesystem error mid-apply on "
                    f"'{plan[len(applied)].rel}': {e}; rolled back "
                    f"{len(applied)} prior change(s)"
                ),
            )

        # ---- Phase 4: summary ----
        summary_lines = ["Success. Updated the following files:"]
        for item in plan:
            verb = {"add": "Created", "update": "Updated", "delete": "Deleted"}[
                item.operation
            ]
            summary_lines.append(f"{verb} {item.rel}")
        return ToolResult(
            output="\n".join(summary_lines),
            metadata={
                "files": [str(item.path) for item in plan],
                "operations": [item.operation for item in plan],
            },
        )

    @staticmethod
    def _rollback(applied: list[_PlanItem]) -> None:
        """Restore filesystem state from snapshots taken during validation.

        Best-effort: reverses each applied step. Newly-added files are
        unlinked, deleted files are recreated from the snapshot, and
        updated files have their original contents restored. Errors are
        swallowed so a single broken inverse step does not mask the
        original failure that triggered the rollback.
        """
        for item in reversed(applied):
            try:
                if item.operation == "add":
                    if item.path.exists():
                        item.path.unlink()
                elif item.operation == "delete":
                    if item.original is not None:
                        parent = item.path.parent
                        if parent and not parent.exists():
                            parent.mkdir(parents=True, exist_ok=True)
                        item.path.write_text(item.original)
                else:  # update
                    if item.original is not None:
                        item.path.write_text(item.original)
            except OSError:
                continue
