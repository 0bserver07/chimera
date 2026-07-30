"""Tests for PluginExtensionRegistry interceptor registration.

Covers the register/unregister/get/get_all surfaces, registration-order
preservation, seam-name validation (closed set — a typo must fail loudly,
never register a chain that can never fire), the drift guard pinning
INTERCEPTOR_SEAMS to the fields of the core Interceptors dataclass, and
the BasePlugin.register_interceptors activation hook.
"""
from __future__ import annotations

import dataclasses

import pytest

from chimera.core.interception import Interceptors
from chimera.plugins.base import BasePlugin, ComponentRegistry
from chimera.plugins.registry import INTERCEPTOR_SEAMS, PluginExtensionRegistry


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the plugin registry between tests."""
    PluginExtensionRegistry._reset()
    yield
    PluginExtensionRegistry._reset()


def _fn(name: str):
    def interceptor(*args):
        return None

    interceptor.__qualname__ = name
    return interceptor


class TestRegisterInterceptor:
    def test_register_and_get(self) -> None:
        gate = _fn("gate")
        PluginExtensionRegistry.register_interceptor("tool_call", gate)
        assert PluginExtensionRegistry.get_interceptors("tool_call") == [gate]

    def test_registration_order_preserved(self) -> None:
        first, second = _fn("first"), _fn("second")
        PluginExtensionRegistry.register_interceptor("tool_call", first)
        PluginExtensionRegistry.register_interceptor("tool_call", second)
        assert PluginExtensionRegistry.get_interceptors("tool_call") == [first, second]

    def test_seams_are_independent(self) -> None:
        gate, scrub = _fn("gate"), _fn("scrub")
        PluginExtensionRegistry.register_interceptor("tool_call", gate)
        PluginExtensionRegistry.register_interceptor("tool_result", scrub)
        assert PluginExtensionRegistry.get_interceptors("tool_call") == [gate]
        assert PluginExtensionRegistry.get_interceptors("tool_result") == [scrub]

    def test_get_interceptors_empty_by_default(self) -> None:
        for seam in INTERCEPTOR_SEAMS:
            assert PluginExtensionRegistry.get_interceptors(seam) == []

    def test_get_interceptors_returns_copy(self) -> None:
        gate = _fn("gate")
        PluginExtensionRegistry.register_interceptor("context", gate)
        returned = PluginExtensionRegistry.get_interceptors("context")
        returned.clear()
        assert PluginExtensionRegistry.get_interceptors("context") == [gate]

    def test_unknown_seam_raises_on_register(self) -> None:
        with pytest.raises(ValueError, match="unknown interceptor seam"):
            PluginExtensionRegistry.register_interceptor("tool_cal", _fn("typo"))

    def test_unknown_seam_raises_on_get(self) -> None:
        with pytest.raises(ValueError, match="unknown interceptor seam"):
            PluginExtensionRegistry.get_interceptors("toolcall")

    def test_unknown_seam_raises_on_unregister(self) -> None:
        with pytest.raises(ValueError, match="unknown interceptor seam"):
            PluginExtensionRegistry.unregister_interceptor("nope", _fn("x"))


class TestUnregisterInterceptor:
    def test_unregister_removes(self) -> None:
        gate = _fn("gate")
        PluginExtensionRegistry.register_interceptor("tool_call", gate)
        PluginExtensionRegistry.unregister_interceptor("tool_call", gate)
        assert PluginExtensionRegistry.get_interceptors("tool_call") == []

    def test_unregister_absent_is_noop(self) -> None:
        PluginExtensionRegistry.unregister_interceptor("tool_call", _fn("ghost"))
        assert PluginExtensionRegistry.get_interceptors("tool_call") == []

    def test_unregister_matches_rederived_bound_method(self) -> None:
        """Bound methods compare equal across attribute accesses; a plugin's
        deactivate() can therefore unregister ``self._gate`` even though each
        access creates a fresh bound-method object."""

        class Holder:
            def gate(self, call):
                return None

        holder = Holder()
        PluginExtensionRegistry.register_interceptor("tool_call", holder.gate)
        PluginExtensionRegistry.unregister_interceptor("tool_call", holder.gate)
        assert PluginExtensionRegistry.get_interceptors("tool_call") == []


class TestGetAllInterceptors:
    def test_empty_registry_yields_empty_bundle(self) -> None:
        bundle = PluginExtensionRegistry.get_all_interceptors()
        assert isinstance(bundle, Interceptors)
        assert bundle.provider_request == []
        assert bundle.tool_call == []
        assert bundle.tool_result == []
        assert bundle.context == []

    def test_bundle_carries_all_seams_in_order(self) -> None:
        req, gate1, gate2, scrub, ctx = (
            _fn("req"), _fn("gate1"), _fn("gate2"), _fn("scrub"), _fn("ctx"),
        )
        PluginExtensionRegistry.register_interceptor("provider_request", req)
        PluginExtensionRegistry.register_interceptor("tool_call", gate1)
        PluginExtensionRegistry.register_interceptor("tool_call", gate2)
        PluginExtensionRegistry.register_interceptor("tool_result", scrub)
        PluginExtensionRegistry.register_interceptor("context", ctx)

        bundle = PluginExtensionRegistry.get_all_interceptors()
        assert bundle.provider_request == [req]
        assert bundle.tool_call == [gate1, gate2]
        assert bundle.tool_result == [scrub]
        assert bundle.context == [ctx]

    def test_bundle_is_detached_from_registry(self) -> None:
        PluginExtensionRegistry.register_interceptor("tool_call", _fn("gate"))
        bundle = PluginExtensionRegistry.get_all_interceptors()
        bundle.tool_call.clear()
        assert len(PluginExtensionRegistry.get_interceptors("tool_call")) == 1

    def test_reset_clears_interceptors(self) -> None:
        PluginExtensionRegistry.register_interceptor("tool_call", _fn("gate"))
        PluginExtensionRegistry._reset()
        assert PluginExtensionRegistry.get_interceptors("tool_call") == []


class TestSeamDriftGuard:
    def test_interceptor_seams_match_interceptors_dataclass_fields(self) -> None:
        """INTERCEPTOR_SEAMS must name exactly the Interceptors fields — a
        seam added to core without a registry entry (or vice versa) fails
        here instead of silently never firing."""
        field_names = tuple(f.name for f in dataclasses.fields(Interceptors))
        assert INTERCEPTOR_SEAMS == field_names

    def test_resync_accounting_shares_the_closed_seam_set(self) -> None:
        """The hot-swap seam's per-seam interceptor accounting
        (``chimera.assembly.resync``) iterates its own copy of the closed
        set — its ResyncReport entries, registry snapshot, and generic-fold
        bound are all keyed on it. A seam added to the core ``Interceptors``
        dataclass that resync does not know fails here, instead of as a
        chain ``/resync`` silently neither reports nor rebinds."""
        from chimera.assembly.resync import _INTERCEPTOR_SEAMS

        field_names = tuple(f.name for f in dataclasses.fields(Interceptors))
        assert _INTERCEPTOR_SEAMS == field_names


class TestBasePluginInterceptorHook:
    def test_activate_calls_register_interceptors(self) -> None:
        calls: list[str] = []

        class Plugin(BasePlugin):
            @property
            def name(self) -> str:
                return "interceptor-carrier"

            def register_interceptors(self, registry: ComponentRegistry) -> None:
                calls.append("interceptors")

        Plugin().activate(ComponentRegistry())
        assert calls == ["interceptors"]

    def test_activation_registers_into_class_registry(self) -> None:
        class Plugin(BasePlugin):
            @property
            def name(self) -> str:
                return "gate-carrier"

            def register_interceptors(self, registry: ComponentRegistry) -> None:
                PluginExtensionRegistry.register_interceptor("tool_call", self._gate)

            def _gate(self, call):
                return None

        plugin = Plugin()
        plugin.activate(ComponentRegistry())
        assert PluginExtensionRegistry.get_interceptors("tool_call") == [plugin._gate]
