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

try:
    from chimera.env import CloudEnvironment, RemoteEnvironment
except ImportError:
    pass

# Providers
from chimera.providers import ModelConfig, Provider, ProviderCatalog, Response, StreamEvent, create_provider
from chimera.providers.cost import calculate_cost, estimate_cost, register_model_cost
from chimera.providers.cost_tracker import CostLimitExceeded, CostTracker, StepUsage, TokenUsage

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
from chimera.tools.browser import BrowserTool

# Convenience
from chimera.synthesize import synthesize

# LSP
from chimera.lsp import Diagnostic, LSPClient, LSPManager, LSPTool, Severity

# Critic
from chimera.critic import ChecklistCritic, Critic, CriticConfig, CriticMixin, CriticMode, CriticResult, LLMCritic

# ACP
from chimera.acp import ACPClient, ACPResponse, ACPSessionConfig, ACPToolCall, ExternalAgentTool

# Extension modules
from chimera.core.loop_config import LoopConfig
from chimera.events import Event, EventBus
from chimera.compaction import (
    AtomicGroup,
    CompactionStrategy,
    CompactionUrgency,
    CompactionView,
    InsufficientCompactionError,
    ThresholdCompaction,
    TokenCounter,
)
from chimera.detection import DetectionResult, ExactRepeatDetector
from chimera.permissions import AuditLog, PermissionAction, PermissionRuleset, RiskLevel, classify_risk
from chimera.streaming import ConsoleStreamHandler, StreamingReAct
from chimera.sessions import EventLog, EventSourcedSession, InMemoryStorage, Session
from chimera.auth import AuthManager, Credential
from chimera.agents import AgentConfig, AgentFactory, AgentLoader, AgentRegistry, FileAgentDef
from chimera.agents.loader import create_default_registry, load_custom_agents
from chimera.plugins import BasePlugin, DirectoryPluginLoader, Hook, MCPServerConfig, Marketplace, PluginExtensionRegistry, PluginInfo, PluginManager
from chimera.config import ChimeraConfig, DiscriminatedUnion, ProjectConfig, Skill, SkillRegistry, StructuredOutput
from chimera.mcp import MCPClient, MCPToolSource

# Security
from chimera.security import (
    CompositeSecurityAnalyzer,
    ConfirmAboveThreshold,
    ConfirmationPolicy,
    LLMSecurityAnalyzer,
    NeverConfirm,
    RuleBasedSecurityAnalyzer,
    SecurityAnalyzer,
    SecurityRisk,
)

# Secrets
from chimera.secrets import RedactionMiddleware, SecretDetector, SecretRegistry

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
    "BrowserTool",
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
    # Critic
    "ChecklistCritic",
    "Critic",
    "CriticConfig",
    "CriticMixin",
    "CriticMode",
    "CriticResult",
    "LLMCritic",
    # ACP
    "ACPClient",
    "ACPResponse",
    "ACPSessionConfig",
    "ACPToolCall",
    "ExternalAgentTool",
    # Extension modules
    "LoopConfig",
    "EventBus",
    "Event",
    "AtomicGroup",
    "CompactionStrategy",
    "CompactionUrgency",
    "CompactionView",
    "InsufficientCompactionError",
    "ThresholdCompaction",
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
    "EventLog",
    "EventSourcedSession",
    "InMemoryStorage",
    "AuthManager",
    "Credential",
    "AgentConfig",
    "AgentFactory",
    "AgentLoader",
    "AgentRegistry",
    "FileAgentDef",
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
    "DirectoryPluginLoader",
    "Hook",
    "MCPServerConfig",
    "Marketplace",
    "PluginExtensionRegistry",
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
    "ChimeraConfig",
    "DiscriminatedUnion",
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
    # Security
    "CompositeSecurityAnalyzer",
    "ConfirmAboveThreshold",
    "ConfirmationPolicy",
    "LLMSecurityAnalyzer",
    "NeverConfirm",
    "RuleBasedSecurityAnalyzer",
    "SecurityAnalyzer",
    "SecurityRisk",
    # Secrets
    "RedactionMiddleware",
    "SecretDetector",
    "SecretRegistry",
    # Granular token tracking
    "StepUsage",
    "TokenUsage",
]
