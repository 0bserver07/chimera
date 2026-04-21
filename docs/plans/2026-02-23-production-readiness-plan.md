# Production Readiness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Chimera work end-to-end: real cost tracking from provider responses, the `chimera.synthesize()` one-liner, working CLI, and a repository mapping tool.

**Architecture:** Four features, bottom-up: cost tracking feeds into loops, loops feed into synthesize(), synthesize() feeds into CLI, repo map is an independent tool addition. Tree search strategy covered by existing plan at `docs/plans/2026-02-22-tree-search-plan.md`.

**Tech Stack:** Python 3.11+, `ast` (stdlib) for repo mapping, no new dependencies.

---

## Phase 15: Cost Tracking (Tasks 65–68)

### Task 65: Cost calculation utility

**Files:**
- Create: `chimera/providers/cost.py`
- Test: `tests/test_cost.py`

**Step 1: Write the failing tests**

```python
# tests/test_cost.py
"""Tests for provider cost calculation."""
from __future__ import annotations

from chimera.providers.cost import calculate_cost, PRICING


class TestCalculateCost:
    def test_anthropic_sonnet(self):
        cost = calculate_cost("claude-sonnet-4-20250514", {
            "input_tokens": 1000,
            "output_tokens": 500,
        })
        # sonnet: $3/M input, $15/M output
        expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_anthropic_opus(self):
        cost = calculate_cost("claude-opus-4-20250514", {
            "input_tokens": 1000,
            "output_tokens": 500,
        })
        expected = (1000 * 15.0 + 500 * 75.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_anthropic_haiku(self):
        cost = calculate_cost("claude-haiku-3.5-20241022", {
            "input_tokens": 10000,
            "output_tokens": 2000,
        })
        expected = (10000 * 0.80 + 2000 * 4.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_openai_gpt4o(self):
        cost = calculate_cost("gpt-4o", {
            "input_tokens": 5000,
            "output_tokens": 1000,
        })
        expected = (5000 * 2.50 + 1000 * 10.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_openai_gpt4o_mini(self):
        cost = calculate_cost("gpt-4o-mini", {
            "input_tokens": 5000,
            "output_tokens": 1000,
        })
        expected = (5000 * 0.15 + 1000 * 0.60) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_unknown_model_returns_zero(self):
        cost = calculate_cost("unknown-model-v1", {
            "input_tokens": 1000,
            "output_tokens": 500,
        })
        assert cost == 0.0

    def test_empty_usage(self):
        cost = calculate_cost("claude-sonnet-4-20250514", {})
        assert cost == 0.0

    def test_ollama_returns_zero(self):
        cost = calculate_cost("llama3.1:8b", {
            "input_tokens": 10000,
            "output_tokens": 5000,
        })
        assert cost == 0.0

    def test_pricing_dict_exists(self):
        assert isinstance(PRICING, dict)
        assert len(PRICING) > 0
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_cost.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chimera.providers.cost'`

**Step 3: Write minimal implementation**

```python
# chimera/providers/cost.py
"""Token cost calculation for LLM providers."""
from __future__ import annotations

# Pricing: model_prefix -> (input_cost_per_million, output_cost_per_million)
PRICING: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-3.5": (0.80, 4.0),
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "o1": (15.0, 60.0),
    "o3-mini": (1.10, 4.40),
    # Google
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
}


def calculate_cost(model: str, usage: dict[str, int]) -> float:
    """Calculate the dollar cost of an API call.

    Args:
        model: Model identifier (e.g. "claude-sonnet-4-20250514").
        usage: Dict with "input_tokens" and "output_tokens" keys.

    Returns:
        Cost in USD. Returns 0.0 for unknown models.
    """
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    # Match longest prefix first (gpt-4o-mini before gpt-4o)
    for prefix in sorted(PRICING, key=len, reverse=True):
        if model.startswith(prefix):
            input_price, output_price = PRICING[prefix]
            return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
    return 0.0
```

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_cost.py -v`
Expected: 9 passed

**Step 5: Commit**

```bash
cd . && git add chimera/providers/cost.py tests/test_cost.py && git commit -m "feat: add cost calculation utility with provider pricing table"
```

---

### Task 66: Cost aggregation in ReAct loop

**Files:**
- Modify: `chimera/core/loop.py`
- Modify: `tests/test_loop.py`

**Step 1: Write the failing test**

Append to `tests/test_loop.py`:

```python
def test_react_tracks_cost():
    """ReAct loop should sum costs from provider usage across all steps."""
    from chimera.providers.base import Response
    from chimera.types import ToolCall

    class CostTrackingProvider:
        def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
            if messages and messages[-1].role == "tool":
                return Response(
                    content="Done.",
                    tool_calls=[],
                    usage={"input_tokens": 200, "output_tokens": 50},
                )
            return Response(
                content="Writing.",
                tool_calls=[ToolCall(id="c1", name="write_file", arguments={"path": "f.py", "content": "x=1"})],
                usage={"input_tokens": 500, "output_tokens": 100},
            )

        @property
        def context_window(self):
            return 200_000

        @property
        def supports_tool_use(self):
            return True

        @property
        def model_name(self):
            return "claude-sonnet-4-20250514"

    from chimera.core.loop import ReAct
    from chimera.core.context import Context
    from chimera.tools.write import WriteFileTool
    from chimera.env.local import LocalEnvironment
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment(workdir=tmpdir)
        env.setup()
        ctx = Context(system="test")
        from chimera.types import Message
        ctx.add(Message.user("write something"))
        result = ReAct(max_steps=5).run(
            CostTrackingProvider(), [WriteFileTool()], ctx, env,
        )
        # 2 complete() calls: first returns tool call (500in, 100out), second returns done (200in, 50out)
        # sonnet: $3/M input, $15/M output
        # call1: (500*3 + 100*15) / 1M = (1500 + 1500) / 1M = 0.000003
        # call2: (200*3 + 50*15) / 1M  = (600 + 750)  / 1M = 0.00000135
        assert result.cost > 0
        assert result.success is True
