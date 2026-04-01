"""Command type definitions for the chimera command system."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class CommandType(Enum):
    """Discriminator for the kind of command."""

    PROMPT = "prompt"
    LOCAL = "local"
    LOCAL_UI = "local_ui"


@dataclass
class CommandBase:
    """Shared fields for every command variant."""

    name: str
    description: str
    aliases: list[str] = field(default_factory=list)
    argument_hint: str | None = None
    when_to_use: str | None = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    loaded_from: str = "builtin"
    is_hidden: bool = False
    is_enabled: Callable[[], bool] = field(default=lambda: True)


@dataclass
class PromptCommand(CommandBase):
    """A command that injects a prompt into the model conversation."""

    type: CommandType = CommandType.PROMPT
    progress_message: str = ""
    content_length: int = 0
    allowed_tools: list[str] | None = None
    model: str | None = None
    source: str = "builtin"
    context: str = "inline"
    get_prompt: Callable[..., str] = field(default=lambda: "")


@dataclass
class LocalCommand(CommandBase):
    """A command handled entirely on the client side (sync or async handler)."""

    type: CommandType = CommandType.LOCAL
    handler: Callable[..., Any] = field(default=lambda args: "")


@dataclass
class LocalUICommand(CommandBase):
    """A command that opens or interacts with local UI elements."""

    type: CommandType = CommandType.LOCAL_UI
    handler: Callable[..., None] | None = None


# Union type for all command kinds.
Command = PromptCommand | LocalCommand | LocalUICommand
