"""Chimera: compose coding agents from modular primitives.

This module uses PEP 562 lazy attribute loading (``__getattr__``) so that
``import chimera`` does not eagerly import every subsystem. Each symbol listed
in ``__all__`` is materialized on first access by importing it from its source
submodule. This preserves the "each layer works independently" guarantee --
e.g. ``from chimera.providers.base import Provider`` should not transitively
pull in training, evaluation, workflows, etc.

All previously-supported ``from chimera import X`` call sites continue to work
unchanged; the only observable difference is that ``X`` is loaded on demand.
"""

from __future__ import annotations

import sys as _sys
import types as _types
from typing import Any

__version__ = "0.9.1.dev0"

# Map public name -> fully-qualified submodule that owns it.
# Grouped by section to mirror the original eager-import layout.
_LAZY_ATTRS: dict[str, str] = {
    # Core
    "AGENT_TOOLS": "chimera.core",
    "Agent": "chimera.core",
    "AllowList": "chimera.core",
    "AlwaysDeny": "chimera.core",
    "ApprovalPolicy": "chimera.core",
    "AutoApprove": "chimera.core",
    "BaseTool": "chimera.core",
    "CollectStreamHandler": "chimera.core",
    "Context": "chimera.core",
    "ContextAwareTool": "chimera.core",
    "ContextCompressor": "chimera.core",
    "DEFAULT_TOOLS": "chimera.core",
    "EnsureToolCallMiddleware": "chimera.core",
    "InstructionLayer": "chimera.core",
    "LoggingMiddleware": "chimera.core",
    "LoopDetector": "chimera.core",
    "LoopMiddleware": "chimera.core",
    "MiddlewareChain": "chimera.core",
    "PrintStreamHandler": "chimera.core",
    "Prompt": "chimera.core",
    "ReAct": "chimera.core",
    "SafetyNetMiddleware": "chimera.core",
    "StreamHandler": "chimera.core",
    "ToolGroup": "chimera.core",
    "tool": "chimera.core",
    # Loops
    "PlanAndExecute": "chimera.core.loops",
    "Reflexion": "chimera.core.loops",
    "TreeOfThought": "chimera.core.loops",
    # Assembly
    "CodingAgent": "chimera.assembly.coding_agent",
    # Composition
    "Ensemble": "chimera.composition",
    "Pipeline": "chimera.composition",
    "Supervisor": "chimera.composition",
    # Environment
    "Environment": "chimera.env",
    "GitEnvironment": "chimera.env",
    "LocalEnvironment": "chimera.env",
    "SessionMixin": "chimera.env",
    "CloudEnvironment": "chimera.env",
    "RemoteEnvironment": "chimera.env",
    "FileWatcher": "chimera.env.watcher",
    "WatcherFileChange": "chimera.env.watcher",
    # Providers
    "ModelConfig": "chimera.providers",
    "Provider": "chimera.providers",
    "ProviderCatalog": "chimera.providers",
    "Response": "chimera.providers",
    "StreamEvent": "chimera.providers",
    "create_provider": "chimera.providers",
    "CachedProvider": "chimera.providers.cached",
    "CacheStats": "chimera.providers.cached",
    "calculate_cost": "chimera.providers.cost",
    "estimate_cost": "chimera.providers.cost",
    "register_model_cost": "chimera.providers.cost",
    "CostLimitExceeded": "chimera.providers.cost_tracker",
    "CostTracker": "chimera.providers.cost_tracker",
    "StepUsage": "chimera.providers.cost_tracker",
    "TokenUsage": "chimera.providers.cost_tracker",
    # Training
    "Architecture": "chimera.training",
    "Constraint": "chimera.training",
    "Layer": "chimera.training",
    "SearchSpace": "chimera.training",
    "Spec": "chimera.training",
    "SynthesisTuner": "chimera.training",
    "Trainer": "chimera.training",
    "TrialResult": "chimera.training",
    "TunerResult": "chimera.training",
    "ValidationResult": "chimera.training",
    "ValidationSplit": "chimera.training",
    "CheckpointCallback": "chimera.training.callbacks",
    "CostLimitCallback": "chimera.training.callbacks",
    "ProgressBar": "chimera.training.callbacks",
    "ProgressCallback": "chimera.training.callbacks",
    "OracleCallback": "chimera.training.oracle",
    "AIMOEnsemble": "chimera.training.strategies",
    "Callback": "chimera.training.strategies",
    "CurriculumStrategy": "chimera.training.strategies",
    "EnsembleStrategy": "chimera.training.strategies",
    "EpochResult": "chimera.training.strategies",
    "MajorityVoting": "chimera.training.strategies",
    "Passthrough": "chimera.training.strategies",
    "Strategy": "chimera.training.strategies",
    "SynthesisResult": "chimera.training.strategies",
    "TestConvergence": "chimera.training.strategies",
    "TreeSearch": "chimera.training.strategies",
    # Evaluation
    "Benchmark": "chimera.eval",
    "CompositeGrader": "chimera.eval",
    "EvalResult": "chimera.eval",
    "FileExistsGrader": "chimera.eval",
    "GradeResult": "chimera.eval",
    "Grader": "chimera.eval",
    "Harness": "chimera.eval",
    "LLMRubricGrader": "chimera.eval",
    "OverfitSignal": "chimera.eval",
    "PatternMatchGrader": "chimera.eval",
    "SchemaGrader": "chimera.eval",
    "TaskEvalResult": "chimera.eval",
    "TestPassGrader": "chimera.eval",
    "avg_cost": "chimera.eval",
    "avg_steps": "chimera.eval",
    "check_hardcoded_answers": "chimera.eval",
    "check_output_similarity": "chimera.eval",
    "pass_at_k": "chimera.eval",
    "resolve_rate": "chimera.eval",
    # Tools
    "RepoMapTool": "chimera.tools.repo_map",
    "ImageReadTool": "chimera.tools.image_read",
    "ImportEdge": "chimera.tools.import_graph",
    "ImportGraph": "chimera.tools.import_graph",
    "BrowserTool": "chimera.tools.browser",
    "ThinkTool": "chimera.tools.think",
    "AskUserTool": "chimera.tools.ask_user",
    "TodoTool": "chimera.tools.todo",
    "DMailTool": "chimera.tools.dmail",
    "DefinitionLookupTool": "chimera.tools.definition_lookup",
    "WebSearchTool": "chimera.tools.web_search",
    "GroundedSearchTool": "chimera.tools.grounded_search",
    "SemanticSearchTool": "chimera.tools.codebase_index",
    "CodebaseIndex": "chimera.tools.codebase_index",
    "EmbeddingIndex": "chimera.tools.embedding_index",
    # Wire
    "Wire": "chimera.wire",
    "WireMessage": "chimera.wire",
    "WireRequest": "chimera.wire",
    "WireResponse": "chimera.wire",
    # Skills
    "Flow": "chimera.skills.flow",
    "FlowEdge": "chimera.skills.flow",
    "FlowError": "chimera.skills.flow",
    "FlowNode": "chimera.skills.flow",
    "parse_choice": "chimera.skills.flow",
    # Convenience
    "synthesize": "chimera.synthesize",
    # LSP
    "Diagnostic": "chimera.lsp",
    "LSPClient": "chimera.lsp",
    "LSPManager": "chimera.lsp",
    "LSPTool": "chimera.lsp",
    "Severity": "chimera.lsp",
    # Critic
    "ChecklistCritic": "chimera.critic",
    "Critic": "chimera.critic",
    "CriticConfig": "chimera.critic",
    "CriticMixin": "chimera.critic",
    "CriticMode": "chimera.critic",
    "CriticResult": "chimera.critic",
    "LLMCritic": "chimera.critic",
    # ACP
    "ACPClient": "chimera.acp",
    "ACPResponse": "chimera.acp",
    "ACPSessionConfig": "chimera.acp",
    "ACPToolCall": "chimera.acp",
    "ExternalAgentTool": "chimera.acp",
    # Extension modules
    "LoopConfig": "chimera.core.loop_config",
    "Event": "chimera.events",
    "EventBus": "chimera.events",
    "AtomicGroup": "chimera.compaction",
    "CompactionStrategy": "chimera.compaction",
    "CompactionUrgency": "chimera.compaction",
    "CompactionView": "chimera.compaction",
    "InsufficientCompactionError": "chimera.compaction",
    "ThresholdCompaction": "chimera.compaction",
    "TokenCounter": "chimera.compaction",
    "SmartCompaction": "chimera.compaction.smart",
    "SmartCompactionConfig": "chimera.compaction.smart",
    "ThoughtStripCompaction": "chimera.compaction.thought_strip",
    "DetectionResult": "chimera.detection",
    "ExactRepeatDetector": "chimera.detection",
    "AuditLog": "chimera.permissions",
    "PermissionAction": "chimera.permissions",
    "PermissionRuleset": "chimera.permissions",
    "RiskLevel": "chimera.permissions",
    "classify_risk": "chimera.permissions",
    "InteractiveApprover": "chimera.permissions.interactive",
    "ApprovalMemory": "chimera.permissions.interactive",
    "ApprovalDecision": "chimera.permissions.interactive",
    "ConsoleStreamHandler": "chimera.streaming",
    "StreamingReAct": "chimera.streaming",
    "EventLog": "chimera.sessions",
    "EventSourcedSession": "chimera.sessions",
    "InMemoryStorage": "chimera.sessions",
    "LongTermMemory": "chimera.sessions",
    "MemoryEntry": "chimera.sessions",
    "Session": "chimera.sessions",
    "AuthManager": "chimera.auth",
    "Credential": "chimera.auth",
    "AgentConfig": "chimera.agents",
    "AgentFactory": "chimera.agents",
    "AgentLoader": "chimera.agents",
    "AgentPreset": "chimera.agents",
    "AgentRegistry": "chimera.agents",
    "FileAgentDef": "chimera.agents",
    "InvestigatorAgent": "chimera.agents.investigator",
    "Investigation": "chimera.agents.investigator",
    "MicroagentSpawner": "chimera.agents.microagent",
    "MicroagentConfig": "chimera.agents.microagent",
    "create_default_registry": "chimera.agents.loader",
    "load_custom_agents": "chimera.agents.loader",
    "Complexity": "chimera.agents.dispatch",
    "Dispatcher": "chimera.agents.dispatch",
    "ForceRoute": "chimera.agents.dispatch",
    "RequestClassifier": "chimera.agents.dispatch",
    "BasePlugin": "chimera.plugins",
    "DirectoryPluginLoader": "chimera.plugins",
    "Hook": "chimera.plugins",
    "MCPServerConfig": "chimera.plugins",
    "Marketplace": "chimera.plugins",
    "MarketplaceRegistry": "chimera.plugins",
    "PluginExtensionRegistry": "chimera.plugins",
    "PluginInfo": "chimera.plugins",
    "PluginManager": "chimera.plugins",
    "ChimeraConfig": "chimera.config",
    "DiscriminatedUnion": "chimera.config",
    "ProjectConfig": "chimera.config",
    "Skill": "chimera.config",
    "SkillRegistry": "chimera.config",
    "StructuredOutput": "chimera.config",
    "MCPClient": "chimera.mcp",
    "MCPToolSource": "chimera.mcp",
    # Discipline
    "BOUNDED_EXPLORATION": "chimera.discipline",
    "BOUNDED_RETRY": "chimera.discipline",
    "DepthGuard": "chimera.discipline",
    "DisciplineGuard": "chimera.discipline",
    "DisciplinePattern": "chimera.discipline",
    "DisciplineViolation": "chimera.discipline",
    "Gate": "chimera.discipline",
    "GuardResult": "chimera.discipline",
    "InstructionAnchor": "chimera.discipline",
    "Phase": "chimera.discipline",
    "PhasedWorkflow": "chimera.discipline",
    "RetryBudgetGuard": "chimera.discipline",
    "SCOPE_ONLY": "chimera.discipline",
    "ScopeGuard": "chimera.discipline",
    "STRICT": "chimera.discipline",
    "VERIFY_FIRST": "chimera.discipline",
    "VerificationGuard": "chimera.discipline",
    # Security
    "AccessLevel": "chimera.security",
    "CompositeSecurityAnalyzer": "chimera.security",
    "ConfirmAboveThreshold": "chimera.security",
    "ConfirmationPolicy": "chimera.security",
    "LLMSecurityAnalyzer": "chimera.security",
    "NetworkRule": "chimera.security",
    "NeverConfirm": "chimera.security",
    "PathRule": "chimera.security",
    "RuleBasedSecurityAnalyzer": "chimera.security",
    "SandboxPolicy": "chimera.security",
    "SecurityAnalyzer": "chimera.security",
    "SecurityRisk": "chimera.security",
    # Secrets
    "RedactionMiddleware": "chimera.secrets",
    "SecretDetector": "chimera.secrets",
    "SecretRegistry": "chimera.secrets",
    # Docs
    "DocGenerator": "chimera.docs.generator",
    "DocSection": "chimera.docs.generator",
    # TestGen
    "CoverageReport": "chimera.testgen",
    "TestCase": "chimera.testgen",
    "TestGenerator": "chimera.testgen",
    "parse_coverage": "chimera.testgen",
    # CI
    "CIFixWorkflow": "chimera.ci",
    "FailureInfo": "chimera.ci",
    "parse_ci_log": "chimera.ci",
    # Review
    "BUILTIN_PERSPECTIVES": "chimera.review",
    "PerspectiveRegistry": "chimera.review",
    "ReviewComment": "chimera.review",
    "ReviewFeedback": "chimera.review",
    "ReviewOrchestrator": "chimera.review",
    "ReviewPerspective": "chimera.review",
    "ReviewSeverity": "chimera.review",  # aliased from chimera.review.Severity
    # Research
    "Finding": "chimera.research",
    "ResearchPlan": "chimera.research",
    "Researcher": "chimera.research",
    "Source": "chimera.research",
    # Migration
    "MigrationPlan": "chimera.migration",
    "MigrationPlanner": "chimera.migration",
    "MigrationRule": "chimera.migration",
    # Learning
    "CATEGORY_THRESHOLDS": "chimera.learning",
    "FeedbackTracker": "chimera.learning",
    "LearningInjector": "chimera.learning",
    "LearningStore": "chimera.learning",
    "MetricsCollector": "chimera.learning",
    "Observation": "chimera.learning",
    "ObservationCategory": "chimera.learning",
    "SessionMetrics": "chimera.learning",
    # Context
    "ContextItem": "chimera.context.focus",
    "FocusChain": "chimera.context.focus",
    "CompressProcessor": "chimera.context.history",
    "CompositeProcessor": "chimera.context.history",
    "HistoryProcessor": "chimera.context.history",
    "PruneProcessor": "chimera.context.history",
    "TruncateProcessor": "chimera.context.history",
    "Mention": "chimera.context.mentions",
    "MentionResolver": "chimera.context.mentions",
    "RepoMapMiddleware": "chimera.context.repo_map",
    "generate_repo_map": "chimera.context.repo_map",
    "ContextCache": "chimera.context.cache",
    "CacheEntry": "chimera.context.cache",
    # Message Queue
    "MessageQueue": "chimera.core.message_queue",
    "MessageQueueMiddleware": "chimera.core.queue_middleware",
    # Core extensions (Phase 36-38)
    "TruncationConfig": "chimera.core.truncation",
    "truncate_output": "chimera.core.truncation",
    "truncate_result_output": "chimera.core.truncation",
    "AgentController": "chimera.core.controller",
    "AgentState": "chimera.core.controller",
    "StateTransition": "chimera.core.controller",
    "Trajectory": "chimera.core.trajectory",
    "TrajectoryStep": "chimera.core.trajectory",
    "filter_successful": "chimera.core.trajectory",
    "sort_by_cost": "chimera.core.trajectory",
    "EditProposal": "chimera.core.proposed_edit",
    "ProposedEdit": "chimera.core.proposed_edit",
    "EditStatus": "chimera.core.proposed_edit",
    "ApplyMiddleware": "chimera.core.apply_middleware",
    "LSPFeedbackMiddleware": "chimera.core.lsp_feedback",
    # Checkpoints
    "CheckpointInfo": "chimera.checkpoints",
    "CheckpointManager": "chimera.checkpoints",
    "GhostCommitManager": "chimera.checkpoints_ghost",
    "GhostSnapshot": "chimera.checkpoints_ghost",
    # Server
    "AgentServer": "chimera.server",
    "WebhookEvent": "chimera.server",
    # Workflows
    "CommitStrategy": "chimera.workflows",
    "GitWorkflow": "chimera.workflows",
    "CommitStyle": "chimera.workflows.commit_style",
    "infer_and_generate": "chimera.workflows.commit_style",
    "analyze_style": "chimera.workflows.commit_style",
    # Transactions
    "FileTransaction": "chimera.transactions",
    "StagedChange": "chimera.transactions",
    "TransactionState": "chimera.transactions",
    # Types
    "async_drain_steps": "chimera.core.loop",
    "drain_steps": "chimera.core.loop",
    "AgentResult": "chimera.types",
    "ChangeType": "chimera.types",
    "CommandResult": "chimera.types",
    "ContentBlock": "chimera.types",
    "FileChange": "chimera.types",
    "ImageContent": "chimera.types",
    "Message": "chimera.types",
    "PendingApproval": "chimera.types",
    "StepResult": "chimera.types",
    "TextContent": "chimera.types",
    "TestResult": "chimera.types",
    "ToolCall": "chimera.types",
    "ToolResult": "chimera.types",
}

