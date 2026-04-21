# AIMO3 Competition Module — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build AIMO3 competition support into Chimera as reusable framework blocks — ModalProvider, VerifyTool, AIMOBenchmark, MajorityVoting strategy, and Kaggle notebook.

**Architecture:** New components slot into Chimera's existing layer stack: ModalProvider (Layer 2), VerifyTool (Layer 3), AIMOBenchmark (Layer 4), MajorityVoting + AIMOEnsemble (Layer 5). Each component is independently testable. The MajorityVoting strategy samples N solutions per problem, executes Python code via bash tool, and takes the consensus integer answer.

**Tech Stack:** Python 3.11+, httpx (existing optional dep), modal (new optional dep), pytest. No new required dependencies.

**Design doc:** `docs/plans/2026-02-25-aimo3-competition-design.md`

---

### Task 1: ModalProvider

**Files:**
- Create: `chimera/providers/modal.py`
- Modify: `chimera/providers/__init__.py`
- Modify: `chimera/providers/factory.py`
- Test: `tests/test_provider_modal.py`

**Context:** The ModalProvider wraps Modal's serverless GPU inference. It deploys a vLLM container on Modal, then delegates to `OpenAICompatibleProvider` for actual inference calls. Modal is an optional dependency — import-guarded like httpx in the existing providers. Follow the exact patterns from `chimera/providers/ollama.py` and `chimera/providers/compatible.py`.

**Step 1: Write the failing test**

Create `tests/test_provider_modal.py`:

```python
# tests/test_provider_modal.py
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.modal import ModalProvider
from chimera.types import Message


@pytest.fixture
def provider():
    """Create ModalProvider with mocked modal and httpx."""
    with patch("chimera.providers.modal.modal") as mock_modal, \
         patch("chimera.providers.modal.httpx") as mock_httpx:
        p = ModalProvider(
            model="Qwen/Qwen3-235B-AWQ",
            gpu="H100",
        )
        yield p, mock_modal, mock_httpx


def test_model_name(provider):
    prov, _, _ = provider
    assert prov.model_name == "Qwen/Qwen3-235B-AWQ"


def test_context_window(provider):
    prov, _, _ = provider
    assert prov.context_window == 131_072


def test_supports_tool_use(provider):
    prov, _, _ = provider
    assert prov.supports_tool_use is True


def test_complete_text_response(provider):
    prov, mock_modal, mock_httpx = provider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{
            "message": {"role": "assistant", "content": "The answer is 42."},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    mock_httpx.post.return_value = mock_response

    result = prov.complete([Message.user("What is 6*7?")])
    assert result.content == "The answer is 42."
    assert result.has_tool_calls is False


def test_complete_tool_call(provider):
    prov, mock_modal, mock_httpx = provider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "python -c \\"print(42)\\""}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 150, "completion_tokens": 30},
    }
    mock_httpx.post.return_value = mock_response

    result = prov.complete([Message.user("Compute 6*7")])
    assert result.has_tool_calls is True
    assert result.tool_calls[0].name == "bash"


def test_env_vars_for_credentials():
    """ModalProvider reads MODAL_TOKEN_ID and MODAL_TOKEN_SECRET from env."""
    with patch("chimera.providers.modal.modal") as mock_modal, \
         patch("chimera.providers.modal.httpx"), \
         patch.dict("os.environ", {
             "MODAL_TOKEN_ID": "ak-test",
             "MODAL_TOKEN_SECRET": "as-test",
         }):
        p = ModalProvider(model="test-model")
        assert p._token_id == "ak-test"
        assert p._token_secret == "as-test"
```

**Step 2: Run test to verify it fails**

Run: `cd . && python -m pytest tests/test_provider_modal.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chimera.providers.modal'`

**Step 3: Write the implementation**

Create `chimera/providers/modal.py`:

```python
# chimera/providers/modal.py
from __future__ import annotations

import json
import os
from typing import Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message, ToolCall

try:
    import modal
except ImportError:
    modal = None  # type: ignore[assignment]

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class ModalProvider(Provider):
    """Modal serverless GPU inference provider.

    Deploys a vLLM container on Modal GPUs, then calls it via
    the OpenAI-compatible /v1/chat/completions endpoint.

    Requires: pip install modal httpx
    Auth: MODAL_TOKEN_ID + MODAL_TOKEN_SECRET env vars, or pass directly.
    """

    def __init__(
        self,
        model: str,
        gpu: str = "H100",
        token_id: str | None = None,
        token_secret: str | None = None,
        base_url: str | None = None,
        context_length: int = 131_072,
    ) -> None:
        if httpx is None:
            raise ImportError("pip install httpx")
        self._model = model
        self._gpu = gpu
        self._token_id = token_id or os.environ.get("MODAL_TOKEN_ID", "")
        self._token_secret = token_secret or os.environ.get("MODAL_TOKEN_SECRET", "")
        self._base_url = base_url  # If set, skip Modal deployment and call directly
        self._context_length = context_length

    def _get_base_url(self) -> str:
        """Get the vLLM endpoint URL. If base_url was provided, use it directly."""
        if self._base_url:
            return self._base_url.rstrip("/")
        # In production, this would deploy/lookup a Modal app running vLLM
        # and return its URL. For now, require explicit base_url or
        # expect the Modal app to be deployed separately.
        raise ValueError(
            "ModalProvider requires base_url pointing to a running vLLM instance on Modal. "
            "Deploy with: modal deploy chimera/providers/modal_app.py"
        )

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
        base_url = self._get_base_url()
        api_messages = self._convert_messages(messages)

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = self._convert_tools(tools)

        endpoint = f"{base_url}/v1/chat/completions"
        resp = httpx.post(endpoint, json=payload, timeout=300)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        content = choice["message"].get("content") or ""

        tool_calls = []
        for tc in choice["message"].get("tool_calls", []) or []:
            args = tc["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            tool_calls.append(ToolCall(
                id=tc.get("id", f"call_{id(tc)}"),
                name=tc["function"]["name"],
                arguments=args,
            ))

        usage = data.get("usage", {})
        return Response(
            content=content,
            tool_calls=tool_calls,
            usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        )

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        api_messages = []
        for msg in messages:
            if msg.role == "tool":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.call_id,
                    "content": msg.content,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                tc_list = []
                for tc in msg.tool_calls:
                    tc_list.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    })
                api_messages.append({
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": tc_list,
                })
            else:
                api_messages.append({"role": msg.role, "content": msg.content})
        return api_messages

    def _convert_tools(self, tools: list[ToolSchema]) -> list[dict[str, Any]]:
        result = []
        for tool in tools:
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", tool.get("parameters", {})),
                },
            })
        return result

    @property
    def context_window(self) -> int:
        return self._context_length

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model
```

