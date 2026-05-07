"""Tests for declarative permission rules in ``chimera otter`` (W13 G6).

Covers:

* Schema parsing & defensive validation
  (:class:`OtterPermissionRule`, :func:`parse_action`).
* On-disk round-trip (``load_permission_rules`` /
  ``save_permission_rules``).
* Pattern matching via the existing
  :class:`chimera.permissions.rule.PermissionRuleset` (last-match
  wins, fnmatch globs, optional argument constraints).
* Mutation primitives (``add_rule`` / ``remove_rule`` / ``list_rules``).
* The ``/permissions`` slash command surface.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.otter import permission_rules as pr
from chimera.otter.slash import cmd_permissions
from chimera.permissions.base import PermissionAction


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _CapturePrinter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)


@pytest.fixture
def perms_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the on-disk store under ``tmp_path`` for the test."""
    target = tmp_path / "permissions.json"
    monkeypatch.setenv("CHIMERA_PERMISSIONS_FILE", str(target))
    return target


# ---------------------------------------------------------------------------
# parse_action
# ---------------------------------------------------------------------------


def test_parse_action_canonical() -> None:
    assert pr.parse_action("allow") is PermissionAction.ALLOW
    assert pr.parse_action("deny") is PermissionAction.DENY
    assert pr.parse_action("ask") is PermissionAction.ASK


def test_parse_action_aliases() -> None:
    assert pr.parse_action("permit") is PermissionAction.ALLOW
    assert pr.parse_action("block") is PermissionAction.DENY
    assert pr.parse_action("prompt") is PermissionAction.ASK
    assert pr.parse_action("confirm") is PermissionAction.ASK


def test_parse_action_is_case_insensitive_and_strips() -> None:
    assert pr.parse_action("  ALLOW  ") is PermissionAction.ALLOW


def test_parse_action_rejects_unknown() -> None:
    with pytest.raises(pr.PermissionRulesError):
        pr.parse_action("yes-please")


# ---------------------------------------------------------------------------
# OtterPermissionRule schema
# ---------------------------------------------------------------------------


def test_rule_to_json_omits_empty_optional_fields() -> None:
    rule = pr.OtterPermissionRule(tool="bash", action="ask")
    payload = rule.to_json()
    assert payload == {"tool": "bash", "action": "ask"}


def test_rule_to_json_round_trips() -> None:
    rule = pr.OtterPermissionRule(
        tool="bash",
        action="deny",
        arg_key="command",
        arg_pattern="rm -rf*",
        description="block destructive recursive deletes",
    )
    payload = rule.to_json()
    rebuilt = pr.OtterPermissionRule.from_json(payload)
    assert rebuilt == rule


def test_rule_from_json_rejects_missing_tool() -> None:
    with pytest.raises(pr.PermissionRulesError):
        pr.OtterPermissionRule.from_json({"action": "allow"})


def test_rule_from_json_rejects_missing_action() -> None:
    with pytest.raises(pr.PermissionRulesError):
        pr.OtterPermissionRule.from_json({"tool": "bash"})


def test_rule_from_json_rejects_bad_action() -> None:
    with pytest.raises(pr.PermissionRulesError):
        pr.OtterPermissionRule.from_json({"tool": "bash", "action": "explode"})


def test_rule_to_rule_translates_to_chimera_rule() -> None:
    rule = pr.OtterPermissionRule(tool="bash", action="deny", arg_key="command", arg_pattern="rm*")
    chim = rule.to_rule()
    assert chim.tool_pattern == "bash"
    assert chim.action is PermissionAction.DENY
    assert chim.arg_key == "command"
    assert chim.arg_pattern == "rm*"


# ---------------------------------------------------------------------------
# default_permissions_path / env override
# ---------------------------------------------------------------------------


def test_default_permissions_path_honors_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    target = tmp_path / "alt.json"
    monkeypatch.setenv("CHIMERA_PERMISSIONS_FILE", str(target))
    assert pr.default_permissions_path() == target


def test_default_permissions_path_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_PERMISSIONS_FILE", raising=False)
    assert pr.default_permissions_path() == Path.home() / ".chimera" / "permissions.json"


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


