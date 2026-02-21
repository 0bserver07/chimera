# Chimera Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the MVP vertical slice of Chimera -- from `chimera.fit("spec", tests="./tests/")` to a trained codebase.

**Architecture:** Six-layer monolithic framework. Build bottom-up: Environment -> Provider -> Tools -> Agent -> Training -> CLI. Each layer has protocol + implementation. Zero required dependencies in core.

**Tech Stack:** Python 3.11+, pytest, ruff, mypy. Optional: anthropic SDK, openai SDK.

**Design Doc:** `docs/plans/2026-02-20-chimera-framework-design.md`

---

## Phase 1: Project Scaffold + Core Data Types

### Task 1: Project setup

**Files:**
- Create: `chimera/__init__.py`
- Create: `chimera/py.typed`
- Create: `pyproject.toml`
- Create: `tests/__init__.py`

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "chimera-ai"
dynamic = ["version"]
description = "The Keras of agentic coding."
readme = "README.md"
requires-python = ">=3.11"
license = {text = "AGPL-3.0"}
keywords = ["ai", "agents", "coding", "framework", "training"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3",
    "Topic :: Software Development :: Code Generators",
]

[project.optional-dependencies]
anthropic = ["anthropic>=0.40"]
openai = ["openai>=1.50"]
all = ["chimera-ai[anthropic,openai]"]
dev = ["pytest>=8.0", "ruff>=0.4", "mypy>=1.10"]

[project.scripts]
chimera = "chimera.cli.main:main"

[tool.hatch.version]
path = "chimera/__init__.py"

[tool.hatch.build.targets.wheel]
packages = ["chimera"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.mypy]
python_version = "3.11"
strict = true
```

**Step 2: Create chimera/__init__.py**

```python
from __future__ import annotations

__version__ = "0.1.0"
```

**Step 3: Create chimera/py.typed and tests/__init__.py**

Empty files.

**Step 4: Verify setup**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && pip install -e ".[dev]"`
Expected: Installs successfully

Run: `python -c "import chimera; print(chimera.__version__)"`
Expected: `0.1.0`

**Step 5: Commit**

```bash
git add pyproject.toml chimera/ tests/
git commit -m "feat: project scaffold with pyproject.toml"
```

---

### Task 2: Core data types

**Files:**
- Create: `chimera/types.py`
- Create: `tests/test_types.py`

**Step 1: Write the failing tests**

```python
# tests/test_types.py
from chimera.types import (
    Message,
    ToolCall,
    ToolResult,
    CommandResult,
    TestResult,
    StepResult,
    AgentResult,
)


def test_message_user():
    msg = Message.user("hello")
    assert msg.role == "user"
    assert msg.content == "hello"


def test_message_assistant():
    msg = Message.assistant("hi", tool_calls=[ToolCall(id="1", name="read", arguments={"path": "x"})])
    assert msg.role == "assistant"
    assert len(msg.tool_calls) == 1


def test_message_tool():
    msg = Message.tool(call_id="1", content="file contents")
    assert msg.role == "tool"
    assert msg.call_id == "1"


def test_tool_result():
    r = ToolResult(output="hello", error=None, metadata={"lines": 5})
    assert r.output == "hello"
    assert r.success is True


def test_tool_result_error():
    r = ToolResult(output="", error="not found")
    assert r.success is False


def test_command_result():
    r = CommandResult(stdout="ok", stderr="", exit_code=0)
    assert r.success is True


def test_command_result_failure():
    r = CommandResult(stdout="", stderr="error", exit_code=1)
    assert r.success is False


def test_test_result():
    r = TestResult(passed=8, failed=2, errors=0, output="...")
    assert r.total == 10
    assert r.pass_rate == 0.8
    assert r.all_passed is False


def test_test_result_all_pass():
    r = TestResult(passed=5, failed=0, errors=0, output="ok")
    assert r.all_passed is True
    assert r.pass_rate == 1.0


def test_step_result():
    r = StepResult(
        message=Message.assistant("done"),
        tool_calls=[],
        done=True,
    )
    assert r.done is True


def test_agent_result():
    r = AgentResult(
        output="completed",
        steps=5,
        tool_calls_total=12,
        cost=0.05,
        success=True,
    )
    assert r.success is True
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_types.py -v`
Expected: FAIL (module not found)

