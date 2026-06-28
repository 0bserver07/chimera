"""Universal environment factory — one entry point for every sandbox backend.

``create_environment(provider, **opts)`` returns a ready (un-``setup``)
:class:`~chimera.env.base.Environment` for any registered backend: local,
git, docker, ssh, remote, cloud, modal, e2b, ….  Optional-dependency backends
raise a clear, install-hinted error when their package is missing.

This unifies the previously ad-hoc construction paths (config files,
``bench-compare --env``, per-task ``env_factory`` callables, ``docker_env_factory``)
behind a single, extensible registry.  Custom backends register via
:func:`register_environment`.

Example:
    ```python
    from chimera.env.factory import create_environment

    with create_environment("local", workdir="/tmp/work") as env:
        env.write_file("main.py", "print('hi')")
        print(env.run_command("python main.py").stdout)
    ```
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from chimera.env.base import Environment

# Provider name -> (module path, class name, pip-extra hint or "").
_BUILTIN: dict[str, tuple[str, str, str]] = {
    "local": ("chimera.env.local", "LocalEnvironment", ""),
    "git": ("chimera.env.git_env", "GitEnvironment", ""),
    "docker": ("chimera.env.docker", "DockerEnvironment", "docker"),
    "ssh": ("chimera.env.ssh", "SSHEnvironment", ""),
    "ssh-async": ("chimera.env.ssh", "AsyncSSHEnvironment", ""),
    "remote": ("chimera.env.remote", "RemoteEnvironment", "remote"),
    "cloud": ("chimera.env.cloud", "CloudEnvironment", "remote"),
    "modal": ("chimera.env.modal_sandbox", "ModalSandboxEnvironment", "modal-sandbox"),
    "e2b": ("chimera.env.e2b", "E2BEnvironment", "e2b"),
}

_CUSTOM: dict[str, Callable[..., "Environment"]] = {}


def register_environment(name: str, factory: Callable[..., "Environment"]) -> None:
    """Register a custom environment provider.

    Args:
        name: Provider key used with :func:`create_environment`.  Overrides a
            built-in of the same name.
        factory: Callable returning an :class:`Environment`; it receives the
            keyword options passed to :func:`create_environment`.
    """
    _CUSTOM[name] = factory


def unregister_environment(name: str) -> None:
    """Remove a previously registered custom provider (no-op if absent)."""
    _CUSTOM.pop(name, None)


def available_providers() -> list[str]:
    """Return all registered provider names (built-in + custom), sorted."""
    return sorted(set(_BUILTIN) | set(_CUSTOM))


def create_environment(provider: str = "local", **opts: Any) -> "Environment":
    """Create an :class:`Environment` for *provider*.

    Args:
        provider: Backend name (see :func:`available_providers`).
        **opts: Backend-specific constructor keyword arguments (e.g.
            ``workdir=`` for local, ``image=`` for docker, ``api_key=`` for
            cloud providers).

    Returns:
        A ready (un-``setup``) :class:`Environment` instance.  Call
        :meth:`Environment.setup` or use it as a context manager.

    Raises:
        ValueError: If *provider* is not registered.
        ImportError: If the backend's optional dependency is not installed.
    """
    if provider in _CUSTOM:
        return _CUSTOM[provider](**opts)
    if provider not in _BUILTIN:
        raise ValueError(
            f"Unknown environment provider {provider!r}. "
            f"Available: {', '.join(available_providers())}"
        )
    module_path, class_name, extra = _BUILTIN[provider]
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        hint = f" Install it with: pip install 'chimera-run[{extra}]'" if extra else ""
        raise ImportError(
            f"Environment provider {provider!r} requires an optional "
            f"dependency that is not installed.{hint}"
        ) from exc
    env_cls = getattr(module, class_name)
    env: Environment = env_cls(**opts)
    return env
