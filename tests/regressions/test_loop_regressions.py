"""Real shipped bugs replayed through the hermetic agent-loop harness.

Each test is named for the commit that fixed the bug, states the original
failure mode, and re-runs it through REAL production code (AgentLoop /
CodingAgent / eval Harness / LintFeedbackLoop) with only the model scripted
(:class:`~chimera.providers.faux.FauxProvider`). Reverting the fix makes the
matching test fail again.

These are fast offline regression locks, not validation — the repo rule
stands: a feature is not "done" until verified against a real LLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.core.budget import BudgetedProvider, BudgetEnforcer, BudgetSpec
from chimera.core.loop_events import LoopEventType
from chimera.providers.cost import calculate_cost
from chimera.providers.faux import FauxProvider
from chimera.testing import create_harness

_USAGE = {"input_tokens": 100_000, "output_tokens": 10_000}
_PER_CALL = calculate_cost("glm-5.2", _USAGE)


def _budget_script() -> list[dict[str, Any]]:
    """Two priced turns: one real tool call, then a terminal answer."""
    return [
        {
            "text": "inspecting",
            "tool_calls": [{"name": "list_files", "arguments": {}}],
            "usage": dict(_USAGE),
        },
        {"text": "done", "usage": dict(_USAGE)},
    ]


# ---------------------------------------------------------------------------
# eb87310 — fix(budget): record cost on the async + streaming provider paths
# ---------------------------------------------------------------------------


def test_eb87310_budget_records_cost_on_async_complete_path(tmp_path: Path) -> None:
    """eb87310: ``max_cost`` never tripped for assembled agents (async path).

    Original failure mode: ``BudgetedProvider`` recorded LLM calls/cost only
    in its sync ``complete()``; the assembled ``AgentLoop`` drives
    ``async_complete``, which fell through ``__getattr__`` to the inner
    provider unrecorded — so ``tally.llm_calls``/``cost_usd`` stayed 0 and
    ``max_cost_usd``/``max_llm_calls`` could never trip. Pre-fix, every
    assertion below on the enforcer read zero.
    """
    assert _PER_CALL > 0.0  # guard: the model is priced, the check is real
    faux = FauxProvider(_budget_script(), model="glm-5.2")
    enforcer = BudgetEnforcer(BudgetSpec(max_cost_usd=_PER_CALL * 1.5))
    run = create_harness(
        provider=BudgetedProvider(faux, enforcer),
        workspace=tmp_path,
    ).run("do the task")

    assert run.reason == "completed"
    assert enforcer.tally.llm_calls == 2  # was 0 before eb87310
    assert enforcer.tally.cost_usd == pytest.approx(2 * _PER_CALL)
    assert enforcer.exhausted  # the cost cap trips once the 2nd call lands
    assert (enforcer.exhausted_reason or "").startswith("cost")


def test_eb87310_budget_records_cost_on_async_stream_path(tmp_path: Path) -> None:
    """eb87310: the streaming leg of the same gap.

    Original failure mode: ``async_stream`` also fell through ``__getattr__``
    to the *inner* provider, whose base-class bridge iterates the inner
    ``stream()`` — the wrapper's recording sync ``stream()`` never ran, so
    streamed assembled runs (the default ``coding_agent`` preset streams)
    accrued no llm-call/cost tally either.
    """
    faux = FauxProvider(_budget_script(), model="glm-5.2")
    enforcer = BudgetEnforcer(BudgetSpec(max_llm_calls=2))
    run = create_harness(
        provider=BudgetedProvider(faux, enforcer),
        workspace=tmp_path,
        stream=True,
    ).run("do the task")

    assert run.reason == "completed"
    assert enforcer.tally.llm_calls == 2  # exactly one record per stream
    assert enforcer.tally.cost_usd == pytest.approx(2 * _PER_CALL)
    assert enforcer.exhausted
    assert (enforcer.exhausted_reason or "").startswith("llm_calls")


# ---------------------------------------------------------------------------
# c4840a5 — feat(tui): display-only elision; persistence records FULL output
# ---------------------------------------------------------------------------


def test_c4840a5_persistence_path_records_full_tool_output(tmp_path: Path) -> None:
    """c4840a5: the fixed 1500-char chop leaked display truncation into the record.

    Original failure mode: ``format_event`` unconditionally chopped tool
    output over 1500 chars to ``head[:800] + "… [truncated] …" + tail[-500:]``.
    ``Lane.record`` — the *persistence* caller — went through the same code,
    so every long tool result was silently gutted in the session record
    (R-FOLD-3 violation). The fix made elision an opt-in display keyword;
    persistence callers keep the default and record the full output.

    Replayed with a REAL tool result: a bash ``cat`` of a >1500-char file
    through the real loop, rendered with ``format_event`` persistence
    defaults.
    """
    pytest.importorskip("rich")  # CI installs no tui extra
    from chimera.tui.render import format_event

    line = "L{:04d} " + "x" * 70
    big = "\n".join(line.format(i) for i in range(60))  # ~4600 chars
    assert len(big) > 1500  # guard: over the old chop threshold
    (tmp_path / "big.txt").write_text(big)

    run = create_harness(
        turns=[
            {
                "text": "reading",
                "tool_calls": [{"name": "bash", "arguments": {"command": "cat big.txt"}}],
            },
            {"text": "done"},
        ],
        workspace=tmp_path,
    ).run("show the file")
    (result_ev,) = run.events_of(LoopEventType.tool_result)

    # Persistence path: format_event defaults (elide off, markdown off).
    chunks: list[str] = []
    rendered = format_event(result_ev, chunks)
    recorded = "".join(getattr(r, "plain", str(r)) for r in rendered)

    assert "truncated" not in recorded  # the old chop marker
    assert "L0030" in recorded  # the middle — exactly what the chop dropped
    assert big in recorded  # byte-complete record of the real tool output

    # Sanity: display elision still exists — but only when asked for.
    elided = "".join(
        getattr(r, "plain", str(r)) for r in format_event(result_ev, [], elide=True)
    )
    assert len(elided) < len(recorded)


# ---------------------------------------------------------------------------
# 0275ec3 — fix(eval): errored/empty runs can no longer grade as pass
# ---------------------------------------------------------------------------


def test_0275ec3_errored_empty_run_cannot_grade_as_pass(tmp_path: Path) -> None:
    """0275ec3: 16/39 Modal-grid cells were status=error yet ``passed>0``.

    Original failure mode: the eval Harness graded errored runs
    unconditionally, and lenient benchmark evaluators (HumanEval+'s staged
    test defined-but-never-called ``check``) turned an errored, EMPTY run
    into a pass. The fix guards grading: failed run + empty answer →
    ``passed=False`` without calling ``evaluate()``.

    Replayed through the full production chain — FauxProvider error →
    CodingAgent → AgentLoop → aggregate_events → Harness — with a
    worst-case benchmark whose ``evaluate()`` passes anything.
    """
    from chimera.eval.coding_agent_adapter import CodingAgentAdapter
    from chimera.eval.harness import Benchmark, Harness

    class _AlwaysPassBenchmark(Benchmark):
        """Worst-case lenient grader: everything passes, even nothing."""

        def name(self) -> str:
            return "lenient"

        def tasks(self) -> list[dict[str, Any]]:
            return [{"id": "t1", "prompt": "solve the task"}]

        def evaluate(self, task: dict, agent_output: str, env: Any) -> bool:  # noqa: ARG002
            return True

    class _Env:
        def __init__(self) -> None:
            self.workdir = str(tmp_path)

        def setup(self) -> None:
            pass

        def cleanup(self) -> None:
            pass

    provider = FauxProvider([{"error": "simulated rate limit"}], model="glm-5.2")
    # swebench preset: non-streaming, so the provider error propagates as a
    # real failed run (success=False, empty output) — the pre-fix trap.
    adapter = CodingAgentAdapter(provider, preset="swebench")
    report = Harness(_AlwaysPassBenchmark(), adapter, env_factory=_Env).run()

    assert report.total == 1
    assert report.passed == 0  # pre-0275ec3 this errored, empty run "passed"


# ---------------------------------------------------------------------------
# 9c19e7a — fix(loops): LintFeedbackLoop respects linter exit code
# ---------------------------------------------------------------------------


def test_9c19e7a_lint_loop_trusts_exit_code_not_output(tmp_path: Path) -> None:
    """9c19e7a: successful linters that print output derailed the agent.

    Original failure mode: ``_run_linter`` returned stdout+stderr and the
    loop treated ANY non-empty output as lint errors — but ruff exits 0 on
    success while still printing ("All checks passed!", or a "No Python
    files found" warning on an empty workspace). The bogus "fix these lint
    errors" round derailed the model into lint commentary instead of the
    task. The fix keys on the exit code: 0 → no errors.

    Replayed through the real sync loop with a fake linter that prints on
    exit 0 — pre-fix this consumed extra provider calls for phantom fixes.
    """
    from chimera.core.context import Context
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.loops.lint_feedback import LintFeedbackLoop
    from chimera.env.local import LocalEnvironment
    from chimera.types import Message

    linter = tmp_path / "fakelint"
    linter.write_text("#!/bin/sh\necho 'All checks passed!'\nexit 0\n")
    linter.chmod(0o755)

    provider = FauxProvider([{"text": "the solution"}])
    loop = LintFeedbackLoop(
        inner=ReAct(max_steps=3, config=LoopConfig(yolo_mode=True)),
        linter=str(linter),
        lint_args=[],
    )
    context = Context(system="test")
    context.add(Message.user("write the solution"))
    result = loop.run(provider, [], context, LocalEnvironment(str(tmp_path)))

    assert result.success is True
    assert result.output == "the solution"
    assert provider.call_count == 1  # pre-fix: 1 + up to 3 phantom fix rounds
    assert loop.lint_history == [""]  # exit 0 means clean, whatever it printed


def test_9c19e7a_lint_loop_still_feeds_back_real_failures(tmp_path: Path) -> None:
    """Companion lock: a NON-zero exit still triggers the fix round.

    Guards against over-correcting 9c19e7a into "lint feedback disabled" —
    the loop must still consume a fix turn when the linter genuinely fails.
    """
    from chimera.core.context import Context
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.loops.lint_feedback import LintFeedbackLoop
    from chimera.env.local import LocalEnvironment
    from chimera.types import Message

    linter = tmp_path / "fakelint"
    linter.write_text("#!/bin/sh\necho 'E501 line too long'\nexit 1\n")
    linter.chmod(0o755)

    provider = FauxProvider([{"text": "draft"}, {"text": "fixed"}])
    loop = LintFeedbackLoop(
        inner=ReAct(max_steps=3, config=LoopConfig(yolo_mode=True)),
        linter=str(linter),
        lint_args=[],
        max_lint_rounds=1,
    )
    context = Context(system="test")
    context.add(Message.user("write it"))
    result = loop.run(provider, [], context, LocalEnvironment(str(tmp_path)))

    assert provider.call_count == 2  # the fix round really ran
    assert "E501" in loop.lint_history[0]
    assert result.output == "fixed"