**Step 3: Implement types**

```python
# chimera/types.py
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
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_types.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add chimera/types.py tests/test_types.py
git commit -m "feat: core data types (Message, ToolResult, TestResult, etc.)"
```

---

## Phase 2: Environment Layer

### Task 3: Environment protocol

**Files:**
- Create: `chimera/env/__init__.py`
- Create: `chimera/env/base.py`
- Create: `tests/test_env.py`

**Step 1: Write the failing tests**

```python
# tests/test_env.py
from chimera.env.base import Environment
from chimera.types import CommandResult, TestResult


def test_environment_is_protocol():
    """Environment should be a Protocol class that can be subclassed."""
    class MyEnv(Environment):
        def setup(self) -> None: pass
        def cleanup(self) -> None: pass
        def read_file(self, path: str) -> str: return ""
        def write_file(self, path: str, content: str) -> None: pass
        def list_files(self, pattern: str = "**/*") -> list[str]: return []
        def run_command(self, cmd: str, timeout: int = 120) -> CommandResult:
            return CommandResult(stdout="", stderr="", exit_code=0)
        def run_tests(self) -> TestResult:
            return TestResult(passed=0, failed=0, errors=0, output="")
        def checkpoint(self) -> str: return "0"
        def restore(self, checkpoint_id: str) -> None: pass

    env = MyEnv()
    assert isinstance(env, Environment)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_env.py -v`
Expected: FAIL

**Step 3: Implement protocol**

```python
# chimera/env/__init__.py
from chimera.env.base import Environment

__all__ = ["Environment"]
```

```python
# chimera/env/base.py
from __future__ import annotations

from abc import ABC, abstractmethod

from chimera.types import CommandResult, TestResult


class Environment(ABC):
    """Where generated code lives and gets tested."""

    @abstractmethod
    def setup(self) -> None:
        """Initialize the workspace."""

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up resources."""

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read a file from the workspace."""

    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """Write a file to the workspace."""

    @abstractmethod
    def list_files(self, pattern: str = "**/*") -> list[str]:
        """List files matching a glob pattern."""

    @abstractmethod
    def run_command(self, cmd: str, timeout: int = 120) -> CommandResult:
        """Run a shell command in the workspace."""

    @abstractmethod
    def run_tests(self) -> TestResult:
        """Execute the test suite and return results."""

    @abstractmethod
    def checkpoint(self) -> str:
        """Save current state. Returns checkpoint ID."""

    @abstractmethod
    def restore(self, checkpoint_id: str) -> None:
        """Restore to a previous checkpoint."""

    def __enter__(self) -> Environment:
        self.setup()
        return self

    def __exit__(self, *args: object) -> None:
        self.cleanup()
```

**Step 4: Run tests**

Run: `pytest tests/test_env.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add chimera/env/ tests/test_env.py
git commit -m "feat: Environment abstract base class"
```

---

### Task 4: Local environment implementation

**Files:**
- Create: `chimera/env/local.py`
- Create: `tests/test_env_local.py`

**Step 1: Write the failing tests**

```python
# tests/test_env_local.py
import os
import tempfile

import pytest

from chimera.env.local import LocalEnvironment


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="python -m pytest")
        e.setup()
        yield e
        e.cleanup()


def test_write_and_read_file(env):
    env.write_file("hello.txt", "world")
    assert env.read_file("hello.txt") == "world"


def test_read_nonexistent_file(env):
    with pytest.raises(FileNotFoundError):
        env.read_file("nope.txt")


def test_write_creates_subdirs(env):
    env.write_file("a/b/c.txt", "deep")
    assert env.read_file("a/b/c.txt") == "deep"


def test_list_files(env):
    env.write_file("a.py", "x")
    env.write_file("b.py", "y")
    env.write_file("sub/c.py", "z")
    files = env.list_files("**/*.py")
    assert len(files) == 3


def test_run_command(env):
    result = env.run_command("echo hello")
    assert result.success
    assert "hello" in result.stdout


def test_run_command_failure(env):
    result = env.run_command("false")
    assert not result.success


def test_checkpoint_and_restore(env):
    env.write_file("data.txt", "version1")
    cp = env.checkpoint()

    env.write_file("data.txt", "version2")
    assert env.read_file("data.txt") == "version2"

    env.restore(cp)
    assert env.read_file("data.txt") == "version1"


def test_run_tests_no_tests(env):
    result = env.run_tests()
    # With no test files, pytest exits with code 5 (no tests collected)
    assert result.total == 0
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_env_local.py -v`
Expected: FAIL

