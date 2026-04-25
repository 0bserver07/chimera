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
        # WHY: Event subclasses carry payloads under varying attribute
        # names (``output`` / ``text`` / ``content`` / ``arguments`` /
        # ``metadata`` / ``tool_metadata``). We use ``getattr``/``setattr``
        # to avoid pyright complaints about attributes that only exist on
        # certain subclasses while keeping the behaviour identical.
        # WHY (audit M-9): the typed shape declares ``output`` as ``str``,
        # but in practice tools sometimes hand the loop a dict/list (e.g.
        # ``{"stdout": "<api key>"}``). Walk containers too so secrets
        # buried inside structured payloads still get redacted before
        # reaching the JSON serializer.
        for attr in ("output", "text", "content"):
            if hasattr(event, attr):
                value = getattr(event, attr)
                if isinstance(value, (dict, list)):
                    setattr(event, attr, self._redact_container(value))
                else:
                    setattr(event, attr, self._redact(value))
        # tool calls carry their args as a dict; secrets in command
        # strings (e.g. ``{"command": "echo $TOKEN"}``) must not leak.
        if hasattr(event, "arguments"):
            arguments = getattr(event, "arguments")
            if isinstance(arguments, dict):
                setattr(event, "arguments", self._redact_container(arguments))
        # WHY (audit M-9): ``ToolResultEvent.tool_metadata`` is a free-form
        # dict; redact strings nested arbitrarily deep so callers that
        # stash response metadata don't inadvertently leak.
        for meta_attr in ("metadata", "tool_metadata"):
            if hasattr(event, meta_attr):
                metadata = getattr(event, meta_attr)
                if isinstance(metadata, dict):
                    setattr(event, meta_attr, self._redact_container(metadata))
        next_handler(event)

    def _redact_container(self, value: object) -> object:
        """Recursively redact strings inside a dict / list payload."""
        if isinstance(value, str):
            return self._redact(value)
        if isinstance(value, dict):
            return {k: self._redact_container(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact_container(item) for item in value]
        return value

    def _redact(self, text: str | None) -> str | None:
        if text is None:
            return None
        result = self.registry.redact(text)
        if self.detect_unknown and self.detector:
            result = self.detector.redact_detected(result)
        return result
