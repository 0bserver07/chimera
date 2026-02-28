# Serving Layer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete the framework analogy with streaming ReAct, real eval CLI, and interactive `chimera code` REPL.

**Architecture:** 3 independent tasks. Task 1 (streaming merge) is an enabler for Task 3 (REPL) but can be tested independently. Task 2 (eval CLI) is fully standalone. All modifications are backward-compatible.

**Tech Stack:** Python 3.11+, stdlib only (no new dependencies)

---

## Context

Chimera has 1072 passing tests. The framework layer is complete: 13 tools, 5 loops, 6 providers, Session, streaming handlers, eval harness with 4 benchmarks. Two gaps remain:

- `ReAct` loop uses `provider.complete()` (blocking). `StreamingReAct` uses `provider.stream()` but lacks permissions/detection/iter_steps. These must merge.
- `chimera eval` and `chimera bench` CLI commands are stubs that print args but don't run anything.
- No `chimera code` interactive REPL exists.

Key files to understand before starting:
- `chimera/core/loop.py` — ReAct loop with iter_steps generator
- `chimera/core/loop_config.py` — LoopConfig dataclass (handler field already exists)
- `chimera/streaming/loop.py` — StreamingReAct with `_accumulate_stream()`
- `chimera/streaming/base.py` — StreamHandler ABC
- `chimera/streaming/handlers.py` — ConsoleStreamHandler, CollectStreamHandler
- `chimera/cli/main.py` — CLI with stub eval/bench handlers
- `chimera/eval/harness.py` — Harness and Benchmark classes
- `chimera/sessions/session.py` — Session with iter_chat()

---

## Task 1: Merge Streaming into ReAct

**Files:**
- Modify: `chimera/core/loop.py:37-181`
- Test: `tests/test_react_streaming.py` (create)

**What changes:** When `LoopConfig.handler` is set, `ReAct.iter_steps()` uses `provider.stream()` + `_accumulate_stream()` instead of `provider.complete()`. Handler callbacks fire for step boundaries and tool execution. When handler is `None`, behavior is identical to current code.

### Implementation

Add a static method `_accumulate_stream` to `ReAct` (copy from `StreamingReAct._accumulate_stream` in `chimera/streaming/loop.py:210-259`). This is an exact copy — same logic, same signature:

```python
@staticmethod
def _accumulate_stream(
    events: Iterator[StreamEvent],
    handler: StreamHandler | None,
) -> Response:
    content_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    current_tool_call: ToolCall | None = None
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

    for event in events:
        if handler:
            handler.handle_event(event)
        if event.type == "text_delta":
            content_parts.append(event.content)
        elif event.type == "tool_call_start":
            current_tool_call = event.tool_call
        elif event.type == "tool_call_delta":
            pass
        elif event.type == "tool_call_complete":
            if event.tool_call is not None:
                tool_calls.append(event.tool_call)
            current_tool_call = None
        elif event.type == "done":
            if current_tool_call is not None:
                tool_calls.append(current_tool_call)
                current_tool_call = None
            if event.tool_call and event.tool_call not in tool_calls:
                tool_calls.append(event.tool_call)
            if event.usage:
                usage = event.usage

    if current_tool_call is not None:
        tool_calls.append(current_tool_call)

    return Response(
        content="".join(content_parts),
        tool_calls=tool_calls,
        usage=usage,
    )
```

Add these imports at the top of `loop.py`:

```python
from typing import Iterator, TYPE_CHECKING
# Add to existing imports:
from chimera.providers.base import Response, StreamEvent
from chimera.types import ToolCall  # add ToolCall to existing import
```

Modify `iter_steps()` — replace the provider call (line 63-65) with:

```python
handler = self.config.handler if self.config else None

if handler:
    handler.on_step_start(steps)
    events = provider.stream(
        context.to_messages(), tools=schemas if schemas else None,
    )
    response = self._accumulate_stream(events, handler)
else:
    response = provider.complete(
        context.to_messages(), tools=schemas if schemas else None,
    )
```

After tool execution (inside the `else` branch at line 152, after `exec_result` is computed and tools are done), add handler callbacks for tool start/end. This goes inside `execute_tool_calls_incremental` flow — but since that function doesn't know about the handler, we add the callbacks at the loop level. After all tool calls in a step are done:

