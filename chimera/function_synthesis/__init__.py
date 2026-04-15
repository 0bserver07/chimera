"""Function synthesis: compile specs into callable neural artifacts."""
from __future__ import annotations

from chimera.function_synthesis.bundle import ChiBundle, ChiBundleError
from chimera.function_synthesis.spec import FunctionSpec

__all__ = ["ChiBundle", "ChiBundleError", "FunctionSpec"]