```

**Step 2: Run test to verify it fails**

Run: `cd . && python -m pytest tests/test_loop.py::test_react_tracks_cost -v`
Expected: FAIL — `assert 0.0 > 0`

**Step 3: Modify implementation**

In `chimera/core/loop.py`, add the import and cost accumulation:

Add at top of file:
```python
from chimera.providers.cost import calculate_cost
```

Replace both `cost=0.0` in `AgentResult` returns with `cost=total_cost`, and add `total_cost = 0.0` at the top of the loop and `total_cost += calculate_cost(provider.model_name, response.usage)` after each `provider.complete()` call.

The full updated `run` method:

```python
def run(
    self,
    provider: Provider,
    tools: list[BaseTool],
    context: Context,
    env: Environment | None,
) -> AgentResult:
    tool_map = {t.name: t for t in tools}
    schemas = [t.to_anthropic_schema() for t in tools]
    steps = 0
    total_tool_calls = 0
    total_cost = 0.0

    for _ in range(self.max_steps):
        steps += 1
        response = provider.complete(context.to_messages(), tools=schemas if schemas else None)
        total_cost += calculate_cost(provider.model_name, response.usage)
        context.add(Message.assistant(response.content, tool_calls=response.tool_calls))

        if not response.has_tool_calls:
            return AgentResult(
                output=response.content,
                steps=steps,
                tool_calls_total=total_tool_calls,
                cost=total_cost,
                success=True,
            )

        for tc in response.tool_calls:
            total_tool_calls += 1
            tool = tool_map.get(tc.name)
            if tool is None:
                context.add(Message.tool(tc.id, f"Error: unknown tool {tc.name}"))
                continue
            result = tool.execute(tc.arguments, env)
            if result.success:
                content = result.output
            else:
                content = f"Error: {result.error}\n{result.output}"
            context.add(Message.tool(tc.id, content))

    return AgentResult(
        output="Max steps reached",
        steps=steps,
        tool_calls_total=total_tool_calls,
        cost=total_cost,
        success=False,
        error="Max steps reached",
    )
```

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_loop.py -v`
Expected: All passed

**Step 5: Commit**

```bash
cd . && git add chimera/core/loop.py tests/test_loop.py && git commit -m "feat: track real costs in ReAct loop from provider usage"
```

---

### Task 67: Cost aggregation in other loops

**Files:**
- Modify: `chimera/core/loops/plan_execute.py`
- Modify: `chimera/core/loops/reflexion.py`
- Modify: `chimera/core/loops/tree_of_thought.py`

**Step 1: Write failing tests**

Create `tests/test_loop_cost.py`:

