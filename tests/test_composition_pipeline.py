from __future__ import annotations

import tempfile

from chimera.composition.pipeline import Pipeline
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.types import Message


class CounterProvider(Provider):
    """Appends a counter to the output."""
    def __init__(self, label: str):
        self.label = label
        self._called = False
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._called = True
        last = messages[-1].content if messages else ""
        return Response(content=f"{last} -> {self.label}", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 10})
    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return False
    @property
    def model_name(self): return f"counter-{self.label}"


class TestPipeline:
    def test_sequential_execution(self):
        agents = [
            Agent(provider=CounterProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=CounterProvider("B"), loop=ReAct(max_steps=1)),
            Agent(provider=CounterProvider("C"), loop=ReAct(max_steps=1)),
        ]
        pipeline = Pipeline(agents)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = pipeline.run("start", env)
            assert result.success
            assert "A" in result.output
            assert "B" in result.output
            assert "C" in result.output

    def test_empty_pipeline(self):
        pipeline = Pipeline([])
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = pipeline.run("input", env)
            assert result.output == "input"

    def test_pipeline_stops_on_failure(self):
        class FailProvider(Provider):
            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                return Response(content="fail", tool_calls=[], usage={"input_tokens": 0, "output_tokens": 0})
            @property
            def context_window(self): return 100_000
            @property
            def supports_tool_use(self): return False
            @property
            def model_name(self): return "fail"

        agents = [
            Agent(provider=CounterProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=CounterProvider("B"), loop=ReAct(max_steps=1)),
        ]
        pipeline = Pipeline(agents)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = pipeline.run("start", env)
            assert result.success
