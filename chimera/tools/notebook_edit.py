"""Jupyter notebook cell editing tool.

CC-parity ``NotebookEdit`` tool: insert, replace, or delete cells in an
``.ipynb`` file using the ``nbformat`` library.  Available only when the
optional ``[notebook]`` extra is installed.
"""
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class NotebookEditTool(BaseTool):
    """Edit cells of a Jupyter notebook.

    Three supported actions:
        ``insert``  -- add a new cell at ``cell_index`` (or append).
        ``replace`` -- overwrite the cell's source.
        ``delete``  -- remove the cell.

    Either ``cell_index`` (0-based int) or ``cell_id`` (nbformat-v4 id)
    may identify the target.
    """

    name = "notebook_edit"
    description = (
        "Edit a Jupyter notebook (.ipynb): insert, replace, or delete a cell. "
        "Identify the cell by 0-based cell_index or by cell_id."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "notebook_path": {
                "type": "string",
                "description": "Filesystem path to the .ipynb file.",
            },
            "cell_index": {
                "type": "integer",
                "description": "0-based cell index. Optional if cell_id given.",
            },
            "cell_id": {
                "type": "string",
                "description": "nbformat v4 cell id. Optional if cell_index given.",
            },
            "action": {
                "type": "string",
                "enum": ["insert", "replace", "delete"],
                "description": "Mutation kind to apply.",
            },
            "content": {
                "type": "string",
                "description": "New cell source (required for insert/replace).",
            },
            "cell_type": {
                "type": "string",
                "enum": ["code", "markdown", "raw"],
                "description": "Cell type for insert/replace; defaults to 'code'.",
            },
        },
        "required": ["notebook_path", "action"],
    }
    is_read_only = False

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Apply the requested mutation to the notebook on disk.

        Args:
            args: See :attr:`parameters`.
            env: Unused; the tool reads/writes via the local filesystem so
                that it works whether or not the agent has an Environment
                attached (parity with CC behavior).

        Returns:
            ToolResult describing what changed, or an error.
        """
        try:
            import nbformat as _nbformat_mod  # type: ignore[import-not-found]
        except ImportError:
            return ToolResult(
                output="",
                error="nbformat not installed. Install with: pip install 'chimera-run[notebook]'",
            )
        # nbformat is an untyped third-party package; cast to Any so mypy
        # does not complain about the dynamic ``read`` / ``write`` calls.
        nbformat: Any = _nbformat_mod

        path = args["notebook_path"]
        action = args["action"]
        cell_type = args.get("cell_type", "code")
        content = args.get("content", "")

        try:
            nb = nbformat.read(path, as_version=4)
        except FileNotFoundError:
            return ToolResult(output="", error=f"Notebook not found: {path}")
        except Exception as exc:
            return ToolResult(output="", error=f"Failed to read notebook: {exc}")

        idx = self._resolve_index(nb, args)
        if action != "insert" and isinstance(idx, ToolResult):
            return idx

        if action == "insert":
            new_cell = self._make_cell(nbformat, cell_type, content)
            insert_at = idx if isinstance(idx, int) else len(nb.cells)
            nb.cells.insert(insert_at, new_cell)
            summary = f"Inserted {cell_type} cell at index {insert_at}"
        elif action == "replace":
            assert isinstance(idx, int)
            existing = nb.cells[idx]
            existing["source"] = content
            # Replacing only mutates source/cell_type per CC semantics; keep id.
            if cell_type and existing.get("cell_type") != cell_type:
                nb.cells[idx] = self._make_cell(
                    nbformat, cell_type, content, cell_id=existing.get("id"),
                )
            summary = f"Replaced cell at index {idx}"
        elif action == "delete":
            assert isinstance(idx, int)
            del nb.cells[idx]
            summary = f"Deleted cell at index {idx}"
        else:
            return ToolResult(output="", error=f"Unknown action: {action}")

        try:
            nbformat.write(nb, path)
        except Exception as exc:
            return ToolResult(output="", error=f"Failed to write notebook: {exc}")
        return ToolResult(output=summary)

    def _resolve_index(
        self, nb: Any, args: dict[str, Any],
    ) -> int | ToolResult:
        """Map (cell_index|cell_id) to a concrete int index.

        Returns a ToolResult on lookup failure so callers can short-circuit.
        """
        if "cell_index" in args and args["cell_index"] is not None:
            ci = int(args["cell_index"])
            if ci < 0 or ci >= len(nb.cells):
                return ToolResult(
                    output="", error=f"cell_index {ci} out of range (0..{len(nb.cells)-1})",
                )
            return ci
        if "cell_id" in args and args["cell_id"]:
            target = args["cell_id"]
            for i, cell in enumerate(nb.cells):
                if cell.get("id") == target:
                    return i
            return ToolResult(output="", error=f"cell_id {target!r} not found")
        # Insert without an index appends; other actions require a target.
        if args.get("action") == "insert":
            return len(nb.cells)
        return ToolResult(output="", error="Either cell_index or cell_id is required")

    def _make_cell(
        self, nbformat: Any, cell_type: str, source: str, cell_id: str | None = None,
    ) -> Any:
        """Build a new nbformat cell of the requested type."""
        if cell_type == "code":
            cell = nbformat.v4.new_code_cell(source=source)
        elif cell_type == "markdown":
            cell = nbformat.v4.new_markdown_cell(source=source)
        else:
            cell = nbformat.v4.new_raw_cell(source=source)
        if cell_id is not None:
            cell["id"] = cell_id
        return cell
