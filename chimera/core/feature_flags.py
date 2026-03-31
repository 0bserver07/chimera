"""Feature flags for gating experimental and production features.

Provides :class:`FeatureFlags` with class-level state (no instantiation
needed) and a :data:`STANDARD_FLAGS` mapping of known flag names to
descriptions.
"""
from __future__ import annotations

import os
from typing import ClassVar

__all__ = ["FeatureFlags", "STANDARD_FLAGS"]


STANDARD_FLAGS: dict[str, str] = {
    "COORDINATOR_MODE": "Enable multi-agent coordinator dispatching",
    "PERSISTENT_MEMORY": "Enable persistent memory across sessions",
    "ANALYTICS": "Enable analytics event collection",
    "BRIDGE_PROTOCOL": "Enable inter-process bridge communication",
}


class FeatureFlags:
    """Class-level feature flag registry.

    Flags have two tiers: base flags set via :meth:`set`, and runtime
    overrides set via :meth:`override`.  :meth:`enabled` checks overrides
    first, then base flags, defaulting to ``False``.
    """

    _flags: ClassVar[dict[str, bool]] = {}
    _runtime_overrides: ClassVar[dict[str, bool]] = {}

    @classmethod
    def set(cls, name: str, value: bool) -> None:
        """Set a base flag value."""
        cls._flags[name] = value

    @classmethod
    def enabled(cls, name: str) -> bool:
        """Check whether *name* is enabled (override > flag > False)."""
        if name in cls._runtime_overrides:
            return cls._runtime_overrides[name]
        return cls._flags.get(name, False)

    @classmethod
    def override(cls, name: str, value: bool) -> None:
        """Set a runtime override that takes precedence over base flags."""
        cls._runtime_overrides[name] = value

    @classmethod
    def from_env(cls) -> None:
        """Read ``CHIMERA_FEATURE_*`` environment variables into base flags.

        Truthy values: ``"1"``, ``"true"``, ``"yes"`` (case-insensitive).
        Everything else is treated as ``False``.
        """
        prefix = "CHIMERA_FEATURE_"
        for key, value in os.environ.items():
            if key.startswith(prefix):
                flag_name = key[len(prefix):]
                cls._flags[flag_name] = value.lower() in ("1", "true", "yes")

    @classmethod
    def reset(cls) -> None:
        """Clear both base flags and runtime overrides (for testing)."""
        cls._flags.clear()
        cls._runtime_overrides.clear()