def test_load_returns_empty_ruleset_for_missing_file(perms_file: Path) -> None:
    assert not perms_file.exists()
    rs = pr.load_permission_rules()
    assert rs.rules == []
    assert rs.default == "ask"
    assert rs.version == pr.DEFAULT_VERSION


def test_load_parses_valid_file(perms_file: Path) -> None:
    perms_file.write_text(json.dumps({
        "version": 1,
        "default": "allow",
        "rules": [
            {"tool": "read_file", "action": "allow"},
            {"tool": "bash", "action": "ask", "description": "interactive bash"},
        ],
    }))
    rs = pr.load_permission_rules()
    assert rs.default == "allow"
    assert len(rs.rules) == 2
    assert rs.rules[0].tool == "read_file"
    assert rs.rules[1].description == "interactive bash"


def test_load_skips_malformed_rule_in_non_strict_mode(perms_file: Path, caplog: pytest.LogCaptureFixture) -> None:
    perms_file.write_text(json.dumps({
        "rules": [
            {"tool": "bash", "action": "allow"},
            {"tool": "no_action_here"},  # missing action
            {"tool": "edit", "action": "deny"},
        ],
    }))
    rs = pr.load_permission_rules()
    # Two valid rules; the bad one is skipped with a logged warning.
    assert [r.tool for r in rs.rules] == ["bash", "edit"]


def test_load_strict_raises_on_bad_rule(perms_file: Path) -> None:
    perms_file.write_text(json.dumps({
        "rules": [{"tool": "bash"}],  # missing action
    }))
    with pytest.raises(pr.PermissionRulesError):
        pr.load_permission_rules(strict=True)


def test_load_strict_raises_on_invalid_json(perms_file: Path) -> None:
    perms_file.write_text("{not json")
    with pytest.raises(pr.PermissionRulesError):
        pr.load_permission_rules(strict=True)


def test_save_round_trips_via_disk(perms_file: Path) -> None:
    rs = pr.RulesetFile(
        default="deny",
        rules=[
            pr.OtterPermissionRule(tool="bash", action="deny"),
            pr.OtterPermissionRule(tool="read_file", action="allow"),
        ],
    )
    pr.save_permission_rules(rs)
    assert perms_file.exists()
    reloaded = pr.load_permission_rules()
    assert reloaded.default == "deny"
    assert [r.tool for r in reloaded.rules] == ["bash", "read_file"]


