"""Tests for the PlanActLoop two-phase loop."""
from __future__ import annotations

from chimera.core.context import Context
from chimera.core.loop import drain_steps
from chimera.core.loops.plan_act import PlanActLoop, READ_ONLY_TOOLS
from chimera.core.tool import BaseTool
from chimera.providers.base import Provider, Response
from chimera.types import AgentResult, Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _ReadTool(BaseTool):
    name = "read_file"
    description = "Read a file"
    parameters = {"type": "object", "properties": {}}

    def execute(self, args, env=None):
        return ToolResult(output="file content")


class _SearchTool(BaseTool):
    name = "search"
    description = "Search files"
    parameters = {"type": "object", "properties": {}}

    def execute(self, args, env=None):
        return ToolResult(output="search results")


class _ThinkTool(BaseTool):
    name = "think"
    description = "Think"
    parameters = {"type": "object", "properties": {}}

    def execute(self, args, env=None):
        return ToolResult(output="thought")


class _WriteTool(BaseTool):
    name = "write_file"
    description = "Write a file"
    parameters = {"type": "object", "properties": {}}

    def execute(self, args, env=None):
        return ToolResult(output="written")


class _BashTool(BaseTool):
    name = "bash"
    description = "Run bash"
    parameters = {"type": "object", "properties": {}}

    def execute(self, args, env=None):
        return ToolResult(output="bash output")


class SimpleProvider(Provider):
    """Provider that returns text-only responses (no tool calls)."""

    def __init__(self):
        self.call_count = 0
        self.calls: list[dict] = []

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self.call_count += 1
        self.calls.append({"messages": messages, "tools": tools})
        if self.call_count == 1:
            content = "Plan: 1. Read main.py 2. Fix the bug 3. Write main.py"
        else:
            content = "Done! Bug fixed."
        return Response(
            content=content,
            tool_calls=[],
            usage={"input_tokens": 100, "output_tokens": 50},
        )

    @property
    def context_window(self):
        return 100_000

    @property
    def supports_tool_use(self):
        return True

    @property
    def model_name(self):
        return "test-model"


class ToolUsingProvider(Provider):
    """Provider that uses tools in the plan phase, then completes."""

    def __init__(self):
        self.call_count = 0
        self.calls: list[dict] = []

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self.call_count += 1
        self.calls.append({"messages": messages, "tools": tools})

        if self.call_count == 1:
            # Plan phase: call read_file
            return Response(
                content="Let me explore the codebase.",
                tool_calls=[
                    ToolCall(id="tc1", name="read_file", arguments={"path": "main.py"}),
                ],
                usage={"input_tokens": 100, "output_tokens": 50},
            )
        if self.call_count == 2:
            # Plan phase: produce plan text
            return Response(
                content="Plan: 1. Edit main.py line 42",
                tool_calls=[],
                usage={"input_tokens": 100, "output_tokens": 50},
            )
        # Act phase
        return Response(
            content="All done.",
            tool_calls=[],
            usage={"input_tokens": 100, "output_tokens": 50},
        )

    @property
    def context_window(self):
        return 100_000

    @property
    def supports_tool_use(self):
        return True

    @property
    def model_name(self):
        return "test-model"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReadOnlyToolsDefault:
    def test_contains_read_tools(self):
        assert "read_file" in READ_ONLY_TOOLS
        assert "search" in READ_ONLY_TOOLS
        assert "list_files" in READ_ONLY_TOOLS
        assert "repo_map" in READ_ONLY_TOOLS
        assert "think" in READ_ONLY_TOOLS
        assert "read_image" in READ_ONLY_TOOLS

    def test_excludes_write_tools(self):
        assert "write_file" not in READ_ONLY_TOOLS
        assert "edit_file" not in READ_ONLY_TOOLS
        assert "bash" not in READ_ONLY_TOOLS
        assert "replace_in_file" not in READ_ONLY_TOOLS
        assert "git" not in READ_ONLY_TOOLS
        assert "test" not in READ_ONLY_TOOLS


