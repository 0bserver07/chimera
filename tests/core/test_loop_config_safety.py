"""Tests for the safety-default behaviour of :class:`LoopConfig`.

Background
----------
Chimera ships tools that can execute shell commands and overwrite files.
Historically a bare ``LoopConfig()`` installed no permission policy at
all, which meant ``Agent().run("rm -rf ~")`` would obey immediately —
no confirmation, no audit, no redaction.  Starting with the safety
overhaul, ``LoopConfig.__post_init__`` installs:

* A :class:`~chimera.permissions.presets.Interactive` policy that ASKs
  before write / destructive tool calls.
* A :class:`~chimera.secrets.RedactionMiddleware` on any attached
  :class:`~chimera.events.base.EventBus`.

These tests pin down the contract so regressions are loud.
"""
from __future__ import annotations

import os

import pytest

from chimera.core.loop_config import UNSAFE_ENV_VAR, LoopConfig
from chimera.events.base import EventBus
from chimera.permissions.base import PermissionAction
from chimera.permissions.presets import AutoApprove, Interactive
from chimera.secrets.redactor import RedactionMiddleware


@pytest.fixture(autouse=True)
def _ensure_safety_is_on(monkeypatch):
    """Safety tests must run with defaults-on, regardless of conftest state.

    Top-level conftest sets ``CHIMERA_UNSAFE=1`` for the bulk test suite
    to avoid boilerplate in unrelated tests; this file is the exception
    that pins the real behaviour, so we explicitly clear the flag for
    every test in this module.  Individual tests that want to toggle
    the flag (see :class:`TestUnsafeEnvVar`) may re-set it.
    """
    monkeypatch.delenv(UNSAFE_ENV_VAR, raising=False)


# ---------------------------------------------------------------------------
# Default permission policy
# ---------------------------------------------------------------------------


class TestDefaultPermissions:
    def test_bare_config_installs_interactive(self):
        config = LoopConfig()
        assert isinstance(config.permissions, Interactive)
        assert config._default_permissions_applied is True

    def test_interactive_asks_for_bash(self):
        # The whole point: a new user doing Agent().run(...) must not
        # have bash commands execute without approval.
        config = LoopConfig()
        assert config.permissions is not None
        assert (
            config.permissions.evaluate("bash", {"command": "rm -rf /"})
            == PermissionAction.ASK
        )

    def test_interactive_allows_read_only(self):
        # Read-only tools still run without interrupt.
        config = LoopConfig()
        assert config.permissions is not None
        assert (
            config.permissions.evaluate("read_file", {"path": "x"})
            == PermissionAction.ALLOW
        )

    def test_yolo_mode_opts_out(self):
        config = LoopConfig(yolo_mode=True)
        assert config.permissions is None
        assert config._default_permissions_applied is False

    def test_explicit_policy_is_preserved(self):
        explicit = AutoApprove()
        config = LoopConfig(permissions=explicit)
        assert config.permissions is explicit
        # Caller-supplied policies do NOT count as "default applied".
        assert config._default_permissions_applied is False


# ---------------------------------------------------------------------------
# Default redaction middleware
# ---------------------------------------------------------------------------


class TestDefaultRedaction:
    def test_event_bus_gets_redaction_middleware(self):
        bus = EventBus()
        LoopConfig(event_bus=bus)
        mws = bus._middlewares  # noqa: SLF001
        assert any(isinstance(mw, RedactionMiddleware) for mw in mws)

    def test_no_event_bus_no_middleware_needed(self):
        # No bus means nothing to attach to — must not crash.
        config = LoopConfig()
        assert config.event_bus is None  # sanity

    def test_secrets_redaction_false_opts_out(self):
        bus = EventBus()
        LoopConfig(event_bus=bus, secrets_redaction=False)
        mws = bus._middlewares  # noqa: SLF001
        assert not any(isinstance(mw, RedactionMiddleware) for mw in mws)

    def test_idempotent_on_preexisting_middleware(self):
        # If the caller already wired a RedactionMiddleware, we don't
        # double-add.
        from chimera.secrets.registry import SecretRegistry

        bus = EventBus()
        user_mw = RedactionMiddleware(registry=SecretRegistry())
        bus.use(user_mw)

        LoopConfig(event_bus=bus)
        mws = [mw for mw in bus._middlewares if isinstance(mw, RedactionMiddleware)]  # noqa: SLF001
        assert len(mws) == 1
        assert mws[0] is user_mw


# ---------------------------------------------------------------------------
# Environment escape hatch
# ---------------------------------------------------------------------------


class TestUnsafeEnvVar:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv(UNSAFE_ENV_VAR, raising=False)

    def test_unsafe_env_var_disables_default_permissions(self, monkeypatch):
        monkeypatch.setenv(UNSAFE_ENV_VAR, "1")
        config = LoopConfig()
        assert config.permissions is None

    def test_unsafe_env_var_disables_redaction(self, monkeypatch):
        monkeypatch.setenv(UNSAFE_ENV_VAR, "true")
        bus = EventBus()
        LoopConfig(event_bus=bus)
        mws = bus._middlewares  # noqa: SLF001
        assert not any(isinstance(mw, RedactionMiddleware) for mw in mws)

    def test_unsafe_env_var_accepts_yes(self, monkeypatch):
        monkeypatch.setenv(UNSAFE_ENV_VAR, "YES")
        assert LoopConfig().permissions is None

    def test_unsafe_env_var_zero_does_not_disable(self, monkeypatch):
        monkeypatch.setenv(UNSAFE_ENV_VAR, "0")
        config = LoopConfig()
        assert isinstance(config.permissions, Interactive)

    def test_env_var_name_is_stable(self):
        # Externally documented; changing this name is a breaking change.
        assert UNSAFE_ENV_VAR == "CHIMERA_UNSAFE"


# ---------------------------------------------------------------------------
# ReAct integration: Agent().run() gets safety out of the box
# ---------------------------------------------------------------------------


class TestReActInheritsSafety:
    def test_bare_react_materialises_safe_config(self):
        from chimera.core.loop import ReAct

        loop = ReAct()
        assert loop.config is not None
        assert isinstance(loop.config.permissions, Interactive)

    def test_explicit_config_is_passed_through(self):
        from chimera.core.loop import ReAct

        cfg = LoopConfig(yolo_mode=True)
        loop = ReAct(config=cfg)
        assert loop.config is cfg
        assert loop.config.permissions is None

    def test_unsafe_env_var_propagates_through_react(self, monkeypatch):
        from chimera.core.loop import ReAct

        monkeypatch.setenv(UNSAFE_ENV_VAR, "1")
        loop = ReAct()
        assert loop.config is not None
        assert loop.config.permissions is None


# ---------------------------------------------------------------------------
# Performance smoke: construction must stay trivial
# ---------------------------------------------------------------------------


def test_construction_cost_is_negligible():
    import time

    # Warm up env lookup
    _ = os.environ.get(UNSAFE_ENV_VAR)

    t0 = time.perf_counter()
    for _ in range(1000):
        LoopConfig()
    elapsed = time.perf_counter() - t0
    # 1000 constructions comfortably under 100ms on any modern laptop.
    # This is a coarse guard against someone wiring an expensive I/O
    # call into __post_init__ by accident.
    assert elapsed < 0.5, f"LoopConfig() x1000 took {elapsed:.3f}s"
