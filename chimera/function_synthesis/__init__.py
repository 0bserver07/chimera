"""Function synthesis: compile specs into callable neural artifacts.

The module exposes both the low-level primitives (``CompilerBackend``,
``RuntimeBackend``, ``ChiBundle``, ``FunctionSpec``) and a top-level
convenience facade for a 2-line compile + invoke flow::

    import chimera.function_synthesis as fs

    slug = fs.compile(spec)    # defaults to LocalCompiler
    fn   = fs.load(slug)       # backend auto-detected from bundle format
    fn("hello")

See :mod:`chimera.function_synthesis.facade` for the details.
"""
from __future__ import annotations

from chimera.function_synthesis.bundle import ChiBundle, ChiBundleError
from chimera.function_synthesis.compiler import CompilerBackend, CompilerError
from chimera.function_synthesis.errors import CacheMissError, OfflineError
from chimera.function_synthesis.facade import (
    compile,
    installed,
    load,
    uninstall,
)
from chimera.function_synthesis.hub import (
    HFHubAdapter,
    HubAdapter,
    HubError,
    S3HubAdapter,
)
from chimera.function_synthesis.runtime import CompiledFunction, RuntimeBackend
from chimera.function_synthesis.spec import FunctionSpec

__all__ = [
    "CacheMissError",
    "ChiBundle",
    "ChiBundleError",
    "CompiledFunction",
    "CompilerBackend",
    "CompilerError",
    "FunctionSpec",
    "HFHubAdapter",
    "HubAdapter",
    "HubError",
    "OfflineError",
    "RuntimeBackend",
    "S3HubAdapter",
    "compile",
    "installed",
    "load",
    "uninstall",
]
