from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: str  # "system", "user", "assistant", "tool"
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    call_id: str | None = None  # For tool messages

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
class StepResult:
    message: Message
    tool_calls: list[ToolCall]
    done: bool


@dataclass
class AgentResult:
    output: str
    steps: int
    tool_calls_total: int
    cost: float
    success: bool
    error: str | None = None
