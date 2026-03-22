"""Tests for chimera.eval.prompt_lab — prompt engineering lab."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chimera.eval.prompt_lab import PromptLab, PromptReport
from chimera.eval.comparative import TaskResult


# -- Helpers ------------------------------------------------------------------


@dataclass
class FakeAgentResult:
    output: str
    cost: float
    steps: int


class FakeAgent:
    """Agent whose output depends on the system prompt it was built with."""

    def __init__(self, system_prompt: str):
        self._system_prompt = system_prompt
        self.cost = 0.01
        self.steps = 2

    def run(self, task: str, env: Any) -> FakeAgentResult:
        # Simulate prompt quality: if "good" is in the system prompt,
        # include "ok" in output; otherwise produce a failing output.
        if "good" in self._system_prompt:
            return FakeAgentResult(output="ok result", cost=self.cost, steps=self.steps)
        return FakeAgentResult(output="bad result", cost=self.cost, steps=self.steps)


class FakeProvider:
    """Minimal provider stub."""

    pass


def fake_agent_factory(provider: Any, system_prompt: str) -> FakeAgent:
    """Build a FakeAgent parameterised by system prompt."""
    return FakeAgent(system_prompt)


# -- Tests --------------------------------------------------------------------


class TestPromptLab:
    def test_add_prompts(self):
        """add_prompt registers named prompt variants for later execution."""
        problems = [{"id": "p1", "prompt": "do X"}]
        lab = PromptLab(FakeProvider(), fake_agent_factory, problems)

        lab.add_prompt("concise", "Be concise.")
        lab.add_prompt("verbose", "Be verbose.")

        assert "concise" in lab._prompts
        assert "verbose" in lab._prompts
        assert lab._prompts["concise"] == "Be concise."

    def test_run_with_mock_provider(self):
        """run() executes all problems through all prompt variants."""
        problems = [
            {"id": "p1", "prompt": "task A", "expected": "ok"},
            {"id": "p2", "prompt": "task B", "expected": "ok"},
        ]
        lab = PromptLab(FakeProvider(), fake_agent_factory, problems)
        lab.add_prompt("good_prompt", "You are a good assistant.")
        lab.add_prompt("bad_prompt", "You are a bad assistant.")

        report = lab.run()

        assert isinstance(report, PromptReport)
        # "good_prompt" contains "good" so FakeAgent returns "ok result"
        assert all(r.passed for r in report.results["good_prompt"])
        # "bad_prompt" does not contain "good", output is "bad result"
        assert all(not r.passed for r in report.results["bad_prompt"])
        # Each prompt should have results for both problems
        assert len(report.results["good_prompt"]) == 2
        assert len(report.results["bad_prompt"]) == 2

    def test_best_prompt_selection(self):
        """best_prompt() returns the prompt with the highest pass rate."""
        results = {
            "weak": [
                TaskResult("p1", "x", 0.01, 2, False),
                TaskResult("p2", "x", 0.01, 2, False),
            ],
            "strong": [
                TaskResult("p1", "ok", 0.01, 2, True),
                TaskResult("p2", "ok", 0.01, 2, True),
            ],
        }
        report = PromptReport(results=results)
        assert report.best_prompt() == "strong"

    def test_prompt_report_summary(self):
        """summary() returns a formatted string with per-prompt stats."""
        results = {
            "alpha": [
                TaskResult("p1", "ok", 0.02, 4, True),
                TaskResult("p2", "ok", 0.04, 6, True),
            ],
            "beta": [
                TaskResult("p1", "nope", 0.01, 2, False),
                TaskResult("p2", "ok", 0.03, 3, True),
            ],
        }
        report = PromptReport(results=results)
        summary = report.summary()

        assert "Prompt Lab Summary" in summary
        assert "alpha" in summary
        assert "beta" in summary
        assert "pass_rate=100.0%" in summary
        assert "pass_rate=50.0%" in summary