**Step 3: Implement LocalEnvironment**

```python
# chimera/env/local.py
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

from chimera.env.base import Environment
from chimera.types import CommandResult, TestResult


class LocalEnvironment(Environment):
    """Local filesystem environment with git-based checkpointing."""

    def __init__(
        self,
        workdir: str,
        test_cmd: str = "python -m pytest",
        timeout: int = 300,
    ) -> None:
        self.workdir = Path(workdir).resolve()
        self.test_cmd = test_cmd
        self.timeout = timeout
        self._checkpoint_dir: Path | None = None

    def setup(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._checkpoint_dir = self.workdir / ".chimera_checkpoints"
        self._checkpoint_dir.mkdir(exist_ok=True)

    def cleanup(self) -> None:
        pass  # Don't delete workdir -- user may want to inspect

    def read_file(self, path: str) -> str:
        full = self.workdir / path
        if not full.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return full.read_text()

    def write_file(self, path: str, content: str) -> None:
        full = self.workdir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    def list_files(self, pattern: str = "**/*") -> list[str]:
        results = []
        for p in self.workdir.rglob("*"):
            if p.is_file() and not str(p).startswith(str(self._checkpoint_dir or "")):
                rel = str(p.relative_to(self.workdir))
                if fnmatch.fnmatch(rel, pattern):
                    results.append(rel)
        return sorted(results)

    def run_command(self, cmd: str, timeout: int | None = None) -> CommandResult:
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(self.workdir),
                timeout=timeout or self.timeout,
            )
            return CommandResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(stdout="", stderr="Command timed out", exit_code=124)

    def run_tests(self) -> TestResult:
        result = self.run_command(self.test_cmd)
        return self._parse_test_output(result)

    def checkpoint(self) -> str:
        assert self._checkpoint_dir is not None
        # Find next checkpoint ID
        existing = [
            int(d.name) for d in self._checkpoint_dir.iterdir()
            if d.is_dir() and d.name.isdigit()
        ]
        cp_id = str(max(existing, default=-1) + 1)
        cp_dir = self._checkpoint_dir / cp_id

        # Copy all non-checkpoint files
        self._copy_workspace(self.workdir, cp_dir)
        return cp_id

    def restore(self, checkpoint_id: str) -> None:
        assert self._checkpoint_dir is not None
        cp_dir = self._checkpoint_dir / checkpoint_id
        if not cp_dir.exists():
            raise ValueError(f"Checkpoint {checkpoint_id} not found")

        # Remove current files (except checkpoints)
        for item in self.workdir.iterdir():
            if item != self._checkpoint_dir:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

        # Restore from checkpoint
        self._copy_workspace(cp_dir, self.workdir)

    def _copy_workspace(self, src: Path, dst: Path) -> None:
        """Copy workspace files, excluding checkpoint directory."""
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name == ".chimera_checkpoints":
                continue
            dest_item = dst / item.name
            if item.is_dir():
                shutil.copytree(item, dest_item, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest_item)

    def _parse_test_output(self, result: CommandResult) -> TestResult:
        """Parse pytest output to extract pass/fail counts."""
        output = result.stdout + result.stderr
        passed = failed = errors = 0

        # Match pytest summary line: "X passed, Y failed, Z errors"
        match = re.search(r"(\d+) passed", output)
        if match:
            passed = int(match.group(1))
        match = re.search(r"(\d+) failed", output)
        if match:
            failed = int(match.group(1))
        match = re.search(r"(\d+) error", output)
        if match:
            errors = int(match.group(1))

        return TestResult(passed=passed, failed=failed, errors=errors, output=output)
```

**Step 4: Run tests**

Run: `pytest tests/test_env_local.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add chimera/env/local.py tests/test_env_local.py
git commit -m "feat: LocalEnvironment with file ops, commands, checkpointing"
```

