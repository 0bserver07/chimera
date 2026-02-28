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
from chimera.env import Environment, GitEnvironment, LocalEnvironment, SessionMixin

# Providers
from chimera.providers import ModelConfig, Provider, ProviderCatalog, Response, StreamEvent, create_provider
from chimera.providers.cost import calculate_cost, register_model_cost

# Training
from chimera.training import Architecture, Constraint, Layer, Spec, Trainer
from chimera.training.callbacks import (
    CheckpointCallback,
    CostLimitCallback,
    ProgressBar,
    ProgressCallback,
)
from chimera.training.strategies import (
    AIMOEnsemble,
    Callback,
    CurriculumStrategy,
    EnsembleStrategy,
    EpochResult,
    MajorityVoting,
    Passthrough,
    Strategy,
    SynthesisResult,
    TestConvergence,
    TreeSearch,
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

# Tools
from chimera.tools.repo_map import RepoMapTool

# Convenience
from chimera.synthesize import synthesize

# LSP
from chimera.lsp import Diagnostic, LSPClient, LSPManager, LSPTool, Severity

# Extension modules
from chimera.core.loop_config import LoopConfig
from chimera.events import Event, EventBus
from chimera.compaction import CompactionStrategy, TokenCounter
from chimera.detection import DetectionResult, ExactRepeatDetector
from chimera.permissions import PermissionAction, PermissionRuleset
from chimera.streaming import ConsoleStreamHandler, StreamingReAct
from chimera.sessions import InMemoryStorage, Session
from chimera.auth import AuthManager, Credential
from chimera.agents import AgentConfig, AgentRegistry
from chimera.plugins import BasePlugin, PluginManager
from chimera.config import ProjectConfig, Skill, SkillRegistry, StructuredOutput
from chimera.mcp import MCPClient, MCPToolSource

# Transactions
from chimera.transactions import FileTransaction, StagedChange, TransactionState

# Types
from chimera.core.loop import async_drain_steps, drain_steps
from chimera.types import (
    AgentResult,
    ChangeType,
    CommandResult,
    FileChange,
    Message,
    PendingApproval,
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
    "SessionMixin",
    # Providers
    "Provider",
    "Response",
    "StreamEvent",
    "calculate_cost",
    "register_model_cost",
    "create_provider",
    "ModelConfig",
    "ProviderCatalog",
    # Training
    "AIMOEnsemble",
    "Architecture",
    "Callback",
    "CheckpointCallback",
    "Constraint",
    "CostLimitCallback",
    "CurriculumStrategy",
    "EnsembleStrategy",
    "EpochResult",
    "Layer",
    "MajorityVoting",
    "Passthrough",
    "ProgressBar",
    "ProgressCallback",
    "Spec",
    "Strategy",
    "SynthesisResult",
    "TestConvergence",
    "Trainer",
    "TreeSearch",
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
    # Tools
    "RepoMapTool",
    # Convenience
    "synthesize",
    # Types
    "AgentResult",
    "CommandResult",
    "Message",
    "StepResult",
    "TestResult",
    "ToolCall",
    "ToolResult",
    # LSP
    "Diagnostic",
    "LSPClient",
    "LSPManager",
    "LSPTool",
    "Severity",
    # New APIs
    "ChangeType",
    "FileChange",
    "PendingApproval",
    "async_drain_steps",
    "drain_steps",
    # Extension modules
    "LoopConfig",
    "EventBus",
    "Event",
    "CompactionStrategy",
    "TokenCounter",
    "DetectionResult",
    "ExactRepeatDetector",
    "PermissionAction",
    "PermissionRuleset",
    "ConsoleStreamHandler",
    "StreamingReAct",
    "Session",
    "InMemoryStorage",
    "AuthManager",
    "Credential",
    "AgentConfig",
    "AgentRegistry",
    # Plugins
    "BasePlugin",
    "PluginManager",
    # Transactions
    "FileTransaction",
    "StagedChange",
    "TransactionState",
    # Config
    "ProjectConfig",
    "Skill",
    "SkillRegistry",
    "StructuredOutput",
    # MCP
    "MCPClient",
    "MCPToolSource",
]
