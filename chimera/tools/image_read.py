"""Tool for reading image files and returning them as base64 content."""

from __future__ import annotations

import base64
import os
from typing import Any, TYPE_CHECKING

from chimera.core.tool import BaseTool
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.env.base import Environment

# Map extensions to MIME types
_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


class ImageReadTool(BaseTool):
    """Read an image file and return its base64-encoded content.

    Supports PNG, JPEG, GIF, WebP, and SVG formats.  When an
    :class:`~chimera.env.base.Environment` is provided the image is read
    via ``run_command``; otherwise the local filesystem is used directly.

    The base64 payload and MIME type are returned in
    :attr:`ToolResult.metadata` so that downstream consumers (e.g.
    vision-capable providers) can attach the image to a
    :class:`~chimera.types.Message`.
    """

    name = "read_image"
    description = "Read an image file and return its base64-encoded content."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the image file",
            },
        },
        "required": ["path"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Execute the tool with the given arguments.

        Args:
            args: Must contain a ``path`` key pointing to the image file.
            env: Optional execution environment for sandboxed reads.

        Returns:
            A :class:`~chimera.types.ToolResult` whose *metadata* dict
            contains ``image_data`` (base64 str), ``media_type``, and
            ``path``.
        """
        path = args.get("path", "")
        if not path:
            return ToolResult(output="", error="path is required")

        # Determine media type
        ext = os.path.splitext(path)[1].lower()
        media_type = _MIME_TYPES.get(ext)
        if media_type is None:
            return ToolResult(output="", error=f"Unsupported image type: {ext}")

        # Resolve path
        if env:
            try:
                # Read as binary via run_command
                result = env.run_command(f"base64 < '{path}'")
                if result.exit_code != 0:
                    return ToolResult(
                        output="", error=f"Failed to read image: {result.stderr}"
                    )
                b64_data = result.stdout.strip().replace("\n", "")
            except Exception as e:
                return ToolResult(output="", error=f"Failed to read image: {e}")
        else:
            try:
                with open(path, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode()
            except FileNotFoundError:
                return ToolResult(output="", error=f"Image not found: {path}")
            except Exception as e:
                return ToolResult(output="", error=f"Failed to read image: {e}")

        return ToolResult(
            output=f"[Image: {path} ({media_type}, {len(b64_data)} bytes base64)]",
            metadata={
                "image_data": b64_data,
                "media_type": media_type,
                "path": path,
            },
        )