def test_save_creates_parent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default ``~/.chimera/`` may not exist on a fresh machine."""
    nested = tmp_path / "nested" / "deeper" / "permissions.json"
    monkeypatch.setenv("CHIMERA_PERMISSIONS_FILE", str(nested))
    assert not nested.parent.exists()
    pr.save_permission_rules(pr.RulesetFile())
    assert nested.exists()


# ---------------------------------------------------------------------------
# build_policy: pattern matching, last-match-wins
# ---------------------------------------------------------------------------


def test_build_policy_default_when_no_rule_matches() -> None:
    policy = pr.build_policy([], default="allow")
    assert policy.evaluate("anything", {}) is PermissionAction.ALLOW


def test_build_policy_matches_exact_tool_name() -> None:
    rules = [pr.OtterPermissionRule(tool="bash", action="deny")]
    policy = pr.build_policy(rules, default="allow")
    assert policy.evaluate("bash", {"command": "ls"}) is PermissionAction.DENY
    assert policy.evaluate("read_file", {}) is PermissionAction.ALLOW


def test_build_policy_supports_wildcard() -> None:
    rules = [
        pr.OtterPermissionRule(tool="*", action="ask"),
        pr.OtterPermissionRule(tool="read_file", action="allow"),
    ]
    policy = pr.build_policy(rules, default="deny")
    # Wildcard matches everything; the more-specific allow wins
    # (last-match semantics).
    assert policy.evaluate("read_file", {}) is PermissionAction.ALLOW
    # Other tools fall through to the wildcard's ask.
    assert policy.evaluate("bash", {}) is PermissionAction.ASK


def test_build_policy_argument_pattern_scopes_match() -> None:
    rules = [
        pr.OtterPermissionRule(tool="bash", action="allow"),
        pr.OtterPermissionRule(
            tool="bash",
            action="deny",
            arg_key="command",
            arg_pattern="rm -rf*",
        ),
    ]
    policy = pr.build_policy(rules, default="ask")
    assert policy.evaluate("bash", {"command": "ls -la"}) is PermissionAction.ALLOW
    assert policy.evaluate("bash", {"command": "rm -rf /tmp"}) is PermissionAction.DENY


def test_build_policy_honors_file_default_when_not_overridden(perms_file: Path) -> None:
    perms_file.write_text(json.dumps({"default": "deny", "rules": []}))
    rs = pr.load_permission_rules()
    policy = pr.build_policy(rs)
    assert policy.evaluate("anything", {}) is PermissionAction.DENY


def test_build_policy_explicit_default_overrides_file() -> None:
    rs = pr.RulesetFile(default="deny", rules=[])
    policy = pr.build_policy(rs, default="allow")
    assert policy.evaluate("x", {}) is PermissionAction.ALLOW


def test_build_policy_accepts_string_default_alias() -> None:
    policy = pr.build_policy([], default="block")
    assert policy.evaluate("x", {}) is PermissionAction.DENY


# ---------------------------------------------------------------------------
# Mutation primitives
# ---------------------------------------------------------------------------


def test_add_rule_appends_to_disk(perms_file: Path) -> None:
    pr.add_rule(pr.OtterPermissionRule(tool="bash", action="ask"))
    assert perms_file.exists()
    rules = pr.list_rules()
    assert len(rules) == 1
    assert rules[0].tool == "bash"


def test_add_rule_accepts_dict_form(perms_file: Path) -> None:
    pr.add_rule({"tool": "edit", "action": "allow"})
    rules = pr.list_rules()
    assert rules[0].action == "allow"


def test_add_rule_validates_action(perms_file: Path) -> None:
    with pytest.raises(pr.PermissionRulesError):
        pr.add_rule({"tool": "bash", "action": "wat"})
    # Nothing should have been persisted on validation failure.
    assert not perms_file.exists() or pr.list_rules() == []


def test_remove_rule_drops_at_index(perms_file: Path) -> None:
    pr.add_rule(pr.OtterPermissionRule(tool="bash", action="deny"))
    pr.add_rule(pr.OtterPermissionRule(tool="read_file", action="allow"))
    removed = pr.remove_rule(0)
    assert removed is not None
    assert removed.tool == "bash"
    remaining = pr.list_rules()
    assert [r.tool for r in remaining] == ["read_file"]


def test_remove_rule_returns_none_for_oob_index(perms_file: Path) -> None:
    assert pr.remove_rule(99) is None


# ---------------------------------------------------------------------------
# /permissions slash command
# ---------------------------------------------------------------------------


def test_slash_list_with_no_rules(perms_file: Path) -> None:
    out = _CapturePrinter()
    cmd_permissions(session=None, env=None, args="list", out=out)
    text = "\n".join(out.lines)
    assert "no rules" in text


def test_slash_bare_invocation_aliases_list(perms_file: Path) -> None:
    out = _CapturePrinter()
    cmd_permissions(session=None, env=None, args="", out=out)
    text = "\n".join(out.lines)
    assert "no rules" in text


def test_slash_list_renders_rules_with_index(perms_file: Path) -> None:
    pr.add_rule(pr.OtterPermissionRule(tool="bash", action="deny", description="be careful"))
    pr.add_rule(pr.OtterPermissionRule(tool="read_file", action="allow"))
    out = _CapturePrinter()
    cmd_permissions(session=None, env=None, args="list", out=out)
    text = "\n".join(out.lines)
    assert "[0] bash -> deny" in text
    assert "be careful" in text
    assert "[1] read_file -> allow" in text


def test_slash_add_persists_rule(perms_file: Path) -> None:
    out = _CapturePrinter()
    cmd_permissions(session=None, env=None, args="add bash deny", out=out)
    text = "\n".join(out.lines)
    assert "added rule" in text
    rules = pr.list_rules()
    assert len(rules) == 1
    assert rules[0].tool == "bash"
    assert rules[0].action == "deny"


def test_slash_add_with_arg_pattern(perms_file: Path) -> None:
    out = _CapturePrinter()
    cmd_permissions(
        session=None, env=None,
        args='add bash deny command="rm -rf*"', out=out,
    )
    rules = pr.list_rules()
    assert rules[0].arg_key == "command"
    assert rules[0].arg_pattern == "rm -rf*"


def test_slash_add_with_description_after_dashdash(perms_file: Path) -> None:
    out = _CapturePrinter()
    cmd_permissions(
        session=None, env=None,
        args="add bash deny -- block destructive deletes", out=out,
    )
    rules = pr.list_rules()
    assert rules[0].description == "block destructive deletes"


def test_slash_add_rejects_bad_action(perms_file: Path) -> None:
    out = _CapturePrinter()
    cmd_permissions(session=None, env=None, args="add bash explode", out=out)
    text = "\n".join(out.lines)
    assert "/permissions add" in text
    assert pr.list_rules() == []


def test_slash_add_with_too_few_args_prints_usage(perms_file: Path) -> None:
    out = _CapturePrinter()
    cmd_permissions(session=None, env=None, args="add", out=out)
    text = "\n".join(out.lines)
    assert "usage:" in text


def test_slash_remove_drops_rule(perms_file: Path) -> None:
    pr.add_rule(pr.OtterPermissionRule(tool="bash", action="deny"))
    out = _CapturePrinter()
    cmd_permissions(session=None, env=None, args="remove 0", out=out)
    text = "\n".join(out.lines)
    assert "removed rule" in text
    assert pr.list_rules() == []


def test_slash_remove_with_oob_index(perms_file: Path) -> None:
    out = _CapturePrinter()
    cmd_permissions(session=None, env=None, args="remove 99", out=out)
    text = "\n".join(out.lines)
    assert "no rule at index" in text


def test_slash_remove_with_non_integer(perms_file: Path) -> None:
    out = _CapturePrinter()
    cmd_permissions(session=None, env=None, args="remove abc", out=out)
    text = "\n".join(out.lines)
    assert "must be an integer" in text


def test_slash_unknown_subcommand_prints_usage(perms_file: Path) -> None:
    out = _CapturePrinter()
    cmd_permissions(session=None, env=None, args="frobnicate", out=out)
    text = "\n".join(out.lines)
    assert "usage:" in text


def test_slash_command_is_registered_in_palette() -> None:
    """``/permissions`` must be exposed in the otter slash palette."""
    from chimera.otter.slash import OTTER_SLASH_COMMANDS, OTTER_SLASH_HELP

    assert "permissions" in OTTER_SLASH_COMMANDS
    assert "permissions" in OTTER_SLASH_HELP


# ---------------------------------------------------------------------------
# End-to-end: file -> policy -> evaluation
# ---------------------------------------------------------------------------


def test_e2e_file_to_policy_blocks_dangerous_bash(perms_file: Path) -> None:
    """The headline scenario: deny ``rm -rf`` via a single declarative rule."""
    perms_file.write_text(json.dumps({
        "default": "allow",
        "rules": [
            {
                "tool": "bash",
                "action": "deny",
                "arg_key": "command",
                "arg_pattern": "rm -rf*",
            },
        ],
    }))

    rs = pr.load_permission_rules()
    policy = pr.build_policy(rs)

    # Safe bash invocations stay allowed.
    assert policy.evaluate("bash", {"command": "ls -la"}) is PermissionAction.ALLOW
    # The dangerous one is blocked by the rule.
    assert policy.evaluate("bash", {"command": "rm -rf /home"}) is PermissionAction.DENY


def test_e2e_wildcard_default_deny_with_explicit_allowlist(perms_file: Path) -> None:
    """Read-only mode: deny by default, allow only the explicit reads."""
    perms_file.write_text(json.dumps({
        "default": "deny",
        "rules": [
            {"tool": "read_file", "action": "allow"},
            {"tool": "search", "action": "allow"},
            {"tool": "list_files", "action": "allow"},
        ],
    }))

    rs = pr.load_permission_rules()
    policy = pr.build_policy(rs)

    assert policy.evaluate("read_file", {}) is PermissionAction.ALLOW
    assert policy.evaluate("search", {}) is PermissionAction.ALLOW
    assert policy.evaluate("bash", {}) is PermissionAction.DENY
    assert policy.evaluate("write_file", {}) is PermissionAction.DENY
