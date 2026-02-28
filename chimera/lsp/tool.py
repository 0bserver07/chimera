"""LSP tool -- exposes language server queries as a Chimera BaseTool."""
from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from chimera.core.tool import BaseTool
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.env.base import Environment
    from chimera.lsp.manager import LSPManager


class LSPTool(BaseTool):
    """Tool that exposes LSP code intelligence queries to the agent.

    Supports: go_to_definition, find_references, hover, document_symbols.

    Args:
        lsp: An LSPManager instance with running sessions.
    """

    name = "lsp"
    description = (
        "Query language servers for code intelligence. Actions: "
        "go_to_definition, find_references, hover, document_symbols, "
        "diagnostics, completion, rename, code_action."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "go_to_definition", "find_references", "hover",
                    "document_symbols", "diagnostics", "completion",
                    "rename", "code_action",
                ],
                "description": "The LSP query to perform.",
            },
            "file": {"type": "string", "description": "File path to query."},
            "line": {"type": "integer", "description": "0-indexed line number."},
            "character": {"type": "integer", "description": "0-indexed character offset."},
            "new_name": {"type": "string", "description": "New name for rename action."},
            "end_line": {"type": "integer", "description": "End line for code_action range."},
            "end_character": {"type": "integer", "description": "End character for code_action range."},
        },
        "required": ["action", "file"],
    }

    def __init__(self, lsp: LSPManager) -> None:
        self._lsp = lsp

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        action = args["action"]
        file_path = args["file"]
        line = args.get("line", 0)
        character = args.get("character", 0)

        session = self._lsp.get_session(file_path)
        if session is None:
            return ToolResult(output="", error=f"No language server for {file_path}")

        uri = Path(file_path).resolve().as_uri()

        try:
            if action == "go_to_definition":
                locations = session.definition(uri, line, character)
                if not locations:
                    return ToolResult(output="No definition found")
                output = "\n".join(
                    f"{loc.get('uri', '?')}:{loc.get('range', {}).get('start', {}).get('line', 0)}"
                    for loc in locations
                )
                return ToolResult(output=output)

            elif action == "find_references":
                refs = session.references(uri, line, character)
                if not refs:
                    return ToolResult(output="No references found")
                output = "\n".join(
                    f"{ref.get('uri', '?')}:{ref.get('range', {}).get('start', {}).get('line', 0)}"
                    for ref in refs
                )
                return ToolResult(output=f"{len(refs)} references:\n{output}")

            elif action == "hover":
                info = session.hover(uri, line, character)
                return ToolResult(output=info or "No hover information")

            elif action == "document_symbols":
                symbols = session.document_symbols(uri)
                if not symbols:
                    return ToolResult(output="No symbols found")
                lines = []
                for sym in symbols:
                    sym_name = sym.get("name", "?")
                    kind = sym.get("kind", 0)
                    line_num = sym.get("range", {}).get("start", {}).get("line", 0)
                    lines.append(f"  {sym_name} (kind={kind}) at line {line_num}")
                return ToolResult(output=f"{len(symbols)} symbols:\n" + "\n".join(lines))

            elif action == "diagnostics":
                diags = session.get_diagnostics(uri)
                if not diags:
                    return ToolResult(output="No diagnostics")
                lines = [d.to_feedback_str() for d in diags]
                return ToolResult(output=f"{len(diags)} diagnostics:\n" + "\n".join(lines))

            elif action == "completion":
                items = session.completion(uri, line, character)
                if not items:
                    return ToolResult(output="No completions")
                labels = [item.get("label", "?") for item in items[:20]]
                return ToolResult(output=f"{len(items)} completions:\n" + "\n".join(labels))

            elif action == "rename":
                new_name = args.get("new_name")
                if not new_name:
                    return ToolResult(output="", error="new_name is required for rename")
                edit = session.rename(uri, line, character, new_name)
                if edit is None:
                    return ToolResult(output="Rename not available")
                changes = edit.get("changes", {})
                total = sum(len(edits) for edits in changes.values())
                return ToolResult(output=f"Rename: {total} edits across {len(changes)} files")

            elif action == "code_action":
                end_line = args.get("end_line", line)
                end_char = args.get("end_character", character)
                actions = session.code_action(uri, line, character, end_line, end_char)
                if not actions:
                    return ToolResult(output="No code actions")
                titles = [a.get("title", "?") for a in actions]
                return ToolResult(output=f"{len(actions)} code actions:\n" + "\n".join(titles))

            else:
                return ToolResult(output="", error=f"Unknown action: {action}")
        except Exception as e:
            return ToolResult(output="", error=f"LSP error: {e}")
