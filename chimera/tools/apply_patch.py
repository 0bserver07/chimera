"""ApplyPatchTool — apply structured patches to files on disk."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class ApplyPatchTool(BaseTool):
    """Apply a structured patch to one or more files."""

    name = "apply_patch"
    description = "Apply a structured patch to one or more files"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "patch": {
                "type": "string",
                "description": "Patch in *** Begin Patch / *** End Patch format",
            },
        },
        "required": ["patch"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        from chimera.core.patch_parser import PatchParser

        parser = PatchParser()
        try:
            patches = parser.parse(args["patch"])
        except Exception as e:
            return ToolResult(output="", error=f"Parse error: {e}")

        results: list[str] = []
        for fp in patches:
            path = fp.path
            full = os.path.join(os.getcwd(), path)

            if fp.operation == "delete":
                if os.path.exists(full):
                    os.unlink(full)
                    results.append(f"Deleted {path}")
                else:
                    results.append(f"Already absent: {path}")
            elif fp.operation == "add":
                content = parser.apply(fp, "")
                os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
                Path(full).write_text(content)
                results.append(f"Created {path}")
            else:  # update
                if not os.path.exists(full):
                    results.append(f"File not found: {path}")
                    continue
                old = Path(full).read_text()
                try:
                    new = parser.apply(fp, old)
                    Path(full).write_text(new)
                    results.append(f"Updated {path}")
                except ValueError as e:
                    results.append(f"Failed {path}: {e}")

        return ToolResult(output="\n".join(results))
