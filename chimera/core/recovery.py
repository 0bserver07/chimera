"""Error recovery system for the chimera agent loop.

Provides multi-stage recovery strategies for errors encountered during
LLM interaction, such as token limit overruns and context overflow.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.core.loop_state import LoopState


class RecoveryStrategy(Enum):
    CONTEXT_COLLAPSE = "context_collapse"
    REACTIVE_COMPACT = "reactive_compact"
    ESCALATE_OUTPUT = "escalate_output"
    MULTI_TURN_RECOVERY = "multi_turn_recovery"
    FALLBACK_MODEL = "fallback_model"


@dataclass
class RecoveryResult:
    should_continue: bool
    reason: str
    strategy_used: RecoveryStrategy | None = None


@dataclass
class WithheldError:
    type: str
    original_error: Exception
    partial_response: Any = None


class ErrorRecovery:
    """Attempts to recover from loop errors using a prioritised strategy ladder."""

    async def attempt_recovery(
        self,
        state: "LoopState",
        error: WithheldError,
    ) -> RecoveryResult:
        if error.type == "max_output_tokens":
            return await self._handle_max_output_tokens(state)
        if error.type == "prompt_too_long":
            return await self._handle_prompt_too_long(state)
        return RecoveryResult(should_continue=False, reason="unrecoverable")

    # ------------------------------------------------------------------
    # Private handlers
    # ------------------------------------------------------------------

    async def _handle_max_output_tokens(self, state: "LoopState") -> RecoveryResult:
        count = state.max_output_tokens_recovery_count
        if count >= 3:
            return RecoveryResult(
                should_continue=False,
                reason="max_output_tokens_exhausted",
            )
        if count == 0:
            state.max_output_tokens_override = 64_000
            state.max_output_tokens_recovery_count += 1
            return RecoveryResult(
                should_continue=True,
                reason="escalating output token limit to 64k",
                strategy_used=RecoveryStrategy.ESCALATE_OUTPUT,
            )
        # 1 <= count < 3
        state.max_output_tokens_recovery_count += 1
        return RecoveryResult(
            should_continue=True,
            reason="retrying with multi-turn recovery",
            strategy_used=RecoveryStrategy.MULTI_TURN_RECOVERY,
        )

    async def _handle_prompt_too_long(self, state: "LoopState") -> RecoveryResult:
        if not state.has_attempted_reactive_compact:
            state.has_attempted_reactive_compact = True
            return RecoveryResult(
                should_continue=True,
                reason="compacting context reactively",
                strategy_used=RecoveryStrategy.REACTIVE_COMPACT,
            )
        return RecoveryResult(
            should_continue=False,
            reason="prompt_too_long",
        )
