# tests/test_permissions.py
from __future__ import annotations

import pytest

from chimera.permissions import (
    AllowList,
    AlwaysDeny,
    AutoApprove,
    Interactive,
    PermissionAction,
    PermissionPolicy,
    PermissionRuleset,
    ReadOnly,
    Rule,
    matches_pattern,
)


# ---------------------------------------------------------------------------
# PermissionAction enum
# ---------------------------------------------------------------------------


class TestPermissionAction:
    def test_allow_value(self) -> None:
        assert PermissionAction.ALLOW.value == "allow"

    def test_deny_value(self) -> None:
        assert PermissionAction.DENY.value == "deny"

    def test_ask_value(self) -> None:
        assert PermissionAction.ASK.value == "ask"

    def test_enum_members_count(self) -> None:
        assert len(PermissionAction) == 3

    def test_from_string(self) -> None:
        assert PermissionAction("allow") is PermissionAction.ALLOW
        assert PermissionAction("deny") is PermissionAction.DENY
        assert PermissionAction("ask") is PermissionAction.ASK


# ---------------------------------------------------------------------------
# PermissionPolicy ABC
# ---------------------------------------------------------------------------


class TestPermissionPolicyABC:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            PermissionPolicy()  # type: ignore[abstract]

    def test_subclass_must_implement_evaluate(self) -> None:
        class Incomplete(PermissionPolicy):
            pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# matches_pattern utility
# ---------------------------------------------------------------------------


class TestMatchesPattern:
    def test_exact_match(self) -> None:
        assert matches_pattern("bash", "bash") is True

    def test_no_match(self) -> None:
        assert matches_pattern("bash", "read") is False

    def test_wildcard_star(self) -> None:
        assert matches_pattern("anything", "*") is True

    def test_prefix_glob(self) -> None:
        assert matches_pattern("write_file", "write_*") is True

    def test_prefix_glob_no_match(self) -> None:
        assert matches_pattern("read_file", "write_*") is False

    def test_suffix_glob(self) -> None:
        assert matches_pattern("my_search", "*_search") is True

    def test_question_mark(self) -> None:
        assert matches_pattern("bash", "bas?") is True
        assert matches_pattern("base", "bas?") is True
        assert matches_pattern("ba", "bas?") is False

    def test_empty_pattern(self) -> None:
        assert matches_pattern("", "") is True
        assert matches_pattern("x", "") is False

    def test_character_class(self) -> None:
        assert matches_pattern("cat", "[cb]at") is True
        assert matches_pattern("bat", "[cb]at") is True
        assert matches_pattern("hat", "[cb]at") is False


# ---------------------------------------------------------------------------
# AutoApprove preset
# ---------------------------------------------------------------------------


class TestAutoApprove:
    def test_always_allows(self) -> None:
        policy = AutoApprove()
        assert policy.evaluate("bash", {}) is PermissionAction.ALLOW

    def test_allows_any_tool(self) -> None:
        policy = AutoApprove()
        assert policy.evaluate("write_file", {"path": "/etc/passwd"}) is PermissionAction.ALLOW

    def test_allows_unknown_tool(self) -> None:
        policy = AutoApprove()
        assert policy.evaluate("nonexistent_tool", {}) is PermissionAction.ALLOW


# ---------------------------------------------------------------------------
# AlwaysDeny preset
# ---------------------------------------------------------------------------


class TestAlwaysDeny:
    def test_always_denies(self) -> None:
        policy = AlwaysDeny()
        assert policy.evaluate("read_file", {}) is PermissionAction.DENY

    def test_denies_any_tool(self) -> None:
        policy = AlwaysDeny()
        assert policy.evaluate("bash", {"command": "ls"}) is PermissionAction.DENY

    def test_denies_unknown_tool(self) -> None:
        policy = AlwaysDeny()
        assert policy.evaluate("safe_tool", {}) is PermissionAction.DENY


# ---------------------------------------------------------------------------
# AllowList preset
# ---------------------------------------------------------------------------


class TestAllowList:
    def test_allows_listed_tool(self) -> None:
        policy = AllowList(allowed=["read_file", "search"])
        assert policy.evaluate("read_file", {}) is PermissionAction.ALLOW

    def test_allows_all_listed_tools(self) -> None:
        policy = AllowList(allowed=["read_file", "search"])
        assert policy.evaluate("search", {}) is PermissionAction.ALLOW

    def test_denies_unlisted_tool(self) -> None:
        policy = AllowList(allowed=["read_file"])
        assert policy.evaluate("bash", {}) is PermissionAction.DENY

    def test_empty_allow_list_denies_all(self) -> None:
        policy = AllowList(allowed=[])
        assert policy.evaluate("read_file", {}) is PermissionAction.DENY

    def test_ignores_args(self) -> None:
        policy = AllowList(allowed=["bash"])
        assert policy.evaluate("bash", {"command": "rm -rf /"}) is PermissionAction.ALLOW


# ---------------------------------------------------------------------------
# ReadOnly preset
# ---------------------------------------------------------------------------


