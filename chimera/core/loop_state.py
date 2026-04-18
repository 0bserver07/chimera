from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from chimera.types import Message


class QuerySource(Enum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"
    FORK = "fork"


@dataclass
class RetryPolicy:
    max_retries: int
    retry_on_529: bool
    retry_on_connection_error: bool
    fallback_model: str | None = None
    max_consecutive_529: int = 3


RETRY_POLICIES: dict[QuerySource, RetryPolicy] = {
    QuerySource.FOREGROUND: RetryPolicy(
        max_retries=5,
        retry_on_529=True,
        retry_on_connection_error=True,
    ),
    QuerySource.BACKGROUND: RetryPolicy(
        max_retries=1,
        retry_on_529=False,
        retry_on_connection_error=True,
    ),
    QuerySource.FORK: RetryPolicy(
        max_retries=2,
        retry_on_529=False,
        retry_on_connection_error=True,
    ),
}


@dataclass
class LoopState:
    messages: list[Message]
    turn_count: int
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    max_output_tokens_override: int | None = None
    transition_reason: str | None = None

    def next_turn(
        self,
        assistant_msg: Message,
        tool_results: list[Message],
    ) -> LoopState:
        """Return a new LoopState with incremented turn_count, appended messages,
        and reset recovery counters."""
        return LoopState(
            messages=self.messages + [assistant_msg] + tool_results,
            turn_count=self.turn_count + 1,
            max_output_tokens_recovery_count=0,
            has_attempted_reactive_compact=False,
            max_output_tokens_override=self.max_output_tokens_override,
            transition_reason=self.transition_reason,
        )
