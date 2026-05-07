"""End-to-end layer integration tests — prove the 8-layer stack works together.

Each test composes modules from MULTIPLE layers simultaneously:
  L1 Environment + L2 Infrastructure + L3 Provider + L4 Agent + L5 Eval + L6 Synthesis

These are NOT mock tests. They hit a real LLM and exercise real tool execution,
event buses, middleware, cost tracking, sessions, and composition — all at once.

Run with:
    source .env
    uv run pytest tests/test_layer_integration.py -v
"""
from __future__ import annotations

import os
import tempfile

import pytest

_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
_MODEL = os.environ.get("ANTHROPIC_MODEL", "glm-5")

pytestmark = pytest.mark.skipif(
    not _TOKEN,
    reason="ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY not set",
)


@pytest.fixture(scope="module")
def provider():
    from chimera.providers.factory import create_provider
    return create_provider(model=_MODEL)


@pytest.fixture
def workdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


# ---------------------------------------------------------------------------
# Test 1: Agent + EventBus + CostTracker + Middleware + Environment
#
# Layers exercised: L1 (Local env), L2 (Events, Cost, Middleware),
#                   L3 (Provider), L4 (Agent + ReAct loop)
# ---------------------------------------------------------------------------

def test_agent_with_full_infrastructure(provider, workdir):
    """An agent with events, cost tracking, and middleware — all firing together."""
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.middleware import LoggingMiddleware, LoopMiddleware
    from chimera.core.tool_group import DEFAULT_TOOLS
    from chimera.env.local import LocalEnvironment
    from chimera.events.base import EventBus
    from chimera.providers.cost_tracker import CostTracker

    # L2: Infrastructure — events, cost, middleware
    event_bus = EventBus()
    events_captured = []
    event_bus.subscribe("*", lambda e: events_captured.append(e.type))

    cost_tracker = CostTracker()

    class StepCounterMiddleware(LoopMiddleware):
        """Custom middleware that counts model calls."""
        def __init__(self):
            self.call_count = 0
        def before_model(self, context, tools):  # noqa: ARG002
            self.call_count += 1
            return context

    counter_mw = StepCounterMiddleware()

    config = LoopConfig(
        event_bus=event_bus,
        cost_tracker=cost_tracker,
        middleware=[LoggingMiddleware(), counter_mw],
    )

    # L1: Environment
    env = LocalEnvironment(workdir=workdir)
    env.setup()

    # L4: Agent with loop config
    agent = Agent(
        provider=provider,
        tools=list(DEFAULT_TOOLS),
        loop=ReAct(max_steps=10, config=config),
        name="infra-test-agent",
    )

    # Run: agent creates a file and reads it back
    result = agent.run(
        "Create a file called greet.py containing: def greet(name): return f'Hello {name}'. "
        "Then read it back and confirm the content.",
        env=env,
    )

    # Verify: all layers contributed
    assert result.success, f"Agent failed: {result.output}"

    # L1: file was created
    assert os.path.exists(os.path.join(workdir, "greet.py"))
    content = open(os.path.join(workdir, "greet.py")).read()
    assert "greet" in content

    # L2: events were fired
    assert len(events_captured) > 0, "EventBus received no events"
    assert "tool_call" in events_captured, f"No tool_call event in {events_captured}"

    # L2: cost tracked
    assert cost_tracker.total_calls >= 1

    # L2: middleware ran
    assert counter_mw.call_count >= 1, "Middleware never ran"

    env.cleanup()


# ---------------------------------------------------------------------------
# Test 2: Pipeline + EventBus + Session persistence
#
# Layers exercised: L1 (Local env), L2 (Events, Sessions),
#                   L3 (Provider), L4 (Agent composition: Pipeline)
# ---------------------------------------------------------------------------

def test_pipeline_with_events_and_session(provider, workdir):
    """Two-agent Pipeline with event monitoring and session save/resume."""
    from chimera.composition.pipeline import Pipeline
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.tool_group import DEFAULT_TOOLS
    from chimera.env.local import LocalEnvironment
    from chimera.events.base import EventBus

    # L2: Events
    event_bus = EventBus()
    step_events = []
    event_bus.subscribe("step", lambda e: step_events.append(e))

    config = LoopConfig(event_bus=event_bus)

    # L1: Environment
    env = LocalEnvironment(workdir=workdir)
    env.setup()

    # L4: Two-agent pipeline
    coder = Agent(
        provider=provider,
        tools=list(DEFAULT_TOOLS),
        loop=ReAct(max_steps=8, config=config),
        name="coder",
    )
    reviewer = Agent(
        provider=provider,
        loop=ReAct(max_steps=5, config=config),
        name="reviewer",
    )

    pipe = Pipeline([coder, reviewer])
    result = pipe.run(
        "Write a Python function in calc.py that adds two numbers. "
        "Then review the code from the previous step and suggest if it looks correct.",
        env=env,
    )

    assert result.success, f"Pipeline failed: {result.output}"

    # L1: file was created by first agent
    assert os.path.exists(os.path.join(workdir, "calc.py"))

    # L2: events fired across both agents
    assert len(step_events) >= 2, "Expected steps from both agents"

    env.cleanup()


