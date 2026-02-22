from __future__ import annotations

from chimera.core.loops.plan_execute import PlanAndExecute
from chimera.core.context import Context
from chimera.providers.base import Provider, Response
from chimera.types import Message, ToolCall


class PlanProvider(Provider):
    """Simulates a plan-then-execute flow."""
    def __init__(self):
        self._step = 0
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._step += 1
        if self._step == 1:
            return Response(
                content="Plan:\n1. Read the file\n2. Fix the bug\n3. Write the file",
                tool_calls=[],
                usage={"input_tokens": 100, "output_tokens": 50},
            )
        if self._step == 2:
            return Response(
                content="Executing step 1",
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "main.py"})],
                usage={"input_tokens": 50, "output_tokens": 30},
            )
        return Response(content="Done!", tool_calls=[], usage={"input_tokens": 20, "output_tokens": 10})
    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return True
    @property
    def model_name(self): return "plan-provider"


class TestPlanAndExecute:
    def test_plan_then_execute(self):
        from chimera.tools.read import ReadFileTool
        from chimera.env.local import LocalEnvironment
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            env.write_file("main.py", "hello")

            loop = PlanAndExecute(max_steps=10)
            provider = PlanProvider()
            context = Context(system="You are helpful")
            context.add(Message.user("Fix the bug"))

            result = loop.run(provider, [ReadFileTool()], context, env)
            assert result.success
            assert result.steps >= 2

    def test_max_steps_respected(self):
        loop = PlanAndExecute(max_steps=1)
        provider = PlanProvider()
        context = Context()
        context.add(Message.user("Do something"))
        result = loop.run(provider, [], context, None)
        assert result.steps == 1
