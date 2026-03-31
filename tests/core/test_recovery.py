"""Tests for chimera.core.recovery — ErrorRecovery with multi-stage strategies."""
from __future__ import annotations

import pytest

from chimera.core.loop_state import LoopState
from chimera.core.recovery import (
    ErrorRecovery,
    RecoveryResult,
    RecoveryStrategy,
    WithheldError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_loop_state(**kwargs) -> LoopState:
    defaults = dict(messages=[], turn_count=0)
    defaults.update(kwargs)
    return LoopState(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_withheld_error():
    """WithheldError can be constructed with required and optional fields."""
    exc = ValueError("something went wrong")
    err = WithheldError(type="max_output_tokens", original_error=exc)
    assert err.type == "max_output_tokens"
    assert err.original_error is exc
    assert err.partial_response is None

    err2 = WithheldError(type="prompt_too_long", original_error=exc, partial_response="partial")
    assert err2.partial_response == "partial"


@pytest.mark.asyncio
async def test_max_output_tokens_escalates():
    """First max_output_tokens error should escalate to 64k tokens."""
    state = make_loop_state()
    recovery = ErrorRecovery()
    error = WithheldError(type="max_output_tokens", original_error=RuntimeError("too long"))

    result = await recovery.attempt_recovery(state, error)

    assert result.should_continue is True
    assert result.strategy_used == RecoveryStrategy.ESCALATE_OUTPUT
    assert state.max_output_tokens_override == 64_000
    assert state.max_output_tokens_recovery_count == 1


@pytest.mark.asyncio
async def test_max_output_tokens_exhausted_after_3():
    """After 3 recovery attempts, max_output_tokens should stop retrying."""
    state = make_loop_state(max_output_tokens_recovery_count=3)
    recovery = ErrorRecovery()
    error = WithheldError(type="max_output_tokens", original_error=RuntimeError("too long"))

    result = await recovery.attempt_recovery(state, error)

    assert result.should_continue is False
    assert result.reason == "max_output_tokens_exhausted"


@pytest.mark.asyncio
async def test_unknown_error_is_unrecoverable():
    """An unrecognised error type should immediately stop the loop."""
    state = make_loop_state()
    recovery = ErrorRecovery()
    error = WithheldError(type="network_error", original_error=OSError("connection refused"))

    result = await recovery.attempt_recovery(state, error)

    assert result.should_continue is False
    assert result.reason == "unrecoverable"
    assert result.strategy_used is None
