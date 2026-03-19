"""Live integration tests for SWE-bench and HumanEval benchmarks.

All tests skip when ANTHROPIC_AUTH_TOKEN (or ANTHROPIC_API_KEY) is not set.

Run with:
    ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic" \
    ANTHROPIC_AUTH_TOKEN="..." \
    ANTHROPIC_MODEL="glm-5" \
    uv run pytest tests/test_benchmarks_live.py -v --tb=short
"""
from __future__ import annotations

import os

import pytest

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.prompt import Prompt
from chimera.eval.benchmarks.custom import CustomBenchmark
from chimera.eval.benchmarks.human_eval import HumanEval
from chimera.eval.benchmarks.swe_bench import SWEBench, SWEBenchInstance
from chimera.eval.harness import Harness
from chimera.eval.metrics import pass_at_k, resolve_rate
from chimera.providers.factory import create_provider
from chimera.types import Message

_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
_MODEL = os.environ.get("ANTHROPIC_MODEL", "glm-5")

pytestmark = pytest.mark.skipif(
    not _TOKEN,
    reason="ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY not set",
)


@pytest.fixture(scope="module")
def provider():
    return create_provider(model=_MODEL)


# ---------------------------------------------------------------------------
# SWE-bench: fix-a-typo task
# ---------------------------------------------------------------------------


class TestSWEBenchLive:
    """SWE-bench integration tests against a real LLM."""

    def test_swe_bench_solves_easy_problem(self, provider):
        """Agent should fix a trivial typo bug when given a clear problem statement."""
        agent = Agent(
            provider=provider,
            tools=[],
            loop=ReAct(max_steps=5),
            prompt=Prompt.from_string(
                "You are a bug-fix agent. Output ONLY the corrected Python code. "
                "No markdown fences, no explanation."
            ),
            name="swe-fix",
        )

        problem = (
            "The function `greet` has a typo — it says 'Helo' instead of 'Hello'.\n\n"
            "```python\n"
            "def greet(name):\n"
            '    return f"Helo, {name}!"\n'
            "```\n\n"
            "Output the corrected function."
        )

        result = agent.run(problem, env=None)
        assert result.success

        # Extract code from output
        code = result.output.strip()
        if "```python" in code:
            code = code.split("```python", 1)[1].split("```", 1)[0]
        elif "```" in code:
            code = code.split("```", 1)[1].split("```", 1)[0]

        # Verify the fix
        namespace: dict = {}
        exec(code.strip(), namespace)  # noqa: S102
        assert namespace["greet"]("World") == "Hello, World!"

    def test_swe_bench_adapter_with_inline_data(self, provider):
        """SWEBench adapter loads programmatically-added instances and produces tasks."""
        bench = SWEBench()
        bench.add_instance(SWEBenchInstance(
            instance_id="test__typo_fix",
            repo="test/repo",
            base_commit="abc",
            problem_statement="Fix the typo: 'Helo' -> 'Hello' in greet().",
        ))

        tasks = bench.tasks()
        assert len(tasks) == 1
        assert tasks[0]["id"] == "test__typo_fix"
        assert "Helo" in tasks[0]["prompt"]


# ---------------------------------------------------------------------------
# HumanEval: implement add(a, b)
# ---------------------------------------------------------------------------