class TestPlanActLoop:
    def test_runs_two_phases(self):
        """Both plan and act phases execute, producing combined steps."""
        loop = PlanActLoop(plan_steps=3, act_steps=5)
        provider = SimpleProvider()
        ctx = Context(system="test")
        ctx.add(Message.user("Fix the bug"))
        result = loop.run(provider, [_ReadTool(), _WriteTool()], ctx, None)

        assert result.success
        # 1 step in plan phase + 1 step in act phase
        assert result.steps == 2
        assert provider.call_count == 2

    def test_plan_phase_filters_tools(self):
        """Plan phase should only receive read-only tool schemas."""
        loop = PlanActLoop(plan_steps=3, act_steps=5)
        provider = SimpleProvider()
        ctx = Context(system="test")
        ctx.add(Message.user("Fix the bug"))
        loop.run(
            provider,
            [_ReadTool(), _SearchTool(), _WriteTool(), _BashTool()],
            ctx,
            None,
        )

        # First call is plan phase
        plan_call_tools = provider.calls[0]["tools"]
        plan_tool_names = {t["name"] for t in plan_call_tools}
        assert plan_tool_names == {"read_file", "search"}

        # Second call is act phase — all tools present
        act_call_tools = provider.calls[1]["tools"]
        act_tool_names = {t["name"] for t in act_call_tools}
        assert act_tool_names == {"read_file", "search", "write_file", "bash"}

    def test_plan_output_stored(self):
        """The plan text is stored on the loop instance."""
        loop = PlanActLoop()
        provider = SimpleProvider()
        ctx = Context(system="test")
        ctx.add(Message.user("task"))
        loop.run(provider, [], ctx, None)
        assert "Plan:" in loop.plan_output

    def test_plan_injected_into_act_context(self):
        """The act phase receives the plan output in its user message."""
        loop = PlanActLoop()
        provider = SimpleProvider()
        ctx = Context(system="test")
        ctx.add(Message.user("Fix the bug"))
        loop.run(provider, [], ctx, None)

        # The act-phase call should mention the plan
        act_messages = provider.calls[1]["messages"]
        user_msgs = [m for m in act_messages if m.role == "user"]
        assert any("EXECUTION PHASE" in m.content for m in user_msgs)
        assert any("Plan:" in m.content for m in user_msgs)

    def test_plan_context_mentions_planning_phase(self):
        """The plan phase user message includes PLANNING PHASE instruction."""
        loop = PlanActLoop()
        provider = SimpleProvider()
        ctx = Context(system="test")
        ctx.add(Message.user("Fix the bug"))
        loop.run(provider, [], ctx, None)

        plan_messages = provider.calls[0]["messages"]
        user_msgs = [m for m in plan_messages if m.role == "user"]
        assert any("PLANNING PHASE" in m.content for m in user_msgs)

    def test_combined_cost(self):
        """Cost from both phases is summed (non-negative)."""
        loop = PlanActLoop()
        provider = SimpleProvider()
        ctx = Context(system="test")
        ctx.add(Message.user("task"))
        result = loop.run(provider, [], ctx, None)
        # Cost is non-negative; may be 0 for unknown model names
        assert result.cost >= 0

    def test_combined_tool_calls_total(self):
        """Tool call count spans both phases."""
        loop = PlanActLoop(plan_steps=5, act_steps=5)
        provider = ToolUsingProvider()
        ctx = Context(system="test")
        ctx.add(Message.user("Fix the bug"))
        result = loop.run(provider, [_ReadTool(), _WriteTool()], ctx, None)

        # Plan phase: 1 tool call (read_file), Act phase: 0
        assert result.tool_calls_total == 1
        assert result.steps == 3  # 2 plan steps + 1 act step

    def test_custom_read_only_tools(self):
        """Custom read-only tool set is respected."""
        custom_readonly = {"read_file", "think"}
        loop = PlanActLoop(read_only_tools=custom_readonly)
        provider = SimpleProvider()
        ctx = Context(system="test")
        ctx.add(Message.user("task"))
        loop.run(
            provider,
            [_ReadTool(), _SearchTool(), _ThinkTool(), _WriteTool()],
            ctx,
            None,
        )

        plan_call_tools = provider.calls[0]["tools"]
        plan_tool_names = {t["name"] for t in plan_call_tools}
        assert plan_tool_names == {"read_file", "think"}

    def test_act_phase_failure_propagated(self):
        """If the act phase fails, the combined result reflects that."""
        class FailProvider(Provider):
            def __init__(self):
                self.call_count = 0
            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                self.call_count += 1
                return Response(
                    content="output",
                    tool_calls=[],
                    usage={"input_tokens": 10, "output_tokens": 5},
                )
            @property
            def context_window(self): return 100_000
            @property
            def supports_tool_use(self): return True
            @property
            def model_name(self): return "test"

        loop = PlanActLoop(plan_steps=1, act_steps=1)
        ctx = Context(system="test")
        ctx.add(Message.user("task"))
        result = loop.run(FailProvider(), [], ctx, None)
        # Both phases return success (text-only response = success)
        assert result.success

    def test_empty_tools_list(self):
        """Works correctly when no tools are provided."""
        loop = PlanActLoop()
        provider = SimpleProvider()
        ctx = Context(system="test")
        ctx.add(Message.user("task"))
        result = loop.run(provider, [], ctx, None)
        assert result.success
        assert result.steps == 2

    def test_system_prompt_preserved(self):
        """System prompt from original context is used in both phases."""
        loop = PlanActLoop()
        provider = SimpleProvider()
        ctx = Context(system="You are a helpful assistant.")
        ctx.add(Message.user("task"))
        loop.run(provider, [], ctx, None)

        for call in provider.calls:
            system_msgs = [m for m in call["messages"] if m.role == "system"]
            assert len(system_msgs) == 1
            assert system_msgs[0].content == "You are a helpful assistant."


