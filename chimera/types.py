from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ContentBlock:
    """Base for multi-modal content blocks."""

    type: str = ""


@dataclass
class TextContent(ContentBlock):
    """Text content block."""

    text: str = ""
    type: str = "text"


@dataclass
class ImageContent(ContentBlock):
    """Image content block (base64-encoded).

    Attributes:
        data: Base64-encoded image data.
        media_type: MIME type, e.g. ``"image/png"`` or ``"image/jpeg"``.
        type: Content block discriminator (always ``"image"``).
    """

    data: str = ""  # base64-encoded image data
    media_type: str = ""  # e.g. "image/png", "image/jpeg"
    type: str = "image"


@dataclass
class Message:
    role: str  # "system", "user", "assistant", "tool"
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    call_id: str | None = None  # For tool messages
    content_blocks: list[ContentBlock] = field(default_factory=list)

    @classmethod
    def system(cls, content: str) -> Message:
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> Message:
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str, tool_calls: list[ToolCall] | None = None) -> Message:
        return cls(role="assistant", content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool(cls, call_id: str, content: str) -> Message:
        return cls(role="tool", content=content, call_id=call_id)

    @classmethod
    def user_with_image(
        cls,
        text: str,
        image_data: str,
        media_type: str = "image/png",
    ) -> Message:
        """Create a user message containing both text and an image.

        Args:
            text: The textual part of the message.
            image_data: Base64-encoded image bytes.
            media_type: MIME type of the image (default ``"image/png"``).

        Returns:
            A ``Message`` with role ``"user"`` and populated
            :attr:`content_blocks`.
        """
        return cls(
            role="user",
            content=text,
            content_blocks=[
                TextContent(text=text),
                ImageContent(data=image_data, media_type=media_type),
            ],
        )

    @property
    def has_images(self) -> bool:
        """Return ``True`` if any content block is an image."""
        return any(isinstance(b, ImageContent) for b in self.content_blocks)


@dataclass
class ToolResult:
    output: str
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int

    @property
    def success(self) -> bool:
        return self.exit_code == 0


@dataclass
class TestResult:
    passed: int
    failed: int
    errors: int
    output: str

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total > 0 else 0.0

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.errors == 0


@dataclass
class PendingApproval:
    """Represents a tool call awaiting user approval.

    Used by :meth:`iter_steps` to pause the loop when a permission policy
    returns ASK.  The consumer calls :meth:`approve` or :meth:`deny` to
    resume execution.
    """

    tool_call: ToolCall
    tool_name: str
    arguments: dict[str, Any]
    reason: str = ""
    _decision: str | None = field(default=None, repr=False)
    _denial_message: str | None = field(default=None, repr=False)

    def approve(self) -> None:
        """Approve the pending tool call."""
        self._decision = "approved"

    def deny(self, message: str = "User denied") -> None:
        """Deny the pending tool call."""
        self._decision = "denied"
        self._denial_message = message

    @property
    def decided(self) -> bool:
        return self._decision is not None

    @property
    def approved(self) -> bool:
        return self._decision == "approved"

    @property
    def denial_message(self) -> str:
        return self._denial_message or ""


@dataclass
class StepResult:
    """Result of a single agent step.

    All fields have defaults so existing code using positional args
    (``StepResult(message, tool_calls, done)``) continues to work.
    """

    message: Message | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False
    step: int = 0
    tool_results: list[ToolResult] = field(default_factory=list)
    cost: float = 0.0
    pending_approval: PendingApproval | None = None


class ChangeType(Enum):
    """Type of file change tracked by :class:`FileChange`."""

    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"


@dataclass
class FileChange:
    """Structured record of a file modification made by a tool."""

    path: str
    change_type: ChangeType
    before_content: str | None = None
    after_content: str | None = None
    diff: str | None = None

    @staticmethod
    def compute_diff(path: str, before: str, after: str) -> str:
        """Compute a unified diff between *before* and *after* content."""
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )


@dataclass
class AgentResult:
    output: str
    steps: int
    tool_calls_total: int
    cost: float
    success: bool
    error: str | None = None
