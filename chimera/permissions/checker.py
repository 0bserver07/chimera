"""Central permission checker implementing the step-by-step algorithm."""
from __future__ import annotations

from typing import Any

from chimera.permissions.context import PermissionContext
from chimera.permissions.decisions import DecisionReason, PermissionDecision
from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import (
    PermissionBehavior,
    PermissionRuleValue,
    RuleSource,
)

__all__ = ["PermissionChecker"]


class PermissionChecker:
    """Evaluate whether a tool invocation should be allowed, denied, or require
    user confirmation.

    Algorithm (step numbers reference the spec):

    1a. Deny rules for tool -> deny
    1b. Ask rules for tool -> ask
    1c. Call tool.check_permissions() if available
    1d. If tool denied -> deny
    1e. If requires_user_interaction and result is ASK -> ask (bypass-immune)
    1g. If result is ASK with reason type "safety_check" -> ask (bypass-immune)
    2a. Bypass / plan / auto / accept_edits mode -> allow (conditionally)
    2b. Allow rules -> allow
    3.  Default -> ask with suggestions
    4.  DONT_ASK post-processing: convert ASK -> DENY
    """

    async def check(
        self,
        tool: Any,
        input_args: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Run the permission algorithm and return a decision."""
        tool_name: str = tool.name
        content = self._get_content(tool, input_args)

        # ---- Phase 1: early exits (deny / ask / tool-level) ---------------

        # 1a. Check deny rules
        deny_match = self._find_rule(context.deny_rules, tool_name, content)
        if deny_match is not None:
            return PermissionDecision.deny(
                message=f"Denied by rule: {deny_match}",
                reason=DecisionReason.rule(deny_match),
            )

        # 1b. Check ask rules
        ask_match = self._find_rule(context.ask_rules, tool_name, content)
        if ask_match is not None:
            return PermissionDecision.ask(
                message=f"Ask rule matched: {ask_match}",
                reason=DecisionReason.rule(ask_match),
                suggestions=self._suggest_rules(tool_name, content),
            )

        # 1c/1d. Tool-level check_permissions (optional hook)
        check_fn = getattr(tool, "check_permissions", None)
        tool_decision: PermissionDecision | None = None
        if check_fn is not None:
            tool_decision = check_fn(input_args, context)
            if tool_decision is not None:
                # 1d. If tool explicitly denied, honour it immediately.
                if tool_decision.behavior == PermissionBehavior.DENY:
                    return tool_decision

                # 1e. If tool requires user interaction and result is ASK,
                # return immediately (bypass-immune).
                if (
                    tool_decision.behavior == PermissionBehavior.ASK
                    and getattr(tool, "requires_user_interaction", False)
                ):
                    return tool_decision

                # 1g. If result is ASK with reason type "safety_check",
                # return immediately (bypass-immune).
                if (
                    tool_decision.behavior == PermissionBehavior.ASK
                    and tool_decision.reason is not None
                    and tool_decision.reason.type == "safety_check"
                ):
                    return tool_decision

                # If tool returned ALLOW, honour it.
                if tool_decision.behavior == PermissionBehavior.ALLOW:
                    return tool_decision

                # Otherwise (non-bypass-immune ASK), fall through to phase 2.

        # ---- Phase 2: mode-based auto-allow --------------------------------

        # 2a. Bypass / auto mode -> allow
        if context.mode in (PermissionMode.BYPASS, PermissionMode.AUTO):
            return PermissionDecision.allow(
                message=f"Allowed by mode: {context.mode.value}",
                reason=DecisionReason.mode(context.mode.value),
            )

        # 2a (PLAN). Plan mode -> allow only if bypass is available
        if context.mode == PermissionMode.PLAN and context.is_bypass_available:
            return PermissionDecision.allow(
                message=f"Allowed by mode: {context.mode.value}",
                reason=DecisionReason.mode(context.mode.value),
            )

        # 2a (ACCEPT_EDITS). Auto-allow file edit tools.
        if context.mode == PermissionMode.ACCEPT_EDITS and self._is_edit_tool(tool):
            return PermissionDecision.allow(
                message=f"Allowed by mode: {context.mode.value}",
                reason=DecisionReason.mode(context.mode.value),
            )

        # 2b. Allow rules
        allow_match = self._find_rule(context.allow_rules, tool_name, content)
        if allow_match is not None:
            return PermissionDecision.allow(
                message=f"Allowed by rule: {allow_match}",
                reason=DecisionReason.rule(allow_match),
            )

        # ---- Phase 3: default -> ask --------------------------------------

        result = PermissionDecision.ask(
            message="No matching rule; user approval required.",
            suggestions=self._suggest_rules(tool_name, content),
        )

        # ---- Phase 4: DONT_ASK post-processing ----------------------------

        if result.behavior == PermissionBehavior.ASK and context.mode == PermissionMode.DONT_ASK:
            return PermissionDecision.deny(
                message="Denied: DONT_ASK mode is active.",
                reason=DecisionReason.mode(context.mode.value),
            )

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_rule(
        self,
        rules: dict[RuleSource, list[str]],
        tool_name: str,
        content: str | None = None,
    ) -> str | None:
        """Search *rules* (across all sources) for the first match against
        *tool_name* (and optionally *content*).  Returns the matched rule
        string or ``None``."""
        # Iterate sources in precedence order (highest first) so that
        # higher-precedence matches win.
        for source in sorted(rules, key=lambda s: s.value, reverse=True):
            for rule_str in rules[source]:
                rv = PermissionRuleValue.from_string(rule_str)
                if rv.matches(tool_name, input_content=content):
                    return rule_str
        return None

    def _suggest_rules(self, tool_name: str, content: str | None = None) -> list[str]:
        """Generate suggested allow/deny rule strings for this tool."""
        suggestions: list[str] = []
        suggestions.append(tool_name)
        if content:
            suggestions.append(f"{tool_name}({content})")
        return suggestions

    @staticmethod
    def _is_edit_tool(tool: Any) -> bool:
        """Return ``True`` if *tool* is a file-edit tool (non-read-only with
        name containing 'edit' or 'write')."""
        is_read_only = getattr(tool, "is_read_only", True)
        if is_read_only:
            return False
        name_lower = getattr(tool, "name", "").lower()
        return "edit" in name_lower or "write" in name_lower

    @staticmethod
    def _get_content(tool: Any, input_args: dict[str, Any]) -> str | None:
        """Extract 'content' from the tool (via optional hook) or return None."""
        get_fn = getattr(tool, "get_permission_content", None)
        if get_fn is not None:
            return get_fn(input_args)
        return None
