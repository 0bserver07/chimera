"""Tests for chimera.shrew.quality_monitor — extends repeat_detection.

Five groups:

1. Empty-response detector.
2. Hallucinated-tool detector (vs. registry).
3. Correction-language detector.
4. Aggregate :func:`assess_response` + :class:`QualityReport`.
5. Stateful :class:`QualityMonitor` lifecycle + correction message
   builder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chimera.shrew.quality_monitor import (
    CORRECTION_TEMPLATES,
    DEFAULT_HISTORY_SIZE,
    QualityIssue,
    QualityMonitor,
    QualityReport,
    assess_response,
    build_correction_message,
    detect_correction_language,
    detect_empty_response,
    detect_hallucinated_tool,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _Call:
    """Minimal duck-typed tool-call shape."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1. Empty-response detector
# ---------------------------------------------------------------------------


class TestDetectEmptyResponse:
    def test_empty_string(self) -> None:
        assert detect_empty_response("", []) is True

    def test_whitespace_only(self) -> None:
        assert detect_empty_response("   \n\t  \n", []) is True

    def test_with_text_is_not_empty(self) -> None:
        assert detect_empty_response("hello", []) is False

    def test_with_tool_call_is_not_empty(self) -> None:
        assert detect_empty_response("", [_Call(name="bash")]) is False


# ---------------------------------------------------------------------------
# 2. Hallucinated-tool detector
# ---------------------------------------------------------------------------


class TestDetectHallucinatedTool:
    def test_no_calls_returns_empty(self) -> None:
        assert detect_hallucinated_tool([], ["bash", "read"]) == ()

    def test_known_tool_passes(self) -> None:
        out = detect_hallucinated_tool([_Call(name="bash")], ["bash", "read"])
        assert out == ()

    def test_unknown_tool_flagged(self) -> None:
        out = detect_hallucinated_tool([_Call(name="run_shell")], ["bash"])
        assert out == ("run_shell",)

    def test_multiple_unknowns_dedup(self) -> None:
        out = detect_hallucinated_tool(
            [_Call(name="run_shell"), _Call(name="run_shell"), _Call(name="write_file")],
            ["bash", "write"],
        )
        assert out == ("run_shell", "write_file")

    def test_case_sensitive(self) -> None:
        # Registry has 'bash' lowercase; model emitted 'BASH' — flagged.
        out = detect_hallucinated_tool([_Call(name="BASH")], ["bash"])
        assert out == ("BASH",)


# ---------------------------------------------------------------------------
# 3. Correction-language detector
# ---------------------------------------------------------------------------


class TestDetectCorrectionLanguage:
    def test_empty_input(self) -> None:
        assert detect_correction_language("") == ""

    def test_no_match(self) -> None:
        assert detect_correction_language("Continuing with the next step.") == ""

    def test_let_me_try_again(self) -> None:
        assert detect_correction_language("OK, let me try again.") == "let me try again"

    def test_apologise(self) -> None:
        assert detect_correction_language("I apologize for the confusion.") == "i apologize"

    def test_starting_over(self) -> None:
        assert detect_correction_language("Starting over from scratch.") == "starting over"

    def test_case_insensitive(self) -> None:
        assert detect_correction_language("LET ME TRY AGAIN") == "let me try again"


# ---------------------------------------------------------------------------
# 4. Aggregate assess_response
# ---------------------------------------------------------------------------


class TestAssessResponse:
    def test_healthy_response_is_clean(self) -> None:
        rpt = assess_response(
            "I will run ls now.",
            [_Call(name="bash")],
            registry=["bash", "read"],
        )
        assert rpt.healthy
        assert rpt.issues == ()

    def test_empty_flagged(self) -> None:
        rpt = assess_response("", [], registry=["bash"])
        assert rpt.has(QualityIssue.EMPTY_RESPONSE)

    def test_hallucinated_flagged(self) -> None:
        rpt = assess_response(
            "Running shell.",
            [_Call(name="run_shell")],
            registry=["bash", "read"],
        )
        assert rpt.has(QualityIssue.HALLUCINATED_TOOL)
        assert rpt.unknown_tool_names == ("run_shell",)

    def test_correction_flagged(self) -> None:
        rpt = assess_response(
            "Let me try again.",
            [_Call(name="bash")],
            registry=["bash"],
        )
        assert rpt.has(QualityIssue.CORRECTION_LANGUAGE)
        assert rpt.correction_phrase == "let me try again"

    def test_loop_flagged(self) -> None:
        # 6 actions: alternating ("bash","x"), ("read","y") repeated 3 times.
        # Loop detector with default min_repeats=2, window=4 should fire
        # on cycle length 2 with 3 repeats.
        actions = [
            ("bash", "x"), ("read", "y"),
            ("bash", "x"), ("read", "y"),
            ("bash", "x"), ("read", "y"),
        ]
        rpt = assess_response(
            "OK.",
            [_Call(name="bash")],
            registry=["bash", "read"],
            recent_actions=actions,
        )
        assert rpt.has(QualityIssue.LOOP_DETECTED)
        assert rpt.loop_cycle == 2

    def test_multiple_issues(self) -> None:
        rpt = assess_response(
            "I apologize. Let me try again.",
            [_Call(name="run_shell")],
            registry=["bash"],
        )
        assert rpt.has(QualityIssue.HALLUCINATED_TOOL)
        assert rpt.has(QualityIssue.CORRECTION_LANGUAGE)

    def test_registry_none_skips_hallucination_check(self) -> None:
        rpt = assess_response(
            "x",
            [_Call(name="anything")],
            registry=None,
        )
        assert not rpt.has(QualityIssue.HALLUCINATED_TOOL)


