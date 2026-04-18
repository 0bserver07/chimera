"""Tests for chimera.permissions.rules — RuleSource, PermissionBehavior,
PermissionRuleValue, PermissionRule."""
from __future__ import annotations


from chimera.permissions.rules import (
    PermissionBehavior,
    PermissionRule,
    PermissionRuleValue,
    RuleSource,
)


# ---------------------------------------------------------------------------
# RuleSource
# ---------------------------------------------------------------------------

class TestRuleSource:
    """RuleSource must have exactly 8 members in precedence order."""

    def test_members(self) -> None:
        expected = [
            "POLICY", "FLAG", "LOCAL", "PROJECT",
            "USER", "CLI_ARG", "COMMAND", "SESSION",
        ]
        assert [m.name for m in RuleSource] == expected

    def test_ordering(self) -> None:
        """Lower-precedence sources appear earlier in declaration order."""
        members = list(RuleSource)
        assert members.index(RuleSource.POLICY) < members.index(RuleSource.SESSION)
        assert members.index(RuleSource.FLAG) < members.index(RuleSource.CLI_ARG)


# ---------------------------------------------------------------------------
# PermissionBehavior
# ---------------------------------------------------------------------------

class TestPermissionBehavior:
    def test_values(self) -> None:
        assert PermissionBehavior.ALLOW.value == "allow"
        assert PermissionBehavior.DENY.value == "deny"
        assert PermissionBehavior.ASK.value == "ask"


# ---------------------------------------------------------------------------
# PermissionRuleValue — from_string / to_string / matches
# ---------------------------------------------------------------------------

class TestPermissionRuleValueParsing:
    """from_string must parse 'ToolName(content)' format."""

    def test_simple_tool_name(self) -> None:
        v = PermissionRuleValue.from_string("Bash")
        assert v.tool_name == "Bash"
        assert v.content is None

    def test_tool_with_content(self) -> None:
        v = PermissionRuleValue.from_string("Bash(ls -la)")
        assert v.tool_name == "Bash"
        assert v.content == "ls -la"

    def test_tool_with_empty_content(self) -> None:
        v = PermissionRuleValue.from_string("Bash()")
        assert v.tool_name == "Bash"
        assert v.content == ""

    def test_tool_with_nested_parens(self) -> None:
        v = PermissionRuleValue.from_string("Bash(echo (hello))")
        assert v.tool_name == "Bash"
        assert v.content == "echo (hello)"

    def test_tool_with_escaped_paren(self) -> None:
        v = PermissionRuleValue.from_string(r"Bash(echo \) done)")
        assert v.tool_name == "Bash"
        assert v.content == "echo ) done"

    def test_wildcard_tool(self) -> None:
        v = PermissionRuleValue.from_string("*")
        assert v.tool_name == "*"
        assert v.content is None


class TestPermissionRuleValueToString:
    def test_roundtrip_simple(self) -> None:
        v = PermissionRuleValue(tool_name="Bash", content=None)
        assert v.to_string() == "Bash"

    def test_roundtrip_with_content(self) -> None:
        v = PermissionRuleValue(tool_name="Bash", content="ls -la")
        assert v.to_string() == "Bash(ls -la)"

    def test_roundtrip_empty_content(self) -> None:
        v = PermissionRuleValue(tool_name="Bash", content="")
        assert v.to_string() == "Bash()"


class TestPermissionRuleValueMatches:
    def test_exact_match(self) -> None:
        v = PermissionRuleValue(tool_name="Bash", content=None)
        assert v.matches("Bash") is True

    def test_no_match(self) -> None:
        v = PermissionRuleValue(tool_name="Bash", content=None)
        assert v.matches("Write") is False

    def test_wildcard_matches_anything(self) -> None:
        v = PermissionRuleValue(tool_name="*", content=None)
        assert v.matches("Bash") is True
        assert v.matches("Write") is True

    def test_glob_pattern(self) -> None:
        v = PermissionRuleValue(tool_name="mcp__server*", content=None)
        assert v.matches("mcp__server__tool") is True
        assert v.matches("mcp__other__tool") is False

    def test_mcp_server_level_match(self) -> None:
        """mcp__server (no trailing wildcard) should match mcp__server__tool
        to support server-level rules."""
        v = PermissionRuleValue(tool_name="mcp__server", content=None)
        assert v.matches("mcp__server__tool") is True
        assert v.matches("mcp__server") is True
        assert v.matches("mcp__other__tool") is False

    def test_content_match(self) -> None:
        v = PermissionRuleValue(tool_name="Bash", content="ls*")
        assert v.matches("Bash", input_content="ls -la") is True
        assert v.matches("Bash", input_content="rm -rf /") is False

    def test_content_no_input(self) -> None:
        """If rule has content pattern but no input_content given, no match."""
        v = PermissionRuleValue(tool_name="Bash", content="ls*")
        assert v.matches("Bash") is False


# ---------------------------------------------------------------------------
# PermissionRule
# ---------------------------------------------------------------------------

class TestPermissionRule:
    def test_construction(self) -> None:
        value = PermissionRuleValue(tool_name="Bash", content=None)
        rule = PermissionRule(
            source=RuleSource.PROJECT,
            behavior=PermissionBehavior.ALLOW,
            value=value,
        )
        assert rule.source == RuleSource.PROJECT
        assert rule.behavior == PermissionBehavior.ALLOW
        assert rule.value.tool_name == "Bash"
