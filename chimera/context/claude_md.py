"""Deprecated alias for :mod:`chimera.context.agent_memory`.

The module was renamed to ``chimera.context.agent_memory``. Importers
should switch to that module; this shim re-exports the public surface
and emits a :class:`DeprecationWarning` on import so existing call sites
keep working. Kept only so that the legacy ``claude_md`` import path
continues to work for one release cycle.
"""
from __future__ import annotations

import warnings

from chimera.context.agent_memory import (
    discover_memory_files,
    inject_memory,
    load_memory,
    parse_frontmatter,
    resolve_imports,
)

warnings.warn(
    "chimera.context.claude_md is deprecated; "
    "import from chimera.context.agent_memory instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "discover_memory_files",
    "inject_memory",
    "load_memory",
    "parse_frontmatter",
    "resolve_imports",
]
