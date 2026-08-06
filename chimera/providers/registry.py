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

    Imports the one built-in module that owns *name* if it has not been loaded
    yet, so asking for ``"anthropic"`` never costs the OpenAI SDK's import.

    Args:
        name: The provider identifier to look up.

    Returns:
        The registered factory callable, or ``None`` if not found.
    """
    factory = _registry.get(name)
    if factory is None:
        _ensure_builtin(name)
        factory = _registry.get(name)
    return factory


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


#: Built-in provider name -> the module whose import self-registers it.
#: Derived by importing each module in a clean interpreter and reading
#: ``list_providers()``, not by reading the source — three modules
#: (``modal_endpoint``, ``xai``, ``acmecloud``) also register ``compatible``
#: as a side effect of importing it, and a hand-written map missed that.
#: ``tests/providers/test_lazy_provider_imports.py`` re-derives it and fails
#: if a module's registrations drift from this table.
_BUILTIN_MODULES: dict[str, str] = {
    "anthropic": "chimera.providers.anthropic",
    "openai": "chimera.providers.openai",
    "google": "chimera.providers.google",
    "ollama": "chimera.providers.ollama",
    "compatible": "chimera.providers.compatible",
    "modal": "chimera.providers.modal",
    "modal-endpoint": "chimera.providers.modal_endpoint",
    "xai": "chimera.providers.xai",
    "acmecloud": "chimera.providers.acmecloud",
    "faux": "chimera.providers.faux",
}


def _ensure_builtin(name: str) -> None:
    """Import only the built-in module that registers *name*.

    Why this exists: ``_ensure_builtins_registered`` imports all ten provider
    modules, and two of them pull heavy vendor SDKs — measured at 445 ms
    (``anthropic``) and 247 ms (``openai``) on a warm dev machine, and **5.1 s
    together** on a modest Linux box, where it was 99% of ``chimera code``'s
    5.1 s time-to-prompt. Talking to Anthropic should not cost the OpenAI SDK's
    import.

    Unknown names are a no-op: a caller may be asking for a provider registered
    at runtime by a plugin, which no built-in module owns.

    Args:
        name: The provider identifier being looked up.
    """
    module = _BUILTIN_MODULES.get(name)
    if module is None:
        return
    import importlib

    importlib.import_module(module)


def _ensure_builtins_registered() -> None:
    """Import **all** built-in provider modules to trigger self-registration.

    Only for callers that must enumerate every provider (``list_providers`` for
    an error message, ``chimera which``). Resolving a single provider goes
    through :func:`_ensure_builtin` instead — see its docstring for the cost.
    """
    global _builtins_registered
    if _builtins_registered:
        return
    _builtins_registered = True
    import importlib

    for module in dict.fromkeys(_BUILTIN_MODULES.values()):
        importlib.import_module(module)