```python
# tests/test_loop_cost.py
"""Tests that all loop types track costs."""
from __future__ import annotations

import tempfile

from chimera.core.context import Context
from chimera.core.loops.plan_execute import PlanAndExecute
from chimera.core.loops.reflexion import Reflexion
from chimera.core.loops.tree_of_thought import TreeOfThought
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.tools.write import WriteFileTool
from chimera.types import Message, ToolCall


class SimpleProvider(Provider):
    """Returns a tool call then finishes."""

    def __init__(self) -> None:
        self._calls = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._calls += 1
        if messages and messages[-1].role == "tool":
            return Response(content="Done.", tool_calls=[], usage={"input_tokens": 100, "output_tokens": 50})
        if self._calls == 1:
            # For PlanAndExecute: first call is a plan (no tools)
            return Response(content="Plan: write a file.", tool_calls=[], usage={"input_tokens": 200, "output_tokens": 100})
        return Response(
            content="Writing.",
            tool_calls=[ToolCall(id=f"c{self._calls}", name="write_file", arguments={"path": "f.py", "content": "x=1"})],
            usage={"input_tokens": 300, "output_tokens": 150},
        )

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "claude-sonnet-4-20250514"


class DirectProvider(Provider):
    """Returns a tool call on first call, then finishes."""

    def __init__(self) -> None:
        self._calls = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._calls += 1
        if messages and messages[-1].role == "tool":
            return Response(content="Done.", tool_calls=[], usage={"input_tokens": 100, "output_tokens": 50})
        return Response(
            content="Writing.",
            tool_calls=[ToolCall(id=f"c{self._calls}", name="write_file", arguments={"path": "f.py", "content": "x=1"})],
            usage={"input_tokens": 300, "output_tokens": 150},
        )

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "claude-sonnet-4-20250514"


def test_plan_execute_tracks_cost():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment(workdir=tmpdir)
        env.setup()
        ctx = Context(system="test")
        ctx.add(Message.user("do something"))
        result = PlanAndExecute(max_steps=10).run(
            SimpleProvider(), [WriteFileTool()], ctx, env,
        )
        assert result.cost > 0


def test_reflexion_tracks_cost():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment(workdir=tmpdir)
        env.setup()
        ctx = Context(system="test")
        ctx.add(Message.user("do something"))
        result = Reflexion(max_steps=10).run(
            DirectProvider(), [WriteFileTool()], ctx, env,
        )
        assert result.cost > 0


def test_tree_of_thought_tracks_cost():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment(workdir=tmpdir)
        env.setup()
        ctx = Context(system="test")
        ctx.add(Message.user("do something"))
        result = TreeOfThought(max_steps=10, n_candidates=2).run(
            DirectProvider(), [WriteFileTool()], ctx, env,
        )
        assert result.cost > 0
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_loop_cost.py -v`
Expected: FAIL — `assert 0.0 > 0` for all three

**Step 3: Modify implementations**

Apply the same pattern to all three loops: add `from chimera.providers.cost import calculate_cost` at top, add `total_cost = 0.0` before the loop, add `total_cost += calculate_cost(provider.model_name, response.usage)` after every `provider.complete()` call, replace all `cost=0.0` with `cost=total_cost`.

**`chimera/core/loops/plan_execute.py`** — Add import at top:
```python
from chimera.providers.cost import calculate_cost
```
Add `total_cost = 0.0` after `plan_generated = False`. After each `provider.complete()` add:
```python
total_cost += calculate_cost(provider.model_name, response.usage)
```
Replace all three `cost=0.0` with `cost=total_cost`.

**`chimera/core/loops/reflexion.py`** — Same pattern. Add import, `total_cost = 0.0` after `action_count = 0`, accumulate after `provider.complete()`, replace `cost=0.0` with `cost=total_cost`.

**`chimera/core/loops/tree_of_thought.py`** — Same pattern. Note this loop calls `provider.complete()` multiple times per step (N candidates + optional eval). All calls must accumulate cost.

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_loop_cost.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
cd . && git add chimera/core/loops/plan_execute.py chimera/core/loops/reflexion.py chimera/core/loops/tree_of_thought.py tests/test_loop_cost.py && git commit -m "feat: track real costs in PlanAndExecute, Reflexion, and TreeOfThought loops"
```

---

### Task 68: Cost tracking integration test

**Files:**
- Modify: `tests/test_integration.py`

**Step 1: Write the test**

Append to `tests/test_integration.py`:

```python
def test_cost_propagates_through_synthesis():
    """Verify that costs from provider usage flow through to SynthesisResult."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_content = (
            "from calc import add, subtract\n"
            "\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
            "\n"
            "def test_subtract():\n"
            "    assert subtract(5, 3) == 2\n"
        )
        Path(tmpdir, "test_calc.py").write_text(test_content)

        env = LocalEnvironment(workdir=tmpdir, test_cmd="python -m pytest test_calc.py -v")
        env.setup()

        agent = Agent(
            provider=MockProvider(),
            tools=[ReadFileTool(), WriteFileTool(), BashTool()],
            loop=ReAct(max_steps=10),
        )
        trainer = Trainer(
            spec=Spec.from_tests(tmpdir, "Implement calculator"),
            agent=agent,
            env=env,
        )
        result = trainer.synthesize(strategy=TestConvergence(max_iterations=5, patience=3))

        assert result.converged is True
        assert result.total_cost > 0  # Costs now flow through
        assert result.history[0].cost > 0
```

**Step 2: Run test to verify it passes**

Run: `cd . && python -m pytest tests/test_integration.py::test_cost_propagates_through_synthesis -v`
Expected: PASS (MockProvider returns usage dicts, cost tracking is now wired)

**Step 3: Commit**

```bash
cd . && git add tests/test_integration.py && git commit -m "test: verify cost propagation through full synthesis loop"
```

---

## Phase 16: synthesize() One-Liner + CLI (Tasks 69–72)

### Task 69: chimera.synthesize() function

**Files:**
- Create: `chimera/synthesize.py`
- Test: `tests/test_synthesize_fn.py`

**Step 1: Write the failing tests**

```python
# tests/test_synthesize_fn.py
"""Tests for chimera.synthesize() convenience function."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from chimera.synthesize import synthesize


