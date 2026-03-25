"""Tests for chimera.discipline — phase gates, scope guards, instruction anchoring."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chimera.discipline import (
    BOUNDED_EXPLORATION,
    BOUNDED_RETRY,
    SCOPE_ONLY,
    STRICT,
    VERIFY_FIRST,
    DepthGuard,
    DisciplineViolation,
    Gate,
    GuardResult,
    InstructionAnchor,
    Phase,
    PhasedWorkflow,
    RetryBudgetGuard,
    ScopeGuard,
    VerificationGuard,
)
from chimera.types import AgentResult, Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_agent(result: AgentResult | None = None) -> MagicMock:
    """Return a mock Agent whose .run() returns *result*."""
    agent = MagicMock()
    if result is None:
        result = AgentResult(output="done", steps=1, tool_calls_total=1, cost=0.01, success=True)
    agent.run.return_value = result
    return agent


def _mock_env() -> MagicMock:
    """Return a mock Environment."""
    return MagicMock()


# ===========================================================================
# Phase / Gate tests
# ===========================================================================


class TestGatePasses:
    """test_gate_passes — gate check returns True -> phase advances."""

    def test_gate_passes(self) -> None:
        gate = Gate(name="always_pass", check=lambda: True)
        phase = Phase(number=1, name="explore", goal="Read the codebase", gate=gate)
        workflow = PhasedWorkflow(phases=[phase])

        agent = _mock_agent()
        result = workflow.run(agent, "fix bug", _mock_env())

        assert result.success is True
        assert len(workflow.completed_phases) == 1


class TestGateFailsRetries:
    """test_gate_fails_retries — gate fails -> phase retries up to max."""

    def test_gate_fails_retries(self) -> None:
        call_count = 0

        def eventually_pass() -> bool:
            nonlocal call_count
            call_count += 1
            return call_count >= 3  # Fails first 2 times, passes on 3rd

        gate = Gate(name="eventual", check=eventually_pass)
        phase = Phase(number=1, name="fix", goal="Apply the fix", gate=gate)
        workflow = PhasedWorkflow(phases=[phase], max_retries=2)

        agent = _mock_agent()
        result = workflow.run(agent, "fix bug", _mock_env())

        assert result.success is True
        # Initial run (fail) + 2 retries = 3 calls total.
        assert agent.run.call_count == 3


class TestGateFailsExhausted:
    """test_gate_fails_exhausted — all retries fail -> AgentResult(success=False)."""

    def test_gate_fails_exhausted(self) -> None:
        gate = Gate(name="never_pass", check=lambda: False)
        phase = Phase(number=1, name="fix", goal="Apply the fix", gate=gate)
        workflow = PhasedWorkflow(phases=[phase], max_retries=2)

        agent = _mock_agent()
        result = workflow.run(agent, "fix bug", _mock_env())

        assert result.success is False
        assert "never_pass" in (result.error or "")
        # Initial run + 2 retries = 3 calls.
        assert agent.run.call_count == 3


class TestPhasedWorkflowSequential:
    """test_phased_workflow_sequential — phases execute in order."""

    def test_phased_workflow_sequential(self) -> None:
        order: list[int] = []

        def make_gate(n: int) -> Gate:
            def check() -> bool:
                order.append(n)
                return True
            return Gate(name=f"gate_{n}", check=check)

        phases = [
            Phase(number=1, name="understand", goal="Read code", gate=make_gate(1)),
            Phase(number=2, name="plan", goal="Plan changes", gate=make_gate(2)),
            Phase(number=3, name="implement", goal="Write code", gate=make_gate(3)),
        ]
        workflow = PhasedWorkflow(phases=phases)
        agent = _mock_agent()
        result = workflow.run(agent, "refactor module", _mock_env())

        assert result.success is True
        assert order == [1, 2, 3]
        assert len(workflow.completed_phases) == 3


class TestPhasedWorkflowReadOnly:
    """test_phased_workflow_read_only — read_only phase excludes write tools."""

    def test_phased_workflow_read_only(self) -> None:
        gate = Gate(name="pass", check=lambda: True)
        phase = Phase(number=1, name="explore", goal="Read only", gate=gate, read_only=True)

        assert phase.read_only is True

        workflow = PhasedWorkflow(phases=[phase])
        agent = _mock_agent()
        result = workflow.run(agent, "explore", _mock_env())

        assert result.success is True
        # The task prefix includes phase info.
        call_args = agent.run.call_args
        assert "Phase 1" in call_args[0][0]


# ===========================================================================
# Guard tests
# ===========================================================================


class TestScopeGuardInScope:
    """test_scope_guard_in_scope — file in task_files -> allowed."""

    def test_scope_guard_in_scope(self) -> None:
        guard = ScopeGuard(task_files={"src/main.py", "src/utils.py"})
        result = guard.check("write_file", {"file_path": "src/main.py"})
        assert result.allowed is True


class TestScopeGuardOutOfScope:
    """test_scope_guard_out_of_scope — file not in task_files -> warning."""

    def test_scope_guard_out_of_scope(self) -> None:
        guard = ScopeGuard(task_files={"src/main.py"})
        result = guard.check("write_file", {"file_path": "src/other.py"})
        assert result.allowed is False
        assert result.severity == "warning"
        assert "outside task scope" in result.reason


class TestScopeGuardBlockSeverity:
    """test_scope_guard_block_severity — severity='block' -> raises DisciplineViolation."""

    def test_scope_guard_block_severity(self) -> None:
        guard = ScopeGuard(task_files={"src/main.py"}, severity="block")
        result = guard.check("write_file", {"file_path": "src/other.py"})
        assert result.allowed is False
        assert result.severity == "block"

        # Demonstrate that callers raise on block severity.
        if not result.allowed and result.severity == "block":
            with pytest.raises(DisciplineViolation, match="scope"):
                raise DisciplineViolation(guard.name, result.reason)


class TestDepthGuardUnderLimit:
    """test_depth_guard_under_limit — reads under limit -> allowed."""

    def test_depth_guard_under_limit(self) -> None:
        guard = DepthGuard(max_depth=5)
        for _ in range(5):
            result = guard.check("read_file", {})
        assert result.allowed is True


class TestDepthGuardOverLimit:
    """test_depth_guard_over_limit — consecutive reads over limit -> warning."""

    def test_depth_guard_over_limit(self) -> None:
        guard = DepthGuard(max_depth=3)
        for _ in range(3):
            result = guard.check("read_file", {})
        assert result.allowed is True  # At limit is fine.

        result = guard.check("read_file", {})
        assert result.allowed is False
        assert result.severity == "warning"
        assert "consecutive reads" in result.reason


class TestDepthGuardResetsOnWrite:
    """test_depth_guard_resets_on_write — write resets counter."""

    def test_depth_guard_resets_on_write(self) -> None:
        guard = DepthGuard(max_depth=3)
        for _ in range(3):
            guard.check("read_file", {})

        # Write resets counter.
        result = guard.check("write_file", {})
        assert result.allowed is True

        # Now we can read 3 more times without warning.
        for _ in range(3):
            result = guard.check("read_file", {})
        assert result.allowed is True


class TestVerificationGuardNoTests:
    """test_verification_guard_no_tests — no test runs -> not allowed."""

    def test_verification_guard_no_tests(self) -> None:
        guard = VerificationGuard()
        result = guard.check("done", {})
        assert result.allowed is False
        assert "No test execution" in result.reason


class TestVerificationGuardTestsRan:
    """test_verification_guard_tests_ran — test run detected -> allowed."""

    def test_verification_guard_tests_ran(self) -> None:
        guard = VerificationGuard()
        guard.check("bash", {"command": "uv run pytest tests/"})
        result = guard.check("done", {})
        assert result.allowed is True


class TestRetryBudgetUnderLimit:
    """test_retry_budget_under_limit — retries under limit -> allowed."""

    def test_retry_budget_under_limit(self) -> None:
        guard = RetryBudgetGuard(max_retries=3)
        ctx = {"file_path": "src/main.py", "change": "fix bug"}
        for _ in range(3):
            result = guard.check("edit_file", ctx)
        assert result.allowed is True


class TestRetryBudgetOverLimit:
    """test_retry_budget_over_limit — similar edits over limit -> warning."""

    def test_retry_budget_over_limit(self) -> None:
        guard = RetryBudgetGuard(max_retries=3)
        ctx = {"file_path": "src/main.py", "change": "fix bug"}
        for _ in range(3):
            guard.check("edit_file", ctx)

        result = guard.check("edit_file", ctx)
        assert result.allowed is False
        assert result.severity == "warning"
        assert "Same edit" in result.reason


# ===========================================================================
# InstructionAnchor tests
# ===========================================================================


class TestAnchorShouldInjectInterval:
    """test_anchor_should_inject_interval — injects at interval."""

    def test_anchor_should_inject_interval(self) -> None:
        anchor = InstructionAnchor(["Stay focused", "Test first"], interval=5)

        # Not at interval.
        context: list[Message] = [Message.user("hello")]
        assert anchor.should_inject(1, context) is False
        assert anchor.should_inject(3, context) is False
        assert anchor.should_inject(4, context) is False

        # At interval.
        assert anchor.should_inject(5, context) is True
        assert anchor.should_inject(10, context) is True


class TestAnchorSkipsIfPresent:
    """test_anchor_skips_if_present — instructions still in context -> skip."""

    def test_anchor_skips_if_present(self) -> None:
        anchor = InstructionAnchor(["Stay focused"], interval=5)

        # Marker present in recent context.
        context = [
            Message.user("hello"),
            Message.assistant("working on it"),
            Message.system("--- INSTRUCTION ANCHOR ---\nStay focused"),
        ]
        assert anchor.should_inject(5, context) is False


class TestAnchorFormat:
    """test_anchor_format — get_injection returns formatted string."""

    def test_anchor_format(self) -> None:
        anchor = InstructionAnchor(["Stay focused", "Test first"], interval=5)
        injection = anchor.get_injection()

        assert "--- INSTRUCTION ANCHOR ---" in injection
        assert "Stay focused" in injection
        assert "Test first" in injection
        # Marker on first line, then instructions.
        lines = injection.split("\n")
        assert lines[0] == "--- INSTRUCTION ANCHOR ---"
        assert lines[1] == "Stay focused"
        assert lines[2] == "Test first"


# ===========================================================================
# Pattern composition tests
# ===========================================================================


class TestPatternComposition:
    """test_pattern_composition — SCOPE_ONLY + VERIFY_FIRST stacks."""

    def test_pattern_composition(self) -> None:
        combined = SCOPE_ONLY + VERIFY_FIRST
        assert len(combined) == 2
        assert isinstance(combined[0], ScopeGuard)
        assert isinstance(combined[1], VerificationGuard)

    def test_strict_has_all_guards(self) -> None:
        assert len(STRICT) == 4
        types = {type(g) for g in STRICT}
        assert types == {ScopeGuard, VerificationGuard, RetryBudgetGuard, DepthGuard}

    def test_bounded_patterns(self) -> None:
        assert len(BOUNDED_RETRY) == 1
        assert isinstance(BOUNDED_RETRY[0], RetryBudgetGuard)
        assert len(BOUNDED_EXPLORATION) == 1
        assert isinstance(BOUNDED_EXPLORATION[0], DepthGuard)

    def test_three_way_composition(self) -> None:
        combined = SCOPE_ONLY + VERIFY_FIRST + BOUNDED_RETRY
        assert len(combined) == 3


# ===========================================================================
# LoopConfig integration
# ===========================================================================


class TestLoopConfigFields:
    """Verify LoopConfig accepts discipline and instruction_anchor fields."""

    def test_loopconfig_discipline_field(self) -> None:
        from chimera.core.loop_config import LoopConfig

        config = LoopConfig(discipline=STRICT)
        assert config.discipline is not None
        assert len(config.discipline) == 4

    def test_loopconfig_anchor_field(self) -> None:
        from chimera.core.loop_config import LoopConfig

        anchor = InstructionAnchor(["Stay on task"], interval=10)
        config = LoopConfig(instruction_anchor=anchor)
        assert config.instruction_anchor is anchor

    def test_loopconfig_defaults_none(self) -> None:
        from chimera.core.loop_config import LoopConfig

        config = LoopConfig()
        assert config.discipline is None
        assert config.instruction_anchor is None


# ===========================================================================
# Guard edge cases
# ===========================================================================


class TestScopeGuardEdgeCases:
    """Additional edge-case coverage for ScopeGuard."""

    def test_no_task_files_always_allows(self) -> None:
        guard = ScopeGuard(task_files=None)
        result = guard.check("write_file", {"file_path": "anything.py"})
        assert result.allowed is True

    def test_read_action_always_allowed(self) -> None:
        guard = ScopeGuard(task_files={"src/main.py"})
        result = guard.check("read_file", {"file_path": "src/other.py"})
        assert result.allowed is True

    def test_no_file_path_in_context(self) -> None:
        guard = ScopeGuard(task_files={"src/main.py"})
        result = guard.check("write_file", {})
        assert result.allowed is True


class TestDepthGuardEdgeCases:
    """Additional edge cases for DepthGuard."""

    def test_non_read_non_write_ignored(self) -> None:
        guard = DepthGuard(max_depth=2)
        for _ in range(10):
            result = guard.check("think", {})
        assert result.allowed is True

    def test_bash_resets_counter(self) -> None:
        guard = DepthGuard(max_depth=2)
        guard.check("read_file", {})
        guard.check("read_file", {})
        guard.check("bash", {})  # Reset
        result = guard.check("read_file", {})
        assert result.allowed is True


class TestVerificationGuardEdgeCases:
    """Additional edge cases for VerificationGuard."""

    def test_unittest_detected(self) -> None:
        guard = VerificationGuard()
        guard.check("bash", {"command": "python -m unittest discover"})
        result = guard.check("done", {})
        assert result.allowed is True

    def test_non_done_always_allowed(self) -> None:
        guard = VerificationGuard()
        result = guard.check("write_file", {})
        assert result.allowed is True


class TestRetryBudgetEdgeCases:
    """Additional edge cases for RetryBudgetGuard."""

    def test_different_edits_allowed(self) -> None:
        guard = RetryBudgetGuard(max_retries=2)
        guard.check("edit_file", {"file_path": "a.py", "change": "fix1"})
        guard.check("edit_file", {"file_path": "a.py", "change": "fix1"})
        # Same file, different change is a different signature.
        result = guard.check("edit_file", {"file_path": "a.py", "change": "fix2"})
        assert result.allowed is True

    def test_non_edit_action_ignored(self) -> None:
        guard = RetryBudgetGuard(max_retries=1)
        for _ in range(10):
            result = guard.check("read_file", {"file_path": "a.py"})
        assert result.allowed is True
