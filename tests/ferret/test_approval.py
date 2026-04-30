"""Tests for ``chimera.ferret.approval``.

Each preset is exercised against a fixture set of synthetic tool calls
covering the four observable categories the policy must distinguish:

* read-family tools (``read_file``, ``search``, ``list_files``, ``repo_map``)
* write-family tools (``write_file``, ``edit_file``, ``replace_in_file``)
* shell + git (``bash``, ``git``)
* unknown / arbitrary tools (a stand-in for tools the policy hasn't
  classified — e.g. an unregistered MCP tool)

The tests are pure-fixture, importing only from ``chimera.permissions`` and
the new module — no LLM, no env, no filesystem.
"""
from __future__ import annotations

from typing import Any

import pytest

from chimera.ferret.approval import (
    ApprovalPreset,
    AutoApprovalPolicy,
    policy_for_preset,
    preset_from_string,
)
from chimera.permissions.base import PermissionAction, PermissionPolicy
from chimera.permissions.presets import AutoApprove, ReadOnly


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


READ_TOOLS: tuple[str, ...] = ("read_file", "search", "list_files", "repo_map")
WRITE_TOOLS: tuple[str, ...] = ("write_file", "edit_file", "replace_in_file")
SHELL_TOOLS: tuple[str, ...] = ("bash", "git")
UNKNOWN_TOOLS: tuple[str, ...] = ("mystery_tool", "third_party_mcp")


@pytest.fixture
def empty_args() -> dict[str, Any]:
    """An empty kwargs dict — most preset decisions are name-only."""
    return {}


@pytest.fixture(params=list(ApprovalPreset))
def any_preset(request: pytest.FixtureRequest) -> ApprovalPreset:
    """Parametrised over every defined preset for cross-cutting invariants."""
    return request.param


# ---------------------------------------------------------------------------
# Enum + factory hygiene
# ---------------------------------------------------------------------------


def test_preset_values_are_canonical_hyphenated_strings() -> None:
    assert ApprovalPreset.READ_ONLY.value == "read-only"
    assert ApprovalPreset.AUTO.value == "auto"
    assert ApprovalPreset.FULL.value == "full"


def test_policy_for_preset_returns_policy_instance(
    any_preset: ApprovalPreset,
) -> None:
    policy = policy_for_preset(any_preset)
    assert isinstance(policy, PermissionPolicy)


def test_policy_for_preset_returns_fresh_instance() -> None:
    # WHY: callers should be free to mutate per-instance state without
    # crosstalk; ferret CLI invocations are short-lived but tests guard
    # against regressing to a singleton.
    a = policy_for_preset(ApprovalPreset.AUTO)
    b = policy_for_preset(ApprovalPreset.AUTO)
    assert a is not b


def test_policy_for_preset_rejects_non_member() -> None:
    with pytest.raises(ValueError, match="Unknown approval preset"):
        policy_for_preset("read-only")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# READ_ONLY preset
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", READ_TOOLS)
def test_read_only_allows_reads(tool: str, empty_args: dict[str, Any]) -> None:
    policy = policy_for_preset(ApprovalPreset.READ_ONLY)
    assert policy.evaluate(tool, empty_args) == PermissionAction.ALLOW


@pytest.mark.parametrize("tool", WRITE_TOOLS + SHELL_TOOLS + UNKNOWN_TOOLS)
def test_read_only_denies_everything_else(
    tool: str, empty_args: dict[str, Any]
) -> None:
    policy = policy_for_preset(ApprovalPreset.READ_ONLY)
    assert policy.evaluate(tool, empty_args) == PermissionAction.DENY


def test_read_only_returns_readonly_instance() -> None:
    assert isinstance(policy_for_preset(ApprovalPreset.READ_ONLY), ReadOnly)