**Step 4: Update factory to support "modal" provider type**

In `chimera/providers/factory.py`, add the `"modal"` case to `create_provider()` and `_infer_provider()`:

- In `create_provider()`, add after the `"compatible"` elif:
```python
    elif provider_type == "modal":
        from chimera.providers.modal import ModalProvider
        return ModalProvider(model=model, base_url=base_url, **kwargs)
```

- In `_infer_provider()`, this doesn't need auto-inference — Modal is always explicit.

**Step 5: Update `chimera/providers/__init__.py`** — no changes needed (factory handles it).

**Step 6: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_provider_modal.py -v`
Expected: All 6 tests PASS

**Step 7: Run full test suite to verify no regressions**

Run: `cd . && python -m pytest tests/ -v`
Expected: All 448+ tests PASS

**Step 8: Commit**

```bash
cd .
git add chimera/providers/modal.py chimera/providers/factory.py tests/test_provider_modal.py
git commit -m "feat: add ModalProvider for serverless GPU inference"
```

---

### Task 2: VerifyTool

**Files:**
- Create: `chimera/tools/verify.py`
- Modify: `chimera/tools/__init__.py`
- Test: `tests/test_tools_verify.py`

**Context:** The VerifyTool lets the agent run Python verification code to cross-check a candidate answer. It executes code via the environment's `run_command()` (same as BashTool) but with a focused interface: takes Python code that should print `True` or `False`. Follow the pattern from `chimera/tools/bash.py`.

**Step 1: Write the failing test**

Create `tests/test_tools_verify.py`:

```python
# tests/test_tools_verify.py
from unittest.mock import MagicMock

import pytest

from chimera.tools.verify import VerifyTool
from chimera.types import CommandResult


@pytest.fixture
def tool():
    return VerifyTool()


@pytest.fixture
def env():
    e = MagicMock()
    return e


def test_tool_name(tool):
    assert tool.name == "verify_answer"


def test_tool_has_parameters(tool):
    assert "code" in tool.parameters["properties"]
    assert "code" in tool.parameters["required"]


def test_verify_passing(tool, env):
    env.run_command.return_value = CommandResult(stdout="True\n", stderr="", exit_code=0)

    result = tool.execute({"code": "print(2 + 2 == 4)"}, env)
    assert result.error is None
    assert "True" in result.output
    assert result.metadata.get("verified") is True


def test_verify_failing(tool, env):
    env.run_command.return_value = CommandResult(stdout="False\n", stderr="", exit_code=0)

    result = tool.execute({"code": "print(2 + 2 == 5)"}, env)
    assert result.error is None
    assert "False" in result.output
    assert result.metadata.get("verified") is False


def test_verify_code_error(tool, env):
    env.run_command.return_value = CommandResult(
        stdout="", stderr="NameError: name 'x' is not defined", exit_code=1
    )

    result = tool.execute({"code": "print(x)"}, env)
    assert result.error is not None
    assert "NameError" in result.output


def test_verify_timeout(tool, env):
    result = tool.execute({"code": "import time; time.sleep(999)", "timeout": 5}, env)
    env.run_command.assert_called_once()
    # Verify timeout was passed to run_command
    call_kwargs = env.run_command.call_args
    assert call_kwargs[1].get("timeout") == 5 or call_kwargs[0][1] == 5


def test_to_openai_schema(tool):
    schema = tool.to_openai_schema()
    assert schema["function"]["name"] == "verify_answer"


def test_to_anthropic_schema(tool):
    schema = tool.to_anthropic_schema()
    assert schema["name"] == "verify_answer"
```

**Step 2: Run test to verify it fails**

Run: `cd . && python -m pytest tests/test_tools_verify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chimera.tools.verify'`

**Step 3: Write the implementation**

Create `chimera/tools/verify.py`:

```python
# chimera/tools/verify.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class VerifyTool(BaseTool):
    """Run Python verification code to cross-check a candidate answer.

    The agent writes Python code that prints True or False.
    The tool executes it and reports whether verification passed.
    """

    name = "verify_answer"
    description = (
        "Run Python verification code to cross-check a candidate answer. "
        "The code should print True if the answer is correct, False otherwise."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code that prints True or False to verify an answer",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 30)",
                "default": 30,
            },
        },
        "required": ["code"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        timeout = args.get("timeout", 30)
        code = args["code"]

        result = env.run_command(f'python3 -c {_shell_quote(code)}', timeout=timeout)

        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"

        if not result.success:
            return ToolResult(output=output, error=f"Exit code {result.exit_code}")

        verified = result.stdout.strip().split("\n")[-1].strip() == "True"
        return ToolResult(
            output=output,
            metadata={"verified": verified},
        )


def _shell_quote(s: str) -> str:
    """Quote a string for safe shell usage."""
    return "'" + s.replace("'", "'\"'\"'") + "'"
```