---

## Phase 3: Provider Layer

### Task 5: Provider protocol

**Files:**
- Create: `chimera/providers/__init__.py`
- Create: `chimera/providers/base.py`
- Create: `tests/test_providers.py`

**Step 1: Write the failing test**

```python
# tests/test_providers.py
from chimera.providers.base import Provider, Response, StreamEvent
from chimera.types import Message, ToolCall


def test_response_dataclass():
    r = Response(
        content="hello",
        tool_calls=[ToolCall(id="1", name="read", arguments={"path": "x"})],
        usage={"input_tokens": 100, "output_tokens": 50},
    )
    assert r.content == "hello"
    assert len(r.tool_calls) == 1
    assert r.usage["input_tokens"] == 100


def test_response_no_tool_calls():
    r = Response(content="done", tool_calls=[], usage={})
    assert r.has_tool_calls is False


def test_response_with_tool_calls():
    r = Response(
        content="",
        tool_calls=[ToolCall(id="1", name="x", arguments={})],
        usage={},
    )
    assert r.has_tool_calls is True


def test_stream_event():
    e = StreamEvent(type="text_delta", content="hi")
    assert e.type == "text_delta"
```

**Step 2: Run tests**

Run: `pytest tests/test_providers.py -v`
Expected: FAIL

**Step 3: Implement protocol**

```python
# chimera/providers/__init__.py
from chimera.providers.base import Provider, Response, StreamEvent

__all__ = ["Provider", "Response", "StreamEvent"]
```

```python
# chimera/providers/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from chimera.types import Message, ToolCall


@dataclass
class Response:
    content: str
    tool_calls: list[ToolCall]
    usage: dict[str, int]

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


@dataclass
class StreamEvent:
    type: str  # "text_delta", "tool_call_start", "tool_call_delta", "done"
    content: str = ""
    tool_call: ToolCall | None = None


ToolSchema = dict[str, Any]


class Provider(ABC):
    """LLM backend. Any class implementing complete() works."""

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
        """Send messages, get a response."""

    @property
    @abstractmethod
    def context_window(self) -> int:
        """Maximum context window size in tokens."""

    @property
    @abstractmethod
    def supports_tool_use(self) -> bool:
        """Whether this provider supports function calling."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The model identifier."""
```

**Step 4: Run tests**

Run: `pytest tests/test_providers.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add chimera/providers/ tests/test_providers.py
git commit -m "feat: Provider protocol with Response and StreamEvent types"
```

---

### Task 6: Anthropic provider

**Files:**
- Create: `chimera/providers/anthropic.py`
- Create: `tests/test_provider_anthropic.py`

This task requires the `anthropic` SDK. Tests mock the API.

**Step 1: Write the failing test**

```python
# tests/test_provider_anthropic.py
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.anthropic import AnthropicProvider
from chimera.types import Message


@pytest.fixture
def provider():
    with patch("chimera.providers.anthropic.anthropic") as mock_mod:
        mock_client = MagicMock()
        mock_mod.Anthropic.return_value = mock_client
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="test-key")
        p._client = mock_client
        yield p, mock_client


def test_complete_text_response(provider):
    prov, mock_client = provider

    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text="Hello!")]
    mock_response.stop_reason = "end_turn"
    mock_response.usage.input_tokens = 100
    mock_response.usage.output_tokens = 20

    mock_client.messages.create.return_value = mock_response

    result = prov.complete([Message.user("Hi")])
    assert result.content == "Hello!"
    assert result.has_tool_calls is False
    assert result.usage["input_tokens"] == 100


def test_complete_tool_call(provider):
    prov, mock_client = provider

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "call_1"
    tool_block.name = "read_file"
    tool_block.input = {"path": "main.py"}

    mock_response = MagicMock()
    mock_response.content = [tool_block]
    mock_response.stop_reason = "tool_use"
    mock_response.usage.input_tokens = 150
    mock_response.usage.output_tokens = 30

    mock_client.messages.create.return_value = mock_response

    result = prov.complete([Message.user("Read main.py")])
    assert result.has_tool_calls is True
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "main.py"}


def test_context_window(provider):
    prov, _ = provider
    assert prov.context_window > 0


def test_supports_tool_use(provider):
    prov, _ = provider
    assert prov.supports_tool_use is True


def test_model_name(provider):
    prov, _ = provider
    assert prov.model_name == "claude-sonnet-4-20250514"
```

