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
    EnsureToolCallMiddleware,
    InstructionLayer,
    LoggingMiddleware,
    LoopDetector,
    LoopMiddleware,
    MiddlewareChain,
    PrintStreamHandler,
    Prompt,
    ReAct,
    SafetyNetMiddleware,
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
from chimera.env.watcher import FileWatcher, FileChange as WatcherFileChange

try:
    from chimera.env import CloudEnvironment, RemoteEnvironment  # noqa: F401
except ImportError:
    pass

# Providers
from chimera.providers import ModelConfig, Provider, ProviderCatalog, Response, StreamEvent, create_provider
from chimera.providers.cached import CachedProvider, CacheStats
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
    CompositeGrader,
    EvalResult,
    FileExistsGrader,
    GradeResult,
    Grader,
    Harness,
    LLMRubricGrader,
    OverfitSignal,
    PatternMatchGrader,
    SchemaGrader,
    TaskEvalResult,
    TestPassGrader,
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
from chimera.tools.web_search import WebSearchTool
from chimera.tools.grounded_search import GroundedSearchTool
from chimera.tools.codebase_index import SemanticSearchTool, CodebaseIndex
from chimera.tools.embedding_index import EmbeddingIndex

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
from chimera.compaction.smart import SmartCompaction, SmartCompactionConfig
from chimera.compaction.thought_strip import ThoughtStripCompaction
from chimera.detection import DetectionResult, ExactRepeatDetector
from chimera.permissions import AuditLog, PermissionAction, PermissionRuleset, RiskLevel, classify_risk
from chimera.permissions.interactive import InteractiveApprover, ApprovalMemory, ApprovalDecision
from chimera.streaming import ConsoleStreamHandler, StreamingReAct
from chimera.sessions import EventLog, EventSourcedSession, InMemoryStorage, LongTermMemory, MemoryEntry, Session
from chimera.auth import AuthManager, Credential
from chimera.agents import AgentConfig, AgentFactory, AgentLoader, AgentPreset, AgentRegistry, FileAgentDef
from chimera.agents.investigator import InvestigatorAgent, Investigation
from chimera.agents.microagent import MicroagentSpawner, MicroagentConfig
from chimera.agents.loader import create_default_registry, load_custom_agents
from chimera.agents.dispatch import Complexity, Dispatcher, ForceRoute, RequestClassifier
from chimera.plugins import BasePlugin, DirectoryPluginLoader, Hook, MCPServerConfig, Marketplace, MarketplaceRegistry, PluginExtensionRegistry, PluginInfo, PluginManager
from chimera.config import ChimeraConfig, DiscriminatedUnion, ProjectConfig, Skill, SkillRegistry, StructuredOutput
from chimera.mcp import MCPClient, MCPToolSource

# Discipline
from chimera.discipline import (
    BOUNDED_EXPLORATION,
    BOUNDED_RETRY,
    DepthGuard,
    DisciplineGuard,
    DisciplinePattern,
    DisciplineViolation,
    Gate,
    GuardResult,
    InstructionAnchor,
    Phase,
    PhasedWorkflow,
    RetryBudgetGuard,
    SCOPE_ONLY,
    ScopeGuard,
    STRICT,
    VERIFY_FIRST,
    VerificationGuard,
)

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
from chimera.review import (
    BUILTIN_PERSPECTIVES,
    PerspectiveRegistry,
    ReviewComment,
    ReviewFeedback,
    ReviewOrchestrator,
    ReviewPerspective,
    Severity as ReviewSeverity,
)

# Research
from chimera.research import Finding, ResearchPlan, Researcher, Source

# Migration
from chimera.migration import MigrationPlan, MigrationPlanner, MigrationRule

# Learning
from chimera.learning import (
    CATEGORY_THRESHOLDS,
    FeedbackTracker,
    LearningInjector,
    LearningStore,
    MetricsCollector,
    Observation,
    ObservationCategory,
    SessionMetrics,
)

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
from chimera.context.repo_map import RepoMapMiddleware, generate_repo_map
from chimera.context.cache import ContextCache, CacheEntry

# Message Queue
from chimera.core.message_queue import MessageQueue
from chimera.core.queue_middleware import MessageQueueMiddleware