# ---------------------------------------------------------------------------
# AUTO preset (composite)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", READ_TOOLS)
def test_auto_allows_reads(tool: str, empty_args: dict[str, Any]) -> None:
    policy = policy_for_preset(ApprovalPreset.AUTO)
    assert policy.evaluate(tool, empty_args) == PermissionAction.ALLOW


@pytest.mark.parametrize("tool", WRITE_TOOLS + SHELL_TOOLS)
def test_auto_asks_for_writes_and_shell(
    tool: str, empty_args: dict[str, Any]
) -> None:
    policy = policy_for_preset(ApprovalPreset.AUTO)
    assert policy.evaluate(tool, empty_args) == PermissionAction.ASK


@pytest.mark.parametrize("tool", UNKNOWN_TOOLS)
def test_auto_asks_for_unknown_tools(
    tool: str, empty_args: dict[str, Any]
) -> None:
    # WHY: unknown tools are treated as side-effecting until proven
    # otherwise — Interactive's default-ASK posture is the safe stance.
    policy = policy_for_preset(ApprovalPreset.AUTO)
    assert policy.evaluate(tool, empty_args) == PermissionAction.ASK


def test_auto_is_auto_approval_policy_type() -> None:
    assert isinstance(policy_for_preset(ApprovalPreset.AUTO), AutoApprovalPolicy)


def test_auto_read_whitelist_matches_read_only() -> None:
    # WHY: AUTO and READ_ONLY must agree on what counts as a "read".
    assert AutoApprovalPolicy.READ_TOOLS == ReadOnly.ALLOW_TOOLS


# ---------------------------------------------------------------------------
# FULL preset
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool", READ_TOOLS + WRITE_TOOLS + SHELL_TOOLS + UNKNOWN_TOOLS
)
def test_full_allows_everything(tool: str, empty_args: dict[str, Any]) -> None:
    policy = policy_for_preset(ApprovalPreset.FULL)
    assert policy.evaluate(tool, empty_args) == PermissionAction.ALLOW


def test_full_returns_autoapprove_instance() -> None:
    assert isinstance(policy_for_preset(ApprovalPreset.FULL), AutoApprove)


# ---------------------------------------------------------------------------
# preset_from_string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("read-only", ApprovalPreset.READ_ONLY),
        ("READ-ONLY", ApprovalPreset.READ_ONLY),
        ("read_only", ApprovalPreset.READ_ONLY),
        ("  auto  ", ApprovalPreset.AUTO),
        ("AUTO", ApprovalPreset.AUTO),
        ("full", ApprovalPreset.FULL),
        ("Full", ApprovalPreset.FULL),
    ],
)
def test_preset_from_string_accepts_canonical_and_aliases(
    raw: str, expected: ApprovalPreset
) -> None:
    assert preset_from_string(raw) is expected


@pytest.mark.parametrize("raw", ["", "yolo", "readonly", "ask", "interactive"])
def test_preset_from_string_rejects_garbage(raw: str) -> None:
    with pytest.raises(ValueError, match="Unknown approval preset"):
        preset_from_string(raw)


def test_preset_from_string_error_lists_accepted_forms() -> None:
    with pytest.raises(ValueError) as exc:
        preset_from_string("nope")
    msg = str(exc.value)
    for expected in ("read-only", "auto", "full"):
        assert expected in msg


# ---------------------------------------------------------------------------
# Cross-cutting invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", READ_TOOLS)
def test_every_preset_permits_or_asks_for_reads(
    any_preset: ApprovalPreset, tool: str, empty_args: dict[str, Any]
) -> None:
    # WHY: a "read" should never be DENY-ed by any of the three presets;
    # READ_ONLY wouldn't be useful otherwise, and FULL/AUTO must trivially
    # allow it. This is the strongest invariant we can assert across all
    # presets without coupling to specific actions.
    policy = policy_for_preset(any_preset)
    action = policy.evaluate(tool, empty_args)
    assert action in {PermissionAction.ALLOW, PermissionAction.ASK}