**Step 2: Run tests**

Run: `pytest tests/test_provider_anthropic.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# chimera/providers/anthropic.py
from __future__ import annotations

import os
from typing import Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message, ToolCall

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]


class AnthropicProvider(Provider):
    """Anthropic Claude provider."""

    CONTEXT_WINDOWS = {
        "claude-opus-4": 200_000,
        "claude-sonnet-4": 200_000,
        "claude-haiku-3.5": 200_000,
    }

    def __init__(self, model: str, api_key: str | None = None) -> None:
        if anthropic is None:
            raise ImportError("pip install chimera-ai[anthropic]")
        self._model = model
        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
        # Separate system message
        system_msg = None
        api_messages = []
        for msg in messages:
            if msg.role == "system":
                system_msg = msg.content
            elif msg.role == "tool":
                api_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.call_id,
                        "content": msg.content,
                    }],
                })
            elif msg.role == "assistant" and msg.tool_calls:
                content: list[dict[str, Any]] = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                api_messages.append({"role": "assistant", "content": content})
            else:
                api_messages.append({"role": msg.role, "content": msg.content})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if tools:
            kwargs["tools"] = tools

        response = self._client.messages.create(**kwargs)

        # Parse response
        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                ))

        return Response(
            content="".join(text_parts),
            tool_calls=tool_calls,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )

    @property
    def context_window(self) -> int:
        for prefix, size in self.CONTEXT_WINDOWS.items():
            if self._model.startswith(prefix):
                return size
        return 200_000  # Default

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model
```

**Step 4: Run tests**

Run: `pytest tests/test_provider_anthropic.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add chimera/providers/anthropic.py tests/test_provider_anthropic.py
git commit -m "feat: Anthropic provider with tool use support"
```

---

## Phase 4: Tool Layer

### Task 7: Tool protocol

**Files:**
- Create: `chimera/core/__init__.py`
- Create: `chimera/core/tool.py`
- Create: `tests/test_tool.py`

**Step 1: Write the failing tests**

```python
# tests/test_tool.py
from chimera.core.tool import BaseTool, tool
from chimera.env.local import LocalEnvironment
from chimera.types import ToolResult


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo a message"
    parameters = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    def execute(self, args: dict, env) -> ToolResult:
        return ToolResult(output=args["message"])


def test_tool_instance():
    t = EchoTool()
    assert t.name == "echo"
    assert t.description == "Echo a message"


def test_tool_execute():
    t = EchoTool()
    result = t.execute({"message": "hello"}, env=None)
    assert result.output == "hello"
    assert result.success


def test_tool_to_schema():
    t = EchoTool()
    schema = t.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"


def test_tool_to_anthropic_schema():
    t = EchoTool()
    schema = t.to_anthropic_schema()
    assert schema["name"] == "echo"
    assert "input_schema" in schema


def test_tool_decorator():
    @tool(name="greet", description="Greet someone", parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    })
    def greet(args, env):
        return ToolResult(output=f"Hello {args['name']}")

    assert greet.name == "greet"
    result = greet.execute({"name": "World"}, env=None)
    assert result.output == "Hello World"
```

**Step 2: Run tests**

Run: `pytest tests/test_tool.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# chimera/core/__init__.py
from chimera.core.tool import BaseTool, tool

__all__ = ["BaseTool", "tool"]
```

```python
# chimera/core/tool.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from chimera.env.base import Environment
from chimera.types import ToolResult


class BaseTool(ABC):
    """Base class for all tools."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema

    @abstractmethod
    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Execute the tool with given arguments."""

    def to_openai_schema(self) -> dict[str, Any]:
        """Convert to OpenAI function calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Convert to Anthropic tool use schema."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


class _FunctionTool(BaseTool):
    """Tool created from a function via decorator."""

    def __init__(
        self,
        func: Callable[..., ToolResult],
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> None:
        self._func = func
        self.name = name
        self.description = description
        self.parameters = parameters

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        return self._func(args, env)


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
) -> Callable[[Callable[..., ToolResult]], _FunctionTool]:
    """Decorator to create a tool from a function."""
    def decorator(func: Callable[..., ToolResult]) -> _FunctionTool:
        return _FunctionTool(func, name, description, parameters)
    return decorator
```