class TestSynthesizeFunction:
    def test_returns_synthesis_result(self):
        """synthesize() returns a SynthesisResult."""
        from chimera.training.strategies.base import SynthesisResult
        from chimera.providers.base import Provider, Response
        from chimera.types import ToolCall

        class QuickProvider(Provider):
            def __init__(self):
                self._calls = 0

            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                self._calls += 1
                if messages and messages[-1].role == "tool":
                    return Response(content="Done.", tool_calls=[], usage={"input_tokens": 50, "output_tokens": 20})
                return Response(
                    content="Writing.",
                    tool_calls=[ToolCall(id=f"c{self._calls}", name="write_file", arguments={
                        "path": "calc.py",
                        "content": "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n",
                    })],
                    usage={"input_tokens": 100, "output_tokens": 50},
                )

            @property
            def context_window(self):
                return 200_000

            @property
            def supports_tool_use(self):
                return True

            @property
            def model_name(self):
                return "mock"

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "test_calc.py").write_text(
                "from calc import add, sub\n"
                "def test_add():\n    assert add(1, 2) == 3\n"
                "def test_sub():\n    assert sub(3, 1) == 2\n"
            )
            with patch("chimera.synthesize.create_provider", return_value=QuickProvider()):
                result = synthesize(
                    "Implement a calculator",
                    tests=tmpdir,
                    workdir=tmpdir,
                    max_iterations=5,
                )
            assert isinstance(result, SynthesisResult)
            assert result.converged is True

    def test_spec_only_no_tests_dir(self):
        """synthesize() works with just a spec string (no tests dir)."""
        from chimera.providers.base import Provider, Response

        class DoneProvider(Provider):
            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                return Response(content="Done.", tool_calls=[], usage={"input_tokens": 50, "output_tokens": 20})

            @property
            def context_window(self):
                return 200_000

            @property
            def supports_tool_use(self):
                return True

            @property
            def model_name(self):
                return "mock"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("chimera.synthesize.create_provider", return_value=DoneProvider()):
                result = synthesize(
                    "Build something",
                    workdir=tmpdir,
                    max_iterations=1,
                )
            # With no tests, pass_rate defaults to 0 and it won't converge
            assert result is not None

    def test_max_cost_creates_callback(self):
        """max_cost parameter creates a CostLimit callback."""
        from chimera.providers.base import Provider, Response

        class DoneProvider(Provider):
            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                return Response(content="Done.", tool_calls=[], usage={"input_tokens": 50, "output_tokens": 20})

            @property
            def context_window(self):
                return 200_000

            @property
            def supports_tool_use(self):
                return True

            @property
            def model_name(self):
                return "mock"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("chimera.synthesize.create_provider", return_value=DoneProvider()):
                # Should not raise
                result = synthesize(
                    "Build something",
                    workdir=tmpdir,
                    max_cost=0.50,
                    max_iterations=1,
                )
            assert result is not None
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_synthesize_fn.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chimera.synthesize'`

**Step 3: Write minimal implementation**

