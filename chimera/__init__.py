from __future__ import annotations

__version__ = "0.1.0"

# Core
from chimera.core import (
    Agent,
    AllowList,
    AlwaysDeny,
    ApprovalPolicy,
    AutoApprove,
    BaseTool,
    CollectStreamHandler,
    Context,
    ContextCompressor,
    DEFAULT_TOOLS,
    LoopDetector,
    PrintStreamHandler,
    Prompt,
    ReAct,
    StreamHandler,
    ToolGroup,
    tool,
)

# Loops
from chimera.core.loops import PlanAndExecute, Reflexion, TreeOfThought

# Composition
from chimera.composition import Ensemble, Pipeline, Supervisor

# Environment
from chimera.env import Environment, GitEnvironment, LocalEnvironment

# Providers
from chimera.providers import Provider, Response, StreamEvent, create_provider

# Training
from chimera.training import Architecture, Constraint, Layer, Spec, Trainer
from chimera.training.callbacks import (
    CheckpointCallback,
    CostLimitCallback,
    ProgressBar,
    ProgressCallback,
)
from chimera.training.strategies import (
    Callback,
    CurriculumStrategy,
    EnsembleStrategy,
    EpochResult,
    Passthrough,
    Strategy,
    SynthesisResult,
    TestConvergence,
)

# Evaluation
from chimera.eval import (
    Benchmark,
    EvalResult,
    Harness,
    OverfitSignal,
    TaskEvalResult,
    avg_cost,
    avg_steps,
    check_hardcoded_answers,
    check_output_similarity,
    pass_at_k,
    resolve_rate,
)

# Types
from chimera.types import (
    AgentResult,
    CommandResult,
    Message,
    StepResult,
    TestResult,
    ToolCall,
    ToolResult,
)

__all__ = [
    # Core
    "Agent",
    "AllowList",
    "AlwaysDeny",
    "ApprovalPolicy",
    "AutoApprove",
    "BaseTool",
    "CollectStreamHandler",
    "Context",
    "ContextCompressor",
    "DEFAULT_TOOLS",
    "LoopDetector",
    "PrintStreamHandler",
    "Prompt",
    "ReAct",
    "StreamHandler",
    "ToolGroup",
    "tool",
    # Loops
    "PlanAndExecute",
    "Reflexion",
    "TreeOfThought",
    # Composition
    "Ensemble",
    "Pipeline",
    "Supervisor",
    # Environment
    "Environment",
    "GitEnvironment",
    "LocalEnvironment",
    # Providers
    "Provider",
    "Response",
    "StreamEvent",
    "create_provider",
    # Training
    "Architecture",
    "Callback",
    "CheckpointCallback",
    "Constraint",
    "CostLimitCallback",
    "CurriculumStrategy",
    "EnsembleStrategy",
    "EpochResult",
    "Layer",
    "Passthrough",
    "ProgressBar",
    "ProgressCallback",
    "Spec",
    "Strategy",
    "SynthesisResult",
    "TestConvergence",
    "Trainer",
    # Evaluation
    "Benchmark",
    "EvalResult",
    "Harness",
    "OverfitSignal",
    "TaskEvalResult",
    "avg_cost",
    "avg_steps",
    "check_hardcoded_answers",
    "check_output_similarity",
    "pass_at_k",
    "resolve_rate",
    # Types
    "AgentResult",
    "CommandResult",
    "Message",
    "StepResult",
    "TestResult",
    "ToolCall",
    "ToolResult",
]