**Step 4: Run tests**

Run: `pytest tests/test_tool.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add chimera/core/ tests/test_tool.py
git commit -m "feat: Tool protocol with BaseTool, decorator, schema conversion"
```

---

### Task 8: Built-in tools (read, write, bash)

**Files:**
- Create: `chimera/tools/__init__.py`
- Create: `chimera/tools/read.py`
- Create: `chimera/tools/write.py`
- Create: `chimera/tools/bash.py`
- Create: `tests/test_tools_builtin.py`

**Step 1: Write the failing tests**

```python
# tests/test_tools_builtin.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.read import ReadFileTool
from chimera.tools.write import WriteFileTool
from chimera.tools.bash import BashTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestReadFileTool:
    def test_read_existing_file(self, env):
        env.write_file("test.txt", "hello world")
        tool = ReadFileTool()
        result = tool.execute({"path": "test.txt"}, env)
        assert result.success
        assert result.output == "hello world"

    def test_read_nonexistent_file(self, env):
        tool = ReadFileTool()
        result = tool.execute({"path": "nope.txt"}, env)
        assert not result.success
        assert "not found" in result.error.lower()

    def test_schema(self):
        tool = ReadFileTool()
        schema = tool.to_anthropic_schema()
        assert schema["name"] == "read_file"


class TestWriteFileTool:
    def test_write_new_file(self, env):
        tool = WriteFileTool()
        result = tool.execute({"path": "out.txt", "content": "data"}, env)
        assert result.success
        assert env.read_file("out.txt") == "data"

    def test_write_creates_dirs(self, env):
        tool = WriteFileTool()
        result = tool.execute({"path": "a/b/c.txt", "content": "deep"}, env)
        assert result.success
        assert env.read_file("a/b/c.txt") == "deep"

    def test_schema(self):
        tool = WriteFileTool()
        assert tool.name == "write_file"


class TestBashTool:
    def test_run_simple_command(self, env):
        tool = BashTool()
        result = tool.execute({"command": "echo hello"}, env)
        assert result.success
        assert "hello" in result.output

    def test_run_failing_command(self, env):
        tool = BashTool()
        result = tool.execute({"command": "false"}, env)
        assert not result.success

    def test_schema(self):
        tool = BashTool()
        assert tool.name == "bash"
```

**Step 2: Run tests**

Run: `pytest tests/test_tools_builtin.py -v`
Expected: FAIL

**Step 3: Implement tools**

```python
# chimera/tools/__init__.py
from chimera.tools.read import ReadFileTool
from chimera.tools.write import WriteFileTool
from chimera.tools.bash import BashTool

read_file = ReadFileTool()
write_file = WriteFileTool()
bash = BashTool()

__all__ = ["ReadFileTool", "WriteFileTool", "BashTool", "read_file", "write_file", "bash"]
```

```python
# chimera/tools/read.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
        },
        "required": ["path"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        try:
            content = env.read_file(args["path"])
            return ToolResult(output=content)
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {args['path']}")
```

```python
# chimera/tools/write.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to a file. Creates parent directories if needed."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        try:
            env.write_file(args["path"], args["content"])
            return ToolResult(output=f"Written to {args['path']}")
        except Exception as e:
            return ToolResult(output="", error=str(e))
```

```python
# chimera/tools/bash.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class BashTool(BaseTool):
    name = "bash"
    description = "Execute a shell command and return its output."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 120},
        },
        "required": ["command"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        timeout = args.get("timeout", 120)
        result = env.run_command(args["command"], timeout=timeout)
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        if result.success:
            return ToolResult(output=output)
        else:
            return ToolResult(output=output, error=f"Exit code {result.exit_code}")
```

**Step 4: Run tests**

Run: `pytest tests/test_tools_builtin.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add chimera/tools/ tests/test_tools_builtin.py
git commit -m "feat: built-in tools (read_file, write_file, bash)"
```

---

## Phase 5: Agent Core