**Step 4: Update `chimera/tools/__init__.py`**

Add VerifyTool import and instance:

```python
from chimera.tools.verify import VerifyTool

verify = VerifyTool()
```

Add `"VerifyTool"` and `"verify"` to `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_tools_verify.py -v`
Expected: All 7 tests PASS

**Step 6: Run full test suite**

Run: `cd . && python -m pytest tests/ -v`
Expected: All tests PASS

**Step 7: Commit**

```bash
cd .
git add chimera/tools/verify.py chimera/tools/__init__.py tests/test_tools_verify.py
git commit -m "feat: add VerifyTool for answer cross-checking"
```

---

### Task 3: AIMOBenchmark

**Files:**
- Create: `chimera/eval/benchmarks/aimo.py`
- Modify: `chimera/eval/benchmarks/__init__.py`
- Test: `tests/test_bench_aimo.py`

**Context:** AIMOBenchmark implements the `Benchmark` ABC from `chimera/eval/harness.py`. It loads AIMO3 problems from a JSON file and evaluates agent output by extracting the last integer. Follow the pattern from `chimera/eval/benchmarks/swe_bench.py`. The `evaluate()` method extracts all integers from the agent's output and takes the last one, comparing it to the ground truth answer.

**Step 1: Write the failing test**

Create `tests/test_bench_aimo.py`:

```python
# tests/test_bench_aimo.py
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from chimera.eval.benchmarks.aimo import AIMOBenchmark, extract_answer


class TestExtractAnswer:
    def test_extracts_last_integer(self):
        assert extract_answer("The answer is 12345") == 12345

    def test_extracts_from_multiple_numbers(self):
        assert extract_answer("I computed 3 + 4 = 7, so the answer is 12345") == 12345

    def test_extracts_from_boxed_latex(self):
        assert extract_answer("\\boxed{42567}") == 42567

    def test_extracts_answer_tag(self):
        assert extract_answer("ANSWER: 99999") == 99999

    def test_returns_none_for_no_number(self):
        assert extract_answer("I don't know") is None

    def test_handles_negative_gracefully(self):
        # AIMO answers are positive integers, but we extract the absolute value
        assert extract_answer("The result is -12345") == 12345

    def test_handles_multiline(self):
        text = "Step 1: compute 100\nStep 2: multiply by 3\nFinal answer: 54321"
        assert extract_answer(text) == 54321


class TestAIMOBenchmark:
    @pytest.fixture
    def problems_file(self, tmp_path):
        problems = [
            {"id": "p1", "problem": "Find x where x^2 = 144", "answer": 12},
            {"id": "p2", "problem": "What is 7! ?", "answer": 5040},
            {"id": "p3", "problem": "Compute gcd(48, 36)", "answer": 12},
        ]
        path = tmp_path / "problems.json"
        path.write_text(json.dumps(problems))
        return str(path)

    def test_name(self, problems_file):
        bench = AIMOBenchmark(problems_path=problems_file)
        assert bench.name() == "aimo3"

    def test_loads_tasks(self, problems_file):
        bench = AIMOBenchmark(problems_path=problems_file)
        tasks = bench.tasks()
        assert len(tasks) == 3
        assert tasks[0]["id"] == "p1"
        assert "prompt" in tasks[0]
        assert tasks[0]["answer"] == 12

    def test_evaluate_correct(self, problems_file):
        bench = AIMOBenchmark(problems_path=problems_file)
        task = bench.tasks()[1]  # answer is 5040
        assert bench.evaluate(task, "The factorial of 7 is 5040", None) is True

    def test_evaluate_incorrect(self, problems_file):
        bench = AIMOBenchmark(problems_path=problems_file)
        task = bench.tasks()[1]  # answer is 5040
        assert bench.evaluate(task, "The answer is 720", None) is False

    def test_evaluate_no_answer(self, problems_file):
        bench = AIMOBenchmark(problems_path=problems_file)
        task = bench.tasks()[0]
        assert bench.evaluate(task, "I cannot solve this", None) is False

    def test_limit(self, problems_file):
        bench = AIMOBenchmark(problems_path=problems_file, limit=2)
        assert len(bench.tasks()) == 2

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("[]")
        bench = AIMOBenchmark(problems_path=str(path))
        assert bench.tasks() == []
```

**Step 2: Run test to verify it fails**

Run: `cd . && python -m pytest tests/test_bench_aimo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chimera.eval.benchmarks.aimo'`

**Step 3: Write the implementation**

Create `chimera/eval/benchmarks/aimo.py`:

