from __future__ import annotations

import tempfile

from chimera.core.loops.tree_of_thought import TreeOfThought
from chimera.core.context import Context
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.tools.read import ReadFileTool
from chimera.types import Message, ToolCall


class DeterministicProvider(Provider):
    """Returns the same response each time (no tool calls)."""
    def __init__(self, response_text: str = "The answer is 42"):
        self.response_text = response_text
        self.call_count = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self.call_count += 1
        return Response(
            content=self.response_text,
            tool_calls=[],
            usage={"input_tokens": 10, "output_tokens": 10},
        )

    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return False
    @property
    def model_name(self): return "deterministic"


class VariedProvider(Provider):
    """Returns different responses each call, then evaluates."""
    def __init__(self):
        self._call = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._call += 1
        # First N calls are candidate generation, next call is evaluation
        if self._call <= 3:
            return Response(
                content=f"Candidate {self._call}",
                tool_calls=[],
                usage={"input_tokens": 10, "output_tokens": 10},
            )
        # Evaluation call: pick candidate 2
        return Response(
            content="2",
            tool_calls=[],
            usage={"input_tokens": 10, "output_tokens": 5},
        )

    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return False
    @property
    def model_name(self): return "varied"


class ToolUsingProvider(Provider):
    """Returns tool calls on first generation, then finishes."""
    def __init__(self):
        self._call = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._call += 1
        # First candidate generates a tool call
        if self._call <= 3:
            return Response(
                content=f"Using tool (call {self._call})",
                tool_calls=[ToolCall(id=f"t{self._call}", name="read_file", arguments={"path": "main.py"})],
                usage={"input_tokens": 10, "output_tokens": 10},
            )
        # After tool result, finish
        return Response(
            content="Done after tool use",
            tool_calls=[],
            usage={"input_tokens": 10, "output_tokens": 10},
        )

    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return True
    @property
    def model_name(self): return "tool-user"


class TestTreeOfThought:
    def test_identical_candidates_no_evaluation(self):
        """When all candidates are identical, no evaluation call is needed."""
        provider = DeterministicProvider("The answer is 42")
        loop = TreeOfThought(max_steps=5, n_candidates=3)
        context = Context(system="You are helpful")
        context.add(Message.user("What is the answer?"))

        result = loop.run(provider, [], context, None)
        assert result.success
        assert result.output == "The answer is 42"
        # 3 candidate generation calls, no evaluation call needed
        assert provider.call_count == 3

    def test_varied_candidates_evaluation(self):
        """When candidates differ, an evaluation call picks the best."""
        provider = VariedProvider()
        loop = TreeOfThought(max_steps=5, n_candidates=3)
        context = Context(system="You are helpful")
        context.add(Message.user("Think about this"))

        result = loop.run(provider, [], context, None)
        assert result.success
        assert result.output == "Candidate 2"

    def test_tool_calls_executed(self):
        """When candidates have tool calls, they are executed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            env.write_file("main.py", "hello world")

            provider = ToolUsingProvider()
            loop = TreeOfThought(max_steps=5, n_candidates=3)
            context = Context(system="You are helpful")
            context.add(Message.user("Read the file"))

            result = loop.run(provider, [ReadFileTool()], context, env)
            assert result.success
            assert result.tool_calls_total >= 1
