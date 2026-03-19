from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ChangeType, FileChange, ToolResult

if TYPE_CHECKING:
    from chimera.core.operations import ReadOps, WriteOps


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to a file. Creates parent directories if needed."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, read_ops: ReadOps | None = None, write_ops: WriteOps | None = None) -> None:
        self._read_ops = read_ops
        self._write_ops = write_ops

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        path = args["path"]
        new_content = args["content"]

        if self._write_ops is not None:
            # Read before-state for diff using read_ops if available
            before: str | None = None
            if self._read_ops is not None:
                try:
                    before = self._read_ops.read_file(path)
                except (FileNotFoundError, OSError):
                    pass

            try:
                self._write_ops.write_file(path, new_content)
            except Exception as e:
                return ToolResult(output="", error=str(e))

            if before is None:
                change_type = ChangeType.CREATE
            else:
                change_type = ChangeType.EDIT

            fc = FileChange(
                path=path,
                change_type=change_type,
                before_content=before,
                after_content=new_content,
                diff=FileChange.compute_diff(path, before or "", new_content),
            )
            return ToolResult(
                output=f"Written to {path}",
                metadata={"file_change": fc},
            )

        assert env is not None

        # Read before-state for diff
        before = None
        try:
            before = env.read_file(path)
        except (FileNotFoundError, OSError):
            pass

        try:
            env.write_file(path, new_content)
        except Exception as e:
            return ToolResult(output="", error=str(e))

        if before is None:
            change_type = ChangeType.CREATE
        else:
            change_type = ChangeType.EDIT

        fc = FileChange(
            path=path,
            change_type=change_type,
            before_content=before,
            after_content=new_content,
            diff=FileChange.compute_diff(path, before or "", new_content),
        )
        return ToolResult(
            output=f"Written to {path}",
            metadata={"file_change": fc},
        )