```python
if handler:
    # Emit tool events for each tool call in this step
    for tc_idx, tc in enumerate(response.tool_calls):
        handler.on_tool_start(tc.name, tc.id)
        if tc_idx < len(exec_result.results):
            tr = exec_result.results[tc_idx]
            content = tr.output if tr.success else f"Error: {tr.error}\n{tr.output}"
            handler.on_tool_end(tc.id, content[:500])
    handler.on_step_end(steps)
```

At the two return points (done=True at line 83 and max_steps at line 174), add:
```python
if handler:
    handler.on_done()
```

### Tests (`tests/test_react_streaming.py`)

```python
"""Tests for ReAct loop with streaming handler."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from chimera.core.context import Context
from chimera.core.loop import ReAct, drain_steps
from chimera.core.loop_config import LoopConfig
from chimera.providers.base import Provider, Response, StreamEvent
from chimera.streaming.handlers import CollectStreamHandler
from chimera.types import Message, ToolCall


class FakeStreamProvider(Provider):
    """Provider that yields streaming events."""

    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self._responses = responses
        self._call = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        raise AssertionError("complete() should not be called when streaming")

    def stream(self, messages, tools=None, temperature=0.0, max_tokens=None):
        events = self._responses[self._call]
        self._call += 1
        yield from events

    @property
    def model_name(self) -> str:
        return "test-model"

    @property
    def context_window(self) -> int:
        return 4096

    @property
    def supports_tool_use(self) -> bool:
        return True


class FakeCompleteProvider(Provider):
    """Provider that only supports complete(), not stream()."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = responses
        self._call = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        resp = self._responses[self._call]
        self._call += 1
        return resp

    @property
    def model_name(self) -> str:
        return "test-model"

    @property
    def context_window(self) -> int:
        return 4096

    @property
    def supports_tool_use(self) -> bool:
        return True


class TestReActStreaming:
    def test_text_streams_to_handler(self):
        """When handler is set, text deltas flow through it."""
        events = [
            StreamEvent(type="text_delta", content="Hello "),
            StreamEvent(type="text_delta", content="world"),
            StreamEvent(type="done", usage={"input_tokens": 10, "output_tokens": 5}),
        ]
        provider = FakeStreamProvider([events])
        handler = CollectStreamHandler()
        loop = ReAct(config=LoopConfig(handler=handler))

        result = drain_steps(loop.iter_steps(provider, [], Context(), None))

        assert result.success
        assert result.output == "Hello world"
        text_events = [e for e in handler.events if e["type"] == "text"]
        assert len(text_events) == 2
        assert text_events[0]["content"] == "Hello "
        assert text_events[1]["content"] == "world"

    def test_no_handler_uses_complete(self):
        """When handler is None, provider.complete() is used (not stream)."""
        provider = FakeCompleteProvider([
            Response(content="Hi", tool_calls=[], usage={"input_tokens": 5, "output_tokens": 2}),
        ])
        loop = ReAct()  # No config, no handler

        result = drain_steps(loop.iter_steps(provider, [], Context(), None))

        assert result.success
        assert result.output == "Hi"

    def test_handler_gets_step_events(self):
        """Handler receives step_start, step_end, and done events."""
        events = [
            StreamEvent(type="text_delta", content="ok"),
            StreamEvent(type="done", usage={"input_tokens": 1, "output_tokens": 1}),
        ]
        provider = FakeStreamProvider([events])
        handler = CollectStreamHandler()
        loop = ReAct(config=LoopConfig(handler=handler))

        drain_steps(loop.iter_steps(provider, [], Context(), None))

        types = [e["type"] for e in handler.events]
        assert "step_start" in types
        assert "step_end" in types
        assert "done" in types

    def test_handler_gets_tool_events(self):
        """Handler receives tool_start and tool_end for tool calls."""
        from chimera.core.tool import tool as tool_decorator

        @tool_decorator(name="greet", description="Say hello")
        def greet(env, name: str = "world") -> str:
            return f"Hello {name}"

        tc = ToolCall(id="c1", name="greet", arguments={"name": "Alice"})
        # Step 1: tool call
        step1_events = [
            StreamEvent(type="tool_call_start", tool_call=tc),
            StreamEvent(type="tool_call_complete", tool_call=tc),
            StreamEvent(type="done", usage={"input_tokens": 10, "output_tokens": 5}),
        ]
        # Step 2: final text
        step2_events = [
            StreamEvent(type="text_delta", content="Done"),
            StreamEvent(type="done", usage={"input_tokens": 10, "output_tokens": 3}),
        ]
        provider = FakeStreamProvider([step1_events, step2_events])
        handler = CollectStreamHandler()
        loop = ReAct(config=LoopConfig(handler=handler))

        result = drain_steps(loop.iter_steps(provider, [greet], Context(), None))

        assert result.success
        types = [e["type"] for e in handler.events]
        assert "tool_start" in types
        assert "tool_end" in types

    def test_accumulate_stream_static(self):
        """ReAct._accumulate_stream works identically to StreamingReAct's."""
        events = [
            StreamEvent(type="text_delta", content="Hello "),
            StreamEvent(type="text_delta", content="world"),
            StreamEvent(type="done", usage={"input_tokens": 10, "output_tokens": 5}),
        ]
        resp = ReAct._accumulate_stream(iter(events), None)
        assert resp.content == "Hello world"
        assert resp.usage == {"input_tokens": 10, "output_tokens": 5}

    def test_streaming_preserves_permissions(self):
        """Streaming + permissions work together (the whole point of the merge)."""
        from chimera.permissions.base import PermissionPolicy, PermissionAction

        class DenyBash(PermissionPolicy):
            def evaluate(self, tool_name, arguments):
                return PermissionAction.DENY if tool_name == "bash" else PermissionAction.ALLOW

        from chimera.core.tool import tool as tool_decorator

        @tool_decorator(name="bash", description="Run command")
        def bash(env, command: str = "ls") -> str:
            return "output"

        tc = ToolCall(id="c1", name="bash", arguments={"command": "ls"})
        step1_events = [
            StreamEvent(type="tool_call_start", tool_call=tc),
            StreamEvent(type="tool_call_complete", tool_call=tc),
            StreamEvent(type="done", usage={"input_tokens": 10, "output_tokens": 5}),
        ]
        step2_events = [
            StreamEvent(type="text_delta", content="OK"),
            StreamEvent(type="done", usage={"input_tokens": 5, "output_tokens": 2}),
        ]
        provider = FakeStreamProvider([step1_events, step2_events])
        handler = CollectStreamHandler()
        loop = ReAct(config=LoopConfig(handler=handler, permissions=DenyBash()))

        result = drain_steps(loop.iter_steps(provider, [bash], Context(), None))

        assert result.success
        # bash was denied but the loop continued

    def test_iter_steps_yields_with_handler(self):
        """iter_steps still yields StepResult even with streaming."""
        events = [
            StreamEvent(type="text_delta", content="answer"),
            StreamEvent(type="done", usage={"input_tokens": 5, "output_tokens": 3}),
        ]
        provider = FakeStreamProvider([events])
        handler = CollectStreamHandler()
        loop = ReAct(config=LoopConfig(handler=handler))

        steps = list(loop.iter_steps(provider, [], Context(), None))
        assert len(steps) == 1
        assert steps[0].done is True
        assert steps[0].message.content == "answer"
```

