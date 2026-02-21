# tests/test_approval.py
from __future__ import annotations

from chimera.core.approval import ApprovalPolicy, AutoApprove, AlwaysDeny, AllowList
from chimera.core.tool import BaseTool
from chimera.types import ToolResult


class FakeTool(BaseTool):
    name = "fake"
    description = "A fake tool"
    parameters = {"type": "object", "properties": {}, "required": []}
    requires_approval = True

    def execute(self, args, env=None):
        return ToolResult(output="ok")


class SafeTool(BaseTool):
    name = "safe"
    description = "A safe tool"
    parameters = {"type": "object", "properties": {}, "required": []}
    requires_approval = False

    def execute(self, args, env=None):
        return ToolResult(output="ok")


class TestAutoApprove:
    def test_auto_approve_always_returns_true(self):
        policy = AutoApprove()
        assert policy.should_approve("fake", {}) is True

    def test_auto_approve_any_tool(self):
        policy = AutoApprove()
        assert policy.should_approve("bash", {"command": "rm -rf /"}) is True


class TestAlwaysDeny:
    def test_deny_returns_false(self):
        policy = AlwaysDeny()
        assert policy.should_approve("fake", {}) is False


class TestAllowList:
    def test_allowed_tool(self):
        policy = AllowList(allowed=["read_file", "search"])
        assert policy.should_approve("read_file", {}) is True

    def test_denied_tool(self):
        policy = AllowList(allowed=["read_file"])
        assert policy.should_approve("bash", {}) is False


class TestToolApprovalFlag:
    def test_tool_requires_approval_default(self):
        """BaseTool.requires_approval defaults to False."""
        from chimera.tools.read import ReadFileTool
        tool = ReadFileTool()
        assert tool.requires_approval is False

    def test_tool_can_set_requires_approval(self):
        tool = FakeTool()
        assert tool.requires_approval is True