# A few public names are re-exported under an alias that differs from their
# source attribute. Resolve the actual attribute name here.
_ALIASES: dict[str, str] = {
    # `from chimera.review import Severity as ReviewSeverity`
    "ReviewSeverity": "Severity",
    # `from chimera.env.watcher import FileChange as WatcherFileChange`
    "WatcherFileChange": "FileChange",
}

__all__ = sorted(_LAZY_ATTRS.keys())  # pyright: ignore[reportUnsupportedDunderAll]


# Names where a public symbol shares a name with a submodule
# (e.g. ``chimera.synthesize`` is both a function and a submodule). Python's
# import system sets ``chimera.__dict__[name] = <submodule>`` whenever any
# code does ``import chimera.<name>``, which would shadow our ``__getattr__``
# and cause ``chimera.<name>`` to return the module instead of the intended
# attribute. We handle these via a custom module class whose ``__getattribute__``
# always re-resolves, so the correct symbol wins regardless of import order.
_SUBMODULE_COLLISIONS: frozenset[str] = frozenset({"synthesize"})


def _resolve(name: str) -> Any:
    """Import the submodule for ``name`` and return the target attribute."""
    module_path = _LAZY_ATTRS[name]
    from importlib import import_module

    try:
        module = import_module(module_path)
    except ImportError as exc:
        raise AttributeError(
            f"module 'chimera' attribute {name!r} is unavailable: {exc}"
        ) from exc

    source_attr = _ALIASES.get(name, name)
    try:
        return getattr(module, source_attr)
    except AttributeError as exc:
        raise AttributeError(
            f"module 'chimera' attribute {name!r} not found in {module_path!r}"
        ) from exc


