"""Security analyzers for evaluating tool call risk."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from chimera.security.risk import SecurityRisk

if TYPE_CHECKING:
    from chimera.providers.base import Provider
    from chimera.types import ToolCall

__all__ = [
    "SecurityAnalyzer",
    "LLMSecurityAnalyzer",
    "RuleBasedSecurityAnalyzer",
    "CompositeSecurityAnalyzer",
]


class SecurityAnalyzer(ABC):
    """Abstract base for security analyzers."""

    @abstractmethod
    def analyze(self, tool_call: ToolCall) -> SecurityRisk:
        """Evaluate the security risk of a tool call."""
        ...

    def analyze_batch(
        self, tool_calls: list[ToolCall],
    ) -> list[tuple[ToolCall, SecurityRisk]]:
        """Analyze multiple tool calls.

        Args:
            tool_calls: List of tool calls to analyze.

        Returns:
            List of (tool_call, risk) tuples.
        """
        return [(tc, self.analyze(tc)) for tc in tool_calls]


class LLMSecurityAnalyzer(SecurityAnalyzer):
    """Uses an LLM to evaluate tool call risk based on content.

    Args:
        provider: LLM provider for risk evaluation.
        model: Optional model override (prefer a cheap/fast model).
    """

    def __init__(self, provider: Provider, model: str | None = None) -> None:
        self.provider = provider
        self.model = model

    def analyze(self, tool_call: ToolCall) -> SecurityRisk:
        prompt = self._build_prompt(tool_call)
        from chimera.types import Message

        messages = [Message.user(prompt)]
        response = self.provider.complete(messages, model=self.model)
        return self._parse_risk(response.content)

    def _build_prompt(self, tool_call: ToolCall) -> str:
        return (
            f"Evaluate the security risk of this tool call.\n\n"
            f"Tool: {tool_call.name}\n"
            f"Arguments: {tool_call.arguments}\n\n"
            f"Classify as one of: LOW, MEDIUM, HIGH\n\n"
            f"- LOW: Read-only operations, safe file paths, harmless commands\n"
            f"- MEDIUM: File writes, installs, network access to known endpoints\n"
            f"- HIGH: Destructive operations (rm -rf, DROP TABLE, force push),\n"
            f"        access to credentials, arbitrary code execution with user data,\n"
            f"        network access to unknown endpoints\n\n"
            f"Respond with ONLY the risk level (LOW, MEDIUM, or HIGH)."
        )

    def _parse_risk(self, response: str) -> SecurityRisk:
        text = response.strip().upper()
        for risk in SecurityRisk:
            if risk.name in text:
                return risk
        return SecurityRisk.UNKNOWN


class RuleBasedSecurityAnalyzer(SecurityAnalyzer):
    """Fast pattern-matching analyzer for known-dangerous patterns."""

    DANGEROUS_PATTERNS = [
        "rm -rf",
        "drop table",
        "drop database",
        "format c:",
        "--force",
        "chmod 777",
        "> /dev/",
        "mkfs.",
        "dd if=",
    ]

    def analyze(self, tool_call: ToolCall) -> SecurityRisk:
        args_str = str(tool_call.arguments).lower()
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern.lower() in args_str:
                return SecurityRisk.HIGH
        return SecurityRisk.LOW


class CompositeSecurityAnalyzer(SecurityAnalyzer):
    """Run rule-based first (fast), escalate to LLM for uncertain cases.

    Args:
        rule_analyzer: Fast pattern-matching analyzer.
        llm_analyzer: LLM-powered analyzer for uncertain cases.
    """

    def __init__(
        self,
        rule_analyzer: RuleBasedSecurityAnalyzer,
        llm_analyzer: LLMSecurityAnalyzer,
    ) -> None:
        self.rule_analyzer = rule_analyzer
        self.llm_analyzer = llm_analyzer

    def analyze(self, tool_call: ToolCall) -> SecurityRisk:
        fast_result = self.rule_analyzer.analyze(tool_call)
        if fast_result == SecurityRisk.HIGH:
            return fast_result
        return self.llm_analyzer.analyze(tool_call)
