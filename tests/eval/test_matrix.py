"""run_matrix over fake runners × fake benchmarks (no LLM, no network).

Drives the real :func:`~chimera.eval.matrix.run_matrix` (which reuses the real
:class:`~chimera.eval.harness.Harness`) with deterministic fakes so the grid
shape, pass-rate maths, summary rendering, and per-cell error isolation are
verified without a model.
"""

from __future__ import annotations

import re
from typing import Any

from chimera.eval.harness import Benchmark
from chimera.eval.matrix import MatrixCell, MatrixReport, run_matrix
from chimera.eval.runners.base import AgentRunResult


class FakeRunner:
    """An :class:`AgentRunner` whose answer is fixed for every task."""

    def __init__(self, id: str, answer: str) -> None:
        self.id = id
        self._answer = answer

    def run(self, task: Any, env: Any = None, budget: Any = None) -> AgentRunResult:
        return AgentRunResult(
            answer=self._answer,
            tool_calls=3,
            llm_calls=2,
            cost_usd=0.01,
            wall_clock_sec=0.5,
            status="completed",
        )


class FakeBenchmark(Benchmark):
    """Two trivial tasks; a task passes iff the agent output equals *golden*."""

    def __init__(self, name: str, golden: str) -> None:
        self._name = name
        self._golden = golden

    def name(self) -> str:
        return self._name

    def tasks(self) -> list[dict[str, Any]]:
        return [
            {"id": f"{self._name}-1", "prompt": "solve one"},
            {"id": f"{self._name}-2", "prompt": "solve two"},
        ]

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        return agent_output == self._golden


def test_run_matrix_builds_full_grid() -> None:
    runners = [FakeRunner("alpha", "GOLD"), FakeRunner("beta", "WRONG")]
    benches = [FakeBenchmark("benchA", "GOLD"), FakeBenchmark("benchB", "GOLD")]

    report = run_matrix(runners, benches, model="glm-5")

    assert isinstance(report, MatrixReport)
    assert report.model == "glm-5"
    assert len(report.cells) == 4  # 2 agents × 2 benchmarks
    assert all(isinstance(c, MatrixCell) for c in report.cells)

    by_agent = report.by_agent()
    # alpha answers GOLD -> passes both tasks on both benchmarks.
    assert by_agent["alpha"]["benchA"].pass_rate == 1.0
    assert by_agent["alpha"]["benchA"].total == 2
    assert by_agent["alpha"]["benchB"].passed == 2
    # beta answers WRONG -> 0% on every column.
    assert by_agent["beta"]["benchA"].pass_rate == 0.0
    assert by_agent["beta"]["benchB"].passed == 0
    # Runner-native counters flow onto the cell.
    assert by_agent["alpha"]["benchA"].tool_calls == 3
    assert by_agent["alpha"]["benchA"].status == "completed"
    assert by_agent["alpha"]["benchA"].cost_usd == 0.02  # 2 tasks × $0.01


def test_matrix_summary_contains_agents_and_benchmarks() -> None:
    runners = [FakeRunner("alpha", "GOLD"), FakeRunner("beta", "GOLD")]
    benches = [FakeBenchmark("benchA", "GOLD"), FakeBenchmark("benchB", "GOLD")]

    text = run_matrix(runners, benches, model="glm-5").summary()

    for token in ("alpha", "beta", "benchA", "benchB"):
        assert token in text


def test_best_per_benchmark_picks_top_agent() -> None:
    runners = [FakeRunner("alpha", "GOLD"), FakeRunner("beta", "WRONG")]
    benches = [FakeBenchmark("benchA", "GOLD")]

    report = run_matrix(runners, benches)

    assert report.best_per_benchmark() == {"benchA": "alpha"}


