from __future__ import annotations

from chimera.types import Message


class Context:
    """Manages conversation history for an agent run."""

    def __init__(self, system: str | None = None) -> None:
        self.system = system
        self.messages: list[Message] = []

    def add(self, message: Message) -> None:
        """Append a message to the conversation history."""
        self.messages.append(message)

    def __len__(self) -> int:
        return len(self.messages)

    def to_messages(self) -> list[Message]:
        """Return messages list with system message prepended if set."""
        result: list[Message] = []
        if self.system is not None:
            result.append(Message.system(self.system))
        result.extend(self.messages)
        return result
