"""Tests for chimera.mcp_servers.review_server — code review MCP server."""
from __future__ import annotations

from chimera.mcp_servers.review_server import ReviewFinding, ReviewMCPServer, review_diff


SAMPLE_DIFF = """\
--- a/app.py
+++ b/app.py
@@ -10,6 +10,12 @@
 import os

+def run_command(cmd):
+    os.system(cmd)
+    result = eval(cmd)
+    password = "secret123"
+    return result
+
 def main():
     pass
"""


class TestRuleBasedReview:
    """Test that rule-based review detects issues in diffs."""

    def test_detects_security_issues(self) -> None:
        findings = review_diff(SAMPLE_DIFF)

        # Should detect os.system, eval, and hardcoded password
        categories = [f.category for f in findings]
        assert "security" in categories

        security_findings = [f for f in findings if f.category == "security"]
        messages_lower = " ".join(f.message.lower() for f in security_findings)

        assert "eval" in messages_lower or "os.system" in messages_lower or "password" in messages_lower
        assert len(security_findings) >= 2  # at least eval + os.system or password

    def test_empty_diff_returns_no_findings(self) -> None:
        findings = review_diff("")
        assert findings == []

    def test_clean_diff_minimal_findings(self) -> None:
        clean_diff = """\
--- a/utils.py
+++ b/utils.py
@@ -1,3 +1,5 @@
 def add(a, b):
     return a + b
+
+def multiply(a, b):
+    return a * b
"""
        findings = review_diff(clean_diff)
        # Clean code should have no security/error findings
        critical = [f for f in findings if f.severity in ("error", "critical")]
        assert len(critical) == 0


class TestReviewMCPServer:
    """Test the MCP server message handling."""

    def test_initialize_and_list_tools(self) -> None:
        server = ReviewMCPServer()

        # Initialize
        resp = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        assert resp is not None
        assert resp["result"]["serverInfo"]["name"] == "chimera-review"

        # List tools
        resp = server.handle_message({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        assert resp is not None
        tools = resp["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "chimera_review_diff" in tool_names

    def test_review_diff_tool_call(self) -> None:
        server = ReviewMCPServer()

        resp = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "chimera_review_diff",
                "arguments": {"diff_text": SAMPLE_DIFF},
            },
        })
        assert resp is not None
        assert "result" in resp
        content = resp["result"]["content"]
        assert len(content) > 0
        # Should have findings text
        text = content[0]["text"]
        assert "issue" in text.lower() or "found" in text.lower()

    def test_missing_diff_returns_error(self) -> None:
        server = ReviewMCPServer()

        resp = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "chimera_review_diff",
                "arguments": {},
            },
        })
        assert resp is not None
        content = resp["result"]["content"]
        assert content[0]["text"].startswith("Error")
        assert resp["result"]["isError"] is True