```python
# chimera/eval/benchmarks/aimo.py
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


SYSTEM_PROMPT = """\
You are a mathematical problem solver. Given an olympiad-level math problem:

1. Reason step-by-step about the approach
2. Write Python code to compute the answer (you may use sympy, numpy, scipy, itertools)
3. Execute the code using the bash tool
4. Verify your answer if possible using the verify_answer tool
5. State your final answer as a single integer on the last line

Your final answer MUST be a non-negative integer. State it clearly as: ANSWER: <number>"""


def extract_answer(text: str) -> int | None:
    """Extract the answer integer from agent output.

    Looks for (in priority order):
    1. "ANSWER: <number>" pattern
    2. \\boxed{<number>} LaTeX pattern
    3. Last integer in the text
    """
    # Try ANSWER: pattern
    match = re.search(r"ANSWER:\s*(-?\d+)", text)
    if match:
        return abs(int(match.group(1)))

    # Try \\boxed{} pattern
    match = re.search(r"\\boxed\{(-?\d+)\}", text)
    if match:
        return abs(int(match.group(1)))

    # Fall back to last integer in text
    integers = re.findall(r"\d+", text)
    if integers:
        return abs(int(integers[-1]))

    return None


class AIMOBenchmark(Benchmark):
    """AIMO Progress Prize 3 benchmark.

    Loads olympiad-level math problems and evaluates by comparing
    the agent's extracted integer answer to the ground truth.
    """

    def __init__(
        self,
        problems_path: str | None = None,
        limit: int | None = None,
    ) -> None:
        self._problems_path = problems_path
        self._limit = limit
        self._tasks: list[dict] | None = None

    def name(self) -> str:
        return "aimo3"

    def tasks(self) -> list[dict[str, Any]]:
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict, agent_output: str, env: Any) -> bool:
        expected = task.get("answer")
        if expected is None:
            return False
        extracted = extract_answer(agent_output)
        if extracted is None:
            return False
        return extracted == expected

    def _load_tasks(self) -> list[dict]:
        if self._problems_path:
            data = json.loads(Path(self._problems_path).read_text())
            problems = data if isinstance(data, list) else data.get("problems", [])
        else:
            problems = []

        tasks = []
        for p in problems:
            tasks.append({
                "id": p["id"],
                "prompt": self._format_prompt(p["problem"]),
                "answer": p["answer"],
            })

        if self._limit:
            tasks = tasks[: self._limit]
        return tasks

    def _format_prompt(self, problem_text: str) -> str:
        return f"Solve the following math problem. {SYSTEM_PROMPT}\n\nPROBLEM:\n{problem_text}"
```

**Step 4: Update `chimera/eval/benchmarks/__init__.py`**

Add:
```python
from chimera.eval.benchmarks.aimo import AIMOBenchmark
```
Add `"AIMOBenchmark"` to `__all__`.

**Step 5: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_bench_aimo.py -v`
Expected: All 12 tests PASS

**Step 6: Run full test suite**

Run: `cd . && python -m pytest tests/ -v`
Expected: All tests PASS

**Step 7: Commit**

```bash
cd .
git add chimera/eval/benchmarks/aimo.py chimera/eval/benchmarks/__init__.py tests/test_bench_aimo.py
git commit -m "feat: add AIMOBenchmark for AIMO Progress Prize 3"
```

---

### Task 4: MajorityVoting Strategy

**Files:**
- Create: `chimera/training/strategies/majority_voting.py`
- Modify: `chimera/training/strategies/__init__.py`
- Modify: `chimera/__init__.py`
- Test: `tests/test_strategy_majority_voting.py`

**Context:** MajorityVoting samples N solutions per problem, extracts integer answers, and returns the most common one. It implements the `Strategy` ABC from `chimera/training/strategies/base.py`. Unlike `TestConvergence` which iterates until tests pass, MajorityVoting runs a fixed number of samples and picks the consensus. It reuses `extract_answer()` from `chimera/eval/benchmarks/aimo.py`.

The strategy needs to work with the existing `Spec`, `Agent`, `Environment` interfaces. It treats the `Spec.to_prompt()` as the problem text, runs the agent N times, and reports convergence if a consensus answer is found.

**Step 1: Write the failing test**

Create `tests/test_strategy_majority_voting.py`:

```python
# tests/test_strategy_majority_voting.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.training.strategies.majority_voting import MajorityVoting


@dataclass
class FakeAgentResult:
    output: str
    steps: int = 3
    tool_calls_total: int = 2
    cost: float = 0.01
    success: bool = True
    error: str | None = None