class TestHumanEvalLive:
    """HumanEval integration tests against a real LLM."""

    def test_humaneval_solves_easy_problem(self, provider):
        """Provider should correctly implement add(a, b) and pass all tests."""
        func_sig = "def add(a: int, b: int) -> int:"
        prompt = (
            "Complete this function. Output ONLY the complete Python function "
            "definition starting with 'def add'. No markdown, no explanation.\n\n"
            f"{func_sig}\n"
            '    """Return the sum of a and b."""\n'
        )

        messages = [
            Message.system(
                "You are a code completion assistant. Output the complete Python "
                "function definition. No markdown fences, no explanation."
            ),
            Message.user(prompt),
        ]
        response = provider.complete(messages, max_tokens=256)

        code = response.content.strip()
        if "```python" in code:
            code = code.split("```python", 1)[1].split("```", 1)[0]
        elif "```" in code:
            code = code.split("```", 1)[1].split("```", 1)[0]
        code = code.strip()

        # If the LLM returned only the body (no def line), wrap it
        if not code.startswith("def "):
            body = "\n".join(f"    {line}" for line in code.splitlines())
            code = f"{func_sig}\n{body}"

        test_code = (
            "assert add(1, 2) == 3\n"
            "assert add(-1, 1) == 0\n"
            "assert add(0, 0) == 0\n"
            "assert add(100, 200) == 300\n"
        )

        full_code = f"{code}\n\n{test_code}"
        namespace: dict = {}
        exec(full_code, namespace)  # noqa: S102
        # If we reach here, all assertions passed

    def test_humaneval_pass_at_1(self, provider):
        """Run 3 easy HumanEval problems and verify pass@1 >= 0.5."""
        problems = [
            {
                "id": "HE/add",
                "prompt": 'def add(a: int, b: int) -> int:\n    """Return a + b."""\n',
                "test": "assert add(2, 3) == 5\nassert add(-1, 1) == 0\n",
            },
            {
                "id": "HE/max2",
                "prompt": 'def max2(a: int, b: int) -> int:\n    """Return the larger of a and b."""\n',
                "test": "assert max2(1, 2) == 2\nassert max2(5, 3) == 5\nassert max2(4, 4) == 4\n",
            },
            {
                "id": "HE/abs",
                "prompt": 'def absolute(n: int) -> int:\n    """Return the absolute value of n."""\n',
                "test": "assert absolute(-5) == 5\nassert absolute(0) == 0\nassert absolute(3) == 3\n",
            },
        ]

        passed = 0
        for prob in problems:
            messages = [
                Message.system("Output ONLY the Python function. No markdown, no explanation."),
                Message.user(f"Complete this function:\n\n{prob['prompt']}"),
            ]
            response = provider.complete(messages, max_tokens=256)
            code = response.content.strip()
            if "```python" in code:
                code = code.split("```python", 1)[1].split("```", 1)[0]
            elif "```" in code:
                code = code.split("```", 1)[1].split("```", 1)[0]
            try:
                exec(f"{code.strip()}\n\n{prob['test']}", {})  # noqa: S102
                passed += 1
            except Exception:
                pass

        p1 = pass_at_k(n=len(problems), c=passed, k=1)
        assert p1 >= 0.5, f"pass@1 = {p1:.3f}, expected >= 0.5 (passed {passed}/{len(problems)})"


# ---------------------------------------------------------------------------
# Harness + CustomBenchmark
# ---------------------------------------------------------------------------


class _MinimalEnv:
    """Minimal environment stub for CustomBenchmark testing.

    Writes agent output to a file, executes it with a test assertion,
    and returns the test result.
    """

    def __init__(self, test_code: str) -> None:
        self._test_code = test_code
        self._agent_output: str = ""

    def setup(self) -> None:
        pass

    def cleanup(self) -> None:
        pass

    def run_tests(self):
        """Run tests against whatever agent_output was captured."""
        from chimera.types import TestResult

        code = self._agent_output
        if "```python" in code:
            code = code.split("```python", 1)[1].split("```", 1)[0]
        elif "```" in code:
            code = code.split("```", 1)[1].split("```", 1)[0]

        full = f"{code.strip()}\n\n{self._test_code}"
        try:
            exec(full, {})  # noqa: S102
            return TestResult(passed=1, failed=0, errors=0, output="OK")
        except Exception as exc:
            return TestResult(passed=0, failed=1, errors=0, output=str(exc))

    def capture(self, output: str) -> None:
        self._agent_output = output


