"""Tests for WebSearchTool."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chimera.tools.web_search import WebSearchTool, _parse_ddg_html


# -- Unit tests for HTML parsing --

_SAMPLE_HTML = """
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2F&rut=abc">Python Documentation</a>
  <a class="result__snippet">Official Python 3 documentation. Tutorials, library reference, and more.</a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fstackoverflow.com%2Fquestions%2Fpython&rut=def">Python - Stack Overflow</a>
  <a class="result__snippet">Questions tagged [python] on Stack Overflow.</a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F&rut=ghi">Welcome to Python.org</a>
  <a class="result__snippet">The official home of Python.</a>
</div>
"""


def test_parse_ddg_html_extracts_results():
    results = _parse_ddg_html(_SAMPLE_HTML, max_results=10)
    assert len(results) == 3
    assert results[0]["title"] == "Python Documentation"
    assert results[0]["url"] == "https://docs.python.org/3/"
    assert "documentation" in results[0]["snippet"].lower()


def test_parse_ddg_html_respects_max_results():
    results = _parse_ddg_html(_SAMPLE_HTML, max_results=1)
    assert len(results) == 1


def test_parse_ddg_html_empty_input():
    results = _parse_ddg_html("", max_results=5)
    assert results == []


def test_parse_ddg_html_strips_tags_from_title():
    html = '<a class="result__a" href="http://example.com">Title with <b>bold</b></a>'
    results = _parse_ddg_html(html, max_results=5)
    assert len(results) == 1
    assert results[0]["title"] == "Title with bold"


# -- Tool execution tests --

@pytest.fixture
def tool():
    return WebSearchTool()


def test_tool_metadata(tool):
    assert tool.name == "web_search"
    assert "query" in tool.parameters["properties"]


def test_tool_execute_formats_output(tool):
    mock_response = MagicMock()
    mock_response.text = _SAMPLE_HTML
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    with patch("chimera.tools.web_search.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_response
        result = tool.execute({"query": "python docs"}, env=None)

    assert result.error is None
    assert "Python Documentation" in result.output
    assert "docs.python.org" in result.output
    assert result.metadata["result_count"] == 3


def test_tool_execute_no_results(tool):
    mock_response = MagicMock()
    mock_response.text = "<html><body>No results</body></html>"
    mock_response.raise_for_status = MagicMock()

    with patch("chimera.tools.web_search.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_response
        result = tool.execute({"query": "xyzzy123nonsense"}, env=None)

    assert "No results found" in result.output


def test_tool_execute_network_error(tool):
    with patch("chimera.tools.web_search.httpx") as mock_httpx:
        mock_httpx.post.side_effect = Exception("Connection refused")
        result = tool.execute({"query": "test"}, env=None)

    assert result.error is not None
    assert "Connection refused" in result.error


def test_tool_max_results_param(tool):
    mock_response = MagicMock()
    mock_response.text = _SAMPLE_HTML
    mock_response.raise_for_status = MagicMock()

    with patch("chimera.tools.web_search.httpx") as mock_httpx:
        mock_httpx.post.return_value = mock_response
        result = tool.execute({"query": "python", "max_results": 2}, env=None)

    assert result.metadata["result_count"] == 2


def test_tool_no_httpx():
    with patch("chimera.tools.web_search.httpx", None):
        tool = WebSearchTool()
        result = tool.execute({"query": "test"}, env=None)
    assert result.error is not None
    assert "httpx" in result.error
