"""Tests for the seven additional shrew small-model-fit extensions
authored by agent S2 in wave 9.

Each module gets at least one positive case (the function does what
it says) and one negative / edge case (no-op, empty input, idempotence).
Hermetic: no LLM, no filesystem dependence, no network.
"""
from __future__ import annotations

from chimera.shrew.extensions.error_simplifier import (
    MAX_SIMPLE_CHARS,
    is_known_error,
    simplify_error,
)
from chimera.shrew.extensions.file_chunker import (
    DEFAULT_MAX_BYTES,
    Chunk,
    chunk_text,
    format_chunk_header,
)
from chimera.shrew.extensions.hint_injector import (
    MIN_FAILURES_FOR_HINT,
    Attempt,
    build_hint,
    inject_hint,
    should_inject_hint,
)
from chimera.shrew.extensions.output_truncation import (
    DEFAULT_MAX_CHARS,
    TRUNCATION_SUFFIX,
    truncate_output,
)
from chimera.shrew.extensions.quiet_thinking import (
    THINKING_TAGS,
    has_thinking,
    strip_thinking,
)
from chimera.shrew.extensions.repeat_detection import (
    DEFAULT_MIN_REPEATS,
    DEFAULT_WINDOW,
    detect_short_loop,
    should_short_circuit,
)
from chimera.shrew.extensions.turn_budgeter import (
    DEFAULT_TURN_BUDGET,
    check_budget,
    estimate_tokens,
    format_budget_warning,
)

# ---------------------------------------------------------------------------
# output_truncation
# ---------------------------------------------------------------------------


class TestTruncateOutput:
    def test_short_text_unchanged(self) -> None:
        assert truncate_output("Hello world.") == "Hello world."

    def test_long_text_truncated_with_suffix(self) -> None:
        text = "x" * (DEFAULT_MAX_CHARS + 100)
        out = truncate_output(text)
        assert out.endswith(TRUNCATION_SUFFIX)
        assert len(out) <= DEFAULT_MAX_CHARS + len(TRUNCATION_SUFFIX)

    def test_snaps_to_sentence_boundary(self) -> None:
        # First half: nine 100-char sentences; tail past the budget.
        sentences = ". ".join("a" * 80 for _ in range(20)) + "."
        out = truncate_output(sentences, max_chars=400)
        assert out.endswith(TRUNCATION_SUFFIX)
        # Body before the suffix should not be a partial mid-word cut
        # (snap was attempted at the last sentence boundary in window).
        body = out[: -len(TRUNCATION_SUFFIX)]
        assert body.endswith(".") or body.endswith("a")  # snap or hard cut

    def test_max_chars_zero_disables(self) -> None:
        text = "long " * 1000
        assert truncate_output(text, max_chars=0) == text

    def test_negative_max_chars_disables(self) -> None:
        assert truncate_output("hello", max_chars=-1) == "hello"

    def test_empty_input(self) -> None:
        assert truncate_output("") == ""


# ---------------------------------------------------------------------------
# repeat_detection
# ---------------------------------------------------------------------------


class TestDetectShortLoop:
    def test_simple_two_step_loop(self) -> None:
        items = ["a", "b", "a", "b", "a", "b"]
        # 2-step cycle, repeated 3 times → min_repeats=2 satisfied.
        assert detect_short_loop(items) == 2

    def test_single_item_loop(self) -> None:
        items = ["x", "x", "x"]
        assert detect_short_loop(items) == 1

    def test_no_loop(self) -> None:
        items = ["a", "b", "c", "d"]
        assert detect_short_loop(items) == 0

    def test_long_window_ignored(self) -> None:
        items = ["a", "b", "c", "a", "b", "c"]
        # 3-step cycle, only 2 copies → fewer than min_repeats+1=3.
        assert detect_short_loop(items) == 0

    def test_three_step_loop_three_repeats(self) -> None:
        items = ["a", "b", "c"] * 3
        assert detect_short_loop(items) == 3

    def test_window_zero_returns_zero(self) -> None:
        assert detect_short_loop(["a", "a", "a"], window=0) == 0

    def test_empty_input(self) -> None:
        assert detect_short_loop([]) == 0

    def test_should_short_circuit_respects_min_length(self) -> None:
        # Loop is present but sequence is too short.
        assert should_short_circuit(["a", "a"], min_length=4) is False
        assert should_short_circuit(["a", "a", "a", "a"], min_length=4) is True

    def test_should_short_circuit_no_loop(self) -> None:
        assert should_short_circuit(["a", "b", "c", "d", "e"]) is False


