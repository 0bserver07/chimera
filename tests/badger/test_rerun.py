"""Tests for ``chimera.badger.rerun`` — rerun-on-failure logic."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from chimera.badger import rerun


# ---------------------------------------------------------------------------
# detect_failure_markers
# ---------------------------------------------------------------------------


def test_detect_pytest_failure() -> None:
    text = "FAILED tests/test_foo.py::test_bar - assert 1 == 2"
    reasons = rerun.detect_failure_markers(text)
    assert "pytest test failure" in reasons


def test_detect_pytest_summary() -> None:
    text = "==== 3 failed, 2 passed in 0.42s ===="
    reasons = rerun.detect_failure_markers(text)
    assert "pytest summary" in reasons


def test_detect_python_traceback() -> None:
    text = (
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "    1 / 0\n"
        "ZeroDivisionError: division by zero"
    )
    reasons = rerun.detect_failure_markers(text)
    assert "Python traceback" in reasons


def test_detect_python_syntax_error() -> None:
    text = "  SyntaxError: invalid syntax"
    reasons = rerun.detect_failure_markers(text)
    assert "Python syntax error" in reasons


def test_detect_rust_compile_error() -> None:
    text = "error[E0308]: mismatched types"
    reasons = rerun.detect_failure_markers(text)
    assert "Rust compile error" in reasons


def test_detect_explicit_failure_marker() -> None:
    text = "BUILD FAILED"
    reasons = rerun.detect_failure_markers(text)
    assert "explicit failure marker" in reasons


def test_detect_no_failure_when_clean() -> None:
    text = "All checks passed. 42 tests, 0 failures."
    reasons = rerun.detect_failure_markers(text)
    assert reasons == []


def test_detect_no_failure_on_empty() -> None:
    assert rerun.detect_failure_markers("") == []
    assert rerun.detect_failure_markers(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# refine_prompt_for_rerun
# ---------------------------------------------------------------------------


def test_refine_prompt_includes_reasons() -> None:
    refined = rerun.refine_prompt_for_rerun(
        "Fix the bug in foo.py",
        ["pytest test failure"],
        attempt=1,
    )
    assert "Fix the bug in foo.py" in refined
    assert "pytest test failure" in refined
    assert "rerun 1" in refined


def test_refine_prompt_handles_empty_reasons() -> None:
    refined = rerun.refine_prompt_for_rerun(
        "do thing", [], attempt=2,
    )
    assert "do thing" in refined
    assert "unspecified failure" in refined


# ---------------------------------------------------------------------------
# run_with_rerun (async)
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    output: str = ""
    success: bool = False


@dataclass
class _FakeAgent:
    """Programmable fake agent for run_with_rerun.

    ``responses`` is consumed in order, one per attempt.
    """

    responses: list[_FakeResult] = field(default_factory=list)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    async def async_run(self, prompt: str, *, env: Any = None) -> _FakeResult:
        self.calls.append((prompt, env))
        if self.responses:
            return self.responses.pop(0)
        return _FakeResult(output="", success=False)


def test_run_with_rerun_succeeds_first_try() -> None:
    """No rerun fires when the first attempt succeeds with no markers."""
    agent = _FakeAgent(
        responses=[_FakeResult(output="all good", success=True)],
    )
    result = asyncio.run(rerun.run_with_rerun(agent, "do it", max_reruns=2))
    assert result.success
    assert len(agent.calls) == 1


def test_run_with_rerun_retries_on_failure_marker() -> None:
    """Failure markers trigger rerun until success or budget exhausted."""
    agent = _FakeAgent(
        responses=[
            _FakeResult(output="FAILED tests/test_x.py::foo", success=False),
            _FakeResult(output="all green", success=True),
        ],
    )
    result = asyncio.run(rerun.run_with_rerun(agent, "fix it", max_reruns=2))
    assert result.success
    assert len(agent.calls) == 2
    # First call uses original prompt; second uses refined prompt.
    assert agent.calls[0][0] == "fix it"
    assert "rerun 1" in agent.calls[1][0]
    assert "fix it" in agent.calls[1][0]


def test_run_with_rerun_exhausts_budget() -> None:
    """All attempts can fail; the last result is returned."""
    agent = _FakeAgent(
        responses=[
            _FakeResult(output="FAILED ttt::a", success=False),
            _FakeResult(output="FAILED ttt::b", success=False),
            _FakeResult(output="FAILED ttt::c", success=False),
        ],
    )
    result = asyncio.run(rerun.run_with_rerun(agent, "fix it", max_reruns=2))
    assert not result.success
    assert "FAILED ttt::c" in result.output
    assert len(agent.calls) == 3  # 1 + 2 reruns


def test_run_with_rerun_zero_budget_disables_rerun() -> None:
    """``max_reruns=0`` means a single attempt with no retries."""
    agent = _FakeAgent(
        responses=[_FakeResult(output="FAILED", success=False)],
    )
    result = asyncio.run(rerun.run_with_rerun(agent, "fix it", max_reruns=0))
    assert not result.success
    assert len(agent.calls) == 1


def test_run_with_rerun_no_marker_no_retry() -> None:
    """When a result fails but no markers fire, we don't waste a rerun."""
    agent = _FakeAgent(
        responses=[_FakeResult(output="something else", success=False)],
    )
    result = asyncio.run(rerun.run_with_rerun(agent, "do", max_reruns=2))
    # Only one call: agent reported failure but no marker so we bail.
    assert len(agent.calls) == 1
    assert result.output == "something else"
