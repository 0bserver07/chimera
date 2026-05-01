"""Tests for the mid-session ``/sandbox`` and ``/approval`` rewiring.

Wave 9 fix: the slash handlers now mutate the active session's
:class:`~chimera.core.loop_config.LoopConfig` so a preset / mode change
takes effect on the very next tool call. The plumbing is two thread-safe
proxies:

* :class:`chimera.ferret.approval.MutablePermissionPolicy` — wraps the
  preset's :class:`~chimera.permissions.base.PermissionPolicy` and lets
  ``/approval`` swap the inner reference atomically.
* :class:`chimera.ferret.sandbox.MutableSandboxMode` — holds the active
  :class:`~chimera.ferret.sandbox.SandboxMode` for a
  :class:`~chimera.ferret.sandbox.SandboxedEnvironment` and lets
  ``/sandbox`` swap it atomically.

These tests verify the proxies themselves and that firing the ferret
slash handlers re-shapes the next tool-call evaluation as advertised.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.core.loop_config import LoopConfig
from chimera.env.local import LocalEnvironment
from chimera.ferret.approval import (
    ApprovalPreset,
    MutablePermissionPolicy,
    policy_for_preset,
)
from chimera.ferret.sandbox import (
    MutableSandboxMode,
    SandboxedEnvironment,
    SandboxMode,
    SandboxViolation,
)
from chimera.ferret.slash import cmd_approval, cmd_sandbox
from chimera.permissions.base import PermissionAction, PermissionPolicy
from chimera.permissions.presets import AutoApprove, ReadOnly


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CapturePrinter:
    """Tiny callable that records each line printed by a handler."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)


class _FakeLoop:
    def __init__(self, config: LoopConfig) -> None:
        self.config = config


class _FakeAgent:
    """Minimal agent shape the slash handlers walk to find LoopConfig."""

    def __init__(self, *, config: LoopConfig, env: Any | None = None) -> None:
        self.loop = _FakeLoop(config)
        self.env = env


class _LiveSession:
    """Session that exposes the agent + env the way the ferret REPL does."""

    def __init__(
        self,
        *,
        config: LoopConfig,
        env: Any | None = None,
        sandbox_mode: str = "read-only",
        approval_preset: str = "read-only",
    ) -> None:
        self.context = None
        self.provider = None
        self.cost_tracker = None
        self.sandbox_mode = sandbox_mode
        self.approval_preset = approval_preset
        self.file_tracker = None
        self.agent = _FakeAgent(config=config, env=env)
        self.env = env


# ---------------------------------------------------------------------------
# MutablePermissionPolicy — proxy semantics
# ---------------------------------------------------------------------------


def test_mutable_permission_policy_is_a_permission_policy() -> None:
    proxy = MutablePermissionPolicy(ReadOnly())
    assert isinstance(proxy, PermissionPolicy)


def test_mutable_permission_policy_delegates_to_inner() -> None:
    proxy = MutablePermissionPolicy(ReadOnly())
    # ReadOnly allows reads, denies bash.
    assert proxy.evaluate("read_file", {}) is PermissionAction.ALLOW
    assert proxy.evaluate("bash", {"command": "ls"}) is PermissionAction.DENY


def test_mutable_permission_policy_set_inner_swaps_atomically() -> None:
    proxy = MutablePermissionPolicy(ReadOnly())
    previous = proxy.set_inner(AutoApprove())
    assert isinstance(previous, ReadOnly)
    # AutoApprove allows everything.
    assert proxy.evaluate("bash", {"command": "rm -rf /"}) is PermissionAction.ALLOW
    assert proxy.evaluate("read_file", {}) is PermissionAction.ALLOW


def test_mutable_permission_policy_get_inner_returns_live_reference() -> None:
    inner = ReadOnly()
    proxy = MutablePermissionPolicy(inner)
    assert proxy.get_inner() is inner
    assert proxy.inner is inner
    new_inner = AutoApprove()
    proxy.set_inner(new_inner)
    assert proxy.get_inner() is new_inner
    assert proxy.inner is new_inner


def test_mutable_permission_policy_rejects_non_policy() -> None:
    proxy = MutablePermissionPolicy(ReadOnly())
    with pytest.raises(TypeError, match="PermissionPolicy"):
        proxy.set_inner("not-a-policy")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# MutableSandboxMode — holder semantics
# ---------------------------------------------------------------------------


def test_mutable_sandbox_mode_default_is_read_only() -> None:
    holder = MutableSandboxMode()
    assert holder.get() is SandboxMode.READ_ONLY


def test_mutable_sandbox_mode_set_returns_previous() -> None:
    holder = MutableSandboxMode(SandboxMode.READ_ONLY)
    previous = holder.set(SandboxMode.WORKSPACE_WRITE)
    assert previous is SandboxMode.READ_ONLY
    assert holder.get() is SandboxMode.WORKSPACE_WRITE