### Task 9: Context and Prompt

**Files:**
- Create: `chimera/core/context.py`
- Create: `chimera/core/prompt.py`
- Create: `tests/test_context.py`

Context manages conversation history. Prompt handles system prompt construction.

**Step 1: Write failing tests**

```python
# tests/test_context.py
from chimera.core.context import Context
from chimera.core.prompt import Prompt
from chimera.types import Message, ToolCall


def test_context_empty():
    ctx = Context()
    assert ctx.messages == []
    assert len(ctx) == 0


def test_context_add_message():
    ctx = Context()
    ctx.add(Message.user("hello"))
    assert len(ctx) == 1
    assert ctx.messages[0].content == "hello"


def test_context_add_multiple():
    ctx = Context()
    ctx.add(Message.user("q1"))
    ctx.add(Message.assistant("a1"))
    assert len(ctx) == 2


def test_context_to_messages():
    ctx = Context(system="You are helpful.")
    ctx.add(Message.user("hello"))
    msgs = ctx.to_messages()
    assert msgs[0].role == "system"
    assert msgs[1].role == "user"


def test_prompt_from_string():
    p = Prompt.from_string("You are a coder.")
    assert "coder" in p.render()


def test_prompt_from_string_with_tools():
    p = Prompt.from_string("You are a coder.")
    rendered = p.render(tools=["read_file", "write_file"])
    assert "read_file" in rendered


def test_prompt_from_file(tmp_path):
    f = tmp_path / "prompt.txt"
    f.write_text("You are a {{role}}.")
    p = Prompt.from_file(str(f))
    rendered = p.render(role="tester")
    assert "tester" in rendered
```

**Step 2: Run tests, verify fail, implement, verify pass**

Implement `Context` as a list of Messages with system prompt support. Implement `Prompt` with simple `{{variable}}` template substitution (no Jinja2 dependency).

**Step 3: Commit**

```bash
git add chimera/core/context.py chimera/core/prompt.py tests/test_context.py
git commit -m "feat: Context and Prompt for agent conversation management"
```

---

### Task 10: ReAct loop

**Files:**
- Create: `chimera/core/loop.py`
- Create: `tests/test_loop.py`

The ReAct loop: Reason -> Act (tool call) -> Observe (tool result) -> repeat until done.

**Step 1: Write failing tests**

Test with a mock provider that returns a sequence of responses (some with tool calls, final without).

**Step 2: Implement**

```python
# chimera/core/loop.py (key structure)
class ReAct:
    def __init__(self, max_steps: int = 50):
        self.max_steps = max_steps

    def run(self, provider, tools, context, env) -> AgentResult:
        tool_map = {t.name: t for t in tools}
        schemas = [t.to_anthropic_schema() for t in tools]
        steps = 0
        total_tool_calls = 0

        for _ in range(self.max_steps):
            steps += 1
            response = provider.complete(context.to_messages(), tools=schemas)
            context.add(Message.assistant(response.content, tool_calls=response.tool_calls))

            if not response.has_tool_calls:
                return AgentResult(output=response.content, steps=steps,
                                   tool_calls_total=total_tool_calls, cost=0.0, success=True)

            for tc in response.tool_calls:
                total_tool_calls += 1
                tool = tool_map.get(tc.name)
                if tool is None:
                    context.add(Message.tool(tc.id, f"Error: unknown tool {tc.name}"))
                    continue
                result = tool.execute(tc.arguments, env)
                content = result.output if result.success else f"Error: {result.error}\n{result.output}"
                context.add(Message.tool(tc.id, content))

        return AgentResult(output="Max steps reached", steps=steps,
                           tool_calls_total=total_tool_calls, cost=0.0, success=False,
                           error="Max steps reached")
```

**Step 3: Commit**

```bash
git add chimera/core/loop.py tests/test_loop.py
git commit -m "feat: ReAct loop with tool execution"
```

---

### Task 11: Agent class

**Files:**
- Create: `chimera/core/agent.py` (update existing)
- Create: `tests/test_agent.py`

Agent = Provider + Tools + Loop + Prompt. The `run()` method creates a Context, sets up the prompt, and runs the loop.

**Step 1: Write failing tests with mock provider**
**Step 2: Implement Agent.run(task, env)**
**Step 3: Commit**

