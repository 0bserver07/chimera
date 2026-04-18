"""Tests for chimera.core.tool_deferral — Tool Deferral Integration."""
from __future__ import annotations

from chimera.core.tool import BaseTool
from chimera.core.tool_deferral import ToolDeferralManager
from chimera.types import ToolResult


class _FakeTool(BaseTool):
    """Minimal tool for testing."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.parameters = {}

    def execute(self, args, env):
        return ToolResult(output="ok")


def _make_tools(n: int, names: list[str] | None = None) -> list[BaseTool]:
    """Create n fake tools with given or generated names."""
    if names:
        return [_FakeTool(name=name, description=f"Tool {name}") for name in names]
    return [_FakeTool(name=f"tool_{i}", description=f"Tool {i}") for i in range(n)]


class TestUnderLimit:
    """When total tools <= MAX_EAGER, all are eager and none are deferred."""

    def test_all_eager_when_few(self):
        tools = _make_tools(10)
        mgr = ToolDeferralManager(tools)
        assert len(mgr.get_eager_tools()) == 10
        assert len(mgr.get_deferred_tools()) == 0

    def test_exactly_at_limit(self):
        tools = _make_tools(30)
        mgr = ToolDeferralManager(tools)
        assert len(mgr.get_eager_tools()) == 30
        assert len(mgr.get_deferred_tools()) == 0


class TestOverLimit:
    """When total tools > MAX_EAGER, only ALWAYS_EAGER tools are eager."""

    def test_defers_non_essential(self):
        eager_names = list(ToolDeferralManager.ALWAYS_EAGER)
        extra_names = [f"extra_{i}" for i in range(25)]
        all_names = eager_names + extra_names
        tools = _make_tools(0, names=all_names)
        mgr = ToolDeferralManager(tools)
        eager = mgr.get_eager_tools()
        deferred = mgr.get_deferred_tools()
        eager_tool_names = {t.name for t in eager}
        deferred_tool_names = {t.name for t in deferred}
        # All ALWAYS_EAGER tools should be in eager
        for name in ToolDeferralManager.ALWAYS_EAGER:
            assert name in eager_tool_names
        # Extra tools should be deferred
        for name in extra_names:
            assert name in deferred_tool_names

    def test_eager_plus_deferred_equals_total(self):
        names = list(ToolDeferralManager.ALWAYS_EAGER) + [f"x_{i}" for i in range(25)]
        tools = _make_tools(0, names=names)
        mgr = ToolDeferralManager(tools)
        total = len(mgr.get_eager_tools()) + len(mgr.get_deferred_tools())
        assert total == len(names)


class TestSearch:
    """search finds tools by name or description keyword."""

    def test_search_by_name(self):
        tools = [
            _FakeTool(name="bash", description="Run shell commands"),
            _FakeTool(name="read_file", description="Read a file"),
            _FakeTool(name="grep", description="Search file contents"),
        ]
        mgr = ToolDeferralManager(tools)
        results = mgr.search("bash")
        assert len(results) == 1
        assert results[0].name == "bash"

    def test_search_by_description(self):
        tools = [
            _FakeTool(name="bash", description="Run shell commands"),
            _FakeTool(name="read_file", description="Read a file"),
        ]
        mgr = ToolDeferralManager(tools)
        results = mgr.search("shell")
        assert len(results) == 1
        assert results[0].name == "bash"

    def test_search_case_insensitive(self):
        tools = [_FakeTool(name="Bash", description="Run shell commands")]
        mgr = ToolDeferralManager(tools)
        results = mgr.search("bash")
        assert len(results) == 1

    def test_search_no_match(self):
        tools = [_FakeTool(name="bash", description="Run shell commands")]
        mgr = ToolDeferralManager(tools)
        results = mgr.search("nonexistent")
        assert len(results) == 0


class TestGetTool:
    """get_tool retrieves any tool by exact name."""

    def test_get_existing(self):
        tools = [_FakeTool(name="bash", description="Run shell")]
        mgr = ToolDeferralManager(tools)
        t = mgr.get_tool("bash")
        assert t is not None
        assert t.name == "bash"

    def test_get_nonexistent(self):
        tools = [_FakeTool(name="bash", description="Run shell")]
        mgr = ToolDeferralManager(tools)
        assert mgr.get_tool("nonexistent") is None