def test_failing_cell_becomes_error_and_does_not_abort_grid() -> None:
    class BoomRunner:
        id = "boom"

        def run(self, task: Any, env: Any = None, budget: Any = None) -> AgentRunResult:
            raise RuntimeError("kaboom")

    runners: list[Any] = [BoomRunner(), FakeRunner("ok", "GOLD")]
    benches = [FakeBenchmark("benchA", "GOLD")]

    report = run_matrix(runners, benches)
    by_agent = report.by_agent()

    assert by_agent["boom"]["benchA"].status == "error"
    assert by_agent["boom"]["benchA"].budget_honored is False
    assert "kaboom" in by_agent["boom"]["benchA"].budget_note
    # The healthy runner still ran — one bad cell did not abort the sweep.
    assert by_agent["ok"]["benchA"].pass_rate == 1.0


class _PromptCapturingRunner:
    """Records every prompt it is driven with; always completes."""

    def __init__(self) -> None:
        self.id = "capture"
        self.prompts: list[str] = []

    def run(self, task: Any, env: Any = None, budget: Any = None) -> AgentRunResult:
        self.prompts.append(str(task))
        return AgentRunResult(answer="x", status="completed")


def test_answer_contract_appended_by_default() -> None:
    from chimera.eval.matrix import FINAL_ANSWER_CONTRACT

    runner = _PromptCapturingRunner()
    run_matrix([runner], [FakeBenchmark("benchC", "x")])

    assert runner.prompts, "runner was never driven"
    assert all(p.endswith(FINAL_ANSWER_CONTRACT) for p in runner.prompts)
    # The original task prompt is preserved in front of the suffix.
    assert runner.prompts[0].startswith("solve one")


def test_answer_contract_off_leaves_prompt_untouched() -> None:
    runner = _PromptCapturingRunner()
    run_matrix([runner], [FakeBenchmark("benchC", "x")], answer_contract=False)

    assert runner.prompts == ["solve one", "solve two"]


# --------------------------------------------------------------------------- #
# GAP A — env-artifact harvesting (file-artifact agents made gradeable).
# --------------------------------------------------------------------------- #
class _ExecBenchmark(Benchmark):
    """Answer-graded like HumanEval: extract fenced code, exec, check RESULT.

    A task passes iff the (fence-extracted) answer executes and binds
    ``RESULT == 42`` — so an agent that writes its solution to a file and ends
    on prose only passes once the harvest folds that file into the answer.
    """

    _FENCE = re.compile(r"```(?:python)?\s*\n?(.*?)```", re.DOTALL)

    def __init__(self, name: str = "exec-bench") -> None:
        self._name = name

    def name(self) -> str:
        return self._name

    def tasks(self) -> list[dict[str, Any]]:
        return [{"id": "t1", "prompt": "make RESULT equal 42"}]

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        blocks = self._FENCE.findall(agent_output)
        code = "\n\n".join(b.strip("\n") for b in blocks) if blocks else agent_output
        namespace: dict[str, Any] = {}
        try:
            exec(code, namespace)  # noqa: S102 — evaluating agent-produced code is the point
        except Exception:
            return False
        return namespace.get("RESULT") == 42


class _FileWritingRunner:
    """Writes its solution to a workspace file and ends on prose (no fence)."""

    def __init__(self, id: str = "filewriter", filename: str = "impl.py") -> None:
        self.id = id
        self.filename = filename
        self.last: AgentRunResult | None = None

    def run(self, task: Any, env: Any = None, budget: Any = None) -> AgentRunResult:
        if env is not None:
            env.write_file(self.filename, "RESULT = 42\n")
        self.last = AgentRunResult(
            answer="I implemented the solution in the workspace.",
            status="completed",
        )
        return self.last


def _tmp_env_factory(tmp_path: Any) -> Any:
    import tempfile

    from chimera.env.local import LocalEnvironment

    def factory() -> LocalEnvironment:
        return LocalEnvironment(workdir=tempfile.mkdtemp(dir=str(tmp_path)))

    return factory


def test_harvest_makes_file_writing_agent_gradeable(tmp_path: Any) -> None:
    runner = _FileWritingRunner()
    report = run_matrix(
        [runner], [_ExecBenchmark()], env_factory=_tmp_env_factory(tmp_path)
    )

    cell = report.by_agent()[runner.id]["exec-bench"]
    assert cell.pass_rate == 1.0  # prose answer scored 0 without the harvest
    # Honesty: the harvested file is recorded on the runner result's raw.
    assert runner.last is not None
    assert runner.last.raw.get("harvested_files") == ["impl.py"]


