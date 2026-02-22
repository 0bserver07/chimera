"""Chimera — a composable coding agent framework.

Synthesize codebases from specifications using AI agents.

Usage::

    import chimera

    # One-liner
    result = chimera.synthesize("Build a REST API", tests="./tests/")

    # Configured
    trainer = chimera.Trainer(
        spec=chimera.Spec.from_string("Build a task API"),
        agent=chimera.Agent(provider=provider),
        env=chimera.LocalEnvironment("./output"),
    )
    result = trainer.synthesize(strategy=chimera.TestConvergence())
"""

from __future__ import annotations

__version__ = "0.1.0"

# Layer 1: Environment
from chimera.env.base import Environment
from chimera.env.local import LocalEnvironment

# Layer 2: Provider
from chimera.providers.base import Provider, Response, StreamEvent

# Layer 3: Agent
from chimera.core.agent import Agent
from chimera.core.tool import BaseTool, tool
from chimera.core.context import Context
from chimera.core.prompt import Prompt
from chimera.core.loop import ReAct
from chimera.types import (
    Message,
    ToolCall,
    ToolResult,
    CommandResult,
    TestResult,
    StepResult,
    AgentResult,
)

# Layer 5: Synthesis
from chimera.training.spec import Spec
from chimera.training.architecture import Architecture, Layer
from chimera.training.constraint import Constraint, ConstraintResult
from chimera.training.trainer import Trainer
from chimera.training.callbacks import CostLimit, EpochCheckpoint, HistoryRecorder
from chimera.training.strategies.base import Strategy, SynthesisResult, EpochResult, Callback
from chimera.training.strategies.convergence import TestConvergence

# Tools submodule
from chimera import tools


def synthesize(
    spec_text: str,
    tests: str | None = None,
    agent: Agent | None = None,
    provider: Provider | None = None,
    strategy: Strategy | None = None,
    output_dir: str = "./output",
    constraints: list[Constraint] | None = None,
    callbacks: list[Callback] | None = None,
    **kwargs,
) -> SynthesisResult:
    """One-liner: synthesize a codebase from a spec.

    Args:
        spec_text: Natural language description of what to build.
        tests: Path to test directory. If provided, tests ARE the spec.
        agent: Pre-configured agent. If None, requires provider.
        provider: LLM provider. If None and agent is None, raises ValueError.
        strategy: Synthesis strategy. Defaults to TestConvergence().
        output_dir: Where to write generated code.
        constraints: Optional constraints beyond test passing.
        callbacks: Optional synthesis callbacks.

    Returns:
        SynthesisResult with convergence status and history.

    Example::

        result = chimera.synthesize(
            "Build a REST API for task management",
            tests="./tests/",
            provider=some_provider,
        )
    """
    # Build spec
    if tests:
        spec = Spec.from_tests(tests, description=spec_text)
    else:
        spec = Spec.from_string(spec_text)

    # Build or use agent
    if agent is None:
        if provider is None:
            raise ValueError(
                "Either 'agent' or 'provider' must be specified. "
                "Example: chimera.synthesize('...', provider=some_provider)"
            )
        agent = Agent(
            provider=provider,
            tools=[tools.read_file, tools.write_file, tools.bash],
        )

    # Build environment
    env = LocalEnvironment(
        workdir=output_dir,
        test_cmd=f"python -m pytest {tests}" if tests else "python -m pytest",
    )
    env.setup()

    # Build trainer and run
    trainer = Trainer(
        spec=spec,
        agent=agent,
        env=env,
        constraints=constraints or [],
    )

    return trainer.synthesize(
        strategy=strategy,
        callbacks=callbacks,
    )


__all__ = [
    # Meta
    "__version__",
    # One-liner
    "synthesize",
    # Environment
    "Environment",
    "LocalEnvironment",
    # Provider
    "Provider",
    "Response",
    "StreamEvent",
    # Agent
    "Agent",
    "BaseTool",
    "tool",
    "Context",
    "Prompt",
    "ReAct",
    # Types
    "Message",
    "ToolCall",
    "ToolResult",
    "CommandResult",
    "TestResult",
    "StepResult",
    "AgentResult",
    # Synthesis
    "Spec",
    "Architecture",
    "Layer",
    "Constraint",
    "ConstraintResult",
    "Trainer",
    "CostLimit",
    "EpochCheckpoint",
    "HistoryRecorder",
    "Strategy",
    "SynthesisResult",
    "EpochResult",
    "Callback",
    "TestConvergence",
    # Tools
    "tools",
]