class FakeAgent:
    """Agent that returns pre-configured responses in order."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._idx = 0

    def run(self, task: str, env: Any) -> FakeAgentResult:
        output = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return FakeAgentResult(output=output)


class FakeSpec:
    def to_prompt(self) -> str:
        return "What is 2+2?"


class FakeEnv:
    def checkpoint(self) -> str:
        return "cp0"

    def restore(self, checkpoint_id: str) -> None:
        pass

    def run_tests(self):
        return MagicMock(pass_rate=1.0, passed=1, total=1, all_passed=True)


class TestMajorityVoting:
    def test_clear_consensus(self):
        """When most samples agree, converges with that answer."""
        agent = FakeAgent([
            "ANSWER: 42", "ANSWER: 42", "ANSWER: 42",
            "ANSWER: 99", "ANSWER: 42",
        ])
        strategy = MajorityVoting(n_samples=5, temperature=0.7)
        result = strategy.run(agent, FakeSpec(), FakeEnv())

        assert result.converged is True
        assert result.best_pass_rate == 1.0
        # The winning answer should be in the history metadata
        assert any("42" in str(h.agent_output) for h in result.history)

    def test_no_consensus(self):
        """When no answer reaches min_agreement, does not converge."""
        agent = FakeAgent([
            "ANSWER: 1", "ANSWER: 2", "ANSWER: 3",
            "ANSWER: 4", "ANSWER: 5",
        ])
        strategy = MajorityVoting(n_samples=5, min_agreement=2)
        result = strategy.run(agent, FakeSpec(), FakeEnv())

        assert result.converged is False

    def test_early_stopping(self):
        """Stops early once majority is reached."""
        call_count = 0
        original_responses = ["ANSWER: 42"] * 16

        class CountingAgent(FakeAgent):
            def run(self, task, env):
                nonlocal call_count
                call_count += 1
                return super().run(task, env)

        agent = CountingAgent(original_responses)
        # With 16 samples but early stopping, should stop before all 16
        strategy = MajorityVoting(n_samples=16, min_agreement=3)
        result = strategy.run(agent, FakeSpec(), FakeEnv())

        assert result.converged is True
        # Should have stopped early (well before 16)
        assert call_count < 16

    def test_tracks_cost(self):
        agent = FakeAgent(["ANSWER: 42"] * 4)
        strategy = MajorityVoting(n_samples=4)
        result = strategy.run(agent, FakeSpec(), FakeEnv())

        assert result.total_cost == pytest.approx(0.04)

    def test_callbacks_called(self):
        agent = FakeAgent(["ANSWER: 42"] * 3)
        strategy = MajorityVoting(n_samples=3)

        cb = MagicMock()
        cb.on_epoch_end.return_value = True
        result = strategy.run(agent, FakeSpec(), FakeEnv(), callbacks=[cb])

        assert cb.on_synthesis_start.called
        assert cb.on_synthesis_end.called
        assert cb.on_epoch_end.call_count == 3

    def test_callback_can_stop_early(self):
        agent = FakeAgent(["ANSWER: 42"] * 10)
        strategy = MajorityVoting(n_samples=10)

        cb = MagicMock()
        cb.on_epoch_end.return_value = False  # Stop immediately
        result = strategy.run(agent, FakeSpec(), FakeEnv(), callbacks=[cb])

        assert result.iterations == 1

    def test_no_extractable_answer(self):
        agent = FakeAgent(["I don't know"] * 4)
        strategy = MajorityVoting(n_samples=4, min_agreement=2)
        result = strategy.run(agent, FakeSpec(), FakeEnv())

        assert result.converged is False

    def test_winning_answer_in_result(self):
        """The winning answer is stored in SynthesisResult for downstream use."""
        agent = FakeAgent(["ANSWER: 12345"] * 5)
        strategy = MajorityVoting(n_samples=5)
        result = strategy.run(agent, FakeSpec(), FakeEnv())

        assert result.converged is True
        # The last history entry's agent_output contains the winning answer
        assert "12345" in result.history[-1].agent_output
```

**Step 2: Run test to verify it fails**

Run: `cd . && python -m pytest tests/test_strategy_majority_voting.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write the implementation**

Create `chimera/training/strategies/majority_voting.py`:

```python
# chimera/training/strategies/majority_voting.py
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from chimera.eval.benchmarks.aimo import extract_answer
from chimera.training.strategies.base import (
    Callback,
    EpochResult,
    Strategy,
    SynthesisResult,
)

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.training.constraint import Constraint
    from chimera.training.spec import Spec


class MajorityVoting(Strategy):
    """Sample N solutions and pick the consensus answer.

    Each epoch: run the agent once, extract an integer answer from output.
    After all samples (or early stopping), return the most common answer.
    Converges if the top answer has >= min_agreement votes.
    """

    def __init__(
        self,
        n_samples: int = 16,
        temperature: float = 0.7,
        min_agreement: int = 2,
    ) -> None:
        self.n_samples = n_samples
        self.temperature = temperature
        self.min_agreement = min_agreement

    def run(
        self,
        agent: Agent,
        spec: Spec,
        env: Environment,
        constraints: list[Constraint] | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult:
        callbacks = callbacks or []
        for cb in callbacks:
            cb.on_synthesis_start()

        task = spec.to_prompt()
        history: list[EpochResult] = []
        votes: Counter[int] = Counter()
        total_cost = 0.0

        for sample_num in range(1, self.n_samples + 1):
            for cb in callbacks:
                cb.on_epoch_start(sample_num)

            agent_result = agent.run(task, env)
            total_cost += agent_result.cost

            answer = extract_answer(agent_result.output)
            if answer is not None:
                votes[answer] += 1

            epoch = EpochResult(
                epoch=sample_num,
                pass_rate=1.0 if answer is not None else 0.0,
                passed=1 if answer is not None else 0,
                total=1,
                agent_output=agent_result.output,
                improved=answer is not None,
                cost=agent_result.cost,
            )
            history.append(epoch)

            # Callback check
            should_continue = True
            for cb in callbacks:
                ret = cb.on_epoch_end(sample_num, epoch)
                if ret is False:
                    should_continue = False

            if not should_continue:
                break

            # Early stopping: check if top answer has clear majority
            if votes:
                top_answer, top_count = votes.most_common(1)[0]
                remaining = self.n_samples - sample_num
                # Stop if top answer already has enough votes AND
                # no other answer can overtake it
                if top_count >= self.min_agreement:
                    second_count = votes.most_common(2)[-1][1] if len(votes) > 1 else 0
                    if top_count > second_count + remaining:
                        break

        # Determine winner
        converged = False
        winning_answer = None
        if votes:
            top_answer, top_count = votes.most_common(1)[0]
            if top_count >= self.min_agreement:
                converged = True
                winning_answer = top_answer

        # Add summary epoch with winning answer
        if winning_answer is not None:
            history.append(EpochResult(
                epoch=len(history) + 1,
                pass_rate=1.0,
                passed=1,
                total=1,
                agent_output=f"ANSWER: {winning_answer}",
                improved=True,
                cost=0.0,
            ))

        result = SynthesisResult(
            converged=converged,
            iterations=len(history) - (1 if winning_answer is not None else 0),
            total_cost=total_cost,
            best_pass_rate=1.0 if converged else 0.0,
            history=history,
            failure_reason=None if converged else "No consensus reached",
        )
        for cb in callbacks:
            cb.on_synthesis_end(result)
        return result
```

**Step 4: Update `chimera/training/strategies/__init__.py`**

Add:
```python
from chimera.training.strategies.majority_voting import MajorityVoting
```
Add `"MajorityVoting"` to `__all__`.

**Step 5: Update `chimera/__init__.py`**

Add `MajorityVoting` to the strategies import block and `__all__`.

**Step 6: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_strategy_majority_voting.py -v`
Expected: All 8 tests PASS

**Step 7: Run full test suite**

Run: `cd . && python -m pytest tests/ -v`
Expected: All tests PASS

**Step 8: Commit**

```bash
cd .
git add chimera/training/strategies/majority_voting.py chimera/training/strategies/__init__.py chimera/__init__.py tests/test_strategy_majority_voting.py
git commit -m "feat: add MajorityVoting strategy for pass@N consensus"
```

---

### Task 5: AIMOEnsemble Strategy

**Files:**
- Create: `chimera/training/strategies/aimo_ensemble.py`
- Modify: `chimera/training/strategies/__init__.py`
- Modify: `chimera/__init__.py`
- Test: `tests/test_strategy_aimo_ensemble.py`

**Context:** AIMOEnsemble composes MajorityVoting and TreeSearch. It runs MajorityVoting first (fast). If no consensus, falls back to TreeSearch for deeper exploration. This uses Chimera's existing `TreeSearch` from `chimera/training/strategies/tree_search.py`.

**Step 1: Write the failing test**

Create `tests/test_strategy_aimo_ensemble.py`:

```python
# tests/test_strategy_aimo_ensemble.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.training.strategies.aimo_ensemble import AIMOEnsemble
from chimera.training.strategies.base import SynthesisResult, EpochResult


@dataclass
class FakeAgentResult:
    output: str
    steps: int = 3
    tool_calls_total: int = 2
    cost: float = 0.01
    success: bool = True
    error: str | None = None


class FakeSpec:
    def to_prompt(self) -> str:
        return "Solve this problem"


class FakeEnv:
    def checkpoint(self) -> str:
        return "cp0"

    def restore(self, checkpoint_id: str) -> None:
        pass

    def run_tests(self):
        return MagicMock(pass_rate=1.0, passed=1, total=1, all_passed=True)


class TestAIMOEnsemble:
    def test_returns_voting_result_when_converged(self):
        """If MajorityVoting converges, returns immediately without TreeSearch."""

        class ConvergingAgent:
            _idx = 0
            def run(self, task, env):
                self._idx += 1
                return FakeAgentResult(output="ANSWER: 42")

        strategy = AIMOEnsemble(voting_samples=4, min_agreement=2)
        result = strategy.run(ConvergingAgent(), FakeSpec(), FakeEnv())

        assert result.converged is True

    def test_falls_back_to_tree_search(self):
        """If MajorityVoting fails, TreeSearch is attempted."""

        class DivergingAgent:
            _idx = 0
            def run(self, task, env):
                self._idx += 1
                return FakeAgentResult(output=f"ANSWER: {self._idx}")

        strategy = AIMOEnsemble(voting_samples=4, min_agreement=3)

        # Patch TreeSearch.run to return a converged result
        with patch(
            "chimera.training.strategies.aimo_ensemble.TreeSearch.run"
        ) as mock_tree:
            mock_tree.return_value = SynthesisResult(
                converged=True, iterations=5, total_cost=0.1,
                best_pass_rate=1.0, history=[],
            )
            result = strategy.run(DivergingAgent(), FakeSpec(), FakeEnv())

        assert result.converged is True
        assert mock_tree.called

    def test_tracks_total_cost(self):
        class ConvergingAgent:
            def run(self, task, env):
                return FakeAgentResult(output="ANSWER: 42", cost=0.05)

        strategy = AIMOEnsemble(voting_samples=3, min_agreement=2)
        result = strategy.run(ConvergingAgent(), FakeSpec(), FakeEnv())

        assert result.total_cost > 0
```

**Step 2: Run test to verify it fails**

Run: `cd . && python -m pytest tests/test_strategy_aimo_ensemble.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write the implementation**

Create `chimera/training/strategies/aimo_ensemble.py`:

```python
# chimera/training/strategies/aimo_ensemble.py
from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.training.strategies.base import (
    Callback,
    Strategy,
    SynthesisResult,
)
from chimera.training.strategies.majority_voting import MajorityVoting
from chimera.training.strategies.tree_search import TreeSearch

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.training.constraint import Constraint
    from chimera.training.spec import Spec


class AIMOEnsemble(Strategy):
    """Two-phase strategy: MajorityVoting first, TreeSearch fallback.

    Phase 1: Run MajorityVoting for fast consensus.
    Phase 2: If no consensus, fall back to TreeSearch for deeper exploration.
    """

    def __init__(
        self,
        voting_samples: int = 8,
        min_agreement: int = 2,
        temperature: float = 0.7,
        tree_branch_factor: int = 3,
        tree_max_depth: int = 5,
        tree_max_nodes: int = 10,
    ) -> None:
        self.voting_samples = voting_samples
        self.min_agreement = min_agreement
        self.temperature = temperature
        self.tree_branch_factor = tree_branch_factor
        self.tree_max_depth = tree_max_depth
        self.tree_max_nodes = tree_max_nodes

    def run(
        self,
        agent: Agent,
        spec: Spec,
        env: Environment,
        constraints: list[Constraint] | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult:
        # Phase 1: MajorityVoting
        voting = MajorityVoting(
            n_samples=self.voting_samples,
            temperature=self.temperature,
            min_agreement=self.min_agreement,
        )
        result = voting.run(agent, spec, env, constraints, callbacks)
        if result.converged:
            return result

        # Phase 2: TreeSearch fallback
        tree = TreeSearch(
            branch_factor=self.tree_branch_factor,
            max_depth=self.tree_max_depth,
            max_nodes=self.tree_max_nodes,
        )
        tree_result = tree.run(agent, spec, env, constraints, callbacks)

        # Combine costs
        return SynthesisResult(
            converged=tree_result.converged,
            iterations=result.iterations + tree_result.iterations,
            total_cost=result.total_cost + tree_result.total_cost,
            best_pass_rate=tree_result.best_pass_rate,
            history=result.history + tree_result.history,
            failure_reason=tree_result.failure_reason if not tree_result.converged else None,
        )
```

**Step 4: Update `chimera/training/strategies/__init__.py`**

Add:
```python
from chimera.training.strategies.aimo_ensemble import AIMOEnsemble
```
Add `"AIMOEnsemble"` to `__all__`.

**Step 5: Update `chimera/__init__.py`**

Add `AIMOEnsemble` to the strategies import block and `__all__`.

**Step 6: Run tests**

Run: `cd . && python -m pytest tests/test_strategy_aimo_ensemble.py -v`
Expected: All 3 tests PASS

**Step 7: Run full test suite**

Run: `cd . && python -m pytest tests/ -v`
Expected: All tests PASS

**Step 8: Commit**

```bash
cd .
git add chimera/training/strategies/aimo_ensemble.py chimera/training/strategies/__init__.py chimera/__init__.py tests/test_strategy_aimo_ensemble.py
git commit -m "feat: add AIMOEnsemble strategy (voting + tree search fallback)"
```

---

### Task 6: Wire Exports and Integration Test

**Files:**
- Modify: `chimera/__init__.py` (verify all exports)
- Modify: `chimera/eval/benchmarks/__init__.py` (verify exports)
- Create: `tests/test_aimo_integration.py`

**Context:** Verify everything is wired together and works end-to-end with mock components. This is the integration test that proves a full AIMO evaluation loop works: AIMOBenchmark → Harness → Agent (with MajorityVoting) → VerifyTool → result.

**Step 1: Write the integration test**

Create `tests/test_aimo_integration.py`:

```python
# tests/test_aimo_integration.py
"""End-to-end integration test for AIMO3 pipeline."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.eval.benchmarks.aimo import AIMOBenchmark
from chimera.eval.harness import Harness


