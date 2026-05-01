"""Runtime provider registry for pluggable provider factories."""
from __future__ import annotations

from typing import Callable

from chimera.providers.base import Provider

ProviderFactory = Callable[..., Provider]

_registry: dict[str, ProviderFactory] = {}
_builtins_registered = False


def register_provider(name: str, factory: ProviderFactory) -> None:
    """Register a provider factory by name.

    Args:
        name: The provider identifier (e.g. ``"anthropic"``, ``"openai"``).
        factory: A callable that accepts keyword arguments and returns a
            :class:`~chimera.providers.base.Provider` instance.
    """
    _registry[name] = factory


def get_provider_factory(name: str) -> ProviderFactory | None:
    """Look up a registered provider factory by name.

    Args:
        name: The provider identifier to look up.

    Returns:
        The registered factory callable, or ``None`` if not found.
    """
    return _registry.get(name)


def list_providers() -> list[str]:
    """Return all registered provider names.

    Returns:
        A list of registered provider name strings.
    """
    return list(_registry.keys())


def unregister_provider(name: str) -> None:
    """Remove a provider from the registry.

    Args:
        name: The provider identifier to remove.  A no-op if not registered.
    """
    _registry.pop(name, None)


def _ensure_builtins_registered() -> None:
    """Import all built-in provider modules to trigger self-registration."""
    global _builtins_registered
    if _builtins_registered:
        return
    _builtins_registered = True
    import chimera.providers.anthropic  # noqa: F401
    import chimera.providers.openai  # noqa: F401
    import chimera.providers.google  # noqa: F401
    import chimera.providers.ollama  # noqa: F401
    import chimera.providers.compatible  # noqa: F401
    import chimera.providers.modal  # noqa: F401
    import chimera.providers.xai  # noqa: F401
