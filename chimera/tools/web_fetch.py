# chimera/tools/web_fetch.py
from __future__ import annotations

import re
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

try:
    import httpx  # type: ignore[import-not-found]
except ImportError:
    httpx = None  # type: ignore[assignment]


class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = "Fetch a URL and return its content as text. HTML tags are stripped."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch"},
        },
        "required": ["url"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        if httpx is None:
            return ToolResult(output="", error="httpx not installed. pip install httpx")

        url = args["url"]
        try:
            response = httpx.get(url, timeout=30, follow_redirects=True)
            content = response.text
            # Strip HTML tags for readability
            if "text/html" in response.headers.get("content-type", ""):
                content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
                content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)
                content = re.sub(r"<[^>]+>", "", content)
                content = re.sub(r"\s+", " ", content).strip()
            return ToolResult(output=content[:50000])  # Truncate large pages
        except Exception as e:
            return ToolResult(output="", error=str(e))
