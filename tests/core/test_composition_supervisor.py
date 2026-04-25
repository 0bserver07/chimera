from __future__ import annotations

import tempfile

from chimera.composition.supervisor import Supervisor
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.types import ToolCall


class CoordinatorProvider(Provider):
    """Simulates a supervisor that delegates to workers."""
    def __init__(self):
        self._step = 0
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._step += 1
        if self._step == 1:
            return Response(
                content="I'll delegate to worker_a",
                tool_calls=[ToolCall(id="c1", name="delegate", arguments={"task": "Do the work"})],
                usage={"input_tokens": 100, "output_tokens": 50},
            )
        return Response(content="All done!", tool_calls=[], usage={"input_tokens": 50, "output_tokens": 20})
    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return True
    @property
    def model_name(self): return "coordinator"


class WorkerProvider(Provider):
    def __init__(self, label: str):
        self.label = label
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return Response(content=f"Done by {self.label}", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 10})
    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return False
    @property
    def model_name(self): return self.label


class TestSupervisor:
    def test_supervisor_delegates(self):
        workers = {
            "delegate": Agent(provider=WorkerProvider("A"), loop=ReAct(max_steps=1)),
        }
        supervisor = Supervisor(
            coordinator=Agent(provider=CoordinatorProvider(), loop=ReAct(max_steps=5)),
            workers=workers,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = supervisor.run("Manage the project", env)
            assert result.success

    def test_supervisor_no_workers(self):
        supervisor = Supervisor(
            coordinator=Agent(provider=WorkerProvider("solo"), loop=ReAct(max_steps=1)),
            workers={},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = supervisor.run("Do it yourself", env)
            assert result.success