def test_mutable_sandbox_mode_accepts_kebab_case_string() -> None:
    holder = MutableSandboxMode("read-only")
    holder.set("workspace-write-network")
    assert holder.get() is SandboxMode.WORKSPACE_WRITE_NETWORK


# ---------------------------------------------------------------------------
# SandboxedEnvironment — mode reads through the holder
# ---------------------------------------------------------------------------


@pytest.fixture
def local_env(tmp_path: Path) -> LocalEnvironment:
    env = LocalEnvironment(workdir=str(tmp_path))
    env.setup()
    (tmp_path / "hello.txt").write_text("hi\n")
    return env


def test_sandboxed_env_exposes_mode_holder(local_env: LocalEnvironment) -> None:
    env = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    assert isinstance(env.mode_holder, MutableSandboxMode)
    assert env.mode is SandboxMode.READ_ONLY


def test_sandboxed_env_set_mode_swaps_live(local_env: LocalEnvironment) -> None:
    env = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    # Under read-only, write_file is blocked.
    with pytest.raises(SandboxViolation):
        env.write_file("new.txt", "x")
    # Swap mid-flight; next write succeeds.
    previous = env.set_mode(SandboxMode.WORKSPACE_WRITE)
    assert previous is SandboxMode.READ_ONLY
    env.write_file("new.txt", "x")
    assert (Path(local_env.workdir) / "new.txt").read_text() == "x"


def test_sandboxed_env_mode_assignment_routes_through_holder(
    local_env: LocalEnvironment,
) -> None:
    env = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    env.mode = SandboxMode.WORKSPACE_WRITE
    # Both surfaces agree.
    assert env.mode is SandboxMode.WORKSPACE_WRITE
    assert env.mode_holder.get() is SandboxMode.WORKSPACE_WRITE


def test_sandboxed_env_can_share_mode_holder(local_env: LocalEnvironment) -> None:
    holder = MutableSandboxMode(SandboxMode.READ_ONLY)
    env = SandboxedEnvironment(local_env, mode=holder)
    assert env.mode_holder is holder
    holder.set(SandboxMode.WORKSPACE_WRITE)
    # Env sees the swap without further wiring.
    assert env.mode is SandboxMode.WORKSPACE_WRITE


# ---------------------------------------------------------------------------
# /approval — slash handler mutates LoopConfig.permissions live
# ---------------------------------------------------------------------------


def test_approval_slash_swaps_live_loopconfig_permissions() -> None:
    """``/approval read-only`` mid-session must deny the next bash call."""
    # Start with FULL preset so initial state allows everything.
    proxy = MutablePermissionPolicy(policy_for_preset(ApprovalPreset.FULL))
    config = LoopConfig(permissions=proxy)
    session = _LiveSession(config=config, approval_preset="full")

    # Sanity: bash currently allowed.
    assert config.permissions is proxy
    assert proxy.evaluate("bash", {"command": "ls"}) is PermissionAction.ALLOW

    out = _CapturePrinter()
    cmd_approval(session, None, "read-only", out)

    # The visible state and the live policy both swapped.
    assert session.approval_preset == "read-only"
    assert isinstance(proxy.get_inner(), ReadOnly)
    # Next tool-call evaluation sees the new policy.
    assert proxy.evaluate("bash", {"command": "ls"}) is PermissionAction.DENY
    assert proxy.evaluate("read_file", {}) is PermissionAction.ALLOW

    # And the slash handler announced the transition.
    rendered = "\n".join(out.lines)
    assert "/approval" in rendered
    assert "full" in rendered and "read-only" in rendered


def test_approval_slash_round_trip_full_to_read_only_to_full() -> None:
    """Multiple swaps each take effect on the very next evaluation."""
    proxy = MutablePermissionPolicy(policy_for_preset(ApprovalPreset.FULL))
    config = LoopConfig(permissions=proxy)
    session = _LiveSession(config=config, approval_preset="full")
    out = _CapturePrinter()

    cmd_approval(session, None, "read-only", out)
    assert proxy.evaluate("bash", {"command": "ls"}) is PermissionAction.DENY

    cmd_approval(session, None, "full", out)
    assert proxy.evaluate("bash", {"command": "ls"}) is PermissionAction.ALLOW
    assert session.approval_preset == "full"


def test_approval_slash_without_proxy_only_updates_visible_state() -> None:
    """Sessions without a MutablePermissionPolicy proxy must not crash."""
    config = LoopConfig(permissions=ReadOnly())  # plain policy, not proxied
    session = _LiveSession(config=config, approval_preset="read-only")
    out = _CapturePrinter()

    cmd_approval(session, None, "full", out)

    assert session.approval_preset == "full"
    # The plain policy was left untouched — this is the documented
    # fallback path for sessions that predate proxy installation.
    assert isinstance(config.permissions, ReadOnly)