def test_harvest_off_leaves_answer_ungradeable(tmp_path: Any) -> None:
    runner = _FileWritingRunner()
    report = run_matrix(
        [runner],
        [_ExecBenchmark()],
        env_factory=_tmp_env_factory(tmp_path),
        harvest_env_artifacts=False,
    )

    cell = report.by_agent()[runner.id]["exec-bench"]
    assert cell.pass_rate == 0.0  # prose stays prose; nothing to grade
    assert runner.last is not None
    assert runner.last.raw.get("harvested_files") is None


def test_harvest_noop_when_answer_already_has_fence(tmp_path: Any) -> None:
    # The answer already carries the gradeable artifact; a decoy file on disk
    # must NOT be appended (that would clobber the real answer with 999).
    class _FencedRunner:
        id = "fenced"

        def __init__(self) -> None:
            self.last: AgentRunResult | None = None

        def run(self, task: Any, env: Any = None, budget: Any = None) -> AgentRunResult:
            if env is not None:
                env.write_file("decoy.py", "RESULT = 999\n")
            self.last = AgentRunResult(
                answer="```python\nRESULT = 42\n```", status="completed"
            )
            return self.last

    runner = _FencedRunner()
    report = run_matrix(
        [runner], [_ExecBenchmark()], env_factory=_tmp_env_factory(tmp_path)
    )

    cell = report.by_agent()["fenced"]["exec-bench"]
    assert cell.pass_rate == 1.0  # graded from the fenced answer (42), not the decoy
    assert runner.last is not None
    assert runner.last.raw.get("harvested_files") is None


def test_harvest_noop_without_env() -> None:
    runner = _FileWritingRunner()
    report = run_matrix([runner], [_ExecBenchmark()])  # no env_factory -> env is None

    cell = report.by_agent()[runner.id]["exec-bench"]
    assert cell.pass_rate == 0.0
    assert runner.last is not None
    assert runner.last.raw.get("harvested_files") is None


def test_has_code_fence() -> None:
    from chimera.eval.matrix import _has_code_fence

    assert _has_code_fence("```python\nx = 1\n```")
    assert _has_code_fence("prefix ```x``` suffix")
    assert not _has_code_fence("just prose, no code")
    assert not _has_code_fence("")


def test_harvest_env_code_reads_single_py_file(tmp_path: Any) -> None:
    from chimera.env.local import LocalEnvironment
    from chimera.eval.matrix import _harvest_env_code

    env = LocalEnvironment(workdir=str(tmp_path))
    env.setup()
    env.write_file("sol.py", "def f():\n    return 1\n")

    appendix, names = _harvest_env_code(env)
    assert names == ["sol.py"]
    assert appendix.startswith("```python")
    assert "def f():" in appendix


def test_harvest_env_code_skips_when_too_many_files(tmp_path: Any) -> None:
    from chimera.env.local import LocalEnvironment
    from chimera.eval.matrix import _MAX_HARVEST_FILES, _harvest_env_code

    env = LocalEnvironment(workdir=str(tmp_path))
    env.setup()
    for i in range(_MAX_HARVEST_FILES + 1):
        env.write_file(f"m{i}.py", "x = 1\n")

    assert _harvest_env_code(env) == ("", [])


def test_harvest_env_code_skips_dotpaths_and_non_python(tmp_path: Any) -> None:
    from chimera.env.local import LocalEnvironment
    from chimera.eval.matrix import _harvest_env_code

    env = LocalEnvironment(workdir=str(tmp_path))
    env.setup()
    env.write_file("real.py", "R = 1\n")
    env.write_file("_stdin.txt", "5\n")  # grader scratch, not .py
    env.write_file(".hidden/mod.py", "H = 1\n")  # dot-path scratch

    appendix, names = _harvest_env_code(env)
    assert names == ["real.py"]
    assert "R = 1" in appendix
