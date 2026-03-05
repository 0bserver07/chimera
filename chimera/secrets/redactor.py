"""Event bus middleware for secret redaction."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from chimera.events.middleware import Middleware
from chimera.secrets.registry import SecretRegistry

if TYPE_CHECKING:
    from chimera.events.base import Event
    from chimera.secrets.detector import SecretDetector

__all__ = ["RedactionMiddleware"]


class RedactionMiddleware(Middleware):
    """EventBus middleware that redacts secrets from events before dispatch.

    Args:
        registry: Registry of known secrets to redact.
        detector: Optional pattern-based detector for unknown secrets.
        detect_unknown: If True and detector is provided, also redact
            pattern-detected secrets not in the registry.
    """

    def __init__(
        self,
        registry: SecretRegistry,
        detector: SecretDetector | None = None,
        detect_unknown: bool = False,
    ) -> None:
        self.registry = registry
        self.detector = detector
        self.detect_unknown = detect_unknown

    def process(self, event: Event, next_handler: Callable[[Event], None]) -> None:
        """Redact secrets from event data before it reaches handlers."""
        if hasattr(event, "output"):
            event.output = self._redact(event.output)
        if hasattr(event, "text"):
            event.text = self._redact(event.text)
        if hasattr(event, "content"):
            event.content = self._redact(event.content)
        if hasattr(event, "metadata") and isinstance(event.metadata, dict):
            event.metadata = {
                k: self._redact(v) if isinstance(v, str) else v
                for k, v in event.metadata.items()
            }
        next_handler(event)

    def _redact(self, text: str | None) -> str | None:
        if text is None:
            return None
        result = self.registry.redact(text)
        if self.detect_unknown and self.detector:
            result = self.detector.redact_detected(result)
        return result