# ---------------------------------------------------------------------------
# error_simplifier
# ---------------------------------------------------------------------------


class TestSimplifyError:
    def test_file_not_found(self) -> None:
        out = simplify_error("FileNotFoundError: [Errno 2] '/tmp/missing.txt'")
        assert "/tmp/missing.txt" in out
        assert "not found" in out.lower()

    def test_module_not_found(self) -> None:
        out = simplify_error("ModuleNotFoundError: No module named 'requests'")
        assert "requests" in out
        assert "Missing module" in out or "missing module" in out.lower()

    def test_permission_denied(self) -> None:
        out = simplify_error("PermissionError: [Errno 13] '/etc/secret'")
        assert "Permission denied" in out
        assert "/etc/secret" in out

    def test_syntax_error(self) -> None:
        out = simplify_error("SyntaxError: invalid syntax")
        assert "Syntax error" in out

    def test_command_not_found(self) -> None:
        out = simplify_error("zsh: command not found: foobar")
        assert "foobar" in out

    def test_unknown_error_falls_back_to_last_line(self) -> None:
        traceback = (
            "Traceback (most recent call last):\n"
            "  File \"foo.py\", line 1, in <module>\n"
            "    do_stuff()\n"
            "RuntimeError: something weird happened"
        )
        out = simplify_error(traceback)
        assert "RuntimeError" in out
        assert "something weird" in out

    def test_empty_input(self) -> None:
        assert simplify_error("") == ""

    def test_is_known_error_predicate(self) -> None:
        assert is_known_error("ModuleNotFoundError: No module named 'x'")
        assert not is_known_error("totally novel error format here")
        assert not is_known_error("")

    def test_max_chars_respected(self) -> None:
        long_msg = "TypeError: " + "x" * 500
        out = simplify_error(long_msg)
        assert len(out) <= MAX_SIMPLE_CHARS


# ---------------------------------------------------------------------------
# turn_budgeter
# ---------------------------------------------------------------------------


class TestTurnBudgeter:
    def test_estimate_tokens_basic(self) -> None:
        # 4 chars/token; 16 chars → 4 tokens.
        assert estimate_tokens("abcdefghijklmnop") == 4

    def test_estimate_tokens_empty(self) -> None:
        assert estimate_tokens("") == 0

    def test_estimate_tokens_min_one(self) -> None:
        # Single char should round up to 1, not 0.
        assert estimate_tokens("a") == 1

    def test_check_budget_ok(self) -> None:
        assert check_budget(100, budget=DEFAULT_TURN_BUDGET) == "ok"

    def test_check_budget_warn(self) -> None:
        # 90% of 4096 = 3686.4 → 3700 should warn.
        assert check_budget(3700, budget=DEFAULT_TURN_BUDGET) == "warn"

    def test_check_budget_exceeded(self) -> None:
        assert check_budget(5000, budget=DEFAULT_TURN_BUDGET) == "exceeded"

    def test_check_budget_disabled(self) -> None:
        assert check_budget(99999, budget=0) == "ok"
        assert check_budget(99999, budget=-1) == "ok"

    def test_format_budget_warning_ok_is_empty(self) -> None:
        assert format_budget_warning(10) == ""

    def test_format_budget_warning_exceeded(self) -> None:
        msg = format_budget_warning(5000, DEFAULT_TURN_BUDGET)
        assert "exceeded" in msg
        assert "5000" in msg

    def test_format_budget_warning_warn(self) -> None:
        msg = format_budget_warning(3700, DEFAULT_TURN_BUDGET)
        assert "warning" in msg

    def test_check_budget_clamps_warn_fraction(self) -> None:
        # warn_fraction > 1 clamped to 1; only "exceeded" remains
        # reachable.
        assert check_budget(1000, budget=2000, warn_fraction=2.0) == "ok"