def test_approval_slash_rejects_unknown_preset_without_touching_proxy() -> None:
    proxy = MutablePermissionPolicy(policy_for_preset(ApprovalPreset.FULL))
    config = LoopConfig(permissions=proxy)
    session = _LiveSession(config=config, approval_preset="full")
    out = _CapturePrinter()

    cmd_approval(session, None, "yolo-mode", out)

    rendered = "\n".join(out.lines)
    assert "unknown preset" in rendered
    assert session.approval_preset == "full"
    # Proxy inner unchanged.
    assert isinstance(proxy.get_inner(), AutoApprove)


# ---------------------------------------------------------------------------
# /sandbox — slash handler mutates the live SandboxedEnvironment
# ---------------------------------------------------------------------------


def test_sandbox_slash_swaps_live_env_mode(local_env: LocalEnvironment) -> None:
    """``/sandbox workspace-write`` mid-session must unblock writes."""
    env = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    config = LoopConfig(permissions=AutoApprove())
    session = _LiveSession(
        config=config, env=env, sandbox_mode="read-only",
    )

    # Sanity: writes currently blocked.
    with pytest.raises(SandboxViolation):
        env.write_file("a.txt", "1")

    out = _CapturePrinter()
    cmd_sandbox(session, env, "workspace-write", out)

    # Visible state + live env both swapped.
    assert session.sandbox_mode == "workspace-write"
    assert env.mode is SandboxMode.WORKSPACE_WRITE
    # Next write succeeds.
    env.write_file("a.txt", "1")
    assert (Path(local_env.workdir) / "a.txt").read_text() == "1"


def test_sandbox_slash_resolves_env_off_session(
    local_env: LocalEnvironment,
) -> None:
    """When the slash handler is invoked with env=None, it walks ``session.env``."""
    env = SandboxedEnvironment(local_env, mode=SandboxMode.READ_ONLY)
    config = LoopConfig(permissions=AutoApprove())
    session = _LiveSession(
        config=config, env=env, sandbox_mode="read-only",
    )

    out = _CapturePrinter()
    # Pass env=None — the handler must reach into the session.
    cmd_sandbox(session, None, "workspace-write-network", out)

    assert env.mode is SandboxMode.WORKSPACE_WRITE_NETWORK
    assert session.sandbox_mode == "workspace-write-network"


def test_sandbox_slash_without_live_env_only_updates_visible_state() -> None:
    """Sessions without a SandboxedEnvironment must not crash."""
    config = LoopConfig(permissions=AutoApprove())
    session = _LiveSession(
        config=config, env=None, sandbox_mode="read-only",
    )
    out = _CapturePrinter()

    cmd_sandbox(session, None, "workspace-write", out)

    assert session.sandbox_mode == "workspace-write"
    rendered = "\n".join(out.lines)
    assert "/sandbox" in rendered


def test_sandbox_slash_rejects_unknown_mode_without_touching_env(
    local_env: LocalEnvironment,
) -> None:
    env = SandboxedEnvironment(local_env, mode=SandboxMode.WORKSPACE_WRITE)
    config = LoopConfig(permissions=AutoApprove())
    session = _LiveSession(
        config=config, env=env, sandbox_mode="workspace-write",
    )
    out = _CapturePrinter()

    cmd_sandbox(session, env, "loose", out)

    rendered = "\n".join(out.lines)
    assert "unknown mode" in rendered
    assert session.sandbox_mode == "workspace-write"
    assert env.mode is SandboxMode.WORKSPACE_WRITE


# ---------------------------------------------------------------------------
# End-to-end: the next tool call's permission evaluation sees the swap
# ---------------------------------------------------------------------------


def test_loopconfig_permissions_evaluation_hits_new_policy_after_slash() -> None:
    """Simulate the tool_executor read of ``config.permissions.evaluate(...)``."""
    proxy = MutablePermissionPolicy(policy_for_preset(ApprovalPreset.FULL))
    config = LoopConfig(permissions=proxy)
    session = _LiveSession(config=config, approval_preset="full")

    # Pre-slash: bash allowed.
    live_perms = config.permissions
    assert live_perms is not None
    action_before = live_perms.evaluate("bash", {"command": "ls"})
    assert action_before is PermissionAction.ALLOW

    out = _CapturePrinter()
    cmd_approval(session, None, "read-only", out)

    # Post-slash: bash denied — same call site, same proxy reference,
    # different inner policy.
    live_perms = config.permissions
    assert live_perms is not None
    action_after = live_perms.evaluate("bash", {"command": "ls"})
    assert action_after is PermissionAction.DENY
