"""Web search tool using DuckDuckGo (no API key required)."""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

_DDG_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Chimera/0.1; +https://github.com/chimera-ai)",
}


def _parse_ddg_html(html: str, max_results: int) -> list[dict[str, str]]:
    """Extract search results from DuckDuckGo HTML response."""
    results: list[dict[str, str]] = []
    # Match result blocks: <a class="result__a" href="...">title</a>
    # and <a class="result__snippet">snippet</a>
    link_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    for i, (raw_url, raw_title) in enumerate(links):
        if i >= max_results:
            break
        # Clean HTML tags from title/snippet
        title = re.sub(r"<[^>]+>", "", raw_title).strip()
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()

        # DDG wraps URLs in a redirect — extract the actual URL
        url = raw_url
        if "uddg=" in url:
            match = re.search(r"uddg=([^&]+)", url)
            if match:
                url = urllib.parse.unquote(match.group(1))

        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})

    return results


class WebSearchTool(BaseTool):
    """Search the web using DuckDuckGo and return ranked results."""

    name = "web_search"
    description = (
        "Search the web and return results with titles, snippets, and URLs. "
        "Use this to find documentation, Stack Overflow answers, API references, etc."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 5)",
            },
        },
        "required": ["query"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        if httpx is None:
            return ToolResult(output="", error="httpx not installed. pip install httpx")

        query = args["query"]
        max_results = args.get("max_results", 5)

        try:
            response = httpx.post(
                _DDG_URL,
                data={"q": query},
                headers=_HEADERS,
                timeout=15,
                follow_redirects=True,
            )
            response.raise_for_status()
        except Exception as e:
            return ToolResult(output="", error=f"Search request failed: {e}")

        results = _parse_ddg_html(response.text, max_results)

        if not results:
            return ToolResult(output=f"No results found for: {query}")

        lines: list[str] = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['url']}")
            if r["snippet"]:
                lines.append(f"   {r['snippet']}")
            lines.append("")

        return ToolResult(
            output="\n".join(lines).strip(),
            metadata={"query": query, "result_count": len(results), "results": results},
        )