```bash
git add chimera/core/agent.py tests/test_agent.py
git commit -m "feat: Agent class composing Provider + Tools + Loop + Prompt"
```

---

## Phase 6: Training Layer

### Task 12: Spec and Architecture

**Files:**
- Create: `chimera/training/__init__.py`
- Create: `chimera/training/spec.py`
- Create: `chimera/training/architecture.py`
- Create: `tests/test_training_spec.py`

Spec: text or file or test directory. Architecture: list of Layers with dependencies.

**Step 1: Write tests for Spec (from string, from file, from tests dir)**
**Step 2: Write tests for Architecture and Layer (dependencies, topological sort)**
**Step 3: Implement**
**Step 4: Commit**

---

### Task 13: Constraints

**Files:**
- Create: `chimera/training/constraint.py`
- Create: `tests/test_constraints.py`

Constraints: tests_pass, coverage, max_complexity, max_files, custom.

**Step 1: Write tests**
**Step 2: Implement**
**Step 3: Commit**

---

### Task 14: TestConvergence strategy

**Files:**
- Create: `chimera/training/strategies/__init__.py`
- Create: `chimera/training/strategies/convergence.py`
- Create: `tests/test_strategy_convergence.py`

The core training loop: epoch -> agent generates -> run tests -> evaluate -> checkpoint -> repeat.

**Step 1: Write tests with mock agent and environment**
**Step 2: Implement the epoch loop with early stopping and rollback**
**Step 3: Commit**

---

### Task 15: Trainer and Result

**Files:**
- Create: `chimera/training/trainer.py`
- Create: `chimera/training/callbacks.py`
- Create: `tests/test_trainer.py`

Trainer ties together Architecture + Spec + Agent + Strategy + Constraints + Environment. Callbacks: Checkpoint, CostLimit, ProgressBar.

**Step 1: Write tests**
**Step 2: Implement**
**Step 3: Commit**

---

## Phase 7: End-to-End Integration

### Task 16: Public API (`chimera/__init__.py`)

**Files:**
- Modify: `chimera/__init__.py`

Export all public classes and the `chimera.fit()` one-liner.

```python
# chimera/__init__.py
from chimera.core.agent import Agent
from chimera.core.tool import BaseTool, tool
from chimera.env.base import Environment
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider
from chimera.training.spec import Spec
from chimera.training.architecture import Architecture, Layer
from chimera.training.trainer import Trainer
from chimera.training.constraint import Constraint
from chimera.training.strategies.convergence import TestConvergence

def fit(spec_text, tests=None, **kwargs):
    """One-liner: train a codebase from a spec."""
    ...
```

---

### Task 17: Integration test

**Files:**
- Create: `tests/test_integration.py`

End-to-end test: given a simple spec and test suite, train a codebase. Uses mock provider to simulate LLM responses that progressively solve the tests.

```python
# tests/test_integration.py
def test_end_to_end_training():
    """Train a simple calculator module from spec + tests."""
    # 1. Create temp dir with test file
    # 2. Configure Trainer with mock provider
    # 3. Run trainer.fit()
    # 4. Assert result.converged
    # 5. Assert generated code passes tests
```

---

## Phase 8: CLI (outline)

### Task 18: `chimera train` command

**Files:**
- Create: `chimera/cli/main.py`
- Create: `chimera/cli/train.py`

Uses argparse. Parses --spec, --output, --strategy, --model flags. Constructs Trainer and runs fit().

---

## Summary

| Phase | Tasks | What it builds |
|-------|-------|---------------|
| 1 | 1-2 | Project scaffold, core data types |
| 2 | 3-4 | Environment protocol + LocalEnvironment |
| 3 | 5-6 | Provider protocol + Anthropic provider |
| 4 | 7-8 | Tool protocol + built-in tools |
| 5 | 9-11 | Context, Prompt, ReAct loop, Agent |
| 6 | 12-15 | Spec, Architecture, Constraints, Strategy, Trainer |
| 7 | 16-17 | Public API, integration test |
| 8 | 18 | CLI |

After Phase 7, you have a working `chimera.fit()`. After Phase 8, you have `chimera train --spec spec.md`.