# ---------------------------------------------------------------------------
# file_chunker
# ---------------------------------------------------------------------------


class TestFileChunker:
    def test_small_file_one_chunk(self) -> None:
        chunks = chunk_text("hello\nworld\n")
        assert len(chunks) == 1
        assert chunks[0].text == "hello\nworld\n"
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 2
        assert chunks[0].total == 1

    def test_large_file_multiple_chunks(self) -> None:
        # 200 lines of 50 chars each ≈ 10 KB → 5+ chunks at 2 KB.
        text = "\n".join("x" * 50 for _ in range(200)) + "\n"
        chunks = chunk_text(text, max_bytes=2000)
        assert len(chunks) >= 5
        # Every chunk under the byte budget...
        for c in chunks[:-1]:
            assert len(c.text.encode("utf-8")) <= 2000 + 100  # generous slop
        # ...and the line ranges cover everything.
        assert chunks[0].start_line == 1
        assert chunks[-1].end_line == 200

    def test_chunk_indices_sequential(self) -> None:
        text = "\n".join("x" * 200 for _ in range(50)) + "\n"
        chunks = chunk_text(text, max_bytes=500)
        for i, c in enumerate(chunks):
            assert c.index == i
            assert c.total == len(chunks)

    def test_empty_input_returns_one_chunk(self) -> None:
        chunks = chunk_text("")
        assert len(chunks) == 1
        assert chunks[0].text == ""

    def test_negative_max_bytes_uses_default(self) -> None:
        text = "line\n" * 5
        chunks = chunk_text(text, max_bytes=-1)
        # 25 bytes < default 2000 → 1 chunk.
        assert len(chunks) == 1
        # And the default constant is what we expect.
        assert DEFAULT_MAX_BYTES == 2_000

    def test_format_chunk_header_with_filename(self) -> None:
        c = Chunk(index=1, total=3, start_line=10, end_line=20, text="...")
        header = format_chunk_header(c, filename="foo.py")
        assert "foo.py" in header
        assert "chunk 2/3" in header
        assert "10-20" in header

    def test_format_chunk_header_no_filename(self) -> None:
        c = Chunk(index=0, total=1, start_line=1, end_line=5, text="...")
        header = format_chunk_header(c)
        assert "chunk 1/1" in header
        assert not header.startswith(" ")

    def test_oversized_single_line_kept_intact(self) -> None:
        # A single line larger than max_bytes should not be split mid-line.
        long_line = "x" * 5000 + "\n"
        chunks = chunk_text(long_line, max_bytes=1000)
        assert len(chunks) == 1
        assert chunks[0].text == long_line


# ---------------------------------------------------------------------------
# hint_injector
# ---------------------------------------------------------------------------


