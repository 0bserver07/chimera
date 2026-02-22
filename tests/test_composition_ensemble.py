from __future__ import annotations

import tempfile

from chimera.composition.ensemble import Ensemble
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.types import Message


class LabelProvider(Provider):
    def __init__(self, label: str):
        self.label = label
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return Response(content=f"Result from {self.label}", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 10})
    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return False
    @property
    def model_name(self): return self.label


class TestEnsemble:
    def test_all_agents_run(self):
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            assert len(results) == 2
            labels = [r.output for r in results]
            assert any("A" in l for l in labels)
            assert any("B" in l for l in labels)

    def test_best_result(self):
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            best = ensemble.best(results)
            assert best.success

    def test_empty_ensemble(self):
        ensemble = Ensemble([])
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            assert results == []
