from __future__ import annotations

__version__ = "0.1.0"

# Core
from chimera.core import (
    AGENT_TOOLS,
    Agent,
    AllowList,
    AlwaysDeny,
    ApprovalPolicy,
    AutoApprove,
    BaseTool,
    CollectStreamHandler,
    Context,
    ContextAwareTool,
    ContextCompressor,
    DEFAULT_TOOLS,
    InstructionLayer,
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
    from chimera.env import CloudEnvironment, RemoteEnvironment  # noqa: F401
except ImportError:
    pass

# Providers
from chimera.providers import ModelConfig, Provider, ProviderCatalog, Response, StreamEvent, create_provider
from chimera.providers.cost import calculate_cost, estimate_cost, register_model_cost
from chimera.providers.cost_tracker import CostLimitExceeded, CostTracker, StepUsage, TokenUsage

# Training
from chimera.training import Architecture, Constraint, Layer, SearchSpace, Spec, SynthesisTuner, Trainer, TrialResult, TunerResult, ValidationResult, ValidationSplit
from chimera.training.callbacks import (
    CheckpointCallback,
    CostLimitCallback,
    ProgressBar,
    ProgressCallback,
)
from chimera.training.oracle import OracleCallback
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
from chimera.tools.think import ThinkTool
from chimera.tools.ask_user import AskUserTool
from chimera.tools.todo import TodoTool
from chimera.tools.dmail import DMailTool
from chimera.tools.definition_lookup import DefinitionLookupTool

# Wire
from chimera.wire import Wire, WireMessage, WireRequest, WireResponse

# Skills
from chimera.skills.flow import Flow, FlowEdge, FlowError, FlowNode, parse_choice

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
from chimera.sessions import EventLog, EventSourcedSession, InMemoryStorage, LongTermMemory, MemoryEntry, Session
from chimera.auth import AuthManager, Credential
from chimera.agents import AgentConfig, AgentFactory, AgentLoader, AgentRegistry, FileAgentDef
from chimera.agents.loader import create_default_registry, load_custom_agents
from chimera.plugins import BasePlugin, DirectoryPluginLoader, Hook, MCPServerConfig, Marketplace, MarketplaceRegistry, PluginExtensionRegistry, PluginInfo, PluginManager
from chimera.config import ChimeraConfig, DiscriminatedUnion, ProjectConfig, Skill, SkillRegistry, StructuredOutput
from chimera.mcp import MCPClient, MCPToolSource

# Security
from chimera.security import (
    AccessLevel,
    CompositeSecurityAnalyzer,
    ConfirmAboveThreshold,
    ConfirmationPolicy,
    LLMSecurityAnalyzer,
    NetworkRule,
    NeverConfirm,
    PathRule,
    RuleBasedSecurityAnalyzer,
    SandboxPolicy,
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

# Context
from chimera.context.focus import ContextItem, FocusChain
from chimera.context.history import (
    CompressProcessor,
    CompositeProcessor,
    HistoryProcessor,
    PruneProcessor,
    TruncateProcessor,
)
from chimera.context.mentions import Mention, MentionResolver

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
    "AGENT_TOOLS",
    "Agent",
    "AllowList",
    "AlwaysDeny",
    "ApprovalPolicy",
    "AutoApprove",
    "BaseTool",
    "CollectStreamHandler",
    "Context",
    "ContextAwareTool",
    "ContextCompressor",
    "DEFAULT_TOOLS",
    "InstructionLayer",
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
    "OracleCallback",
    "Passthrough",
    "ProgressBar",
    "ProgressCallback",
    "Spec",
    "Strategy",
    "SynthesisResult",
    "TestConvergence",
    "Trainer",
    "TreeSearch",
    "TrialResult",
    "TunerResult",
    "SearchSpace",
    "SynthesisTuner",
    "ValidationResult",
    "ValidationSplit",
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
    "AskUserTool",
    "BrowserTool",
    "DefinitionLookupTool",
    "DMailTool",
    "ImageReadTool",
    "ImportEdge",
    "ImportGraph",
    "RepoMapTool",
    "ThinkTool",
    "TodoTool",
    # Wire
    "Wire",
    "WireMessage",
    "WireRequest",
    "WireResponse",
    # Skills
    "Flow",
    "FlowEdge",
    "FlowError",
    "FlowNode",
    "parse_choice",
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
    "LongTermMemory",
    "MemoryEntry",
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
    # Context
    "CompressProcessor",
    "CompositeProcessor",
    "ContextItem",
    "FocusChain",
    "HistoryProcessor",
    "Mention",
    "MentionResolver",
    "PruneProcessor",
    "TruncateProcessor",
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
    "MarketplaceRegistry",
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
    "AccessLevel",
    "CompositeSecurityAnalyzer",
    "ConfirmAboveThreshold",
    "ConfirmationPolicy",
    "LLMSecurityAnalyzer",
    "NetworkRule",
    "NeverConfirm",
    "PathRule",
    "RuleBasedSecurityAnalyzer",
    "SandboxPolicy",
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
