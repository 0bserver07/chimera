"""Chain of UUIDs linking consecutive messages in a transcript."""

from __future__ import annotations

import uuid
from typing import Any


class UUIDChain:
    """Generate parent-child UUID links for a sequence of messages.

    Each call to :meth:`next` returns the UUID of the *previous*
    message (or ``None`` for the first one) and records a fresh UUID
    for the current message.  Progress messages are skipped.
    """

    def __init__(self) -> None:
        self._last_uuid: str | None = None

    def next(self, message: Any) -> str | None:
        """Return the parent UUID for *message*, then advance the chain.

        If the message has ``metadata`` with ``type == "progress"`` it is
        skipped and ``None`` is returned without advancing.
        """
        metadata = getattr(message, "metadata", None)
        if isinstance(metadata, dict) and metadata.get("type") == "progress":
            return None

        parent = self._last_uuid
        self._last_uuid = str(uuid.uuid4())
        return parent