class TestReadOnly:
    def test_allows_read_file(self) -> None:
        policy = ReadOnly()
        assert policy.evaluate("read_file", {}) is PermissionAction.ALLOW

    def test_allows_search(self) -> None:
        policy = ReadOnly()
        assert policy.evaluate("search", {}) is PermissionAction.ALLOW

    def test_allows_list_files(self) -> None:
        policy = ReadOnly()
        assert policy.evaluate("list_files", {}) is PermissionAction.ALLOW

    def test_allows_repo_map(self) -> None:
        policy = ReadOnly()
        assert policy.evaluate("repo_map", {}) is PermissionAction.ALLOW

    def test_denies_bash(self) -> None:
        policy = ReadOnly()
        assert policy.evaluate("bash", {}) is PermissionAction.DENY

    def test_denies_write_file(self) -> None:
        policy = ReadOnly()
        assert policy.evaluate("write_file", {}) is PermissionAction.DENY

    def test_denies_edit_file(self) -> None:
        policy = ReadOnly()
        assert policy.evaluate("edit_file", {}) is PermissionAction.DENY

    def test_denies_unknown_tool(self) -> None:
        policy = ReadOnly()
        assert policy.evaluate("unknown", {}) is PermissionAction.DENY


# ---------------------------------------------------------------------------
# Interactive preset
# ---------------------------------------------------------------------------


class TestInteractive:
    def test_allows_read_tools(self) -> None:
        policy = Interactive()
        for tool in ("read_file", "search", "list_files", "repo_map"):
            assert policy.evaluate(tool, {}) is PermissionAction.ALLOW

    def test_asks_for_write_tools(self) -> None:
        policy = Interactive()
        for tool in ("bash", "write_file", "edit_file", "replace_in_file", "git"):
            assert policy.evaluate(tool, {}) is PermissionAction.ASK

    def test_asks_for_unknown_tools(self) -> None:
        policy = Interactive()
        assert policy.evaluate("deploy_nuke", {}) is PermissionAction.ASK


# ---------------------------------------------------------------------------
# Rule dataclass
# ---------------------------------------------------------------------------


class TestRule:
    def test_rule_defaults(self) -> None:
        rule = Rule(tool_pattern="*", action=PermissionAction.ALLOW)
        assert rule.arg_key is None
        assert rule.arg_pattern is None
        assert rule.description == ""

    def test_rule_with_all_fields(self) -> None:
        rule = Rule(
            tool_pattern="bash",
            action=PermissionAction.DENY,
            arg_key="command",
            arg_pattern="rm *",
            description="Deny dangerous rm commands",
        )
        assert rule.tool_pattern == "bash"
        assert rule.action is PermissionAction.DENY
        assert rule.arg_key == "command"
        assert rule.arg_pattern == "rm *"
        assert rule.description == "Deny dangerous rm commands"


# ---------------------------------------------------------------------------
# PermissionRuleset
# ---------------------------------------------------------------------------