```python
# chimera/synthesize.py
"""Top-level synthesize() convenience function."""
from __future__ import annotations

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.tool_group import DEFAULT_TOOLS
from chimera.env.local import LocalEnvironment
from chimera.providers.factory import create_provider
from chimera.training.callbacks import CostLimit
from chimera.training.spec import Spec
from chimera.training.strategies.base import Callback, SynthesisResult
from chimera.training.strategies.convergence import TestConvergence
from chimera.training.trainer import Trainer


def synthesize(
    spec: str,
    *,
    tests: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    workdir: str = ".",
    max_iterations: int = 50,
    patience: int = 5,
    max_cost: float | None = None,
    max_steps: int = 50,
) -> SynthesisResult:
    """Synthesize a codebase from a specification. One function, batteries included.

    Args:
        spec: What to build (text description or path to spec file).
        tests: Path to test directory. If provided, convergence = all tests pass.
        model: Model identifier (e.g. "claude-sonnet-4-20250514", "gpt-4o").
        workdir: Working directory for generated code.
        max_iterations: Maximum synthesis epochs.
        patience: Stop after this many epochs without improvement.
        max_cost: Optional dollar budget. Stops synthesis when exceeded.
        max_steps: Maximum agent steps per epoch.

    Returns:
        SynthesisResult with convergence status, cost, and history.
    """
    provider = create_provider(model=model)

    test_cmd = f"python -m pytest {tests} -v" if tests else "python -m pytest -v"
    env = LocalEnvironment(workdir=workdir, test_cmd=test_cmd)
    env.setup()

    agent = Agent(
        provider=provider,
        tools=list(DEFAULT_TOOLS),
        loop=ReAct(max_steps=max_steps),
    )

    if tests:
        spec_obj = Spec.from_tests(tests, spec)
    else:
        spec_obj = Spec.from_string(spec)

    callbacks: list[Callback] = []
    if max_cost is not None:
        callbacks.append(CostLimit(max_cost=max_cost))

    trainer = Trainer(spec=spec_obj, agent=agent, env=env)
    return trainer.synthesize(
        strategy=TestConvergence(max_iterations=max_iterations, patience=patience),
        callbacks=callbacks,
    )
```

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_synthesize_fn.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
cd . && git add chimera/synthesize.py tests/test_synthesize_fn.py && git commit -m "feat: add chimera.synthesize() one-liner convenience function"
```

---

### Task 70: Export synthesize from chimera package

**Files:**
- Modify: `chimera/__init__.py`
- Modify: `tests/test_integration.py`

**Step 1: Write the failing test**

Update `test_synthesize_one_liner` in `tests/test_integration.py`:

```python
def test_synthesize_one_liner():
    """Test that chimera.synthesize is importable from the top-level package."""
    import chimera
    assert hasattr(chimera, "synthesize")
    assert callable(chimera.synthesize)
```

**Step 2: Run test to verify it fails**

Run: `cd . && python -m pytest tests/test_integration.py::test_synthesize_one_liner -v`
Expected: FAIL — `assert False` (hasattr returns False)

**Step 3: Modify chimera/__init__.py**

Add to imports section (after the Training imports):

```python
# Convenience
from chimera.synthesize import synthesize
```

Add `"synthesize"` to `__all__`.

**Step 4: Run test to verify it passes**

Run: `cd . && python -m pytest tests/test_integration.py::test_synthesize_one_liner -v`
Expected: PASS

**Step 5: Commit**

```bash
cd . && git add chimera/__init__.py tests/test_integration.py && git commit -m "feat: export synthesize() from chimera top-level package"
```

---

### Task 71: Wire CLI run_synthesize()

**Files:**
- Modify: `chimera/cli/main.py`
- Modify: `tests/test_cli.py`

**Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
from unittest.mock import patch, MagicMock
from chimera.training.strategies.base import SynthesisResult


def test_run_synthesize_calls_synthesize_function():
    """CLI synthesize should call chimera.synthesize.synthesize()."""
    mock_result = SynthesisResult(
        converged=True,
        iterations=3,
        total_cost=0.05,
        best_pass_rate=1.0,
        history=[],
    )
    with patch("chimera.cli.main.synthesize_fn", return_value=mock_result) as mock_synth:
        result = main(["synthesize", "--spec", "Build a calc", "--tests", "./tests/", "--model", "claude-sonnet-4-20250514"])
    assert result == 0
    mock_synth.assert_called_once()
    call_kwargs = mock_synth.call_args
    assert call_kwargs[0][0] == "Build a calc"  # spec positional
    assert call_kwargs[1]["tests"] == "./tests/"
    assert call_kwargs[1]["model"] == "claude-sonnet-4-20250514"


def test_run_synthesize_reports_failure():
    """CLI reports non-zero exit on failed synthesis."""
    mock_result = SynthesisResult(
        converged=False,
        iterations=50,
        total_cost=5.0,
        best_pass_rate=0.6,
        history=[],
        failure_reason="Max iterations reached",
    )
    with patch("chimera.cli.main.synthesize_fn", return_value=mock_result):
        result = main(["synthesize", "--spec", "Build something"])
    assert result == 1
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_cli.py::test_run_synthesize_calls_synthesize_function -v`
Expected: FAIL — `AttributeError: module has no attribute 'synthesize_fn'`

**Step 3: Modify implementation**

Replace `run_synthesize` in `chimera/cli/main.py`:

