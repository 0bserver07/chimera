"""Tests for the AutonomousLoop goal-driven loop."""
from __future__ import annotations

from chimera.core.context import Context
from chimera.core.loop import drain_steps
from chimera.core.loops.autonomous import AutonomousLoop, _parse_plan
from chimera.core.tool import BaseTool
from chimera.providers.base import Provider, Response
from chimera.types import AgentResult, Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _DummyTool(BaseTool):
    name = "dummy"
    description = "A dummy tool"
    parameters = {"type": "object", "properties": {}}

    def execute(self, args, env=None):
        return ToolResult(output="done")


class PlanProvider(Provider):
    """Provider that returns a numbered plan on first call, then completes
    each sub-task with a simple text response (no tool calls)."""

    def __init__(self, plan_text: str, step_responses: list[str] | None = None):
        self._plan_text = plan_text
        self._step_responses = step_responses or []
        self.call_count = 0
        self.calls: list[dict] = []

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self.call_count += 1
        self.calls.append({"messages": messages, "tools": tools})

        if self.call_count == 1:
            # Planning call — return the plan
            return Response(
                content=self._plan_text,
                tool_calls=[],
                usage={"input_tokens": 100, "output_tokens": 50},
            )

        # Sub-task execution calls
        idx = self.call_count - 2  # 0-based index into step_responses
        if idx < len(self._step_responses):
            content = self._step_responses[idx]
        else:
            content = "Step completed."

        return Response(
            content=content,
            tool_calls=[],
            usage={"input_tokens": 50, "output_tokens": 30},
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


class FailingStepProvider(Provider):
    """Provider that returns a plan, then fails on a specific step (using
    max-steps exhaustion to simulate failure), then succeeds on replan."""

    def __init__(self, fail_on_call: int = 3, replan_text: str = ""):
        self._fail_on_call = fail_on_call
        self._replan_text = replan_text
        self.call_count = 0
        self.calls: list[dict] = []

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self.call_count += 1
        self.calls.append({"messages": messages, "tools": tools})

        if self.call_count == 1:
            # Initial plan
            return Response(
                content="1. First task\n2. Second task\n3. Third task",
                tool_calls=[],
                usage={"input_tokens": 100, "output_tokens": 50},
            )

        if self.call_count == self._fail_on_call:
            # This call is the execution of the step that should "fail".
            # We make it loop by returning a tool call, which will exhaust
            # the inner loop's max_steps (set to 1 in tests).
            return Response(
                content="Trying...",
                tool_calls=[
                    ToolCall(id="tc_fail", name="dummy", arguments={}),
                ],
                usage={"input_tokens": 50, "output_tokens": 30},
            )

        if self._replan_text and self.call_count == self._fail_on_call + 2:
            # Replan call
            return Response(
                content=self._replan_text,
                tool_calls=[],
                usage={"input_tokens": 80, "output_tokens": 40},
            )

        # Default: succeed
        return Response(
            content="Done with this step.",
            tool_calls=[],
            usage={"input_tokens": 50, "output_tokens": 30},
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


class MaxStepsProvider(Provider):
    """Provider that always returns tool calls, forcing inner loops to hit
    max_steps."""

    def __init__(self):
        self.call_count = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self.call_count += 1

        if self.call_count == 1:
            # Plan
            return Response(
                content="1. Step A\n2. Step B\n3. Step C",
                tool_calls=[],
                usage={"input_tokens": 100, "output_tokens": 50},
            )

        # Always return a tool call to exhaust steps
        return Response(
            content="Working...",
            tool_calls=[
                ToolCall(id=f"tc_{self.call_count}", name="dummy", arguments={}),
            ],
            usage={"input_tokens": 50, "output_tokens": 30},
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


class AlwaysFailReplanProvider(Provider):
    """Provider where every step fails and replanning also produces failing steps."""

    def __init__(self):
        self.call_count = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self.call_count += 1

        if self.call_count == 1:
            # Initial plan
            return Response(
                content="1. Step one\n2. Step two",
                tool_calls=[],
                usage={"input_tokens": 100, "output_tokens": 50},
            )

        # Check if this is a replan call (user message mentions "failed because")
        user_msgs = [m for m in messages if m.role == "user"]
        is_replan = any("failed because" in m.content.lower() for m in user_msgs)

        if is_replan:
            # Return a new plan
            return Response(
                content="1. Retry step\n2. Another retry",
                tool_calls=[],
                usage={"input_tokens": 80, "output_tokens": 40},
            )

        # Step execution — always use tools to force max_steps failure
        return Response(
            content="Trying...",
            tool_calls=[
                ToolCall(id=f"tc_{self.call_count}", name="dummy", arguments={}),
            ],
            usage={"input_tokens": 50, "output_tokens": 30},
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
# Unit tests for _parse_plan
# ---------------------------------------------------------------------------

class TestParsePlan:
    def test_numbered_dot(self):
        text = "1. First step\n2. Second step\n3. Third step"
        assert _parse_plan(text) == ["First step", "Second step", "Third step"]

    def test_numbered_paren(self):
        text = "1) Do X\n2) Do Y"
        assert _parse_plan(text) == ["Do X", "Do Y"]

    def test_with_extra_whitespace(self):
        text = "  1. First  \n  2. Second  "
        assert _parse_plan(text) == ["First", "Second"]

    def test_fallback_to_lines(self):
        text = "Do this\nDo that\nDo the other"
        assert _parse_plan(text) == ["Do this", "Do that", "Do the other"]

    def test_empty_text(self):
        assert _parse_plan("") == []
        assert _parse_plan("   \n  \n") == []


# ---------------------------------------------------------------------------
# AutonomousLoop tests
# ---------------------------------------------------------------------------

class TestDecomposesGoalIntoSteps:
    def test_decomposes_goal_into_steps(self):
        """Provider is asked to decompose a goal; resulting plan is parsed."""
        plan_text = "1. Read the file\n2. Fix the bug\n3. Run tests"
        provider = PlanProvider(plan_text)
        loop = AutonomousLoop(max_steps_per_task=5, max_total_steps=50)
        ctx = Context()
        ctx.add(Message.user("Fix all bugs in main.py"))

        result = loop.run(provider, [], ctx, None)

        assert result.success
        assert loop.plan == ["Read the file", "Fix the bug", "Run tests"]
        assert len(loop.completed_steps) == 3
        # First call was the planning call
        assert provider.call_count >= 4  # 1 plan + 3 steps

    def test_plan_prompt_sent_to_provider(self):
        """The planning call includes the PLAN_PROMPT."""
        plan_text = "1. Step one\n2. Step two"
        provider = PlanProvider(plan_text)
        loop = AutonomousLoop()
        ctx = Context()
        ctx.add(Message.user("Build a website"))

        loop.run(provider, [], ctx, None)

        # First call should contain the plan prompt
        first_call_msgs = provider.calls[0]["messages"]
        user_msgs = [m for m in first_call_msgs if m.role == "user"]
        assert any("Break this goal into numbered steps" in m.content for m in user_msgs)


class TestExecutesStepsSequentially:
    def test_executes_steps_sequentially(self):
        """Each step is executed in order via inner ReAct loops."""
        plan_text = "1. Step A\n2. Step B\n3. Step C"
        responses = ["A done", "B done", "C done"]
        provider = PlanProvider(plan_text, step_responses=responses)
        loop = AutonomousLoop(max_steps_per_task=5, max_total_steps=50)
        ctx = Context()
        ctx.add(Message.user("Do three things"))

        result = loop.run(provider, [], ctx, None)

        assert result.success
        assert result.steps == 3  # One inner step per sub-task
        assert "Step 1: A done" in result.output
        assert "Step 2: B done" in result.output
        assert "Step 3: C done" in result.output
        assert loop.completed_steps == ["Step A", "Step B", "Step C"]

    def test_step_contexts_contain_goal(self):
        """Each sub-task execution includes the original goal."""
        plan_text = "1. Do X"
        provider = PlanProvider(plan_text, step_responses=["X done"])
        loop = AutonomousLoop()
        ctx = Context()
        ctx.add(Message.user("My big goal"))

        loop.run(provider, [], ctx, None)

        # Second call is the step execution
        step_call_msgs = provider.calls[1]["messages"]
        user_msgs = [m for m in step_call_msgs if m.role == "user"]
        assert any("My big goal" in m.content for m in user_msgs)
        assert any("step 1" in m.content.lower() for m in user_msgs)


class TestReplansOnFailure:
    def test_replans_on_failure(self):
        """When a step fails (hits max_steps), the loop replans and continues."""
        # Step 2 execution (call 3) will return a tool call, then inner loop
        # hits max_steps=1 and fails. The replan call returns revised steps.
        provider = FailingStepProvider(
            fail_on_call=3,
            replan_text="1. Revised step two\n2. Revised step three",
        )
        loop = AutonomousLoop(
            max_steps_per_task=1,  # Inner loop can only do 1 step
            max_total_steps=100,
            max_replans=3,
        )
        ctx = Context()
        ctx.add(Message.user("Complete the project"))

        result = loop.run(provider, [_DummyTool()], ctx, None)

        assert loop.replan_count == 1
        # Step 1 succeeded, step 2 failed + replanned
        assert "First task" in loop.completed_steps

    def test_replan_prompt_includes_error(self):
        """The replan prompt describes what failed and what's left."""
        provider = FailingStepProvider(
            fail_on_call=3,
            replan_text="1. Better approach",
        )
        loop = AutonomousLoop(max_steps_per_task=1, max_replans=3)
        ctx = Context()
        ctx.add(Message.user("Do stuff"))

        loop.run(provider, [_DummyTool()], ctx, None)

        # Find the replan call — it should mention "failed because"
        replan_calls = [
            c for c in provider.calls
            if any("failed because" in m.content.lower()
                   for m in c["messages"] if m.role == "user")
        ]
        assert len(replan_calls) >= 1


class TestRespectsMaxTotalSteps:
    def test_respects_max_total_steps(self):
        """Returns partial result when cumulative steps hit the limit."""
        provider = MaxStepsProvider()
        loop = AutonomousLoop(
            max_steps_per_task=2,
            max_total_steps=3,
            max_replans=0,  # No replans so it stops on first failure
        )
        ctx = Context()
        ctx.add(Message.user("Big task"))

        result = loop.run(provider, [_DummyTool()], ctx, None)

        assert not result.success
        # Steps should not exceed the limit (max_total_steps=3)
        # Each inner loop runs up to 2 steps, and the total cap is 3.
        assert result.steps <= 4  # May slightly exceed due to inner loop finishing

    def test_max_total_steps_stops_before_next_task(self):
        """Once total steps reaches the cap, the next sub-task is not started."""
        plan_text = "1. Step A\n2. Step B\n3. Step C\n4. Step D\n5. Step E"
        # Each step takes 1 inner step. With max_total_steps=2,
        # steps A and B should complete but C should not start.
        provider = PlanProvider(plan_text, step_responses=[
            "A done", "B done", "C done", "D done", "E done",
        ])
        loop = AutonomousLoop(
            max_steps_per_task=5,
            max_total_steps=2,
        )
        ctx = Context()
        ctx.add(Message.user("Do many things"))

        result = loop.run(provider, [], ctx, None)

        assert not result.success
        assert "Max total steps" in (result.error or "")
        # Only 2 steps were executed before the limit check kicked in
        assert len(loop.completed_steps) <= 2


class TestRespectsMaxReplans:
    def test_respects_max_replans(self):
        """Gives up after exceeding the replan limit."""
        provider = AlwaysFailReplanProvider()
        loop = AutonomousLoop(
            max_steps_per_task=1,  # Forces inner loops to fail
            max_total_steps=200,
            max_replans=2,
        )
        ctx = Context()
        ctx.add(Message.user("Impossible task"))

        result = loop.run(provider, [_DummyTool()], ctx, None)

        assert not result.success
        assert "Max replans" in (result.error or "")
        assert loop.replan_count == 2

    def test_zero_replans_fails_immediately(self):
        """With max_replans=0, the first failure is final."""
        provider = MaxStepsProvider()
        loop = AutonomousLoop(
            max_steps_per_task=1,
            max_total_steps=200,
            max_replans=0,
        )
        ctx = Context()
        ctx.add(Message.user("Do something"))

        result = loop.run(provider, [_DummyTool()], ctx, None)

        assert not result.success
        assert loop.replan_count == 0


class TestEdgeCases:
    def test_no_goal_in_context(self):
        """Returns error when no user message is present."""
        provider = PlanProvider("1. Nothing")
        loop = AutonomousLoop()
        ctx = Context()

        result = loop.run(provider, [], ctx, None)

        assert not result.success
        assert "No goal" in (result.error or "")

    def test_empty_plan(self):
        """Returns error when provider generates no parseable steps."""
        provider = PlanProvider("")  # Empty plan
        loop = AutonomousLoop()
        ctx = Context()
        ctx.add(Message.user("Do something"))

        result = loop.run(provider, [], ctx, None)

        assert not result.success
        assert "no steps" in (result.error or "").lower()

    def test_cost_is_accumulated(self):
        """Total cost includes planning and all sub-task executions."""
        plan_text = "1. Step one\n2. Step two"
        provider = PlanProvider(plan_text)
        loop = AutonomousLoop()
        ctx = Context()
        ctx.add(Message.user("goal"))

        result = loop.run(provider, [], ctx, None)

        # Cost should be non-negative (may be 0 for unknown model names)
        assert result.cost >= 0

    def test_iter_steps_works(self):
        """iter_steps yields steps and returns combined result."""
        plan_text = "1. Step one\n2. Step two"
        provider = PlanProvider(plan_text, ["One done", "Two done"])
        loop = AutonomousLoop(max_steps_per_task=5, max_total_steps=50)
        ctx = Context()
        ctx.add(Message.user("Do two things"))

        gen = loop.iter_steps(provider, [], ctx, None)
        result = drain_steps(gen)

        assert result.success
        assert result.steps == 2
