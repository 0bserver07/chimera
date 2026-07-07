"""Unit tests for the benchmark-matrix failure taxonomy.

Drives :func:`chimera.eval.error_taxonomy.classify_failure` with concrete
``(status, error_msg)`` pairs — no LLM, no network, no mocks — pinning the
status-first mapping, each message-signature category, the priority ordering,
case-insensitivity, and the ``UNKNOWN`` fallback.
"""

from __future__ import annotations

import pytest

from chimera.eval.error_taxonomy import FailureCategory, classify_failure


class TestStatusMapping:
    """Statuses are resolved before any message inspection."""

    @pytest.mark.parametrize("status", ["completed", "ok", "success", "passed"])
    def test_success_statuses_are_unknown(self, status: str) -> None:
        # A success carries no failure to categorize.
        assert classify_failure(status) is FailureCategory.UNKNOWN

    def test_budget_exhausted_status(self) -> None:
        assert classify_failure("budget_exhausted") is FailureCategory.BUDGET_EXHAUSTED

    def test_timeout_status(self) -> None:
        assert classify_failure("timeout") is FailureCategory.TIMEOUT

    def test_bare_error_status_without_message_is_unknown(self) -> None:
        # "error" alone gives no signal about the cause.
        assert classify_failure("error") is FailureCategory.UNKNOWN
        assert classify_failure("error", None) is FailureCategory.UNKNOWN
        assert classify_failure("error", "") is FailureCategory.UNKNOWN

    def test_success_status_ignores_error_message(self) -> None:
        # A completed cell's note is a budget remark, not an error — do not let a
        # scary-looking note flip a success into a failure category.
        assert (
            classify_failure("completed", "rate limit hit earlier")
            is FailureCategory.UNKNOWN
        )

    def test_status_mapping_beats_message(self) -> None:
        # budget_exhausted / timeout win regardless of the message content.
        assert (
            classify_failure("budget_exhausted", "json decode error")
            is FailureCategory.BUDGET_EXHAUSTED
        )
        assert (
            classify_failure("timeout", "connection reset by peer")
            is FailureCategory.TIMEOUT
        )

    def test_status_is_case_and_whitespace_insensitive(self) -> None:
        assert (
            classify_failure("  Budget_Exhausted  ") is FailureCategory.BUDGET_EXHAUSTED
        )
        assert classify_failure("TIMEOUT") is FailureCategory.TIMEOUT
        assert classify_failure(" OK ") is FailureCategory.UNKNOWN


class TestMessageSignatures:
    """Every non-status category is reachable from a representative message."""

    @pytest.mark.parametrize(
        "message",
        [
            "rate limit exceeded, retry after 30s",
            "429 Too Many Requests",
            "APIConnectionError: connection reset by peer",
            "Connection refused while calling the model provider",
            "server overloaded, please try again",
            "SSL handshake failed",
        ],
    )
    def test_provider_error(self, message: str) -> None:
        assert classify_failure("error", message) is FailureCategory.PROVIDER_ERROR

    @pytest.mark.parametrize(
        "message",
        [
            "operation timed out after 300s",
            "TimeoutError: deadline exceeded",
            "the request timeout was reached",
        ],
    )
    def test_timeout_from_message(self, message: str) -> None:
        assert classify_failure("error", message) is FailureCategory.TIMEOUT

    @pytest.mark.parametrize(
        "message",
        [
            "tool execution failed for edit",
            "subprocess exited with code 1",
            "command failed: returncode 2",
            "PermissionError: permission denied",
            "OSError: no such file or directory",
        ],
    )
    def test_tool_error(self, message: str) -> None:
        assert classify_failure("error", message) is FailureCategory.TOOL_ERROR

    @pytest.mark.parametrize(
        "message",
        [
            "grader raised an exception",
            "evaluate() failed on task 3",
            "evaluation step crashed",
            "grading harness error",
        ],
    )
    def test_grader_error(self, message: str) -> None:
        assert classify_failure("error", message) is FailureCategory.GRADER_ERROR

    @pytest.mark.parametrize(
        "message",
        [
            "empty response from agent",
            "no output produced",
            "agent returned no final answer",
            "final message was blank",
        ],
    )
    def test_empty_output(self, message: str) -> None:
        assert classify_failure("error", message) is FailureCategory.EMPTY_OUTPUT

    @pytest.mark.parametrize(
        "message",
        [
            "JSONDecodeError: Expecting value: line 1 column 1",
            "failed to parse the model response",
            "could not decode the payload",
            "malformed output block",
        ],
    )
    def test_parse_error(self, message: str) -> None:
        assert classify_failure("error", message) is FailureCategory.PARSE_ERROR

    def test_message_matching_is_case_insensitive(self) -> None:
        assert classify_failure("error", "RATE LIMIT EXCEEDED") is (
            FailureCategory.PROVIDER_ERROR
        )
        assert classify_failure("error", "JSONDecodeError") is (
            FailureCategory.PARSE_ERROR
        )