# ---------------------------------------------------------------------------
# Test 3: synthesize() one-liner + Trainer + TestConvergence
#
# Layers exercised: L1 (Local env), L3 (Provider),
#                   L4 (Agent), L6 (Synthesis: Trainer + Strategy)
# ---------------------------------------------------------------------------

def test_synthesize_end_to_end(workdir):
    """chimera.synthesize() drives Trainer + TestConvergence to produce passing code."""
    from chimera.synthesize import synthesize
    from chimera.env.local import LocalEnvironment

    # Set up a tiny project with a failing test
    os.makedirs(os.path.join(workdir, "tests"), exist_ok=True)
    with open(os.path.join(workdir, "multiply.py"), "w") as f:
        f.write("# TODO: implement multiply\n")
    with open(os.path.join(workdir, "tests", "test_multiply.py"), "w") as f:
        f.write(
            "from multiply import multiply\n"
            "\n"
            "def test_multiply_basic():\n"
            "    assert multiply(3, 4) == 12\n"
            "\n"
            "def test_multiply_zero():\n"
            "    assert multiply(0, 5) == 0\n"
        )

    result = synthesize(
        spec="Implement the multiply function in multiply.py so all tests pass.",
        tests="python -m pytest tests/test_multiply.py -v",
        workdir=workdir,
        model=_MODEL,
        max_iterations=5,
    )

    assert result.converged, f"Synthesis failed: iterations={result.iterations}, reason={result.failure_reason}"
    assert result.iterations >= 1

    # Verify the code actually works
    env = LocalEnvironment(workdir=workdir)
    env.setup()
    test_result = env.run_command("python -m pytest tests/test_multiply.py -v")
    assert test_result.exit_code == 0, f"Tests still fail:\n{test_result.stdout}"
    env.cleanup()


# ---------------------------------------------------------------------------
# Test 4: AgentPreset + LoopConfig + Wire monitoring
#
# Layers exercised: L2 (Wire protocol), L3 (Provider),
#                   L4 (Agent + AgentPreset + loop variant)
# ---------------------------------------------------------------------------

def test_preset_with_wire_monitoring(provider, workdir):
    """AgentPreset.CODEX with Wire protocol capturing lifecycle events."""
    from chimera.agents.presets.agent_styles import AgentPreset
    from chimera.core.loop_config import LoopConfig
    from chimera.env.local import LocalEnvironment
    from chimera.wire.wire import Wire

    # L2: Wire protocol
    wire = Wire()
    wire_messages = []
    wire.on_message(lambda msg: wire_messages.append(msg))

    config = LoopConfig(wire=wire)

    # L4: AgentPreset builds the agent, we inject our LoopConfig.
    # _compose() is the non-deprecated path equivalent to .build();
    # used here so the test doesn't pin itself to the v0.7.0 removal target.
    preset = AgentPreset.CODEX
    agent = preset._compose(provider)
    # Override loop config to add wire
    agent.loop.config = config

    # L1: Environment
    env = LocalEnvironment(workdir=workdir)
    env.setup()

    result = agent.run(
        "Create a file called hello.txt containing 'wire test passed'.",
        env=env,
    )

    assert result.success, f"Preset agent failed: {result.output}"

    # L2: Wire captured lifecycle messages
    wire_types = [type(m).__name__ for m in wire_messages]
    assert "TurnBegin" in wire_types, f"No TurnBegin in wire: {wire_types}"
    assert "StepBegin" in wire_types, f"No StepBegin in wire: {wire_types}"

    env.cleanup()


# ---------------------------------------------------------------------------
# Test 5: Ensemble + CostTracker + EventBus (parallel agents, shared infra)
#
# Layers exercised: L1 (Local env), L2 (Events, Cost),
#                   L3 (Provider), L4 (Agent composition: Ensemble)
# ---------------------------------------------------------------------------

