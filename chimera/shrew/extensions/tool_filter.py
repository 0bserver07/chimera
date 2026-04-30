"""Filter the agent's tool list down to what tiny models can handle.

Empirically, sub-9B models choke on tools whose schemas are large or
whose call patterns require multi-step planning across the call
itself. The upstream small-coder project addresses this with the
``tool-gating`` extension; we ship a smaller, declarative variant
here.

Public surface:

* :func:`filter_tools_for_model` — drops the configured tool names
  from a tool list when the active model is below
  :data:`TINY_MODEL_THRESHOLD_B`.
* :data:`TOOLS_TO_DROP_FOR_TINY` — the default deny-set. Tunable by
  callers via the ``extra_drops`` argument.
* :func:`model_size_billions` — helper that maps a model id to its
  approximate parameter count, falling back to the MoE catalogue.

Stdlib-only. Pure function. Returns a *new* list; the input is
never mutated.
"""
from __future__ import annotations

import re
from typing import Any, Final

from chimera.shrew.extensions.moe_offload import MOE_MODEL_CATALOG

__all__ = [
    "TINY_MODEL_THRESHOLD_B",
    "TOOLS_TO_DROP_FOR_TINY",
    "filter_tools_for_model",
    "model_size_billions",
]


#: Models below this size (in billions of parameters) are
#: considered "tiny" and have their tool surface trimmed. 9B is the
#: line the upstream paper draws between "needs trimming" and
#: "doesn't"; we copy it.
TINY_MODEL_THRESHOLD_B: Final[float] = 9.0


#: Tool names that empirically confuse sub-9B models. Sourced from:
#:   - the upstream tool-gating extension
#:   - shrew benchmark dry-runs against Qwen3.5-9B
#:
#: Names mirror Chimera's built-in tool registry (``chimera/tools/``).
TOOLS_TO_DROP_FOR_TINY: Final[frozenset[str]] = frozenset(
    {
        "web_fetch",
        "browser",
        "browser_navigate",
        "browser_click",
        "browser_extract",
        "image_read",
        "delegate",
        "import_graph",
        "repo_map",
    }
)


# Patterns we consider "complex MCP" — names from external MCP
# servers that ship overlong / over-parametric schemas. Sub-9B
# models confuse these for their built-in counterparts and pick the
# wrong one. Pattern is conservative: must contain ``mcp`` *and*
# (``__`` namespace separator OR a long suffix).
_COMPLEX_MCP_RE: Final[re.Pattern[str]] = re.compile(
    r"^mcp[_]{2}.+__.+$",
    re.IGNORECASE,
)


def model_size_billions(model_id: str) -> float | None:
    """Map ``model_id`` to its approximate parameter count.

    Lookup order:
      1. :data:`MOE_MODEL_CATALOG` (preferred — MoE-active params).
      2. A regex over the id itself looking for ``\\d+(\\.\\d+)?b``.

    Returns ``None`` when neither path matches; callers should treat
    that as "unknown size, assume large" and skip filtering.
    """
    if not model_id:
        return None

    lookup = model_id.strip().lower()
    bare = lookup.split("/", 1)[1] if "/" in lookup else lookup

    # 1. Catalogue lookup — uses *active* params for MoE so that
    # qwen3.6-35b-a3b is treated as a 3B-active model for the
    # purposes of tool filtering, which matches reality.
    direct = MOE_MODEL_CATALOG.get(bare)
    if direct is not None:
        return direct.active_b
    for profile in MOE_MODEL_CATALOG.values():
        if bare in {alias.lower() for alias in profile.aliases}:
            return profile.active_b

    # 2. Regex fallback. Matches "9b", "9.7B", "13b-instruct", etc.
    match = re.search(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*b(?![a-z0-9])", lookup)
    if match:
        try:
            return float(match.group(1))
        except ValueError:  # pragma: no cover - regex guarantees a number
            return None
    return None


def _tool_name(tool: Any) -> str:
    """Best-effort extraction of a tool's name string.

    Accepts:
      * :class:`chimera.core.tool.BaseTool` instances (have a
        ``name`` attribute);
      * dict-shaped tool descriptors (``tool["name"]``);
      * bare strings (treated as the name).
    """
    if isinstance(tool, str):
        return tool
    if isinstance(tool, dict):
        name = tool.get("name", "")
        return str(name) if name is not None else ""
    name = getattr(tool, "name", "")
    return str(name) if name is not None else ""


def _should_drop(name: str, drops: frozenset[str]) -> bool:
    if not name:
        return False
    lowered = name.lower()
    if lowered in {d.lower() for d in drops}:
        return True
    return bool(_COMPLEX_MCP_RE.match(name))


def filter_tools_for_model(
    tools: list[Any],
    model_id: str,
    *,
    extra_drops: frozenset[str] | None = None,
) -> list[Any]:
    """Return a tool list trimmed for ``model_id``'s capability band.

    For models at or above :data:`TINY_MODEL_THRESHOLD_B` (or
    unknown sizes — fail-open so frontier models keep their full
    surface), the input list is returned unchanged (a fresh copy).

    For tiny models, tools whose names appear in
    :data:`TOOLS_TO_DROP_FOR_TINY` (plus ``extra_drops``) or match
    the complex-MCP pattern are removed.

    Args:
        tools: List of tool descriptors. Items may be
            :class:`~chimera.core.tool.BaseTool` instances, dicts
            with a ``"name"`` key, or bare strings — we handle all
            three so this helper can sit at multiple integration
            points (registry, schema list, simple name list).
        model_id: Model identifier (bare or ``provider/id``).
        extra_drops: Optional extra tool-name set to merge with the
            built-in deny list. Useful for project-specific
            overrides.

    Returns:
        A new list (input unmodified) with the disallowed tools
        removed. Order of surviving tools is preserved.
    """
    out = list(tools)  # always a fresh shallow copy
    size = model_size_billions(model_id)
    if size is None or size >= TINY_MODEL_THRESHOLD_B:
        return out

    drops = TOOLS_TO_DROP_FOR_TINY
    if extra_drops:
        drops = frozenset(drops | extra_drops)

    return [t for t in out if not _should_drop(_tool_name(t), drops)]