def __getattr__(name: str) -> Any:
    """PEP 562 lazy attribute loader.

    Imports the submodule that owns ``name``, fetches the attribute, caches it
    in this module's globals, and returns it. Subsequent accesses hit the cache
    and skip the import machinery entirely.
    """
    if name not in _LAZY_ATTRS:
        raise AttributeError(f"module 'chimera' has no attribute {name!r}")
    value = _resolve(name)
    # Cache non-collision names so future lookups skip __getattr__ entirely.
    if name not in _SUBMODULE_COLLISIONS:
        globals()[name] = value
    return value


def __dir__() -> list[str]:
    return list(__all__)


# ---------------------------------------------------------------------------
# Submodule-collision shim.
# ---------------------------------------------------------------------------
# For names in ``_SUBMODULE_COLLISIONS`` we can't rely on ``__getattr__``
# alone, because Python's import system writes the submodule object into
# ``chimera.__dict__`` whenever it gets imported (e.g. via
# ``import chimera.synthesize``). That shadows ``__getattr__``. We fix this
# by swapping the module's class to one whose ``__getattribute__`` redirects
# collision names to the correct attribute at every access.
class _ChimeraModule(_types.ModuleType):
    def __getattribute__(self, name: str) -> Any:
        if name in _SUBMODULE_COLLISIONS:
            return _resolve(name)
        return super().__getattribute__(name)


_sys.modules[__name__].__class__ = _ChimeraModule
