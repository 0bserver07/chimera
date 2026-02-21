# tests/test_tools_web_fetch.py
from unittest.mock import patch, MagicMock

import pytest

from chimera.tools.web_fetch import WebFetchTool


class TestWebFetchTool:
    def test_fetch_success(self):
        tool = WebFetchTool()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>Hello World</body></html>"
        mock_response.headers = {"content-type": "text/html"}

        with patch("chimera.tools.web_fetch.httpx") as mock_httpx:
            mock_httpx.get.return_value = mock_response
            result = tool.execute({"url": "https://example.com"}, None)
        assert result.success
        assert "Hello World" in result.output

    def test_fetch_failure(self):
        tool = WebFetchTool()
        with patch("chimera.tools.web_fetch.httpx") as mock_httpx:
            mock_httpx.get.side_effect = Exception("Connection refused")
            result = tool.execute({"url": "https://bad.example.com"}, None)
        assert not result.success
        assert "Connection refused" in result.error

    def test_fetch_no_httpx(self):
        tool = WebFetchTool()
        with patch("chimera.tools.web_fetch.httpx", None):
            result = tool.execute({"url": "https://example.com"}, None)
        assert not result.success
        assert "httpx" in result.error.lower()

    def test_schema(self):
        tool = WebFetchTool()
        assert tool.name == "web_fetch"
        schema = tool.to_anthropic_schema()
        assert "url" in str(schema)
