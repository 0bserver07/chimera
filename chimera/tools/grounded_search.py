"""Grounded search: search → fetch → extract → cite.

Combines WebSearchTool and WebFetchTool into a research workflow that
returns answers with source citations. Replicates Gemini CLI's grounding
without needing Google APIs.
"""
from __future__ import annotations

import re
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


def _extract_text(html: str, max_chars: int = 5000) -> str:
    """Strip HTML tags and truncate."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _find_relevant_passage(text: str, query_words: list[str], window: int = 300) -> str:
    """Find the most relevant passage in text based on query words."""
    text_lower = text.lower()
    best_pos = 0
    best_score = 0

    for i in range(0, len(text_lower) - window, 50):
        chunk = text_lower[i : i + window]
        score = sum(1 for w in query_words if w in chunk)
        if score > best_score:
            best_score = score
            best_pos = i

    if best_score == 0:
        return text[:window]

    start = max(0, best_pos - 50)
    end = min(len(text), best_pos + window + 50)
    return text[start:end].strip()


class GroundedSearchTool(BaseTool):
    """Search the web, fetch top results, extract relevant passages with citations."""

    name = "grounded_search"
    description = (
        "Research a topic by searching the web, reading top results, and "
        "returning relevant passages with source citations. Use this for "
        "factual questions, documentation lookups, or current events."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The research question or search query",
            },
            "num_sources": {
                "type": "integer",
                "description": "Number of sources to read (default: 3)",
            },
        },
        "required": ["query"],
    }

    def __init__(self, max_fetch_size: int = 10000) -> None:
        self._max_fetch_size = max_fetch_size

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        if httpx is None:
            return ToolResult(output="", error="httpx not installed. pip install httpx")

        query = args["query"]
        num_sources = args.get("num_sources", 3)
        query_words = [w.lower() for w in query.split() if len(w) > 2]

        # Step 1: Search
        from chimera.tools.web_search import WebSearchTool
        search_tool = WebSearchTool()
        search_result = search_tool.execute({"query": query, "max_results": num_sources + 2}, env=env)

        if search_result.error:
            return ToolResult(output="", error=f"Search failed: {search_result.error}")

        results = (search_result.metadata or {}).get("results", [])
        if not results:
            return ToolResult(output=f"No results found for: {query}")

        # Step 2: Fetch and extract from top results
        citations: list[dict[str, str]] = []
        for r in results[:num_sources]:
            url = r.get("url", "")
            title = r.get("title", "")
            if not url:
                continue

            try:
                resp = httpx.get(url, timeout=10, follow_redirects=True)
                text = _extract_text(resp.text, self._max_fetch_size)
                passage = _find_relevant_passage(text, query_words)
                citations.append({
                    "title": title,
                    "url": url,
                    "passage": passage,
                })
            except Exception:
                # Skip failed fetches, use search snippet instead
                snippet = r.get("snippet", "")
                if snippet:
                    citations.append({
                        "title": title,
                        "url": url,
                        "passage": snippet,
                    })

        if not citations:
            return ToolResult(output=f"Could not fetch any sources for: {query}")

        # Step 3: Format with citations
        lines: list[str] = [f"Research results for: {query}\n"]
        for i, c in enumerate(citations, 1):
            lines.append(f"[{i}] {c['title']}")
            lines.append(f"    Source: {c['url']}")
            lines.append(f"    {c['passage']}")
            lines.append("")

        lines.append("Sources:")
        for i, c in enumerate(citations, 1):
            lines.append(f"  [{i}] {c['url']}")

        return ToolResult(
            output="\n".join(lines).strip(),
            metadata={
                "query": query,
                "source_count": len(citations),
                "citations": citations,
            },
        )