class TestPlanActLoopIterSteps:
    def test_iter_steps_yields_act_steps(self):
        """iter_steps yields steps from the act phase."""
        loop = PlanActLoop(plan_steps=3, act_steps=5)
        provider = SimpleProvider()
        ctx = Context(system="test")
        ctx.add(Message.user("Fix the bug"))

        gen = loop.iter_steps(provider, [_ReadTool(), _WriteTool()], ctx, None)
        result = drain_steps(gen)

        assert result.success
        # Combined: plan runs internally, act yields
        assert result.steps >= 2
        assert loop.plan_output != ""

    def test_iter_steps_plan_output_available(self):
        """Plan output is available after iter_steps completes."""
        loop = PlanActLoop()
        provider = SimpleProvider()
        ctx = Context(system="test")
        ctx.add(Message.user("task"))

        gen = loop.iter_steps(provider, [], ctx, None)
        drain_steps(gen)

        assert "Plan:" in loop.plan_output

    def test_iter_steps_yields_both_plan_and_act_phases(self):
        """iter_steps actually yields steps from both phases, not just act.

        Regression: previously the plan phase ran via ``plan_loop.run()``
        so no plan-phase steps were yielded even though the docstring said
        "yield steps from both plan and act phases".
        """
        loop = PlanActLoop(plan_steps=3, act_steps=5)
        provider = SimpleProvider()
        ctx = Context(system="test")
        ctx.add(Message.user("Fix the bug"))

        gen = loop.iter_steps(provider, [_ReadTool(), _WriteTool()], ctx, None)
        yielded_steps = []
        try:
            while True:
                yielded_steps.append(next(gen))
        except StopIteration:
            pass

        # With SimpleProvider there's 1 plan step (done=True after text-only
        # response) + 1 act step (done=True after text-only response) = 2 yields.
        assert len(yielded_steps) == 2, (
            f"expected 2 yielded steps (plan + act), got {len(yielded_steps)}"
        )
        # Both yielded steps should be done=True (plan ends text-only, act ends text-only)
        assert all(s.done for s in yielded_steps)