@dataclass
class FakeAgentResult:
    output: str
    steps: int = 3
    tool_calls_total: int = 2
    cost: float = 0.01
    success: bool = True
    error: str | None = None


class FakeAgent:
    """Agent that 'solves' problems by returning the correct answer."""

    def __init__(self, answers: dict[str, int]):
        self._answers = answers

    def run(self, task: str, env: Any) -> FakeAgentResult:
        # Find the problem ID by checking which answer matches the task
        for problem_text, answer in self._answers.items():
            if problem_text in task:
                return FakeAgentResult(output=f"After solving, ANSWER: {answer}")
        return FakeAgentResult(output="I cannot solve this problem")


class TestAIMOIntegration:
    @pytest.fixture
    def problems_file(self, tmp_path):
        problems = [
            {"id": "p1", "problem": "What is 2^10?", "answer": 1024},
            {"id": "p2", "problem": "What is 13!?", "answer": 6227020800},
            {"id": "p3", "problem": "What is gcd(100, 75)?", "answer": 25},
        ]
        path = tmp_path / "problems.json"
        path.write_text(json.dumps(problems))
        return str(path)

    def test_full_pipeline(self, problems_file):
        """Full pipeline: load problems → run agent → evaluate answers."""
        benchmark = AIMOBenchmark(problems_path=problems_file)
        agent = FakeAgent({
            "What is 2^10?": 1024,
            "What is 13!?": 6227020800,
            "What is gcd(100, 75)?": 25,
        })

        harness = Harness(benchmark=benchmark, agent=agent)
        result = harness.run()

        assert result.benchmark == "aimo3"
        assert result.total == 3
        assert result.passed == 3
        assert result.pass_rate == 1.0

    def test_partial_solve(self, problems_file):
        """Agent solves some but not all problems."""
        benchmark = AIMOBenchmark(problems_path=problems_file)
        agent = FakeAgent({
            "What is 2^10?": 1024,
            # Wrong answers for p2 and p3
            "What is 13!?": 999,
            "What is gcd(100, 75)?": 50,
        })

        harness = Harness(benchmark=benchmark, agent=agent)
        result = harness.run()

        assert result.total == 3
        assert result.passed == 1
        assert result.pass_rate == pytest.approx(1 / 3)

    def test_imports_from_top_level(self):
        """Verify all new components are importable from chimera package."""
        from chimera import MajorityVoting, AIMOEnsemble
        from chimera.eval.benchmarks import AIMOBenchmark
        from chimera.tools import VerifyTool, verify

        assert MajorityVoting is not None
        assert AIMOEnsemble is not None
        assert AIMOBenchmark is not None
        assert VerifyTool is not None
        assert verify is not None
