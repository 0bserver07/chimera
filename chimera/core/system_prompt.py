"""System prompt construction with cacheable layers.

Provides :class:`PromptLayer`, :class:`SystemPrompt`, and
:class:`SystemPromptBuilder` for assembling multi-layer system prompts
with fine-grained cache control.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PromptLayer:
    """A single named layer of the system prompt."""

    name: str
    content: str
    cacheable: bool = True


@dataclass
class SystemPrompt:
    """Assembled system prompt composed of ordered layers."""

    layers: list[PromptLayer] = field(default_factory=list)

    def to_string(self) -> str:
        """Join all non-empty layer contents with double newlines."""
        parts = [layer.content for layer in self.layers if layer.content]
        return "\n\n".join(parts)

    def cache_prefix(self) -> str:
        """Join only cacheable layers with double newlines."""
        parts = [
            layer.content
            for layer in self.layers
            if layer.content and layer.cacheable
        ]
        return "\n\n".join(parts)

    def to_api_messages(self) -> list[dict]:
        """Convert layers to API message blocks.

        Each layer becomes ``{"type": "text", "text": content}``.
        Cacheable layers get ``{"cache_control": {"type": "ephemeral"}}``
        added — *except* the last cacheable layer (to allow the API to
        see the full prefix before caching).
        """
        messages: list[dict] = []
        for layer in self.layers:
            if not layer.content:
                continue
            messages.append({"type": "text", "text": layer.content})

        # Determine which cacheable layers should get cache_control
        # (all cacheable except the last one in the list)
        cacheable_indices = [
            i for i, layer in enumerate(self.layers)
            if layer.content and layer.cacheable
        ]
        # Map from layer index to message index (skipping empty layers)
        layer_to_msg: dict[int, int] = {}
        msg_idx = 0
        for layer_idx, layer in enumerate(self.layers):
            if layer.content:
                layer_to_msg[layer_idx] = msg_idx
                msg_idx += 1

        # Add cache_control to all cacheable layers except the last one
        if cacheable_indices:
            for layer_idx in cacheable_indices[:-1]:
                mi = layer_to_msg[layer_idx]
                messages[mi]["cache_control"] = {"type": "ephemeral"}

        return messages


class SystemPromptBuilder:
    """Fluent builder for constructing a :class:`SystemPrompt`."""

    def __init__(self) -> None:
        self._layers: list[PromptLayer] = []

    def add_layer(
        self, name: str, content: str, cacheable: bool = True,
    ) -> SystemPromptBuilder:
        """Add a named layer. Returns self for chaining."""
        self._layers.append(PromptLayer(name=name, content=content, cacheable=cacheable))
        return self

    def build(self) -> SystemPrompt:
        """Build and return the assembled :class:`SystemPrompt`."""
        return SystemPrompt(layers=list(self._layers))
