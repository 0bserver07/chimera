"""LoopConfig: optional configuration injected into all loop variants."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.checkpoints import CheckpointManager
    from chimera.checkpoints_ghost import GhostCommitManager
    from chimera.compaction.base import CompactionStrategy
    from chimera.compaction.summary import SummaryCompaction
    from chimera.core.cancellation import CancellationToken
    from chimera.core.file_tracker import FileTracker
    from chimera.core.message_queue import MessageQueues
    from chimera.core.middleware import LoopMiddleware
    from chimera.core.truncation import TruncationConfig
    from chimera.detection.actions import LoopDetector
    from chimera.discipline.anchor import InstructionAnchor
    from chimera.discipline.guard import DisciplineGuard
    from chimera.events.base import EventBus
    from chimera.hooks.emitter import HookEmitter
    from chimera.learning.feedback import FeedbackTracker
    from chimera.learning.injector import LearningInjector
    from chimera.learning.store import LearningStore
    from chimera.permissions.audit import AuditLog
    from chimera.permissions.base import PermissionPolicy
    from chimera.lsp.manager import LSPManager
    from chimera.providers.cost_tracker import CostTracker
    from chimera.streaming.base import StreamHandler
    from chimera.wire.wire import Wire
    from chimera.workflows.git_workflow import GitWorkflow

__all__ = ["LoopConfig", "UNSAFE_ENV_VAR"]


#: Environment-variable escape hatch.  When set to ``"1"``, ``"true"``, or
#: ``"yes"`` (case-insensitive), the safety defaults applied by
#: :class:`LoopConfig.__post_init__` are skipped.  Intended for CI and
#: internal use; never set in production.
UNSAFE_ENV_VAR = "CHIMERA_UNSAFE"


def _unsafe_env_set() -> bool:
    val = os.environ.get(UNSAFE_ENV_VAR, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


@dataclass
class LoopConfig:
    """Optional behaviour injections for any loop variant.

    Safety is **on by default**: when :attr:`permissions` is ``None`` and
    :attr:`yolo_mode` is ``False``, the dataclass installs a safe
    :class:`~chimera.permissions.presets.Interactive` policy that ASKs
    before write / destructive tool calls.  Similarly, when an event bus
    is attached and :attr:`secrets_redaction` is not explicitly disabled,
    a :class:`~chimera.secrets.RedactionMiddleware` is wired so token-
    shaped strings never leak into subscribers (logs, telemetry, UI).

    Opt-outs (all backward-compatible):

    * ``yolo_mode=True`` — disables default permissions; tools run freely.
    * ``permissions=AutoApprove()`` — explicit policy of any kind also
      short-circuits the default.
    * ``secrets_redaction=False`` — disables auto-wired redaction.
    * ``CHIMERA_UNSAFE=1`` environment variable — disables ALL safety
      defaults process-wide (for CI / internal benchmarking).

    Example::

        config = LoopConfig(
            permissions=PermissionRuleset(rules=[...]),
            detector=LoopDetector(on_detect=OnDetect.WARN),
            handler=ConsoleStreamHandler(),
            event_bus=EventBus(),
        )
        loop = ReAct(max_steps=50, config=config)
    """

    permissions: PermissionPolicy | None = None
    detector: LoopDetector | None = None
    compaction: CompactionStrategy | None = None
    handler: StreamHandler | None = None
    event_bus: EventBus | None = None
    auto_compact_threshold: float = 0.8
    lsp: LSPManager | None = None
    cost_tracker: CostTracker | None = None
    audit_log: AuditLog | None = None
    checkpoint_manager: CheckpointManager | None = None
    git_workflow: GitWorkflow | None = None
    wire: Wire | None = None
    middleware: list[LoopMiddleware] | None = None
    truncation: TruncationConfig | None = None
    ghost_commits: GhostCommitManager | None = None
    file_tracker: FileTracker | None = None
    cancellation: CancellationToken | None = None
    message_queues: MessageQueues | None = None
    discipline: list[DisciplineGuard] | None = None
    instruction_anchor: InstructionAnchor | None = None
    learning: LearningStore | None = None
    feedback_tracker: FeedbackTracker | None = None
    learning_injector: LearningInjector | None = None
    # Hook emitter — when set, PreToolUse hooks fire from tool_executor and
    # may mutate args (updatedInput) or override permissions
    # (permissionDecision = allow|deny|ask).
    hook_emitter: HookEmitter | None = None
    # Per-tool-call timeout (seconds). When set, each tool dispatch in the
    # async executor is wrapped in ``asyncio.wait_for(...)``; on timeout the
    # tool returns a synthetic error result so the loop can continue rather
    # than crashing the whole run. ``None`` (default) disables the wrap.
    # Audit H-4. See chimera/core/tool_executor.py:async_execute_tool_calls_incremental.
    tool_timeout_s: float | None = None

    # -- LLM-condensation (M11) --
    # When BOTH fields are set, ``ReAct.async_iter_steps`` will run the
    # conversation through ``condensation.compact(...)`` every
    # ``condense_every_n_steps`` steps (1-indexed; first trigger at step
    # ``N``).  This is the runtime hookup for the ``should_condense``
    # contract exposed by SWE-bench Verified's adapter
    # (see ``chimera/eval/benchmarks/swe_bench_verified.py``).  Both fields
    # default to ``None`` so existing loops are unaffected.
    condensation: SummaryCompaction | None = None
    condense_every_n_steps: int | None = None

    # -- Safety-default controls (opt-out) --
    yolo_mode: bool = False
    secrets_redaction: bool | None = None
    # Marker: True iff the permission policy was installed by __post_init__.
    # Consumers (tests, audit tooling) may inspect it; never set it manually.
    _default_permissions_applied: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        """Install safety defaults unless the caller has opted out.

        The cost of this method is a single env-var lookup plus one
        attribute comparison per field — negligible vs. the network
        latency of a provider call.
        """
        if _unsafe_env_set():
            # Global escape hatch: do nothing, preserve legacy behaviour.
            return

        # -- Default permission policy --
        if self.permissions is None and not self.yolo_mode:
            # Local import keeps the module import-light and avoids
            # any chance of circular imports at package init time.
            from chimera.permissions.presets import Interactive

            self.permissions = Interactive()
            self._default_permissions_applied = True

        # -- Default redaction middleware --
        # Only attach when there's an event bus to attach to AND the
        # caller hasn't explicitly set secrets_redaction=False.
        if self.event_bus is not None and self.secrets_redaction is not False:
            from chimera.secrets.detector import SecretDetector
            from chimera.secrets.redactor import RedactionMiddleware
            from chimera.secrets.registry import SecretRegistry

            # Avoid double-wiring if the caller already attached one.
            already = any(
                isinstance(mw, RedactionMiddleware)
                for mw in getattr(self.event_bus, "_middlewares", [])
            )
            if not already:
                registry = SecretRegistry()
                # Best-effort: pick up common provider API keys so they
                # never leak into event payloads.  Only env vars that
                # are actually set contribute anything.
                registry.register_from_env(
                    "ANTHROPIC_API_KEY",
                    "OPENAI_API_KEY",
                    "GOOGLE_API_KEY",
                    "GEMINI_API_KEY",
                    "Z_AI_API_KEY",
                    "CHIMERA_API_KEY",
                )
                detector = SecretDetector()
                self.event_bus.use(
                    RedactionMiddleware(
                        registry=registry,
                        detector=detector,
                        detect_unknown=True,
                    )
                )
