from chimera.env.base import Environment
from chimera.env.git_env import GitEnvironment
from chimera.env.local import LocalEnvironment
from chimera.env.session import SessionMixin

__all__ = ["Environment", "GitEnvironment", "LocalEnvironment", "SessionMixin"]

# DockerEnvironment is conditionally available (requires `docker` package)
try:
    from chimera.env.docker import DockerEnvironment

    __all__ += ["DockerEnvironment"]  # type: ignore[assignment]
except ImportError:
    pass
