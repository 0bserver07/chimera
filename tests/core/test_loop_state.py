from __future__ import annotations


from chimera.core.loop_state import (
    RETRY_POLICIES,
    LoopState,
    QuerySource,
    RetryPolicy,
)
from chimera.types import Message


def test_query_source_enum() -> None:
    assert QuerySource.FOREGROUND.value == "foreground"
    assert QuerySource.BACKGROUND.value == "background"
    assert QuerySource.FORK.value == "fork"
    # All three members exist and are distinct
    assert len(QuerySource) == 3


def test_retry_policy_foreground_retries_529() -> None:
    policy = RETRY_POLICIES[QuerySource.FOREGROUND]
    assert isinstance(policy, RetryPolicy)
    assert policy.max_retries == 5
    assert policy.retry_on_529 is True
    assert policy.retry_on_connection_error is True


def test_retry_policy_background_does_not_retry_529() -> None:
    bg = RETRY_POLICIES[QuerySource.BACKGROUND]
    assert bg.max_retries == 1
    assert bg.retry_on_529 is False
    assert bg.retry_on_connection_error is True

    fork = RETRY_POLICIES[QuerySource.FORK]
    assert fork.max_retries == 2
    assert fork.retry_on_529 is False
    assert fork.retry_on_connection_error is True


def test_loop_state_next_turn() -> None:
    initial_messages = [Message.user("hello")]
    state = LoopState(
        messages=initial_messages,
        turn_count=0,
        max_output_tokens_recovery_count=2,
        has_attempted_reactive_compact=True,
    )

    assistant_msg = Message.assistant("I'll help with that.")
    tool_results = [Message.tool("call_1", "result text")]

    next_state = state.next_turn(assistant_msg, tool_results)

    # turn_count incremented
    assert next_state.turn_count == 1

    # messages appended: original + assistant + tool results
    assert len(next_state.messages) == 3
    assert next_state.messages[0] is initial_messages[0]
    assert next_state.messages[1] is assistant_msg
    assert next_state.messages[2] is tool_results[0]

    # recovery counters reset
    assert next_state.max_output_tokens_recovery_count == 0
    assert next_state.has_attempted_reactive_compact is False

    # original state untouched (immutability)
    assert state.turn_count == 0
    assert len(state.messages) == 1
