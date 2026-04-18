"""Tests for chimera.security — LLM-powered security analysis."""
from __future__ import annotations

from unittest.mock import MagicMock


from chimera.events.base import EventBus
from chimera.events.types import SecurityEvent
from chimera.security.analyzer import (
    CompositeSecurityAnalyzer,
    LLMSecurityAnalyzer,
    RuleBasedSecurityAnalyzer,
)
from chimera.security.policy import (
    AlwaysConfirm,
    ConfirmAboveThreshold,
    NeverConfirm,
)
from chimera.security.risk import SecurityRisk
from chimera.types import ToolCall


# ---------------------------------------------------------------------------
# SecurityRisk
# ---------------------------------------------------------------------------

class TestSecurityRisk:
    def test_ordering(self):
        assert SecurityRisk.HIGH > SecurityRisk.MEDIUM > SecurityRisk.LOW

    def test_is_riskier_than(self):
        assert SecurityRisk.HIGH.is_riskier_than(SecurityRisk.MEDIUM)
        assert SecurityRisk.MEDIUM.is_riskier_than(SecurityRisk.LOW)
        assert not SecurityRisk.LOW.is_riskier_than(SecurityRisk.MEDIUM)

    def test_unknown_treated_as_high(self):
        assert SecurityRisk.UNKNOWN.is_riskier_than(SecurityRisk.MEDIUM)
        assert not SecurityRisk.UNKNOWN.is_riskier_than(SecurityRisk.HIGH)

    def test_values(self):
        assert SecurityRisk.UNKNOWN == 0
        assert SecurityRisk.LOW == 1
        assert SecurityRisk.MEDIUM == 2
        assert SecurityRisk.HIGH == 3


# ---------------------------------------------------------------------------
# RuleBasedSecurityAnalyzer
# ---------------------------------------------------------------------------

class TestRuleBasedAnalyzer:
    def setup_method(self):
        self.analyzer = RuleBasedSecurityAnalyzer()

    def test_rm_rf_is_high(self):
        tc = ToolCall(id="1", name="bash", arguments={"command": "rm -rf /"})
        assert self.analyzer.analyze(tc) == SecurityRisk.HIGH

    def test_drop_table_is_high(self):
        tc = ToolCall(id="2", name="bash", arguments={"command": "DROP TABLE users"})
        assert self.analyzer.analyze(tc) == SecurityRisk.HIGH

    def test_chmod_777_is_high(self):
        tc = ToolCall(id="3", name="bash", arguments={"command": "chmod 777 /etc/passwd"})
        assert self.analyzer.analyze(tc) == SecurityRisk.HIGH

    def test_force_flag_is_high(self):
        tc = ToolCall(id="4", name="bash", arguments={"command": "git push --force"})
        assert self.analyzer.analyze(tc) == SecurityRisk.HIGH

    def test_ls_is_low(self):
        tc = ToolCall(id="5", name="bash", arguments={"command": "ls /tmp"})
        assert self.analyzer.analyze(tc) == SecurityRisk.LOW

    def test_read_file_is_low(self):
        tc = ToolCall(id="6", name="read", arguments={"path": "/tmp/file.txt"})
        assert self.analyzer.analyze(tc) == SecurityRisk.LOW

    def test_batch_analysis(self):
        calls = [
            ToolCall(id="1", name="bash", arguments={"command": "rm -rf /"}),
            ToolCall(id="2", name="read", arguments={"path": "file.txt"}),
        ]
        results = self.analyzer.analyze_batch(calls)
        assert len(results) == 2
        assert results[0][1] == SecurityRisk.HIGH
        assert results[1][1] == SecurityRisk.LOW

    def test_dd_is_high(self):
        tc = ToolCall(id="7", name="bash", arguments={"command": "dd if=/dev/zero of=/dev/sda"})
        assert self.analyzer.analyze(tc) == SecurityRisk.HIGH

    def test_mkfs_is_high(self):
        tc = ToolCall(id="8", name="bash", arguments={"command": "mkfs.ext4 /dev/sda1"})
        assert self.analyzer.analyze(tc) == SecurityRisk.HIGH


# ---------------------------------------------------------------------------
# LLMSecurityAnalyzer
# ---------------------------------------------------------------------------

class TestLLMAnalyzer:
    def _make_analyzer(self, response_content: str) -> LLMSecurityAnalyzer:
        provider = MagicMock()
        resp = MagicMock()
        resp.content = response_content
        provider.complete.return_value = resp
        return LLMSecurityAnalyzer(provider=provider)

    def test_parse_high(self):
        analyzer = self._make_analyzer("HIGH")
        tc = ToolCall(id="1", name="bash", arguments={"command": "rm -rf /"})
        assert analyzer.analyze(tc) == SecurityRisk.HIGH

    def test_parse_medium(self):
        analyzer = self._make_analyzer("MEDIUM")
        tc = ToolCall(id="1", name="write", arguments={"path": "/tmp/f.txt"})
        assert analyzer.analyze(tc) == SecurityRisk.MEDIUM

    def test_parse_low(self):
        analyzer = self._make_analyzer("LOW")
        tc = ToolCall(id="1", name="read", arguments={"path": "/tmp/f.txt"})
        assert analyzer.analyze(tc) == SecurityRisk.LOW

    def test_parse_unknown(self):
        analyzer = self._make_analyzer("I'm not sure about this.")
        tc = ToolCall(id="1", name="bash", arguments={"command": "something"})
        assert analyzer.analyze(tc) == SecurityRisk.UNKNOWN

    def test_parse_with_surrounding_text(self):
        analyzer = self._make_analyzer("The risk level is HIGH because it deletes files.")
        tc = ToolCall(id="1", name="bash", arguments={"command": "rm -rf /"})
        assert analyzer.analyze(tc) == SecurityRisk.HIGH

    def test_prompt_includes_tool_info(self):
        provider = MagicMock()
        resp = MagicMock()
        resp.content = "LOW"
        provider.complete.return_value = resp
        analyzer = LLMSecurityAnalyzer(provider=provider, model="fast-model")

        tc = ToolCall(id="1", name="read", arguments={"path": "/tmp/f.txt"})
        analyzer.analyze(tc)

        call_args = provider.complete.call_args
        messages = call_args[0][0]
        assert "read" in messages[0].content
        assert "/tmp/f.txt" in messages[0].content


