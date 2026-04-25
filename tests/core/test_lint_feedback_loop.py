"""Tests for LintFeedbackLoop."""

from unittest.mock import MagicMock, patch

from chimera.core.context import Context
from chimera.core.loops.lint_feedback import LintFeedbackLoop
from chimera.types import AgentResult, Message


def _mock_inner(result=None):
    inner = MagicMock()
    if result is None:
        result = AgentResult(
            output="done", steps=1, tool_calls_total=1, cost=0.01, success=True,
        )
    inner.run.return_value = result
    return inner


def test_no_lint_errors():
    """When the linter reports no errors, one lint round is recorded and the
    original result is returned."""
    loop = LintFeedbackLoop(inner=_mock_inner())
    with patch.object(loop, "_run_linter", return_value=""):
        ctx = Context(system="test")
        ctx.add(Message.user("write code"))
        result = loop.run(MagicMock(), [], ctx, None)
        assert result.success
        assert len(loop.lint_history) == 1


def test_lint_errors_trigger_fix():
    """Lint errors should trigger an additional fix run."""
    inner = _mock_inner()
    loop = LintFeedbackLoop(inner=inner, max_lint_rounds=2)
    # First lint: errors, second lint: clean
    with patch.object(loop, "_run_linter", side_effect=["error: unused import\n", ""]):
        ctx = Context(system="test")
        ctx.add(Message.user("write code"))
        loop.run(MagicMock(), [], ctx, None)
        # inner.run called twice: original + fix
        assert inner.run.call_count == 2
        assert len(loop.lint_history) == 2


def test_max_rounds_respected():
    """When lint errors persist, the loop should stop after max_lint_rounds."""
    inner = _mock_inner()
    loop = LintFeedbackLoop(inner=inner, max_lint_rounds=2)
    with patch.object(loop, "_run_linter", return_value="error: always\n"):
        ctx = Context(system="test")
        ctx.add(Message.user("write code"))
        loop.run(MagicMock(), [], ctx, None)
        # 1 original + 2 fix rounds = 3 total
        assert inner.run.call_count == 3


def test_linter_not_found():
    """When the linter binary is not found, _run_linter returns empty string."""
    loop = LintFeedbackLoop(linter="nonexistent_linter_xyz")
    output = loop._run_linter(None)
    assert output == ""


def test_cost_accumulates():
    """Costs from fix rounds should accumulate in the final result."""
    r = AgentResult(
        output="ok", steps=1, tool_calls_total=0, cost=0.01, success=True,
    )
    inner = _mock_inner(r)
    loop = LintFeedbackLoop(inner=inner, max_lint_rounds=1)
    with patch.object(loop, "_run_linter", side_effect=["error\n", ""]):
        ctx = Context(system="test")
        ctx.add(Message.user("write code"))
        result = loop.run(MagicMock(), [], ctx, None)
        assert result.cost == 0.02  # 2 runs x $0.01


def test_steps_accumulate():
    """Steps from fix rounds should accumulate in the final result."""
    r = AgentResult(
        output="ok", steps=3, tool_calls_total=2, cost=0.0, success=True,
    )
    inner = _mock_inner(r)
    loop = LintFeedbackLoop(inner=inner, max_lint_rounds=1)
    with patch.object(loop, "_run_linter", side_effect=["error\n", ""]):
        ctx = Context(system="test")
        ctx.add(Message.user("write code"))
        result = loop.run(MagicMock(), [], ctx, None)
        assert result.steps == 6  # 3 + 3


def test_fix_context_includes_lint_output():
    """The fix context should contain the lint error output."""
    inner = _mock_inner()
    loop = LintFeedbackLoop(inner=inner, max_lint_rounds=1)
    lint_error = "main.py:10:1: F401 'os' imported but unused\n"
    with patch.object(loop, "_run_linter", side_effect=[lint_error, ""]):
        ctx = Context(system="sys prompt")
        ctx.add(Message.user("write code"))
        loop.run(MagicMock(), [], ctx, None)
        # Second call should have a fix context with lint errors
        fix_call_ctx = inner.run.call_args_list[1][0][2]  # third positional arg
        fix_messages = fix_call_ctx.to_messages()
        # System prompt should be preserved
        assert fix_messages[0].content == "sys prompt"
        # User message should contain the lint error
        assert "F401" in fix_messages[1].content
        assert "Do not change functionality" in fix_messages[1].content


def test_iter_steps_delegates():
    """iter_steps should delegate directly to the inner loop."""
    inner = _mock_inner()
    sentinel = object()
    inner.iter_steps.return_value = sentinel
    loop = LintFeedbackLoop(inner=inner)
    result = loop.iter_steps(MagicMock(), [], Context(), None)
    assert result is sentinel
    inner.iter_steps.assert_called_once()


def test_custom_linter_and_args():
    """Custom linter name and args should be stored and used."""
    loop = LintFeedbackLoop(
        linter="flake8",
        lint_args=["--max-line-length=120"],
    )
    assert loop._linter == "flake8"
    assert loop._lint_args == ["--max-line-length=120"]


def test_lint_output_truncated_in_prompt():
    """Very long lint output should be truncated to 2000 chars in the prompt."""
    inner = _mock_inner()
    loop = LintFeedbackLoop(inner=inner, max_lint_rounds=1)
    long_output = "Z" * 5000 + "\n"
    with patch.object(loop, "_run_linter", side_effect=[long_output, ""]):
        ctx = Context(system="test")
        ctx.add(Message.user("write code"))
        loop.run(MagicMock(), [], ctx, None)
        fix_call_ctx = inner.run.call_args_list[1][0][2]
        fix_messages = fix_call_ctx.to_messages()
        # The lint output in the message should be truncated
        user_msg = fix_messages[1].content
        # 2000 chars of 'Z' should be present, not 5000
        assert user_msg.count("Z") == 2000