```python
from chimera.synthesize import synthesize as synthesize_fn


def run_synthesize(args: argparse.Namespace) -> int:
    """Execute the synthesize command."""
    if not args.spec and not args.tests:
        print("Error: at least one of --spec or --tests is required.", file=sys.stderr)
        return 1

    spec_text = args.spec or "Make all tests pass."

    try:
        result = synthesize_fn(
            spec_text,
            tests=args.tests,
            model=args.model,
            workdir=args.output,
            max_iterations=args.max_iterations,
            patience=args.patience,
            max_cost=args.max_cost,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if result.converged:
        print(
            f"Synthesis converged in {result.iterations} iterations "
            f"(cost: ${result.total_cost:.4f})",
        )
        return 0
    else:
        print(
            f"Synthesis failed after {result.iterations} iterations "
            f"(best: {result.best_pass_rate:.0%}, cost: ${result.total_cost:.4f})",
            file=sys.stderr,
        )
        if result.failure_reason:
            print(f"Reason: {result.failure_reason}", file=sys.stderr)
        return 1
```

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_cli.py -v`
Expected: All passed

**Step 5: Commit**

```bash
cd . && git add chimera/cli/main.py tests/test_cli.py && git commit -m "feat: wire CLI synthesize command to real synthesis logic"
```

---

### Task 72: Phase 15-16 regression

**Files:** None (verification only)

**Step 1: Run full test suite**

Run: `cd . && python -m pytest -v --tb=short 2>&1 | tail -30`
Expected: 410+ passed, 0 failed

**Step 2: Fix any breakage, commit if needed**

---

## Phase 17: Repository Mapping (Tasks 73–76)

### Task 73: RepoMap core class

**Files:**
- Create: `chimera/tools/repo_map.py`
- Test: `tests/test_repo_map.py`

**Step 1: Write the failing tests**

```python
# tests/test_repo_map.py
"""Tests for repository mapping."""
from __future__ import annotations

import tempfile
from pathlib import Path

from chimera.tools.repo_map import RepoMap


class TestRepoMap:
    def test_maps_python_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "calc.py").write_text(
                "def add(a: int, b: int) -> int:\n"
                "    return a + b\n"
                "\n"
                "def subtract(a, b):\n"
                "    return a - b\n"
            )
            rm = RepoMap(tmpdir)
            output = rm.generate()
            assert "calc.py" in output
            assert "add(a: int, b: int) -> int" in output
            assert "subtract(a, b)" in output

    def test_maps_classes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "models.py").write_text(
                "class User:\n"
                "    def __init__(self, name: str):\n"
                "        self.name = name\n"
                "\n"
                "    def greet(self) -> str:\n"
                "        return f'Hi {self.name}'\n"
            )
            rm = RepoMap(tmpdir)
            output = rm.generate()
            assert "class User" in output
            assert "__init__(self, name: str)" in output
            assert "greet(self) -> str" in output

    def test_maps_nested_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = Path(tmpdir, "pkg", "sub")
            sub.mkdir(parents=True)
            Path(sub, "helper.py").write_text("def util():\n    pass\n")
            rm = RepoMap(tmpdir)
            output = rm.generate()
            assert "pkg/sub/helper.py" in output or "pkg\\sub\\helper.py" in output
            assert "util()" in output

    def test_ignores_non_python(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "data.json").write_text('{"key": "value"}')
            Path(tmpdir, "code.py").write_text("x = 1\n")
            rm = RepoMap(tmpdir)
            output = rm.generate()
            assert "data.json" in output  # Listed but no signatures
            assert "code.py" in output

    def test_respects_max_depth(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            deep = Path(tmpdir, "a", "b", "c")
            deep.mkdir(parents=True)
            Path(deep, "deep.py").write_text("def deep_fn():\n    pass\n")
            Path(tmpdir, "top.py").write_text("def top_fn():\n    pass\n")
            rm = RepoMap(tmpdir, max_depth=1)
            output = rm.generate()
            assert "top.py" in output
            assert "deep.py" not in output

    def test_ignores_hidden_and_venv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, ".git").mkdir()
            Path(tmpdir, ".git", "config").write_text("x")
            Path(tmpdir, "__pycache__").mkdir()
            Path(tmpdir, "__pycache__", "mod.cpython-311.pyc").write_text("x")
            Path(tmpdir, "real.py").write_text("def fn():\n    pass\n")
            rm = RepoMap(tmpdir)
            output = rm.generate()
            assert ".git" not in output
            assert "__pycache__" not in output
            assert "real.py" in output

    def test_handles_syntax_errors(self):
        """Files with syntax errors should be listed but not parsed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "broken.py").write_text("def broken(:\n    pass\n")
            rm = RepoMap(tmpdir)
            output = rm.generate()
            assert "broken.py" in output  # File listed even if unparseable
```

**Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_repo_map.py -v`
Expected: FAIL — `ImportError`

**Step 3: Write minimal implementation**

```python
# chimera/tools/repo_map.py
"""Repository mapping — structural overview of a codebase."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

IGNORE_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "node_modules", ".venv", "venv", ".tox", ".eggs",
    "dist", "build", ".chimera_checkpoints",
}


