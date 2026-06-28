from chimera.env.base import Environment
from chimera.env.factory import (
    available_providers,
    create_environment,
    register_environment,
    unregister_environment,
)
from chimera.env.git_env import GitEnvironment
from chimera.env.local import LocalEnvironment
from chimera.env.session import SessionMixin

__all__ = [
    "Environment",
    "GitEnvironment",
    "LocalEnvironment",
    "SessionMixin",
    "available_providers",
    "create_environment",
    "register_environment",
    "unregister_environment",
]

# DockerEnvironment is conditionally available (requires `docker` package)
try:
    from chimera.env.docker import DockerEnvironment

    __all__ += ["DockerEnvironment"]  # type: ignore[assignment]
except ImportError:
    pass

# RemoteEnvironment is conditionally available (requires `httpx` package)
try:
    from chimera.env.remote import RemoteEnvironment

    __all__ += ["RemoteEnvironment"]  # type: ignore[assignment]
except ImportError:
    pass

# CloudEnvironment is conditionally available (requires `httpx` package)
try:
    from chimera.env.cloud import CloudEnvironment

    __all__ += ["CloudEnvironment"]  # type: ignore[assignment]
except ImportError:
    pass
