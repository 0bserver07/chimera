"""Central permission checker implementing the step-by-step algorithm."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.hooks.emitter import HookEmitter
from chimera.hooks.events import HookEvent
from chimera.permissions.context import PermissionContext
from chimera.permissions.decisions import DecisionReason, PermissionDecision
from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import (
    PermissionBehavior,
    PermissionRuleValue,
    RuleSource,
)

if TYPE_CHECKING:
    from chimera.security.analyzer import SecurityAnalyzer

__all__ = ["PermissionChecker"]


# Hook-output ``permissionDecision`` literals understood by the checker.
# ``"defer"`` means "do not override; continue with the next phase".
_HOOK_DECISION_ALLOW = "allow"
_HOOK_DECISION_DENY = "deny"
_HOOK_DECISION_ASK = "ask"
_HOOK_DECISION_DEFER = "defer"


class PermissionChecker:
    """Evaluate whether a tool invocation should be allowed, denied, or require
    user confirmation.

    Algorithm (step numbers reference the spec):

    1a. Deny rules for tool -> deny
    1b. Ask rules for tool -> ask
    1c. SecurityAnalyzer (optional) — escalates risky calls to ASK/DENY
    1c'.Tool-level ``check_permissions`` hook (optional)
    1d. If tool denied -> deny
    1e. If requires_user_interaction and result is ASK -> ask (bypass-immune)
    1g. If result is ASK with reason type "safety_check" -> ask (bypass-immune)
    2a. Bypass / plan / auto / accept_edits mode -> allow (conditionally)
    2b. Allow rules -> allow
    3.  Default -> ask with suggestions
    4.  DONT_ASK post-processing: convert ASK -> DENY

    A ``permission_decision`` argument (typically sourced from a PreToolUse
    hook's ``hookSpecificOutput.permissionDecision``) overrides the resolver
    when set to ``"allow"``, ``"deny"`` or ``"ask"``; the literal ``"defer"``
    is treated as "no override".

    Args:
        security_analyzer: Optional :class:`SecurityAnalyzer` invoked at step
            1c.  When it returns :class:`SecurityRisk.HIGH` (or ``UNKNOWN``,
            which is treated as HIGH), the call is denied immediately.
    """

    def __init__(
        self,
        security_analyzer: SecurityAnalyzer | None = None,
        *,
        hook_emitter: HookEmitter | None = None,
    ) -> None:
        """Construct a checker, optionally wiring a security analyzer.

        Args:
            security_analyzer: Optional analyzer evaluated at step 1c.
            hook_emitter: Optional :class:`HookEmitter` used to fire
                :data:`HookEvent.PERMISSION_REQUEST` whenever the checker
                returns an ``ASK`` decision and
                :data:`HookEvent.PERMISSION_DENIED` whenever it returns
                ``DENY``.  When ``None``, no hook fires (backwards-compat).
        """
        self._security_analyzer = security_analyzer
        self._hook_emitter = hook_emitter

    async def check(
        self,
        tool: Any,
        input_args: dict[str, Any],
        context: PermissionContext,
        *,
        permission_decision: str | None = None,
    ) -> PermissionDecision:
        """Run the permission algorithm and return a decision.

        Args:
            tool: Tool object (must expose ``name``).
            input_args: Tool input dict.
            context: Active :class:`PermissionContext`.
            permission_decision: Override sourced from a PreToolUse hook's
                ``hookSpecificOutput.permissionDecision``.  Recognised values
                are ``"allow"``, ``"deny"``, ``"ask"`` and ``"defer"``.

        Returns:
            A :class:`PermissionDecision`.
        """
        result = await self._check_internal(
            tool, input_args, context, permission_decision=permission_decision
        )
        await self._emit_decision_hooks(tool, input_args, result)
        return result

    async def _emit_decision_hooks(
        self,
        tool: Any,
        input_args: dict[str, Any],
        decision: PermissionDecision,
    ) -> None:
        """Fire PERMISSION_REQUEST / PERMISSION_DENIED on the right outcomes.

        Hooks are best-effort: any exception is swallowed so a misbehaving
        hook can never block a permission decision.
        """
        if self._hook_emitter is None or not self._hook_emitter.active:
            return
        tool_name = getattr(tool, "name", "") or ""
        try:
            if decision.behavior == PermissionBehavior.ASK:
                await self._hook_emitter.emit(
                    HookEvent.PERMISSION_REQUEST,
                    tool_name=tool_name,
                    tool_input=dict(input_args),
                )
            elif decision.behavior == PermissionBehavior.DENY:
                await self._hook_emitter.emit(
                    HookEvent.PERMISSION_DENIED,
                    tool_name=tool_name,
                    tool_input=dict(input_args),
                )
        except Exception:  # pragma: no cover - hook errors must not propagate
            pass

    async def _check_internal(
        self,
        tool: Any,
        input_args: dict[str, Any],
        context: PermissionContext,
        *,
        permission_decision: str | None = None,
    ) -> PermissionDecision:
        """Original permission-resolution algorithm; see :meth:`check`."""
        tool_name: str = tool.name
        content = self._get_content(tool, input_args)

        # ---- Phase 0: hook override --------------------------------------
        # If a PreToolUse hook explicitly set a permissionDecision (other than
        # "defer"), honour it before consulting any rules.
        hook_decision = self._decision_from_hook(
            permission_decision, tool_name, content
        )
        if hook_decision is not None:
            return hook_decision

        # ---- Phase 1: early exits (deny / ask / security / tool-level) ----

        # 1a. Check deny rules
        deny_match = self._find_rule(
            context.deny_rules, tool_name, content, input_args
        )
        if deny_match is not None:
            return PermissionDecision.deny(
                message=f"Denied by rule: {deny_match}",
                reason=DecisionReason.rule(deny_match),
            )

        # 1b. Check ask rules
        ask_match = self._find_rule(
            context.ask_rules, tool_name, content, input_args
        )
        if ask_match is not None:
            return PermissionDecision.ask(
                message=f"Ask rule matched: {ask_match}",
                reason=DecisionReason.rule(ask_match),
                suggestions=self._suggest_rules(tool_name, content),
            )

        # 1c. SecurityAnalyzer (optional) — denies HIGH-risk calls outright.
        sec_decision = self._evaluate_security(tool_name, input_args)
        if sec_decision is not None:
            return sec_decision

        # 1c'/1d. Tool-level check_permissions (optional hook)
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
        allow_match = self._find_rule(
            context.allow_rules, tool_name, content, input_args
        )
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
        tool_input: dict[str, Any] | None = None,
    ) -> str | None:
        """Find the first rule across all sources that matches the call.

        Args:
            rules: Mapping of :class:`RuleSource` to list of rule strings.
            tool_name: Tool name being matched.
            content: Optional extracted content for legacy content match.
            tool_input: Full tool input dict, used for ``arg_key``
                matching (e.g. ``Bash(command:git push *)``).

        Returns:
            The matching rule string or ``None``.
        """
        # Iterate sources in precedence order (highest first) so that
        # higher-precedence matches win.
        for source in sorted(rules, key=lambda s: s.value, reverse=True):
            for rule_str in rules[source]:
                rv = PermissionRuleValue.from_string(rule_str)
                if rv.matches(
                    tool_name,
                    input_content=content,
                    tool_input=tool_input,
                ):
                    return rule_str
        return None

    def _evaluate_security(
        self,
        tool_name: str,
        input_args: dict[str, Any],
    ) -> PermissionDecision | None:
        """Run the wired :class:`SecurityAnalyzer`, if any, at step 1c.

        High-risk calls (or ``UNKNOWN``, treated as HIGH) are denied
        outright with a ``security`` decision reason.  Lower-risk results
        return ``None`` so the algorithm continues.

        Args:
            tool_name: Tool being invoked.
            input_args: Tool input dict.

        Returns:
            A DENY :class:`PermissionDecision` for HIGH risk, otherwise None.
        """
        if self._security_analyzer is None:
            return None

        # Build a duck-typed ToolCall — the analyzer only reads ``name`` and
        # ``arguments``, so we avoid importing chimera.types here.
        from types import SimpleNamespace

        from chimera.security.risk import SecurityRisk

        tool_call = SimpleNamespace(name=tool_name, arguments=input_args)
        try:
            risk = self._security_analyzer.analyze(tool_call)  # type: ignore[arg-type]
        except Exception:  # pragma: no cover - analyzer is best-effort
            return None

        if risk == SecurityRisk.HIGH or risk == SecurityRisk.UNKNOWN:
            return PermissionDecision.deny(
                message=f"Denied by security analyzer (risk={risk.name}).",
                reason=DecisionReason(type="security", detail=risk.name),
            )
        return None

    @staticmethod
    def _decision_from_hook(
        permission_decision: str | None,
        tool_name: str,
        content: str | None,
    ) -> PermissionDecision | None:
        """Translate a hook ``permissionDecision`` literal into a decision.

        Args:
            permission_decision: One of ``"allow"``, ``"deny"``, ``"ask"``,
                ``"defer"``, or ``None``.
            tool_name: Tool name (for human-readable messages).
            content: Extracted content (for suggestion display).

        Returns:
            A :class:`PermissionDecision` for ``allow``/``deny``/``ask``,
            or ``None`` for ``defer``/``None``/unknown.
        """
        if permission_decision is None:
            return None
        decision = permission_decision.lower()
        if decision in (_HOOK_DECISION_DEFER, ""):
            return None
        reason = DecisionReason(type="hook", detail=decision)
        if decision == _HOOK_DECISION_ALLOW:
            return PermissionDecision.allow(
                message=f"Allowed by PreToolUse hook for {tool_name}.",
                reason=reason,
            )
        if decision == _HOOK_DECISION_DENY:
            return PermissionDecision.deny(
                message=f"Denied by PreToolUse hook for {tool_name}.",
                reason=reason,
            )
        if decision == _HOOK_DECISION_ASK:
            return PermissionDecision.ask(
                message=f"PreToolUse hook requested confirmation for {tool_name}.",
                reason=reason,
                suggestions=[tool_name] + ([f"{tool_name}({content})"] if content else []),
            )
        # Unknown literal — defer to the resolver.
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
            content: str | None = get_fn(input_args)
            return content
        return None