class TestPriorityOrdering:
    """When multiple signatures co-occur, the documented priority resolves it."""

    def test_provider_beats_timeout(self) -> None:
        # A dropped-connection timeout is a provider fault, not the agent clock.
        assert (
            classify_failure("error", "connection timed out")
            is FailureCategory.PROVIDER_ERROR
        )

    def test_tool_beats_empty(self) -> None:
        # "no output from the tool" is a tool failure, not an empty final answer.
        assert (
            classify_failure("error", "tool call produced no output")
            is FailureCategory.TOOL_ERROR
        )

    def test_empty_beats_parse(self) -> None:
        # An empty response is the root cause; the decode failed because of it.
        assert (
            classify_failure("error", "empty response, could not decode json")
            is FailureCategory.EMPTY_OUTPUT
        )


class TestUnknownFallback:
    """Anything the rules do not recognize falls back to UNKNOWN."""

    @pytest.mark.parametrize(
        "status,message",
        [
            ("error", "something inexplicable happened"),
            ("error", None),
            ("error", ""),
            ("weird_status", "no recognizable signature here"),
            ("", "no recognizable signature here"),
            ("crashed", None),
        ],
    )
    def test_unmatched_inputs_are_unknown(
        self, status: str, message: str | None
    ) -> None:
        assert classify_failure(status, message) is FailureCategory.UNKNOWN

    def test_unknown_status_still_uses_message(self) -> None:
        # An unrecognized status is not a dead end — the message can still speak.
        assert (
            classify_failure("weird", "rate limit exceeded")
            is FailureCategory.PROVIDER_ERROR
        )


class TestEnumContract:
    """The enum shape the matrix report depends on."""

    def test_has_expected_members(self) -> None:
        expected = {
            "BUDGET_EXHAUSTED",
            "TOOL_ERROR",
            "PARSE_ERROR",
            "EMPTY_OUTPUT",
            "PROVIDER_ERROR",
            "TIMEOUT",
            "GRADER_ERROR",
            "UNKNOWN",
        }
        assert {member.name for member in FailureCategory} == expected

    def test_values_are_unique_lowercase_strings(self) -> None:
        values = [member.value for member in FailureCategory]
        assert len(values) == len(set(values))  # no duplicates
        for value in values:
            assert value == value.lower()
            assert isinstance(member := FailureCategory(value), FailureCategory)
            assert isinstance(member, str)  # str-valued: JSON-serializable

    def test_member_equals_its_string_value(self) -> None:
        assert FailureCategory.TIMEOUT == "timeout"
        assert FailureCategory.BUDGET_EXHAUSTED == "budget_exhausted"

    def test_classification_is_deterministic(self) -> None:
        # Same inputs, same output — a controlled variable, called twice.
        first = classify_failure("error", "429 too many requests")
        second = classify_failure("error", "429 too many requests")
        assert first is second is FailureCategory.PROVIDER_ERROR