class _HarnessCapturingBenchmark(CustomBenchmark):
    """CustomBenchmark subclass that captures agent output into envs for testing."""

    def __init__(self, tasks_list: list[dict], envs: dict[str, _MinimalEnv]) -> None:
        super().__init__(tasks_list=tasks_list)
        self._envs = envs

    def evaluate(self, task: dict, agent_output: str, env) -> bool:
        task_id = task.get("id", "")
        if task_id in self._envs:
            self._envs[task_id].capture(agent_output)
        return super().evaluate(task, agent_output, env)


class TestHarnessCustomBenchmark:
    """Integration test: Harness runs a CustomBenchmark end-to-end with a real LLM."""

    def test_harness_runs_custom_benchmark(self, provider):
        """Create a 2-task CustomBenchmark, run through Harness, verify results structure."""
        tasks = [
            {
                "id": "custom_add",
                "prompt": (
                    "Implement this Python function. Output ONLY the code, "
                    "no markdown, no explanation.\n\n"
                    "def add(a, b):\n"
                    '    """Return a + b."""\n'
                ),
            },
            {
                "id": "custom_double",
                "prompt": (
                    "Implement this Python function. Output ONLY the code, "
                    "no markdown, no explanation.\n\n"
                    "def double(x):\n"
                    '    """Return x * 2."""\n'
                ),
            },
        ]

        envs = {
            "custom_add": _MinimalEnv("assert add(2, 3) == 5\nassert add(-1, 1) == 0"),
            "custom_double": _MinimalEnv("assert double(5) == 10\nassert double(0) == 0"),
        }

        benchmark = _HarnessCapturingBenchmark(tasks_list=tasks, envs=envs)

        agent = Agent(
            provider=provider,
            tools=[],
            loop=ReAct(max_steps=3),
            prompt=Prompt.from_string(
                "You are a code completion assistant. Output ONLY raw Python code."
            ),
            name="custom-bench-agent",
        )

        harness = Harness(
            benchmark=benchmark,
            agent=agent,
            env_factory=lambda: envs.get(
                benchmark.tasks()[harness._current_task_idx]["id"],  # type: ignore
                _MinimalEnv(""),
            ) if hasattr(harness, "_current_task_idx") else None,
        )

        # Run manually instead of harness.run() because we need to wire
        # env lookup per-task through our capturing benchmark.
        from chimera.eval.harness import EvalResult, TaskEvalResult

        results: list[TaskEvalResult] = []
        for task in benchmark.tasks():
            task_id = task["id"]
            env = envs[task_id]
            env.setup()
            agent_result = agent.run(task["prompt"], None)
            env.capture(agent_result.output)
            passed = benchmark.evaluate(task, agent_result.output, env)
            env.cleanup()
            results.append(TaskEvalResult(
                task_id=task_id,
                passed=passed,
                output=agent_result.output,
                cost=agent_result.cost,
                steps=agent_result.steps,
            ))

        passed_count = sum(1 for r in results if r.passed)
        total = len(results)
        eval_result = EvalResult(
            benchmark="custom",
            total=total,
            passed=passed_count,
            pass_rate=passed_count / total if total > 0 else 0.0,
            results=results,
            total_cost=sum(r.cost for r in results),
        )

        # Verify result structure
        assert eval_result.benchmark == "custom"
        assert eval_result.total == 2
        assert len(eval_result.results) == 2
        assert eval_result.results[0].task_id == "custom_add"
        assert eval_result.results[1].task_id == "custom_double"

        # These are trivial tasks — at least one should pass
        assert eval_result.passed >= 1, (
            f"Expected at least 1 pass on trivial tasks. "
            f"Results: {[(r.task_id, r.passed, r.output[:60]) for r in eval_result.results]}"
        )

        # Verify metrics
        rr = resolve_rate(eval_result.results)
        assert 0.0 <= rr <= 1.0
        assert eval_result.pass_rate == pytest.approx(rr)
        assert eval_result.total_cost >= 0.0