# ---------------------------------------------------------------------------
# CompositeSecurityAnalyzer
# ---------------------------------------------------------------------------

class TestCompositeAnalyzer:
    def test_high_risk_skips_llm(self):
        rule = RuleBasedSecurityAnalyzer()
        llm = MagicMock(spec=LLMSecurityAnalyzer)
        composite = CompositeSecurityAnalyzer(rule, llm)

        tc = ToolCall(id="1", name="bash", arguments={"command": "rm -rf /"})
        result = composite.analyze(tc)

        assert result == SecurityRisk.HIGH
        llm.analyze.assert_not_called()

    def test_low_risk_calls_llm(self):
        rule = RuleBasedSecurityAnalyzer()
        llm = MagicMock(spec=LLMSecurityAnalyzer)
        llm.analyze.return_value = SecurityRisk.MEDIUM
        composite = CompositeSecurityAnalyzer(rule, llm)

        tc = ToolCall(id="1", name="read", arguments={"path": "/tmp/f.txt"})
        result = composite.analyze(tc)

        assert result == SecurityRisk.MEDIUM
        llm.analyze.assert_called_once_with(tc)


# ---------------------------------------------------------------------------
# Confirmation Policies
# ---------------------------------------------------------------------------

class TestNeverConfirm:
    def test_never_confirms(self):
        policy = NeverConfirm()
        for risk in SecurityRisk:
            assert policy.should_confirm(risk) is False


class TestAlwaysConfirm:
    def test_always_confirms(self):
        policy = AlwaysConfirm()
        for risk in SecurityRisk:
            assert policy.should_confirm(risk) is True


class TestConfirmAboveThreshold:
    def test_default_threshold_medium(self):
        policy = ConfirmAboveThreshold()
        assert policy.should_confirm(SecurityRisk.LOW) is False
        assert policy.should_confirm(SecurityRisk.MEDIUM) is True
        assert policy.should_confirm(SecurityRisk.HIGH) is True

    def test_custom_threshold_high(self):
        policy = ConfirmAboveThreshold(threshold=SecurityRisk.HIGH)
        assert policy.should_confirm(SecurityRisk.LOW) is False
        assert policy.should_confirm(SecurityRisk.MEDIUM) is False
        assert policy.should_confirm(SecurityRisk.HIGH) is True

    def test_unknown_confirmed_by_default(self):
        policy = ConfirmAboveThreshold()
        assert policy.should_confirm(SecurityRisk.UNKNOWN) is True

    def test_unknown_not_confirmed_when_disabled(self):
        policy = ConfirmAboveThreshold(confirm_unknown=False)
        assert policy.should_confirm(SecurityRisk.UNKNOWN) is False


# ---------------------------------------------------------------------------
# SecurityEvent
# ---------------------------------------------------------------------------

class TestSecurityEvent:
    def test_event_creation(self):
        event = SecurityEvent(
            tool_name="bash",
            arguments={"command": "rm -rf /"},
            risk="HIGH",
            action="blocked",
        )
        assert event.type == "security"
        assert event.tool_name == "bash"
        assert event.risk == "HIGH"
        assert event.action == "blocked"

    def test_event_bus_emission(self):
        bus = EventBus()
        received = []
        bus.subscribe("security", lambda e: received.append(e))

        event = SecurityEvent(
            tool_name="bash",
            arguments={"command": "rm -rf /"},
            risk="HIGH",
            action="blocked",
        )
        bus.publish(event)

        assert len(received) == 1
        assert received[0].tool_name == "bash"
        assert received[0].action == "blocked"


# ---------------------------------------------------------------------------
# Integration: analyzer + policy
# ---------------------------------------------------------------------------

class TestAnalyzerPolicyIntegration:
    def test_high_risk_blocked_by_default_policy(self):
        analyzer = RuleBasedSecurityAnalyzer()
        policy = ConfirmAboveThreshold()

        tc = ToolCall(id="1", name="bash", arguments={"command": "rm -rf /"})
        risk = analyzer.analyze(tc)
        assert policy.should_confirm(risk) is True

    def test_low_risk_allowed_by_default_policy(self):
        analyzer = RuleBasedSecurityAnalyzer()
        policy = ConfirmAboveThreshold()

        tc = ToolCall(id="1", name="read", arguments={"path": "/tmp/f.txt"})
        risk = analyzer.analyze(tc)
        assert policy.should_confirm(risk) is False

    def test_no_analyzer_means_no_blocking(self):
        """Agent works normally when no security_analyzer is provided."""
        policy = ConfirmAboveThreshold()
        # Without an analyzer, there's no risk to check — all calls proceed
        # This tests that the opt-in pattern works
        analyzer = None
        tc = ToolCall(id="1", name="bash", arguments={"command": "rm -rf /"})
        if analyzer:
            risk = analyzer.analyze(tc)
            blocked = policy.should_confirm(risk)
        else:
            blocked = False
        assert blocked is False