```

**Step 2: Run integration test**

Run: `cd . && python -m pytest tests/test_aimo_integration.py -v`
Expected: All 3 tests PASS

**Step 3: Run full test suite**

Run: `cd . && python -m pytest tests/ -v`
Expected: All tests PASS

**Step 4: Commit**

```bash
cd .
git add tests/test_aimo_integration.py
git commit -m "test: add AIMO3 end-to-end integration test"
```

---

### Task 7: Kaggle Notebook Template

**Files:**
- Create: `chimera/notebooks/aimo3/notebook.py`
- Create: `chimera/notebooks/aimo3/README.md`

**Context:** This is the template for the actual Kaggle submission notebook. It's a Python script (not .ipynb) that can be converted to a notebook. It documents the full setup: starting vLLM, configuring Chimera, running the benchmark, and writing the submission file. This is documentation-as-code — it should work locally with a running vLLM instance and serve as the template for the Kaggle notebook.

**Step 1: Create the notebook template**

Create `chimera/notebooks/aimo3/notebook.py`:

```python
#!/usr/bin/env python3
"""AIMO3 Kaggle Submission Template.

This script is the template for the Kaggle notebook submission.
It demonstrates the full pipeline: vLLM setup → Chimera agent → solve → submit.

Local usage:
    1. Start vLLM: python -m vllm.entrypoints.openai.api_server \
         --model Qwen/Qwen3-235B-AWQ --port 8000
    2. Run: python chimera/notebooks/aimo3/notebook.py \
         --problems path/to/problems.json --output submission.csv

Kaggle usage:
    Convert to notebook cells and adjust paths for Kaggle environment.
"""
from __future__ import annotations

