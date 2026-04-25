"""Tests for the extended ``Tool(arg_key:pattern)`` rule grammar (M2-B).

Covers:
    * arg-key matching for Bash/Read/WebFetch-style patterns
    * tool-name globs (``mcp__*``)
    * back-compat for legacy ``Tool(content_pattern)`` rules
    * bare-tool-name matches (no parens)
    * SecurityAnalyzer wiring at step 1c of :class:`PermissionChecker`
    * PreToolUse-hook ``permissionDecision`` override (allow / deny / defer)
"""
from __future__ import annotations

from typing import Any

import pytest

from chimera.permissions.checker import PermissionChecker
from chimera.permissions.context import PermissionContext
from chimera.permissions.decisions import PermissionDecision
from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import (
    PermissionBehavior,
    PermissionRuleValue,
    RuleSource,
)
from chimera.security.analyzer import SecurityAnalyzer
from chimera.security.risk import SecurityRisk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _StubTool:
    """Minimal tool object — exposes only ``name`` and no permission hooks."""

    def __init__(self, name: str) -> None:
        self.name = name


def _ctx(**kwargs: Any) -> PermissionContext:
    return PermissionContext(mode=PermissionMode.DEFAULT, **kwargs)


# ---------------------------------------------------------------------------
# 1. arg-key parsing + matching
# ---------------------------------------------------------------------------

def test_arg_key_match() -> None:
    """``Bash(command:git push *)`` matches by the ``command`` arg key."""
    rv = PermissionRuleValue.from_string("Bash(command:git push *)")
    assert rv.tool_name == "Bash"
    assert rv.arg_key == "command"
    assert rv.arg_pattern == "git push *"
    assert rv.content is None

    assert rv.matches("Bash", tool_input={"command": "git push origin main"})
    assert not rv.matches("Bash", tool_input={"command": "git status"})


def test_path_glob() -> None:
    """``Read(path:/Users/yadkonrad/**)`` matches paths under a prefix."""
    rv = PermissionRuleValue.from_string("Read(path:/Users/yadkonrad/**)")
    assert rv.arg_key == "path"
    assert rv.arg_pattern == "/Users/yadkonrad/**"

    assert rv.matches("Read", tool_input={"path": "/Users/yadkonrad/dev/foo.py"})
    assert not rv.matches("Read", tool_input={"path": "/etc/passwd"})


def test_url_glob() -> None:
    """WebFetch URL globs work via the ``url`` arg key."""
    rv = PermissionRuleValue.from_string(
        "WebFetch(url:https://docs.anthropic.com/*)"
    )
    assert rv.matches(
        "WebFetch",
        tool_input={"url": "https://docs.anthropic.com/en/docs/foo"},
    )
    assert not rv.matches(
        "WebFetch", tool_input={"url": "https://example.com"}
    )


# ---------------------------------------------------------------------------
# 2. tool-name globs
# ---------------------------------------------------------------------------

def test_tool_glob() -> None:
    """``mcp__*`` matches any MCP tool by glob on the tool name."""
    rv = PermissionRuleValue.from_string("mcp__*")
    assert rv.matches("mcp__filesystem__read_file")
    assert rv.matches("mcp__github__list_repos")
    assert not rv.matches("Bash")


# ---------------------------------------------------------------------------
# 3. back-compat: no colon → legacy content match
# ---------------------------------------------------------------------------

def test_back_compat_no_colon() -> None:
    """``Bash(git push *)`` (no arg key) still works as content match."""
    rv = PermissionRuleValue.from_string("Bash(git push *)")
    assert rv.arg_key is None
    assert rv.arg_pattern is None
    assert rv.content == "git push *"

    assert rv.matches("Bash", input_content="git push origin main")
    assert not rv.matches("Bash", input_content="git status")


def test_back_compat_content_with_colon_in_value() -> None:
    """A body whose LHS isn't an identifier stays a content pattern."""
    # "ls -la:foo" has a space, so it's not parsed as arg_key:arg_pattern.
    rv = PermissionRuleValue.from_string("Bash(ls -la:foo)")
    assert rv.arg_key is None
    assert rv.content == "ls -la:foo"


# ---------------------------------------------------------------------------
# 4. bare tool name
# ---------------------------------------------------------------------------

def test_bare_tool_name() -> None:
    """``Bash`` matches any Bash invocation regardless of args."""
    rv = PermissionRuleValue.from_string("Bash")
    assert rv.matches("Bash")
    assert rv.matches("Bash", tool_input={"command": "anything"})
    assert rv.matches("Bash", input_content="anything")
    assert not rv.matches("Read")


# ---------------------------------------------------------------------------
# 5. SecurityAnalyzer wiring at step 1c
# ---------------------------------------------------------------------------

class _DenyAllAnalyzer(SecurityAnalyzer):
    """Stub analyzer that always reports HIGH risk."""

    def analyze(self, tool_call: Any) -> SecurityRisk:
        return SecurityRisk.HIGH