**Verification:** `python -m pytest tests/test_react_streaming.py -v`

---

## Task 2: Wire chimera eval CLI

**Files:**
- Modify: `chimera/cli/main.py:46-67,92-142,148-208`
- Test: `tests/test_cli_eval.py` (modify existing)

### Implementation

**Add `--model` to eval and bench subparsers.** In `chimera/cli/main.py`, after the existing eval parser args (around line 67), add:

```python
eval_parser.add_argument(
    "--model",
    default="claude-sonnet-4-20250514",
    help="Model to use (default: claude-sonnet-4-20250514)",
)
```

Same for `bench_parser` (around line 87):

```python
bench_parser.add_argument(
    "--model",
    default="claude-sonnet-4-20250514",
    help="Model to use (default: claude-sonnet-4-20250514)",
)
```

**Add benchmark registry and loader.** Add before `run_synthesize` (around line 144):

```python
_BENCHMARKS: dict[str, str] = {
    "human-eval": "chimera.eval.benchmarks.human_eval:HumanEval",
    "swe-bench": "chimera.eval.benchmarks.swe_bench:SWEBench",
    "aimo": "chimera.eval.benchmarks.aimo:AIMOBenchmark",
    "custom": "chimera.eval.benchmarks.custom:CustomBenchmark",
}


def _load_benchmark(name: str, dataset: str | None = None, limit: int | None = None, tasks_dir: str | None = None):
    """Instantiate a benchmark by name."""
    if name not in _BENCHMARKS:
        raise ValueError(f"Unknown benchmark: {name}. Available: {', '.join(_BENCHMARKS)}")
    module_path, class_name = _BENCHMARKS[name].rsplit(":", 1)
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    if name == "custom":
        return cls(tasks_dir=tasks_dir or dataset)
    kwargs = {}
    if dataset:
        kwargs["dataset_path"] = dataset
    if limit:
        kwargs["limit"] = limit
    return cls(**kwargs)


def _result_to_dict(result) -> dict:
    """Convert EvalResult to a JSON-serializable dict."""
    import dataclasses
    return {
        "benchmark": result.benchmark,
        "total": result.total,
        "passed": result.passed,
        "pass_rate": result.pass_rate,
        "total_cost": result.total_cost,
        "results": [dataclasses.asdict(r) for r in result.results],
    }
```

