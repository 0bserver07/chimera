"""CompilerBackend: the abstract interface for producing ``.chi`` bundles.

Concrete compilers (see ``chimera.function_synthesis.compilers``) take a
:class:`FunctionSpec` and return a :class:`ChiBundle`.  The ABC keeps the
synthesis strategy layer independent of any specific compilation backend.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from chimera.function_synthesis.bundle import ChiBundle
from chimera.function_synthesis.spec import FunctionSpec


class CompilerError(ValueError):
    """Raised when a compiler backend cannot produce a bundle."""


class CompilerBackend(ABC):
    """Abstract compiler that turns a :class:`FunctionSpec` into a bundle."""

    @abstractmethod
    def compile(self, spec: FunctionSpec) -> ChiBundle:
        """Compile ``spec`` into a :class:`ChiBundle`."""
