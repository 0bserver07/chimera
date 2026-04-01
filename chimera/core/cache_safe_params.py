"""Cache-safe parameter tracking for LLM calls.

Provides :class:`CacheSafeParams` to snapshot prompt/tool/model state
and :class:`CacheSafeParamsStore` as a module-level singleton for
tracking the current parameters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from chimera.core.system_prompt import SystemPrompt


@dataclass
class CacheSafeParams:
    """Snapshot of parameters sent to the LLM.

    Used to detect when the cache prefix has changed so we can avoid
    invalidating prompt caches unnecessarily.
    """

    system_prompt: SystemPrompt
    tools: list[Any]  # BaseTool or dict[str, Any]
    messages: list[Any]  # Message or dict[str, Any]
    model: str
    max_output_tokens: int | None = None

    def matches(self, other: CacheSafeParams) -> bool:
        """Compare cache-relevant fields: cache_prefix, tool names, model."""
        if self.model != other.model:
            return False
        if self.system_prompt.cache_prefix() != other.system_prompt.cache_prefix():
            return False
        def _tool_name(t: Any) -> str:
            # Accept BaseTool (has .name attr) or dict (has .get("name"))
            if isinstance(t, dict):
                return t.get("name", "")
            return getattr(t, "name", "")

        self_tool_names = sorted(_tool_name(t) for t in self.tools)
        other_tool_names = sorted(_tool_name(t) for t in other.tools)
        return self_tool_names == other_tool_names


class CacheSafeParamsStore:
    """Module-level singleton to store the current :class:`CacheSafeParams`."""

    _current: ClassVar[CacheSafeParams | None] = None

    @classmethod
    def save(cls, params: CacheSafeParams) -> None:
        """Store the current parameters."""
        cls._current = params

    @classmethod
    def get(cls) -> CacheSafeParams | None:
        """Retrieve the current parameters, or ``None`` if not set."""
        return cls._current
