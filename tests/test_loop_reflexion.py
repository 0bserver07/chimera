from __future__ import annotations

import tempfile

from chimera.core.loops.reflexion import Reflexion
from chimera.core.context import Context
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.tools.read import ReadFileTool
from chimera.types import Message, ToolCall


class ReflexionProvider(Provider):
    """Simulates an agent that makes tool calls and eventually finishes."""
    def __init__(self, tool_steps: int = 4):
        self._step = 0
        self._tool_steps = tool_steps

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._step += 1
        if self._step <= self._tool_steps:
            return Response(
                content=f"Step {self._step}: reading file",
                tool_calls=[ToolCall(id=f"c{self._step}", name="read_file", arguments={"path": "main.py"})],
                usage={"input_tokens": 50, "output_tokens": 30},
            )
        return Response(
            content="All done after reflection!",
            tool_calls=[],
            usage={"input_tokens": 20, "output_tokens": 10},
        )

    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return True
    @property
    def model_name(self): return "reflexion-provider"


class TestReflexion:
    def test_basic_reflexion_flow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            env.write_file("main.py", "hello")

            loop = Reflexion(max_steps=20, reflect_every=2)
            provider = ReflexionProvider(tool_steps=3)
            context = Context(system="You are helpful")
            context.add(Message.user("Fix the bug"))

            result = loop.run(provider, [ReadFileTool()], context, env)
            assert result.success
            assert result.tool_calls_total == 3

    def test_reflection_prompt_injected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            env.write_file("main.py", "hello")

            loop = Reflexion(max_steps=20, reflect_every=2)
            provider = ReflexionProvider(tool_steps=4)
            context = Context(system="You are helpful")
            context.add(Message.user("Fix the bug"))

            result = loop.run(provider, [ReadFileTool()], context, env)
            assert result.success
            # Check that the reflection prompt was injected into context
            user_messages = [m for m in context.messages if m.role == "user"]
            reflection_messages = [m for m in user_messages if "Reflect" in m.content]
            assert len(reflection_messages) >= 1

    def test_max_steps_respected(self):
        loop = Reflexion(max_steps=2, reflect_every=1)
        # Provider that always makes tool calls (never finishes)
        provider = ReflexionProvider(tool_steps=100)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            env.write_file("main.py", "hello")

            context = Context()
            context.add(Message.user("Do something"))
            result = loop.run(provider, [ReadFileTool()], context, env)
            assert not result.success
            assert result.steps == 2
            assert "Max steps" in result.error