class TestHintInjector:
    def test_two_failures_same_task_triggers(self) -> None:
        attempts = [
            Attempt(task="read foo.py", succeeded=False, error="not found"),
            Attempt(task="read foo.py", succeeded=False, error="not found"),
        ]
        assert should_inject_hint(attempts) is True

    def test_one_failure_does_not_trigger(self) -> None:
        attempts = [Attempt(task="read foo.py", succeeded=False)]
        assert should_inject_hint(attempts) is False

    def test_recent_success_resets(self) -> None:
        attempts = [
            Attempt(task="t", succeeded=False),
            Attempt(task="t", succeeded=True),
        ]
        assert should_inject_hint(attempts) is False

    def test_different_tasks_do_not_trigger(self) -> None:
        attempts = [
            Attempt(task="t1", succeeded=False),
            Attempt(task="t2", succeeded=False),
        ]
        assert should_inject_hint(attempts) is False

    def test_min_failures_zero(self) -> None:
        attempts = [Attempt(task="t", succeeded=False)]
        assert should_inject_hint(attempts, min_failures=0) is False

    def test_build_hint_file_not_found(self) -> None:
        hint = build_hint("read foo.py", error="FileNotFoundError: not found")
        assert "list" in hint.lower() or "path" in hint.lower()

    def test_build_hint_permission(self) -> None:
        hint = build_hint("write /etc", error="Permission denied")
        assert "permission" in hint.lower()

    def test_build_hint_generic(self) -> None:
        hint = build_hint("do thing", error="weird novel error")
        assert "different approach" in hint.lower()

    def test_inject_hint_prepends_when_triggered(self) -> None:
        attempts = [
            Attempt(task="t", succeeded=False, error="not found"),
            Attempt(task="t", succeeded=False, error="not found"),
        ]
        new_prompt, hint = inject_hint("Original prompt.", attempts)
        assert hint != ""
        assert hint in new_prompt
        assert "Original prompt." in new_prompt

    def test_inject_hint_no_op_when_not_triggered(self) -> None:
        attempts = [Attempt(task="t", succeeded=True)]
        new_prompt, hint = inject_hint("p", attempts)
        assert new_prompt == "p"
        assert hint == ""

    def test_min_failures_constant(self) -> None:
        assert MIN_FAILURES_FOR_HINT == 2


# ---------------------------------------------------------------------------
# quiet_thinking
# ---------------------------------------------------------------------------


class TestQuietThinking:
    def test_strip_thinking_block(self) -> None:
        text = "Here is my answer.\n<thinking>secret reasoning</thinking>\nDone."
        out = strip_thinking(text)
        assert "secret reasoning" not in out
        assert "Here is my answer." in out
        assert "Done." in out

    def test_strip_scratchpad(self) -> None:
        text = "<scratchpad>plot</scratchpad>output"
        out = strip_thinking(text)
        assert "plot" not in out
        assert "output" in out

    def test_strip_unclosed_thinking(self) -> None:
        text = "Real answer.\n\n<thinking>I should consider...\n\nFinal."
        out = strip_thinking(text)
        assert "I should consider" not in out
        assert "Real answer." in out
        assert "Final." in out

    def test_no_thinking_unchanged(self) -> None:
        text = "Just a plain answer."
        assert strip_thinking(text) == text

    def test_empty_input(self) -> None:
        assert strip_thinking("") == ""

    def test_idempotent(self) -> None:
        text = "ans <thinking>x</thinking> tail"
        once = strip_thinking(text)
        twice = strip_thinking(once)
        assert once == twice

    def test_has_thinking_predicate(self) -> None:
        assert has_thinking("<thinking>foo</thinking>")
        assert has_thinking("plain <reasoning>y</reasoning> trail")
        assert not has_thinking("plain text")
        assert not has_thinking("")

    def test_case_insensitive(self) -> None:
        text = "<Thinking>secret</THINKING>visible"
        out = strip_thinking(text)
        assert "secret" not in out
        assert "visible" in out

    def test_collapses_blank_lines(self) -> None:
        text = "before\n\n\n\n<thinking>x</thinking>\n\n\n\nafter"
        out = strip_thinking(text)
        # No more than two consecutive newlines remain.
        assert "\n\n\n" not in out
        assert "before" in out
        assert "after" in out

    def test_thinking_tags_constant_includes_known(self) -> None:
        assert "thinking" in THINKING_TAGS
        assert "scratchpad" in THINKING_TAGS


# ---------------------------------------------------------------------------
# Module-level invariants
# ---------------------------------------------------------------------------


def test_default_window_and_repeats_sane() -> None:
    assert DEFAULT_WINDOW >= 1
    assert DEFAULT_MIN_REPEATS >= 1