import argparse
import csv
import sys

import chimera
from chimera.eval.benchmarks.aimo import AIMOBenchmark
from chimera.tools.verify import VerifyTool


def main(
    problems_path: str,
    output_path: str = "submission.csv",
    model: str = "Qwen/Qwen3-235B-AWQ",
    base_url: str = "http://localhost:8000",
    n_samples: int = 8,
) -> None:
    # --- Provider ---
    provider = chimera.create_provider(
        provider_type="compatible",
        model=model,
        base_url=base_url,
    )

    # --- Agent ---
    agent = chimera.Agent(
        provider=provider,
        tools=[chimera.tools.bash, chimera.tools.read_file, chimera.tools.write_file, VerifyTool()],
        loop=chimera.ReAct(max_steps=30),
    )

    # --- Benchmark ---
    benchmark = AIMOBenchmark(problems_path=problems_path)

    # --- Solve ---
    harness = chimera.Harness(benchmark=benchmark, agent=agent)
    result = harness.run()

    # --- Write submission ---
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "answer"])
        for task_result in result.results:
            from chimera.eval.benchmarks.aimo import extract_answer
            answer = extract_answer(task_result.output)
            writer.writerow([task_result.task_id, answer if answer is not None else 0])

    print(f"Results: {result.passed}/{result.total} ({result.pass_rate:.1%})")
    print(f"Total cost: ${result.total_cost:.4f}")
    print(f"Submission written to: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AIMO3 solver")
    parser.add_argument("--problems", required=True, help="Path to problems JSON")
    parser.add_argument("--output", default="submission.csv", help="Output CSV path")
    parser.add_argument("--model", default="Qwen/Qwen3-235B-AWQ")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--samples", type=int, default=8)
    args = parser.parse_args()
    main(args.problems, args.output, args.model, args.base_url, args.samples)
```

**Step 2: Create README**

Create `chimera/notebooks/aimo3/README.md`:

```markdown
# AIMO3 Kaggle Submission

## Quick Start

### Local Development

1. Start vLLM with a math-capable model:
   ```bash
   python -m vllm.entrypoints.openai.api_server \
     --model Qwen/Qwen3-235B-AWQ --port 8000
   ```

2. Run the solver:
   ```bash
   python chimera/notebooks/aimo3/notebook.py \
     --problems path/to/problems.json \
     --output submission.csv
   ```

### Using Modal (remote GPU)

```python
import chimera

provider = chimera.create_provider(
    provider_type="modal",
    model="Qwen/Qwen3-235B-AWQ",
    base_url="https://your-modal-app.modal.run",
)
```

### Using HuggingFace Inference API

```python
import chimera

provider = chimera.create_provider(
    provider_type="compatible",
    model="Qwen/Qwen3-235B",
    base_url="https://api-inference.huggingface.co/v1",
    api_key="hf_...",
)
```

## Kaggle Notebook Setup

1. Upload model weights as a Kaggle dataset
2. Copy `notebook.py` into a Kaggle notebook
3. Install chimera: `pip install -e /kaggle/input/chimera/`
4. Start vLLM pointing to the uploaded weights
5. Run the solver

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--model` | Qwen/Qwen3-235B-AWQ | Model name for vLLM |
| `--base-url` | http://localhost:8000 | vLLM server URL |
| `--samples` | 8 | Solutions per problem (majority voting) |
```

**Step 3: Commit**

```bash
cd .
git add chimera/notebooks/aimo3/notebook.py chimera/notebooks/aimo3/README.md
git commit -m "feat: add AIMO3 Kaggle notebook template"
```

---

## Summary

| Task | Component | New Files | Tests |
|------|-----------|-----------|-------|
| 1 | ModalProvider | `providers/modal.py` | 6 tests |
| 2 | VerifyTool | `tools/verify.py` | 7 tests |
| 3 | AIMOBenchmark | `eval/benchmarks/aimo.py` | 12 tests |
| 4 | MajorityVoting | `strategies/majority_voting.py` | 8 tests |
| 5 | AIMOEnsemble | `strategies/aimo_ensemble.py` | 3 tests |
| 6 | Integration | — | 3 tests |
| 7 | Kaggle notebook | `notebooks/aimo3/` | — |

**Total: 7 tasks, ~39 new tests, 7 commits.**

After completing these tasks, the next steps would be:
1. Enroll in the AIMO3 competition on Kaggle
2. Test with a real open-weight model (via Modal or local GPU)
3. Iterate on the system prompt for better math reasoning
4. Tune n_samples, temperature, and model choice
5. Submit to Kaggle and evaluate on the public leaderboard
