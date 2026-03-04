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
from chimera.providers.cost import calculate_cost, estimate_cost, register_model_cost
from chimera.providers.cost_tracker import CostLimitExceeded, CostTracker

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
from chimera.tools.image_read import ImageReadTool
from chimera.tools.import_graph import ImportEdge, ImportGraph

# Convenience
from chimera.synthesize import synthesize

# LSP
from chimera.lsp import Diagnostic, LSPClient, LSPManager, LSPTool, Severity

# Extension modules
from chimera.core.loop_config import LoopConfig
from chimera.events import Event, EventBus
from chimera.compaction import CompactionStrategy, TokenCounter
from chimera.detection import DetectionResult, ExactRepeatDetector
from chimera.permissions import AuditLog, PermissionAction, PermissionRuleset, RiskLevel, classify_risk
from chimera.streaming import ConsoleStreamHandler, StreamingReAct
from chimera.sessions import InMemoryStorage, Session
from chimera.auth import AuthManager, Credential
from chimera.agents import AgentConfig, AgentRegistry
from chimera.agents.loader import create_default_registry, load_custom_agents
from chimera.plugins import BasePlugin, Marketplace, PluginInfo, PluginManager
from chimera.config import ProjectConfig, Skill, SkillRegistry, StructuredOutput
from chimera.mcp import MCPClient, MCPToolSource

# Docs
from chimera.docs.generator import DocGenerator, DocSection

# TestGen
from chimera.testgen import CoverageReport, TestCase, TestGenerator, parse_coverage

# CI
from chimera.ci import CIFixWorkflow, FailureInfo, parse_ci_log

# Review
from chimera.review import ReviewComment, ReviewFeedback, ReviewOrchestrator, Severity as ReviewSeverity

# Research
from chimera.research import Finding, ResearchPlan, Researcher, Source

# Migration
from chimera.migration import MigrationPlan, MigrationPlanner, MigrationRule

# Checkpoints
from chimera.checkpoints import CheckpointInfo, CheckpointManager

# Workflows
from chimera.workflows import CommitStrategy, GitWorkflow

# Transactions
from chimera.transactions import FileTransaction, StagedChange, TransactionState

# Types
from chimera.core.loop import async_drain_steps, drain_steps
from chimera.types import (
    AgentResult,
    ChangeType,
    CommandResult,
    ContentBlock,
    FileChange,
    ImageContent,
    Message,
    PendingApproval,
    StepResult,
    TextContent,
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
    "estimate_cost",
    "register_model_cost",
    "CostTracker",
    "CostLimitExceeded",
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
    "ImageReadTool",
    "ImportEdge",
    "ImportGraph",
    "RepoMapTool",
    # Convenience
    "synthesize",
    # Types
    "AgentResult",
    "CommandResult",
    "ContentBlock",
    "ImageContent",
    "Message",
    "TextContent",
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
    "AuditLog",
    "PermissionAction",
    "PermissionRuleset",
    "RiskLevel",
    "classify_risk",
    "ConsoleStreamHandler",
    "StreamingReAct",
    "Session",
    "InMemoryStorage",
    "AuthManager",
    "Credential",
    "AgentConfig",
    "AgentRegistry",
    "create_default_registry",
    "load_custom_agents",
    # Research
    "Finding",
    "ResearchPlan",
    "Researcher",
    "Source",
    # Checkpoints
    "CheckpointInfo",
    "CheckpointManager",
    # Plugins
    "BasePlugin",
    "Marketplace",
    "PluginInfo",
    "PluginManager",
    # Workflows
    "CommitStrategy",
    "GitWorkflow",
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
    # Docs
    "DocGenerator",
    "DocSection",
    # TestGen
    "CoverageReport",
    "TestCase",
    "TestGenerator",
    "parse_coverage",
    # CI
    "CIFixWorkflow",
    "FailureInfo",
    "parse_ci_log",
    # Review
    "ReviewComment",
    "ReviewFeedback",
    "ReviewOrchestrator",
    "ReviewSeverity",
    # Migration
    "MigrationPlan",
    "MigrationPlanner",
    "MigrationRule",
]