class TestPermissionRuleset:
    def test_no_rules_returns_default(self) -> None:
        ruleset = PermissionRuleset(rules=[])
        assert ruleset.evaluate("bash", {}) is PermissionAction.ASK

    def test_no_rules_with_custom_default(self) -> None:
        ruleset = PermissionRuleset(rules=[], default=PermissionAction.DENY)
        assert ruleset.evaluate("bash", {}) is PermissionAction.DENY

    def test_single_matching_rule(self) -> None:
        ruleset = PermissionRuleset(rules=[
            Rule(tool_pattern="bash", action=PermissionAction.DENY),
        ])
        assert ruleset.evaluate("bash", {}) is PermissionAction.DENY

    def test_single_non_matching_rule(self) -> None:
        ruleset = PermissionRuleset(rules=[
            Rule(tool_pattern="write_file", action=PermissionAction.DENY),
        ])
        assert ruleset.evaluate("bash", {}) is PermissionAction.ASK

    def test_tool_pattern_glob(self) -> None:
        ruleset = PermissionRuleset(rules=[
            Rule(tool_pattern="write_*", action=PermissionAction.ASK),
        ])
        assert ruleset.evaluate("write_file", {}) is PermissionAction.ASK
        assert ruleset.evaluate("write_config", {}) is PermissionAction.ASK
        assert ruleset.evaluate("read_file", {}) is PermissionAction.ASK  # default

    def test_wildcard_rule_matches_everything(self) -> None:
        ruleset = PermissionRuleset(rules=[
            Rule(tool_pattern="*", action=PermissionAction.ALLOW),
        ])
        assert ruleset.evaluate("bash", {}) is PermissionAction.ALLOW
        assert ruleset.evaluate("read_file", {}) is PermissionAction.ALLOW

    def test_last_match_wins(self) -> None:
        ruleset = PermissionRuleset(rules=[
            Rule(tool_pattern="*", action=PermissionAction.ALLOW),
            Rule(tool_pattern="bash", action=PermissionAction.DENY),
        ])
        # "bash" matches both rules; the last one (DENY) wins
        assert ruleset.evaluate("bash", {}) is PermissionAction.DENY
        # "read_file" only matches the wildcard; ALLOW
        assert ruleset.evaluate("read_file", {}) is PermissionAction.ALLOW

    def test_last_match_wins_complex(self) -> None:
        ruleset = PermissionRuleset(rules=[
            Rule(tool_pattern="*", action=PermissionAction.DENY),
            Rule(tool_pattern="read_*", action=PermissionAction.ALLOW),
            Rule(tool_pattern="*", action=PermissionAction.ASK),
        ])
        # Everything matches the last wildcard, so ASK always wins
        assert ruleset.evaluate("read_file", {}) is PermissionAction.ASK
        assert ruleset.evaluate("bash", {}) is PermissionAction.ASK

    def test_arg_key_and_arg_pattern_matching(self) -> None:
        ruleset = PermissionRuleset(rules=[
            Rule(
                tool_pattern="bash",
                action=PermissionAction.DENY,
                arg_key="command",
                arg_pattern="rm *",
            ),
        ])
        # Matches both tool and arg pattern
        assert ruleset.evaluate("bash", {"command": "rm -rf /"}) is PermissionAction.DENY
        # Matches tool but not arg pattern
        assert ruleset.evaluate("bash", {"command": "ls"}) is PermissionAction.ASK
        # Matches tool but arg_key missing
        assert ruleset.evaluate("bash", {}) is PermissionAction.ASK

    def test_arg_pattern_without_match_falls_through(self) -> None:
        ruleset = PermissionRuleset(rules=[
            Rule(
                tool_pattern="write_file",
                action=PermissionAction.DENY,
                arg_key="path",
                arg_pattern="/etc/*",
            ),
            Rule(tool_pattern="write_file", action=PermissionAction.ALLOW),
        ])
        # Path matches /etc/*, but ALLOW rule comes later and also matches
        assert ruleset.evaluate("write_file", {"path": "/etc/passwd"}) is PermissionAction.ALLOW
        # Non-/etc/ path: first rule doesn't match, second does
        assert ruleset.evaluate("write_file", {"path": "/tmp/foo"}) is PermissionAction.ALLOW

    def test_arg_pattern_last_match_wins_with_deny_last(self) -> None:
        ruleset = PermissionRuleset(rules=[
            Rule(tool_pattern="write_file", action=PermissionAction.ALLOW),
            Rule(
                tool_pattern="write_file",
                action=PermissionAction.DENY,
                arg_key="path",
                arg_pattern="/etc/*",
            ),
        ])
        # /etc/passwd matches both; DENY is last, so it wins
        assert ruleset.evaluate("write_file", {"path": "/etc/passwd"}) is PermissionAction.DENY
        # /tmp/foo matches first but not second; ALLOW wins
        assert ruleset.evaluate("write_file", {"path": "/tmp/foo"}) is PermissionAction.ALLOW

    def test_arg_value_coerced_to_str(self) -> None:
        """Non-string arg values are coerced via str() for pattern matching."""
        ruleset = PermissionRuleset(rules=[
            Rule(
                tool_pattern="bash",
                action=PermissionAction.DENY,
                arg_key="timeout",
                arg_pattern="99*",
            ),
        ])
        assert ruleset.evaluate("bash", {"timeout": 999}) is PermissionAction.DENY
        assert ruleset.evaluate("bash", {"timeout": 10}) is PermissionAction.ASK

    def test_multiple_rules_different_tools(self) -> None:
        ruleset = PermissionRuleset(rules=[
            Rule(tool_pattern="bash", action=PermissionAction.DENY),
            Rule(tool_pattern="read_*", action=PermissionAction.ALLOW),
            Rule(tool_pattern="write_*", action=PermissionAction.ASK),
        ])
        assert ruleset.evaluate("bash", {}) is PermissionAction.DENY
        assert ruleset.evaluate("read_file", {}) is PermissionAction.ALLOW
        assert ruleset.evaluate("write_file", {}) is PermissionAction.ASK
        assert ruleset.evaluate("git", {}) is PermissionAction.ASK  # default

    def test_gitignore_style_override(self) -> None:
        """Mimic .gitignore: deny all, then re-allow specific tool."""
        ruleset = PermissionRuleset(rules=[
            Rule(tool_pattern="*", action=PermissionAction.DENY),
            Rule(tool_pattern="read_file", action=PermissionAction.ALLOW),
        ])
        assert ruleset.evaluate("read_file", {}) is PermissionAction.ALLOW
        assert ruleset.evaluate("bash", {}) is PermissionAction.DENY
        assert ruleset.evaluate("write_file", {}) is PermissionAction.DENY


# ---------------------------------------------------------------------------
# Integration: PermissionRuleset is a PermissionPolicy
# ---------------------------------------------------------------------------


class TestPermissionRulesetIsPolicy:
    def test_isinstance(self) -> None:
        ruleset = PermissionRuleset(rules=[])
        assert isinstance(ruleset, PermissionPolicy)

    def test_presets_are_policies(self) -> None:
        assert isinstance(AutoApprove(), PermissionPolicy)
        assert isinstance(AlwaysDeny(), PermissionPolicy)
        assert isinstance(AllowList(allowed=[]), PermissionPolicy)
        assert isinstance(ReadOnly(), PermissionPolicy)
        assert isinstance(Interactive(), PermissionPolicy)