class _LowRiskAnalyzer(SecurityAnalyzer):
    """Stub analyzer that reports LOW risk for everything."""

    def analyze(self, tool_call: Any) -> SecurityRisk:
        return SecurityRisk.LOW


@pytest.mark.asyncio
async def test_security_analyzer_phase() -> None:
    """Step 1c denies when the analyzer flags HIGH risk and no rule matched."""
    checker = PermissionChecker(security_analyzer=_DenyAllAnalyzer())
    ctx = _ctx()  # no rules at all
    decision = await checker.check(_StubTool("Bash"), {"command": "ls"}, ctx)
    assert decision.behavior == PermissionBehavior.DENY
    assert decision.reason is not None
    assert decision.reason.type == "security"


@pytest.mark.asyncio
async def test_security_analyzer_low_risk_falls_through() -> None:
    """LOW-risk analyzer does not deny — algorithm proceeds to default ASK."""
    checker = PermissionChecker(security_analyzer=_LowRiskAnalyzer())
    ctx = _ctx()
    decision = await checker.check(_StubTool("Bash"), {"command": "ls"}, ctx)
    assert decision.behavior == PermissionBehavior.ASK


# ---------------------------------------------------------------------------
# 6. PreToolUse hook permissionDecision override
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hook_decision_override() -> None:
    """A hook ``permissionDecision: allow`` overrides the default ASK."""
    checker = PermissionChecker()
    ctx = _ctx()
    decision = await checker.check(
        _StubTool("Bash"),
        {"command": "rm -rf /tmp/scratch"},
        ctx,
        permission_decision="allow",
    )
    assert decision.behavior == PermissionBehavior.ALLOW
    assert decision.reason is not None
    assert decision.reason.type == "hook"


@pytest.mark.asyncio
async def test_hook_decision_override_deny_beats_no_rule() -> None:
    """Hook ``deny`` short-circuits even when no deny rule matched."""
    checker = PermissionChecker()
    ctx = _ctx(allow_rules={RuleSource.PROJECT: ["Bash"]})
    decision = await checker.check(
        _StubTool("Bash"),
        {"command": "ls"},
        ctx,
        permission_decision="deny",
    )
    assert decision.behavior == PermissionBehavior.DENY


@pytest.mark.asyncio
async def test_hook_decision_defer_continues() -> None:
    """``defer`` (and ``None``) leaves the resolver untouched."""
    checker = PermissionChecker()
    ctx = _ctx(allow_rules={RuleSource.PROJECT: ["Bash"]})
    decision = await checker.check(
        _StubTool("Bash"),
        {"command": "ls"},
        ctx,
        permission_decision="defer",
    )
    assert decision.behavior == PermissionBehavior.ALLOW


# ---------------------------------------------------------------------------
# 7. End-to-end: arg-key rule wired through the checker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_arg_key_deny_rule_via_checker() -> None:
    """``Bash(command:git push *)`` deny rule blocks matching invocations."""
    checker = PermissionChecker()
    ctx = _ctx(deny_rules={RuleSource.PROJECT: ["Bash(command:git push *)"]})
    blocked = await checker.check(
        _StubTool("Bash"), {"command": "git push origin main"}, ctx
    )
    assert blocked.behavior == PermissionBehavior.DENY

    allowed = await checker.check(
        _StubTool("Bash"), {"command": "git status"}, ctx
    )
    # No allow rule, no deny match -> default ASK
    assert allowed.behavior == PermissionBehavior.ASK


@pytest.mark.asyncio
async def test_arg_key_allow_rule_via_checker() -> None:
    """``Read(path:/Users/yadkonrad/**)`` allows reads under the prefix."""
    checker = PermissionChecker()
    ctx = _ctx(allow_rules={RuleSource.PROJECT: ["Read(path:/Users/yadkonrad/**)"]})
    allow = await checker.check(
        _StubTool("Read"),
        {"path": "/Users/yadkonrad/foo.py"},
        ctx,
    )
    assert allow.behavior == PermissionBehavior.ALLOW

    other = await checker.check(
        _StubTool("Read"),
        {"path": "/etc/passwd"},
        ctx,
    )
    assert other.behavior == PermissionBehavior.ASK


def test_to_string_roundtrip_arg_key() -> None:
    """``to_string`` round-trips an arg-key rule."""
    rv = PermissionRuleValue.from_string("Bash(command:git push *)")
    assert rv.to_string() == "Bash(command:git push *)"
    rv2 = PermissionRuleValue.from_string(rv.to_string())
    assert rv2.arg_key == "command"
    assert rv2.arg_pattern == "git push *"


# Sanity: PermissionDecision is reachable via this test module so the import
# is not unused — ruff would otherwise prune it.
assert PermissionDecision is not None
