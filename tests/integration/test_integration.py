"""End-to-end integration tests for the Chimera synthesis loop (Task 17).

These tests wire together all layers -- Provider, Agent, Tools, ReAct loop,
LocalEnvironment, Trainer, Strategy, and Callbacks -- and run real synthesis
against a mock LLM provider (no real API calls).
"""

from __future__ import annotations

import tempfile
from pathlib import Path


from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.tools.read import ReadFileTool
from chimera.tools.write import WriteFileTool
from chimera.tools.bash import BashTool
from chimera.training.spec import Spec
from chimera.training.trainer import Trainer
from chimera.training.strategies.convergence import TestConvergence
from chimera.training.callbacks import HistoryRecorder
from chimera.types import ToolCall


# ---------------------------------------------------------------------------
# Mock providers
# ---------------------------------------------------------------------------

class MockProvider(Provider):
    """Simulates an LLM that writes a calculator module in one shot."""

    def __init__(self) -> None:
        self._call_count = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._call_count += 1

        # Check if the last message is a tool result (meaning we just wrote the file)
        if messages and messages[-1].role == "tool":
            # Tool was executed, we're done
            return Response(
                content="I've written the calculator module. It should pass the tests.",
                tool_calls=[],
                usage={"input_tokens": 100, "output_tokens": 50},
            )

        # First call or new epoch -- write the file
        return Response(
            content="I'll create the calculator module.",
            tool_calls=[
                ToolCall(
                    id=f"call_{self._call_count}",
                    name="write_file",
                    arguments={
                        "path": "calc.py",
                        "content": (
                            "def add(a, b):\n"
                            "    return a + b\n"
                            "\n"
                            "def subtract(a, b):\n"
                            "    return a - b\n"
                        ),
                    },
                )
            ],
            usage={"input_tokens": 200, "output_tokens": 100},
        )

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_end_to_end_synthesis():
    """Synthesize a simple calculator module from spec + tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Write the test file
        test_content = (
            "from calc import add, subtract\n"
            "\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
            "\n"
            "def test_subtract():\n"
            "    assert subtract(5, 3) == 2\n"
        )
        Path(tmpdir, "test_calc.py").write_text(test_content)

        # 2. Set up environment
        env = LocalEnvironment(
            workdir=tmpdir,
            test_cmd="python -m pytest test_calc.py -v",
        )
        env.setup()

        # 3. Set up agent
        provider = MockProvider()
        agent = Agent(
            provider=provider,
            tools=[ReadFileTool(), WriteFileTool(), BashTool()],
            loop=ReAct(max_steps=10),
        )

        # 4. Set up trainer
        spec = Spec.from_tests(tmpdir, description="Implement a calculator with add and subtract")
        recorder = HistoryRecorder()
        trainer = Trainer(spec=spec, agent=agent, env=env)

        # 5. Run synthesis
        result = trainer.synthesize(
            strategy=TestConvergence(max_iterations=5, patience=3),
            callbacks=[recorder],
        )

        # 6. Assert convergence
        assert result.converged is True
        assert result.best_pass_rate == 1.0
        assert result.iterations == 1  # Should converge in 1 epoch
        assert len(result.history) == 1

        # 7. Verify the generated code exists and is correct
        calc_content = Path(tmpdir, "calc.py").read_text()
        assert "def add" in calc_content
        assert "def subtract" in calc_content

        # 8. Verify callbacks were called
        assert recorder.started is True
        assert recorder.finished is True
        assert recorder.final_result is not None
        assert recorder.final_result.converged is True


def test_end_to_end_gradual_convergence():
    """Test synthesis that takes multiple epochs to converge."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_content = (
            "from calc import add, subtract\n"
            "\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
            "\n"
            "def test_subtract():\n"
            "    assert subtract(5, 3) == 2\n"
        )
        Path(tmpdir, "test_calc.py").write_text(test_content)

        env = LocalEnvironment(workdir=tmpdir, test_cmd="python -m pytest test_calc.py -v")
        env.setup()

        # Provider that writes broken code first, then correct code.
        # Key insight: the ReAct loop calls complete() multiple times per
        # epoch (once to get the tool call, once after the tool result).
        # We track epochs by detecting non-tool-result first calls.
        class GradualProvider(Provider):
            def __init__(self) -> None:
                self._epoch = 0
                self._waiting_for_tool_result = False

            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                # After a tool result, signal "done" so the ReAct loop ends
                if messages and messages[-1].role == "tool":
                    self._waiting_for_tool_result = False
                    return Response(
                        content="Done.",
                        tool_calls=[],
                        usage={"input_tokens": 50, "output_tokens": 20},
                    )

                # This is a new epoch (first call from the agent for this epoch)
                if not self._waiting_for_tool_result:
                    self._epoch += 1
                    self._waiting_for_tool_result = True

                if self._epoch == 1:
                    # First epoch: broken code (add works, subtract doesn't)
                    code = (
                        "def add(a, b):\n"
                        "    return a + b\n"
                        "\n"
                        "def subtract(a, b):\n"
                        "    return a  # BUG\n"
                    )
                else:
                    # Second epoch onward: fixed code
                    code = (
                        "def add(a, b):\n"
                        "    return a + b\n"
                        "\n"
                        "def subtract(a, b):\n"
                        "    return a - b\n"
                    )

                return Response(
                    content="Writing code.",
                    tool_calls=[
                        ToolCall(
                            id=f"c{self._epoch}",
                            name="write_file",
                            arguments={"path": "calc.py", "content": code},
                        )
                    ],
                    usage={"input_tokens": 100, "output_tokens": 50},
                )

            @property
            def context_window(self) -> int:
                return 200_000

            @property
            def supports_tool_use(self) -> bool:
                return True

            @property
            def model_name(self) -> str:
                return "gradual-mock"

        agent = Agent(
            provider=GradualProvider(),
            tools=[ReadFileTool(), WriteFileTool(), BashTool()],
            loop=ReAct(max_steps=10),
        )

        trainer = Trainer(
            spec=Spec.from_tests(tmpdir, "Implement calculator"),
            agent=agent,
            env=env,
        )

        result = trainer.synthesize(strategy=TestConvergence(max_iterations=5, patience=3))

        assert result.converged is True
        assert result.iterations == 2  # Takes 2 epochs
        assert result.best_pass_rate == 1.0
        assert len(result.history) == 2
        assert result.history[0].pass_rate < 1.0  # First epoch partial
        assert result.history[1].pass_rate == 1.0  # Second epoch converged


def test_synthesize_one_liner():
    """Test that chimera.synthesize is importable from the top-level package."""
    import chimera
    assert hasattr(chimera, "synthesize")
    assert callable(chimera.synthesize)


def test_cost_propagates_through_synthesis():
    """Verify that costs from provider usage flow through to SynthesisResult."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_content = (
            "from calc import add, subtract\n"
            "\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
            "\n"
            "def test_subtract():\n"
            "    assert subtract(5, 3) == 2\n"
        )
        Path(tmpdir, "test_calc.py").write_text(test_content)

        env = LocalEnvironment(workdir=tmpdir, test_cmd="python -m pytest test_calc.py -v")
        env.setup()

        agent = Agent(
            provider=MockProvider(),
            tools=[ReadFileTool(), WriteFileTool(), BashTool()],
            loop=ReAct(max_steps=10),
        )
        trainer = Trainer(
            spec=Spec.from_tests(tmpdir, "Implement calculator"),
            agent=agent,
            env=env,
        )
        result = trainer.synthesize(strategy=TestConvergence(max_iterations=5, patience=3))

        assert result.converged is True
        assert result.total_cost > 0  # Costs now flow through
        assert result.history[0].cost > 0