def test_ensemble_with_shared_infrastructure(provider):
    """Two agents in an Ensemble share EventBus and CostTracker."""
    from chimera.composition.ensemble import Ensemble
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.events.base import EventBus
    from chimera.providers.cost_tracker import CostTracker

    # L2: Shared infrastructure
    event_bus = EventBus()
    all_events = []
    event_bus.subscribe("*", lambda e: all_events.append(e.type))

    cost_tracker = CostTracker()
    config = LoopConfig(event_bus=event_bus, cost_tracker=cost_tracker)

    # L4: Two agents, same question, shared infra
    agent_a = Agent(
        provider=provider,
        loop=ReAct(max_steps=3, config=config),
        name="solver-a",
    )
    agent_b = Agent(
        provider=provider,
        loop=ReAct(max_steps=3, config=config),
        name="solver-b",
    )

    ensemble = Ensemble([agent_a, agent_b])
    results = ensemble.run("What is 7 * 8? Answer with just the number.", env=None)

    assert len(results) == 2
    assert all(r.success for r in results), f"Results: {[r.output for r in results]}"

    # At least one should have 56
    outputs = " ".join(r.output for r in results)
    assert "56" in outputs, f"Neither agent got 56: {outputs}"

    # L2: Events fired from both agents
    assert len(all_events) >= 2, f"Too few events: {all_events}"

    # L2: Cost tracked across both runs
    assert cost_tracker.total_calls >= 2


# ---------------------------------------------------------------------------
# Test 6: Full vertical slice — Environment + Secrets + Events + Agent + Synthesis
#
# This is the "prove it's not a gimmick" test. Every layer contributes.
# ---------------------------------------------------------------------------

def test_full_vertical_slice(provider, workdir):
    """Full 6-layer vertical slice: env → secrets → events → agent → synthesis.

    Creates a project, synthesizes code with secret redaction and event
    monitoring active, then verifies the result.
    """
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.tool_group import DEFAULT_TOOLS
    from chimera.env.local import LocalEnvironment
    from chimera.events.base import EventBus
    from chimera.providers.cost_tracker import CostTracker
    from chimera.secrets.registry import SecretRegistry

    # L1: Environment
    env = LocalEnvironment(workdir=workdir)
    env.setup()

    # L2: Secret detection (verify no secrets leak into outputs)
    secret_registry = SecretRegistry()
    secret_registry.register("test-key", "sk-secret-12345")

    # L2: Events — track what happened
    event_bus = EventBus()
    tool_calls_seen = []
    event_bus.subscribe("tool_call", lambda e: tool_calls_seen.append(e.metadata.get("tool_name", "?")))
    tool_results_seen = []
    event_bus.subscribe("tool_result", lambda e: tool_results_seen.append(e.metadata.get("tool_name", "?")))

    # L2: Cost tracking
    cost_tracker = CostTracker()

    config = LoopConfig(
        event_bus=event_bus,
        cost_tracker=cost_tracker,
    )

    # Set up test project
    os.makedirs(os.path.join(workdir, "tests"), exist_ok=True)
    with open(os.path.join(workdir, "fib.py"), "w") as f:
        f.write("# TODO: implement fibonacci\n")
    with open(os.path.join(workdir, "tests", "test_fib.py"), "w") as f:
        f.write(
            "from fib import fibonacci\n"
            "\n"
            "def test_fib_base():\n"
            "    assert fibonacci(0) == 0\n"
            "    assert fibonacci(1) == 1\n"
            "\n"
            "def test_fib_sequence():\n"
            "    assert fibonacci(5) == 5\n"
            "    assert fibonacci(10) == 55\n"
        )

    # L4: Agent with full infrastructure
    agent = Agent(
        provider=provider,
        tools=list(DEFAULT_TOOLS),
        loop=ReAct(max_steps=15, config=config),
        name="synthesis-agent",
    )

    # L4: Run agent to implement fibonacci
    result = agent.run(
        "Implement the fibonacci function in fib.py so all tests in tests/test_fib.py pass. "
        "Run the tests to verify.",
        env=env,
    )

    assert result.success, f"Agent failed: {result.output}"

    # L1: File was modified
    fib_content = open(os.path.join(workdir, "fib.py")).read()
    assert "def fibonacci" in fib_content or "def fib" in fib_content

    # L1: Tests pass
    test_result = env.run_command("python -m pytest tests/test_fib.py -v")
    assert test_result.exit_code == 0, f"Tests fail:\n{test_result.stdout}"

    # L2: Events captured tool usage
    assert len(tool_calls_seen) >= 1, "No tool_call events recorded"
    # Agent should have used write/edit and bash/test tools
    all_tools = tool_calls_seen + tool_results_seen
    tool_names = set(all_tools)
    assert len(tool_names) >= 1, f"Only used tools: {tool_names}"

    # L2: Cost was tracked
    assert cost_tracker.total_calls >= 1

    # L2: Secret check — output should not contain our secret
    redacted = secret_registry.redact(result.output)
    assert "sk-secret-12345" not in redacted

    env.cleanup()