class RepoMap:
    """Generate a structural overview of a codebase.

    For Python files, extracts class and function signatures using the ast
    module. For other files, lists paths only.
    """

    def __init__(self, root: str, max_depth: int | None = None) -> None:
        self.root = Path(root)
        self.max_depth = max_depth

    def generate(self) -> str:
        """Generate the repo map as a formatted string."""
        lines: list[str] = []
        self._walk(self.root, lines, depth=0)
        return "\n".join(lines)

    def _walk(self, path: Path, lines: list[str], depth: int) -> None:
        if self.max_depth is not None and depth > self.max_depth:
            return

        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        for entry in entries:
            if entry.name in IGNORE_DIRS:
                continue
            if entry.name.startswith("."):
                continue

            rel = entry.relative_to(self.root)

            if entry.is_dir():
                self._walk(entry, lines, depth + 1)
            else:
                indent = "  " * depth
                lines.append(f"{indent}{rel}")
                if entry.suffix == ".py":
                    self._parse_python(entry, lines, depth + 1)

    def _parse_python(self, path: Path, lines: list[str], depth: int) -> None:
        try:
            source = path.read_text()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            return

        indent = "  " * depth
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                lines.append(f"{indent}class {node.name}")
                self._parse_class_body(node, lines, depth + 1)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                sig = self._format_function(node)
                lines.append(f"{indent}{sig}")

    def _parse_class_body(self, cls: ast.ClassDef, lines: list[str], depth: int) -> None:
        indent = "  " * depth
        for node in ast.iter_child_nodes(cls):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                sig = self._format_function(node)
                lines.append(f"{indent}{sig}")

    def _format_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = self._format_args(node.args)
        ret = ""
        if node.returns:
            ret = f" -> {ast.unparse(node.returns)}"
        return f"{node.name}({args}){ret}"

    def _format_args(self, args: ast.arguments) -> str:
        parts: list[str] = []
        for arg in args.args:
            s = arg.arg
            if arg.annotation:
                s += f": {ast.unparse(arg.annotation)}"
            parts.append(s)
        return ", ".join(parts)


class RepoMapTool(BaseTool):
    """Tool that generates a structural overview of the codebase."""

    name = "repo_map"
    description = (
        "Generate a structural map of the repository showing files, classes, "
        "and function signatures. Use this to understand the codebase layout "
        "before making changes."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to map. Defaults to workspace root.",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum directory depth to traverse.",
            },
        },
        "required": [],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        if env is None:
            return ToolResult(output="", error="No environment available")
        path = args.get("path", ".")
        max_depth = args.get("max_depth")
        # Resolve relative to environment workdir
        if hasattr(env, "workdir"):
            base = Path(env.workdir) / path
        else:
            base = Path(path)
        if not base.is_dir():
            return ToolResult(output="", error=f"Not a directory: {path}")
        rm = RepoMap(str(base), max_depth=max_depth)
        output = rm.generate()
        return ToolResult(output=output)
```

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_repo_map.py -v`
Expected: 7 passed

**Step 5: Commit**

```bash
cd . && git add chimera/tools/repo_map.py tests/test_repo_map.py && git commit -m "feat: add RepoMap class and RepoMapTool for structural codebase overview"
```

---

### Task 74: RepoMapTool agent integration tests

**Files:**
- Modify: `tests/test_repo_map.py`

**Step 1: Write the tests**

Append to `tests/test_repo_map.py`:

```python
from chimera.tools.repo_map import RepoMapTool
from chimera.env.local import LocalEnvironment


class TestRepoMapTool:
    def test_tool_schema(self):
        tool = RepoMapTool()
        assert tool.name == "repo_map"
        schema = tool.to_anthropic_schema()
        assert schema["name"] == "repo_map"
        assert "input_schema" in schema

    def test_execute_returns_map(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "app.py").write_text("def main():\n    pass\n")
            env = LocalEnvironment(workdir=tmpdir)
            env.setup()
            tool = RepoMapTool()
            result = tool.execute({}, env)
            assert result.success
            assert "app.py" in result.output
            assert "main()" in result.output

    def test_execute_with_max_depth(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            deep = Path(tmpdir, "a", "b")
            deep.mkdir(parents=True)
            Path(deep, "deep.py").write_text("def fn():\n    pass\n")
            Path(tmpdir, "top.py").write_text("def top():\n    pass\n")
            env = LocalEnvironment(workdir=tmpdir)
            env.setup()
            tool = RepoMapTool()
            result = tool.execute({"max_depth": 0}, env)
            assert "top.py" in result.output
            assert "deep.py" not in result.output

    def test_execute_no_env(self):
        tool = RepoMapTool()
        result = tool.execute({}, None)
        assert not result.success
```

**Step 2: Run tests**

