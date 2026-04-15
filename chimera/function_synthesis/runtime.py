"""CompiledFunction: callable wrapper around a loaded ``.chi`` bundle.

The runtime is backend-agnostic: :class:`RuntimeBackend` is an ABC, and
``chimera.function_synthesis.backends.llama_cpp`` provides the reference
implementation using ``llama-cpp-python``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from chimera.function_synthesis.bundle import ChiBundle


class RuntimeBackend(ABC):
    """Abstract inference backend for compiled functions.

    Implementations must be able to load a :class:`ChiBundle` and run
    inference against it.  Backends are responsible for loading the base
    model and attaching the adapter contained in the bundle.
    """

    @abstractmethod
    def load(self, bundle: ChiBundle) -> None:
        """Load the bundle into the backend, preparing it for inference."""

    @abstractmethod
    def invoke(self, user_input: str, *, max_tokens: int = 256) -> str:
        """Run the loaded function against ``user_input`` and return text."""

    @abstractmethod
    def close(self) -> None:
        """Release any resources held by the backend."""


class CompiledFunction:
    """A loaded ``.chi`` bundle you can call like a Python function."""

    def __init__(self, bundle: ChiBundle, backend: RuntimeBackend) -> None:
        self._bundle = bundle
        self._backend = backend
        backend.load(bundle)

    @classmethod
    def from_path(cls, path: str | Path, *, backend: RuntimeBackend) -> CompiledFunction:
        """Load a ``.chi`` bundle from ``path`` and bind it to ``backend``."""
        return cls(ChiBundle.load(path), backend)

    @property
    def name(self) -> str:
        return self._bundle.spec.name

    @property
    def spec(self) -> Any:
        return self._bundle.spec

    def __call__(self, user_input: str, *, max_tokens: int = 256) -> str:
        return self._backend.invoke(user_input, max_tokens=max_tokens)

    def close(self) -> None:
        self._backend.close()

    def __enter__(self) -> CompiledFunction:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