**Replace `run_eval` stub.** Replace the entire function body:

```python
def run_eval(args: argparse.Namespace) -> int:
    """Execute the eval command."""
    from chimera.core.agent import Agent
    from chimera.core.tool_group import DEFAULT_TOOLS
    from chimera.eval.harness import Harness
    from chimera.providers.factory import create_provider

    try:
        benchmark = _load_benchmark(args.benchmark, dataset=args.dataset, limit=args.limit)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS))
    harness = Harness(benchmark, agent)

    print(f"Running {benchmark.name()} ({len(benchmark.tasks())} tasks) with {args.model}...", file=sys.stderr)
    result = harness.run()

    print(f"\n{result.benchmark}: {result.passed}/{result.total} passed ({result.pass_rate:.1%})", file=sys.stderr)
    print(f"Total cost: ${result.total_cost:.4f}", file=sys.stderr)

    if args.output:
        import json
        with open(args.output, "w") as f:
            json.dump(_result_to_dict(result), f, indent=2)
        print(f"Results written to {args.output}", file=sys.stderr)

    return 0 if result.passed == result.total else 1
```

**Replace `run_bench` stub:**

```python
def run_bench(args: argparse.Namespace) -> int:
    """Execute the bench command."""
    from chimera.core.agent import Agent
    from chimera.core.tool_group import DEFAULT_TOOLS
    from chimera.eval.harness import Harness
    from chimera.providers.factory import create_provider

    try:
        benchmark = _load_benchmark("custom", tasks_dir=args.tasks_dir)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS))
    harness = Harness(benchmark, agent)

    print(f"Running {benchmark.name()} ({len(benchmark.tasks())} tasks) with {args.model}...", file=sys.stderr)
    result = harness.run()

    print(f"\n{result.benchmark}: {result.passed}/{result.total} passed ({result.pass_rate:.1%})", file=sys.stderr)
    print(f"Total cost: ${result.total_cost:.4f}", file=sys.stderr)

    if args.output:
        import json
        with open(args.output, "w") as f:
            json.dump(_result_to_dict(result), f, indent=2)
        print(f"Results written to {args.output}", file=sys.stderr)

    return 0 if result.passed == result.total else 1
```

### Tests

Add these tests to `tests/test_cli_eval.py` (append to existing file):

```python
class TestEvalWiring:
    def test_parse_model_flag(self):
        parser = build_parser()
        args = parser.parse_args([
            "eval", "--benchmark", "human-eval", "--model", "gpt-4o",
        ])
        assert args.model == "gpt-4o"

    def test_model_default(self):
        parser = build_parser()
        args = parser.parse_args(["eval", "--benchmark", "swe-bench"])
        assert args.model == "claude-sonnet-4-20250514"

    def test_load_benchmark_human_eval(self):
        from chimera.cli.main import _load_benchmark
        bench = _load_benchmark("human-eval")
        assert bench.name() == "human-eval"

    def test_load_benchmark_custom(self):
        from chimera.cli.main import _load_benchmark
        bench = _load_benchmark("custom", tasks_dir="/tmp")
        assert bench.name() == "custom"

    def test_load_benchmark_unknown(self):
        from chimera.cli.main import _load_benchmark
        with pytest.raises(ValueError, match="Unknown benchmark"):
            _load_benchmark("nonexistent")

    def test_result_to_dict(self):
        from chimera.cli.main import _result_to_dict
        from chimera.eval.harness import EvalResult, TaskEvalResult
        result = EvalResult(
            benchmark="test",
            total=2,
            passed=1,
            pass_rate=0.5,
            results=[
                TaskEvalResult(task_id="t1", passed=True, output="ok", cost=0.01, steps=3),
                TaskEvalResult(task_id="t2", passed=False, output="fail", cost=0.02, steps=5),
            ],
            total_cost=0.03,
        )
        d = _result_to_dict(result)
        assert d["benchmark"] == "test"
        assert d["passed"] == 1
        assert len(d["results"]) == 2
        assert d["results"][0]["task_id"] == "t1"
```

