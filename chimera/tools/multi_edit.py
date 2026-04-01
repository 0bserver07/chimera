"""Multi-edit tool: apply multiple search-and-replace edits in one call.

Issue #122.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class MultiEditTool(BaseTool):
    """Apply multiple search-and-replace edits across one or more files in a single call."""

    name = "multi_edit"
    description = "Apply multiple search-and-replace edits across one or more files in a single call"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "File path"},
                        "search": {"type": "string", "description": "Text to find"},
                        "replace": {"type": "string", "description": "Replacement text"},
                    },
                    "required": ["file", "search", "replace"],
                },
                "description": "List of edits to apply",
            },
        },
        "required": ["edits"],
    }
    is_concurrency_safe = False

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        edits = args.get("edits", [])
        results: list[str] = []
        for i, edit in enumerate(edits):
            path = edit["file"]
            search = edit["search"]
            replace = edit["replace"]
            try:
                full_path = Path(path) if os.path.isabs(path) else Path(os.getcwd()) / path
                if not full_path.exists():
                    results.append(f"[{i + 1}] {path}: file not found")
                    continue
                content = full_path.read_text()
                if search not in content:
                    results.append(f"[{i + 1}] {path}: search text not found")
                    continue
                new_content = content.replace(search, replace, 1)
                full_path.write_text(new_content)
                results.append(f"[{i + 1}] {path}: edited successfully")
            except Exception as e:
                results.append(f"[{i + 1}] {path}: error — {e}")
        return ToolResult(output="\n".join(results))
