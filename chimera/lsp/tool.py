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
        "go_to_definition, find_references, hover, document_symbols."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["go_to_definition", "find_references", "hover", "document_symbols"],
                "description": "The LSP query to perform.",
            },
            "file": {"type": "string", "description": "File path to query."},
            "line": {"type": "integer", "description": "0-indexed line number (for definition/references/hover)."},
            "character": {"type": "integer", "description": "0-indexed character offset (for definition/references/hover)."},
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

            else:
                return ToolResult(output="", error=f"Unknown action: {action}")
        except Exception as e:
            return ToolResult(output="", error=f"LSP error: {e}")