Also add to `tests/test_cli_bench.py`:

```python
class TestBenchWiring:
    def test_parse_model_flag(self):
        parser = build_parser()
        args = parser.parse_args([
            "bench", "--suite", "custom", "--model", "gpt-4o",
        ])
        assert args.model == "gpt-4o"

    def test_model_default(self):
        parser = build_parser()
        args = parser.parse_args(["bench", "--suite", "custom"])
        assert args.model == "claude-sonnet-4-20250514"
```

**Verification:** `python -m pytest tests/test_cli_eval.py tests/test_cli_bench.py -v`

---

## Task 3: chimera code REPL

**Files:**
- Create: `chimera/cli/code.py`
- Modify: `chimera/cli/main.py` (add `code` subcommand)
- Test: `tests/test_cli_code.py` (create)

### Implementation

**Create `chimera/cli/code.py`:**

```python
"""Interactive coding agent REPL."""
from __future__ import annotations

import os
import sys

from chimera import __version__
from chimera.core.agent import Agent
from chimera.core.loop import ReAct, drain_steps
from chimera.core.loop_config import LoopConfig
from chimera.core.prompt import Prompt
from chimera.core.tool_group import DEFAULT_TOOLS
from chimera.env.local import LocalEnvironment
from chimera.providers.factory import create_provider
from chimera.sessions.session import Session
from chimera.streaming.handlers import ConsoleStreamHandler

_DEFAULT_SYSTEM = """\
You are a coding assistant with access to tools for reading, writing, \
editing files, running commands, searching code, and running tests. \
Help the user with their coding tasks. Be concise and direct."""


def run_code(args) -> int:
    """Run the interactive coding REPL."""
    workdir = os.path.abspath(args.workdir)
    provider = create_provider(model=args.model)
    env = LocalEnvironment(workdir=workdir)
    env.setup()

    # Auto-discover project context
    system = _DEFAULT_SYSTEM
    try:
        from chimera.config.loader import ProjectConfig
        project = ProjectConfig.from_directory(workdir)
        if project and project.rules_text:
            system += "\n\n# Project Context\n" + project.rules_text
    except Exception:
        pass  # Config discovery is best-effort

    handler = ConsoleStreamHandler()
    loop = ReAct(
        max_steps=args.max_steps,
        config=LoopConfig(handler=handler),
    )

    prompt = Prompt.from_string(system)
    agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS), loop=loop, prompt=prompt)
    session = Session(agent=agent, env=env)

    total_cost = 0.0
    print(f"chimera code v{__version__} | model: {args.model} | workdir: {workdir}")
    print("Type /exit to quit.\n")

    while True:
        try:
            user_input = input("> ")
        except (EOFError, KeyboardInterrupt):
            break
        cmd = user_input.strip()
        if cmd in ("/exit", "/quit"):
            break
        if not cmd:
            continue

        try:
            result = drain_steps(session.iter_chat(user_input))
            total_cost += result.cost
            print(f"\n  [cost: ${result.cost:.4f} | steps: {result.steps}]")
        except Exception as e:
            print(f"\nError: {e}", file=sys.stderr)

    print(f"\nSession total: ${total_cost:.4f}")
    env.cleanup()
    return 0
```

**Modify `chimera/cli/main.py`** — add `code` subcommand. After the bench_parser block (around line 88), add:

```python
# ---- code subcommand ----
code_parser = subparsers.add_parser(
    "code",
    help="Interactive coding agent REPL",
)
code_parser.add_argument(
    "--model",
    default="claude-sonnet-4-20250514",
    help="Model to use (default: claude-sonnet-4-20250514)",
)
code_parser.add_argument(
    "--workdir",
    default=".",
    help="Working directory (default: current directory)",
)
code_parser.add_argument(
    "--max-steps",
    type=int,
    default=50,
    help="Maximum agent steps per turn (default: 50)",
)
```

