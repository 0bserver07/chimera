"""Conversation-history management for agent runs.

The :class:`Context` object accumulates messages during a single agent
execution and serialises them (with an optional system message) into the
format expected by provider ``complete()`` calls.
"""

from __future__ import annotations

from chimera.types import Message


class Context:
    """Manages conversation history for a single agent run.

    Attributes:
        system: Optional system-level instruction prepended to every
            ``to_messages()`` call.
        messages: Ordered list of user / assistant / tool messages.
    """

    def __init__(self, system: str | None = None) -> None:
        """Initialise a new conversation context.

        Args:
            system: Optional system prompt.  When set, :meth:`to_messages`
                will prepend a system message to the returned list.
        """
        self.system = system
        self.messages: list[Message] = []

    def add(self, message: Message) -> None:
        """Append a message to the conversation history."""
        self.messages.append(message)

    def __len__(self) -> int:
        return len(self.messages)

    def to_messages(self) -> list[Message]:
        """Return the full messages list with the system message prepended if set.

        Returns:
            A new list of :class:`~chimera.types.Message` objects.  If
            :attr:`system` is not ``None``, a system message is inserted at
            position 0.
        """
        result: list[Message] = []
        if self.system is not None:
            result.append(Message.system(self.system))
        result.extend(self.messages)
        return result