# Core extensions (Phase 36-38)
from chimera.core.truncation import TruncationConfig, truncate_output, truncate_result_output
from chimera.core.controller import AgentController, AgentState, StateTransition
from chimera.core.trajectory import Trajectory, TrajectoryStep, filter_successful, sort_by_cost
from chimera.core.proposed_edit import EditProposal, ProposedEdit, EditStatus
from chimera.core.apply_middleware import ApplyMiddleware
from chimera.core.lsp_feedback import LSPFeedbackMiddleware

# Checkpoints
from chimera.checkpoints import CheckpointInfo, CheckpointManager
from chimera.checkpoints_ghost import GhostCommitManager, GhostSnapshot

# Server
from chimera.server import AgentServer, WebhookEvent

# Workflows
from chimera.workflows import CommitStrategy, GitWorkflow
from chimera.workflows.commit_style import CommitStyle, infer_and_generate, analyze_style

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
    "EnsureToolCallMiddleware",
    "InstructionLayer",
    "LoggingMiddleware",
    "LoopDetector",
    "LoopMiddleware",
    "MiddlewareChain",
    "PrintStreamHandler",
    "Prompt",
    "ReAct",
    "SafetyNetMiddleware",
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
    "CompositeGrader",
    "EvalResult",
    "FileExistsGrader",
    "GradeResult",
    "Grader",
    "Harness",
    "LLMRubricGrader",
    "OverfitSignal",
    "PatternMatchGrader",
    "SchemaGrader",
    "TaskEvalResult",
    "TestPassGrader",
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
    "AgentPreset",
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
    # Server
    "AgentServer",
    "WebhookEvent",
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
    "BUILTIN_PERSPECTIVES",
    "PerspectiveRegistry",
    "ReviewComment",
    "ReviewFeedback",
    "ReviewOrchestrator",
    "ReviewPerspective",
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
    # Message Queue
    "MessageQueue",
    "MessageQueueMiddleware",
    # Granular token tracking
    "StepUsage",
    "TokenUsage",
    # Phase 36-38 modules
    # Tools
    "WebSearchTool",
    "GroundedSearchTool",
    "SemanticSearchTool",
    "CodebaseIndex",
    "EmbeddingIndex",
    # Permissions
    "InteractiveApprover",
    "ApprovalMemory",
    "ApprovalDecision",
    # Compaction
    "SmartCompaction",
    "SmartCompactionConfig",
    "ThoughtStripCompaction",
    # Providers
    "CachedProvider",
    "CacheStats",
    # Checkpoints
    "GhostCommitManager",
    "GhostSnapshot",
    # Agents
    "InvestigatorAgent",
    "Investigation",
    "MicroagentSpawner",
    "MicroagentConfig",
    # Dispatch
    "Complexity",
    "Dispatcher",
    "ForceRoute",
    "RequestClassifier",
    # Context
    "RepoMapMiddleware",
    "generate_repo_map",
    "ContextCache",
    "CacheEntry",
    # Core extensions
    "TruncationConfig",
    "truncate_output",
    "truncate_result_output",
    "AgentController",
    "AgentState",
    "StateTransition",
    "Trajectory",
    "TrajectoryStep",
    "filter_successful",
    "sort_by_cost",
    "EditProposal",
    "ProposedEdit",
    "EditStatus",
    "ApplyMiddleware",
    "LSPFeedbackMiddleware",
    # Workflows
    "CommitStyle",
    "infer_and_generate",
    "analyze_style",
    # Environment
    "FileWatcher",
    "WatcherFileChange",
    # Discipline
    "BOUNDED_EXPLORATION",
    "BOUNDED_RETRY",
    "DepthGuard",
    "DisciplineGuard",
    "DisciplinePattern",
    "DisciplineViolation",
    "Gate",
    "GuardResult",
    "InstructionAnchor",
    "Phase",
    "PhasedWorkflow",
    "RetryBudgetGuard",
    "SCOPE_ONLY",
    "ScopeGuard",
    "STRICT",
    "VERIFY_FIRST",
    "VerificationGuard",
    # Learning
    "CATEGORY_THRESHOLDS",
    "FeedbackTracker",
    "LearningInjector",
    "LearningStore",
    "MetricsCollector",
    "Observation",
    "ObservationCategory",
    "SessionMetrics",
]