In the `main()` function, add the `code` dispatch (around line 225):

```python
elif args.command == "code":
    from chimera.cli.code import run_code
    return run_code(args)
```

### Tests (`tests/test_cli_code.py`)

```python
"""Tests for the chimera code REPL."""
from __future__ import annotations

import pytest

from chimera.cli.main import build_parser


class TestCodeParser:
    def test_parse_code_args(self):
        parser = build_parser()
        args = parser.parse_args([
            "code", "--model", "gpt-4o", "--workdir", "/tmp", "--max-steps", "25",
        ])
        assert args.command == "code"
        assert args.model == "gpt-4o"
        assert args.workdir == "/tmp"
        assert args.max_steps == 25

    def test_code_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["code"])
        assert args.command == "code"
        assert args.model == "claude-sonnet-4-20250514"
        assert args.workdir == "."
        assert args.max_steps == 50

    def test_code_help(self, capsys):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["code", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "interactive" in captured.out.lower() or "repl" in captured.out.lower() or "coding" in captured.out.lower()


class TestCodeModule:
    def test_default_system_prompt(self):
        from chimera.cli.code import _DEFAULT_SYSTEM
        assert "coding assistant" in _DEFAULT_SYSTEM

    def test_run_code_exit(self, monkeypatch, tmp_path):
        """REPL exits cleanly on /exit."""
        from chimera.cli.code import run_code
        import argparse

        # Mock create_provider to avoid needing API keys
        mock_provider = type("P", (), {
            "complete": lambda *a, **kw: None,
            "stream": lambda *a, **kw: iter([]),
            "model_name": "test",
            "context_window": 4096,
            "supports_tool_use": True,
        })()
        monkeypatch.setattr("chimera.cli.code.create_provider", lambda **kw: mock_provider)

        inputs = iter(["/exit"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

        args = argparse.Namespace(model="test", workdir=str(tmp_path), max_steps=10)
        result = run_code(args)
        assert result == 0

    def test_run_code_eof(self, monkeypatch, tmp_path):
        """REPL exits cleanly on EOF (Ctrl+D)."""
        from chimera.cli.code import run_code
        import argparse

        mock_provider = type("P", (), {
            "complete": lambda *a, **kw: None,
            "stream": lambda *a, **kw: iter([]),
            "model_name": "test",
            "context_window": 4096,
            "supports_tool_use": True,
        })()
        monkeypatch.setattr("chimera.cli.code.create_provider", lambda **kw: mock_provider)
        monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(EOFError))

        args = argparse.Namespace(model="test", workdir=str(tmp_path), max_steps=10)
        result = run_code(args)
        assert result == 0

    def test_run_code_empty_input_skipped(self, monkeypatch, tmp_path):
        """Empty lines are skipped, not sent to agent."""
        from chimera.cli.code import run_code
        import argparse

        mock_provider = type("P", (), {
            "complete": lambda *a, **kw: None,
            "stream": lambda *a, **kw: iter([]),
            "model_name": "test",
            "context_window": 4096,
            "supports_tool_use": True,
        })()
        monkeypatch.setattr("chimera.cli.code.create_provider", lambda **kw: mock_provider)

        inputs = iter(["", "   ", "/exit"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

        args = argparse.Namespace(model="test", workdir=str(tmp_path), max_steps=10)
        result = run_code(args)
        assert result == 0
```

**Verification:** `python -m pytest tests/test_cli_code.py -v`

---

## Implementation Order

Tasks are mostly independent but recommended order:

```
Task 1: Merge streaming into ReAct    (enabler for Task 3)
Task 2: Wire chimera eval CLI         (standalone)
Task 3: chimera code REPL             (depends on Task 1)
```

---

## Final Verification

After all tasks:
1. `python -m pytest tests/ -x -q` — all 1072+ existing tests still pass
2. `python -m pytest tests/test_react_streaming.py tests/test_cli_eval.py tests/test_cli_bench.py tests/test_cli_code.py -v` — all new tests pass
3. `python -c "from chimera.cli.code import run_code; from chimera.cli.main import _load_benchmark, _result_to_dict"` — new exports importable
4. `python -m chimera code --help` — shows help text
5. `python -m chimera eval --help` — shows help with --model flag