# ---------------------------------------------------------------------------
# 5. QualityMonitor stateful wrapper
# ---------------------------------------------------------------------------


class TestQualityMonitorStateful:
    def test_observe_records_action(self) -> None:
        mon = QualityMonitor(registry=["bash"])
        mon.observe("Running.", [_Call(name="bash", arguments={"cmd": "ls"})])
        assert len(mon.recent_actions) == 1
        assert mon.recent_actions[0][0] == "bash"

    def test_observe_text_only_records_text_summary(self) -> None:
        mon = QualityMonitor(registry=["bash"])
        mon.observe("just thinking out loud here", [])
        assert mon.recent_actions[0][0] == "text"

    def test_observe_loop_detection(self) -> None:
        mon = QualityMonitor(registry=["bash", "read"])
        # Push 6 alternating actions to trigger the loop detector.
        for _ in range(3):
            mon.observe("a", [_Call(name="bash", arguments={"cmd": "x"})])
            mon.observe("b", [_Call(name="read", arguments={"path": "y"})])
        assert mon.last_report.has(QualityIssue.LOOP_DETECTED)

    def test_history_size_caps(self) -> None:
        mon = QualityMonitor(registry=["bash"], history_size=3)
        for i in range(10):
            mon.observe(f"call {i}", [_Call(name="bash", arguments={"i": i})])
        # Cap respects history_size.
        assert len(mon.recent_actions) == 3

    def test_default_history_size(self) -> None:
        mon = QualityMonitor()
        # The internal deque has exactly DEFAULT_HISTORY_SIZE maxlen.
        assert mon._actions.maxlen == DEFAULT_HISTORY_SIZE  # type: ignore[attr-defined]

    def test_update_registry(self) -> None:
        mon = QualityMonitor(registry=["bash"])
        mon.observe("ok", [_Call(name="custom_tool")])
        assert mon.last_report.has(QualityIssue.HALLUCINATED_TOOL)
        # Add the new tool to the registry; next observe shouldn't flag.
        mon.update_registry(["bash", "custom_tool"])
        mon.observe("ok 2", [_Call(name="custom_tool")])
        assert not mon.last_report.has(QualityIssue.HALLUCINATED_TOOL)

    def test_reset_clears_state(self) -> None:
        mon = QualityMonitor(registry=["bash"])
        mon.observe("x", [_Call(name="bash")])
        mon.reset()
        assert mon.recent_actions == ()
        assert mon.last_report.healthy

    def test_build_followup_returns_empty_when_healthy(self) -> None:
        mon = QualityMonitor(registry=["bash"])
        mon.observe("Doing it.", [_Call(name="bash")])
        assert mon.build_followup() == ""

    def test_build_followup_renders_correction(self) -> None:
        mon = QualityMonitor(registry=["bash"])
        mon.observe("Let me try again.", [_Call(name="run_shell")])
        msg = mon.build_followup()
        # Hallucinated tool template ran.
        assert "run_shell" in msg
        # Correction-language template ran.
        assert "let me try again" in msg.lower()


# ---------------------------------------------------------------------------
# 6. build_correction_message
# ---------------------------------------------------------------------------


class TestBuildCorrectionMessage:
    def test_healthy_returns_empty(self) -> None:
        rpt = QualityReport()
        assert build_correction_message(rpt) == ""

    def test_each_template_renders(self) -> None:
        # Cover every template at least once.
        rpt = QualityReport(
            issues=(
                QualityIssue.EMPTY_RESPONSE,
                QualityIssue.HALLUCINATED_TOOL,
                QualityIssue.CORRECTION_LANGUAGE,
                QualityIssue.LOOP_DETECTED,
            ),
            unknown_tool_names=("foo",),
            loop_cycle=2,
            correction_phrase="let me try again",
        )
        msg = build_correction_message(rpt, available_tools=["bash", "read"])
        assert "empty" in msg.lower()
        assert "foo" in msg
        assert "bash" in msg
        assert "let me try again" in msg
        assert "2-step" in msg

    def test_template_keys_match_issue_enum(self) -> None:
        # Every QualityIssue must have a template — protects against
        # silent regressions when a new issue type is added.
        for issue in QualityIssue:
            assert issue in CORRECTION_TEMPLATES