Run: `cd . && python -m pytest tests/test_repo_map.py -v`
Expected: 11 passed

**Step 3: Commit**

```bash
cd . && git add tests/test_repo_map.py && git commit -m "test: add RepoMapTool integration tests"
```

---

### Task 75: Export RepoMapTool

**Files:**
- Modify: `chimera/tools/__init__.py`
- Modify: `chimera/__init__.py`
- Modify: `tests/test_repo_map.py`

**Step 1: Write the failing test**

Append to `tests/test_repo_map.py`:

```python
class TestRepoMapExports:
    def test_importable_from_tools(self):
        from chimera.tools import RepoMapTool as RMT
        assert RMT is RepoMapTool

    def test_importable_from_chimera(self):
        import chimera
        assert hasattr(chimera, "RepoMapTool")
```

**Step 2: Run test to verify it fails**

Run: `cd . && python -m pytest tests/test_repo_map.py::TestRepoMapExports -v`
Expected: FAIL

**Step 3: Modify exports**

In `chimera/tools/__init__.py`, add:
```python
from chimera.tools.repo_map import RepoMapTool
```
And add `"RepoMapTool"` to `__all__`.

In `chimera/__init__.py`, add to the Tools comment section or after the existing imports, somewhere logical:
```python
from chimera.tools.repo_map import RepoMapTool
```
And add `"RepoMapTool"` to `__all__`.

**Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_repo_map.py -v`
Expected: 13 passed

**Step 5: Commit**

```bash
cd . && git add chimera/tools/__init__.py chimera/__init__.py tests/test_repo_map.py && git commit -m "feat: export RepoMapTool from tools and chimera packages"
```

---

### Task 76: Full regression + cost export

**Files:**
- Modify: `chimera/__init__.py` (export calculate_cost)

**Step 1: Export cost utility**

Add to `chimera/__init__.py`:
```python
from chimera.providers.cost import calculate_cost
```
And add `"calculate_cost"` to `__all__`.

**Step 2: Run full test suite**

Run: `cd . && python -m pytest -v --tb=short 2>&1 | tail -30`
Expected: 420+ passed, 0 failed

**Step 3: Commit**

```bash
cd . && git add chimera/__init__.py && git commit -m "feat: export calculate_cost, full regression passing"
```

---

## Phase 18: Tree Search Strategy (Tasks 77–83)

This phase is already designed and planned in detail at:
`docs/plans/2026-02-22-tree-search-plan.md`

Execute tasks 67–74 from that plan (renumbered here as 77–83 for continuity).
That plan includes: SearchNode data model, TreeSearch constructor, environment cloning helper, core search loop with parallel execution, custom branch_fn support, package exports, regression, and docs update.

---

## Phase 19: Update Docs (Task 84)

### Task 84: Update task-status.md and CONTEXT.md

**Files:**
- Modify: `docs/task-status.md`
- Modify: `CONTEXT.md`

**Step 1: Update task-status.md**

Add Phase 15-17 sections:

```markdown
## Phase 15: Cost Tracking

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 65 | 15 - Cost | Cost calculation utility | `chimera/providers/cost.py` | 9 | DONE |
| 66 | 15 - Cost | ReAct loop cost aggregation | `chimera/core/loop.py` | 1 | DONE |
| 67 | 15 - Cost | Other loops cost aggregation | `chimera/core/loops/*.py` | 3 | DONE |
| 68 | 15 - Cost | Cost integration test | `tests/test_integration.py` | 1 | DONE |

## Phase 16: synthesize() One-Liner + CLI

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 69 | 16 - Synthesize | chimera.synthesize() function | `chimera/synthesize.py` | 3 | DONE |
| 70 | 16 - Synthesize | Export synthesize | `chimera/__init__.py` | 1 | DONE |
| 71 | 16 - Synthesize | Wire CLI run_synthesize() | `chimera/cli/main.py` | 2 | DONE |
| 72 | 16 - Synthesize | Phase 15-16 regression | — | — | DONE |

## Phase 17: Repository Mapping

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 73 | 17 - RepoMap | RepoMap core class | `chimera/tools/repo_map.py` | 7 | DONE |
| 74 | 17 - RepoMap | RepoMapTool integration | `tests/test_repo_map.py` | 4 | DONE |
| 75 | 17 - RepoMap | Package exports | `chimera/__init__.py` | 2 | DONE |
| 76 | 17 - RepoMap | Full regression + cost export | — | — | DONE |
```

Update Phase Summary table and total test count.

**Step 2: Update CONTEXT.md**

Add Phase 15-17 sections under Implementation Progress. Update test count.

**Step 3: Commit**

```bash
cd . && git add docs/task-status.md CONTEXT.md && git commit -m "docs: update progress for Phases 15-17 (cost, synthesize, repo map)"
```
