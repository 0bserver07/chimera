"""SWE-bench **Verified** adapter (issue #84).

The Verified split is a 500-task human-validated subset of SWE-bench Full
with cleaner problem statements, deterministic test specifications, and
fewer flaky / over-specified instances than Lite. The harness contract is
identical to ``SWEBench`` (Lite); the differences live in the *agent
configuration* this adapter recommends:

* **max-step budget** — Verified runs are typically given a much larger
  step budget (default ``500``) so the agent can iterate over patch +
  test cycles. Lite runs default to ``100``.
* **IPython REPL tool** — the agent benefits from a stateful IPython
  shell so it can reproduce, instrument, and verify fixes without
  re-importing modules from scratch on every step.
* **LLM condensation** — when the conversation gets long, an
  ``SummaryCompaction`` instance is used to summarise older turns
  every N steps so the working window stays focused on the current
  hypothesis.

This module ships **only configuration plumbing**, plus a
:meth:`SWEBenchVerified.recommended_loop_config` helper that returns a
populated :class:`~chimera.core.loop_config.LoopConfig` (when that module
is importable). Callers are still responsible for the actual harness run
and Docker image management.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chimera.eval.benchmarks.swe_bench import SWEBench, SWEBenchInstance

if TYPE_CHECKING:
    from chimera.compaction.summary import SummaryCompaction
    from chimera.core.tool import BaseTool
    from chimera.providers.base import Provider


# Default step budgets. Lite was tuned at 100; Verified is given 5x more
# room to iterate per the project memory note in CLAUDE.md.
DEFAULT_LITE_MAX_STEPS = 100
DEFAULT_VERIFIED_MAX_STEPS = 500

# How often (in steps) to fire the LLM-condensation pass. 25 keeps the
# context tight without thrashing the summary provider.
DEFAULT_CONDENSE_EVERY_N_STEPS = 25


@dataclass
class SWEBenchConfig:
    """Recommended runtime configuration for a SWE-bench run.

    This is *advisory* configuration that callers can plug into their
    harness / agent. It is intentionally framework-light so it can be
    consumed by any caller (LoopConfig builders, custom harnesses, or
    test code).

    Attributes:
        variant: ``"lite"`` or ``"verified"``.
        max_steps: Per-task step budget (Verified defaults to 500).
        ipython: Whether to attach an IPython REPL tool to the agent.
        condense_every_n_steps: Trigger LLM condensation every N steps.
            ``0`` disables condensation.
        condense_keep_first: Messages to keep verbatim from the start
            of the conversation when condensing.
        condense_keep_last: Messages to keep verbatim from the end.
    """

    variant: str = "verified"
    max_steps: int = DEFAULT_VERIFIED_MAX_STEPS
    ipython: bool = True
    condense_every_n_steps: int = DEFAULT_CONDENSE_EVERY_N_STEPS
    condense_keep_first: int = 2
    condense_keep_last: int = 20
    extra_tools: list[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.variant not in ("lite", "verified"):
            raise ValueError(
                f"variant must be 'lite' or 'verified', got {self.variant!r}"
            )
        if self.max_steps <= 0:
            raise ValueError(f"max_steps must be > 0, got {self.max_steps}")
        if self.condense_every_n_steps < 0:
            raise ValueError(
                "condense_every_n_steps must be >= 0, "
                f"got {self.condense_every_n_steps}"
            )

    @classmethod
    def for_lite(cls, **overrides: Any) -> "SWEBenchConfig":
        """Build the recommended Lite config (max_steps=100, no IPython)."""
        defaults: dict[str, Any] = {
            "variant": "lite",
            "max_steps": DEFAULT_LITE_MAX_STEPS,
            "ipython": False,
            "condense_every_n_steps": 0,
        }
        defaults.update(overrides)
        return cls(**defaults)

    @classmethod
    def for_verified(cls, **overrides: Any) -> "SWEBenchConfig":
        """Build the recommended Verified config (500 steps, IPython on)."""
        defaults: dict[str, Any] = {
            "variant": "verified",
            "max_steps": DEFAULT_VERIFIED_MAX_STEPS,
            "ipython": True,
            "condense_every_n_steps": DEFAULT_CONDENSE_EVERY_N_STEPS,
        }
        defaults.update(overrides)
        return cls(**defaults)


class SWEBenchVerified(SWEBench):
    """SWE-bench Verified adapter.

    Inherits the full :class:`SWEBench` loader/evaluator (the JSONL
    schema is identical between Lite and Verified) and layers on
    Verified-specific configuration helpers.

    Args:
        dataset_path: Path to JSONL / JSON dataset. Verified upstream
            file is typically ``swe-bench-verified.jsonl``.
        limit: Maximum number of tasks to load.
        split: Dataset split (``"test"`` for the Verified test split).
        max_steps: Per-task step budget. Defaults to 500.
        ipython: Whether to surface the IPython REPL tool. Defaults to
            ``True`` for Verified.
        condense_every_n_steps: How often to LLM-condense the
            conversation. ``0`` to disable.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
        split: str = "test",
        max_steps: int = DEFAULT_VERIFIED_MAX_STEPS,
        ipython: bool = True,
        condense_every_n_steps: int = DEFAULT_CONDENSE_EVERY_N_STEPS,
    ) -> None:
        super().__init__(dataset_path=dataset_path, limit=limit, split=split)
        self._config = SWEBenchConfig(
            variant="verified",
            max_steps=max_steps,
            ipython=ipython,
            condense_every_n_steps=condense_every_n_steps,
        )

    def name(self) -> str:
        return "swe-bench-verified"

    @property
    def config(self) -> SWEBenchConfig:
        """Return the runtime configuration for this run."""
        return self._config

    @property
    def max_steps(self) -> int:
        return self._config.max_steps

    @property
    def ipython_enabled(self) -> bool:
        return self._config.ipython

    def build_ipython_tool(self) -> "BaseTool | None":
        """Construct the IPython tool when enabled.

        Returns ``None`` when ``ipython`` is disabled. The actual tool
        class is imported lazily so the optional dependency surface is
        kept off the import path of callers who do not need it.
        """
        if not self._config.ipython:
            return None
        try:
            from chimera.tools.ipython import IPythonTool
        except ImportError:  # pragma: no cover - tool is in-tree
            return None
        return IPythonTool()

    def build_condensation(
        self,
        provider: "Provider | None" = None,
    ) -> "SummaryCompaction | None":
        """Construct an :class:`SummaryCompaction` matching this config.

        Returns ``None`` when condensation is disabled
        (``condense_every_n_steps == 0``). Otherwise returns a
        compaction strategy with the keep-first / keep-last bounds from
        the config; the provider is optional (falls back to the simple
        role-counter summary when missing).
        """
        if self._config.condense_every_n_steps == 0:
            return None
        from chimera.compaction.summary import SummaryCompaction

        return SummaryCompaction(
            provider=provider,
            keep_first=self._config.condense_keep_first,
            keep_last=self._config.condense_keep_last,
        )

    def should_condense(self, current_step: int) -> bool:
        """Return ``True`` when step ``current_step`` should fire condensation.

        The harness or agent loop calls this after every step. The
        first step is never a condense step (we need *some* history to
        summarize), and we trigger only on multiples of N.
        """
        n = self._config.condense_every_n_steps
        if n <= 0 or current_step <= 0:
            return False
        return current_step % n == 0

    def prepare_agent(self, agent: Any) -> None:
        """Wire IPython + condensation onto an existing agent in place.

        This is the runtime hookup that the :class:`Harness` calls once
        before running the task list. It mutates ``agent`` so that:

        * When :attr:`SWEBenchConfig.ipython` is ``True``, an
          :class:`~chimera.tools.ipython.IPythonTool` is appended to
          ``agent.tools`` (deduplicated by tool name).
        * When :attr:`SWEBenchConfig.condense_every_n_steps` is non-zero
          and the agent has a ``loop`` with a ``config`` attribute, the
          loop's :class:`~chimera.core.loop_config.LoopConfig` has
          ``condensation`` and ``condense_every_n_steps`` populated
          from this benchmark's config (using the agent's ``provider``
          as the summary back-end when available).
        * When :attr:`SWEBenchConfig.max_steps` is set and the agent has
          a ``loop`` with ``max_steps``, the loop's budget is bumped to
          match (but never reduced below the existing value).

        Idempotent: re-running ``prepare_agent`` will not duplicate the
        IPython tool or stomp existing condensation settings unless they
        match this benchmark's expectations.

        Args:
            agent: An object that exposes a ``tools`` list, optional
                ``loop`` (with ``config`` and ``max_steps`` attributes),
                and optional ``provider``.
        """
        # 1. IPython tool — append if missing.
        if self._config.ipython:
            tool = self.build_ipython_tool()
            if tool is not None:
                tools = getattr(agent, "tools", None)
                if tools is not None:
                    has_ipython = any(
                        getattr(t, "name", None) == tool.name for t in tools
                    )
                    if not has_ipython:
                        tools.append(tool)

        # 2. Condensation + max-steps — wire onto the loop's LoopConfig.
        loop = getattr(agent, "loop", None)
        if loop is None:
            return

        # Bump max_steps when the loop's budget is below ours.
        existing_max = getattr(loop, "max_steps", None)
        if isinstance(existing_max, int) and existing_max < self._config.max_steps:
            loop.max_steps = self._config.max_steps

        if self._config.condense_every_n_steps == 0:
            return

        loop_config = getattr(loop, "config", None)
        if loop_config is None:
            return

        provider = getattr(agent, "provider", None)
        if getattr(loop_config, "condensation", None) is None:
            loop_config.condensation = self.build_condensation(provider=provider)
        if getattr(loop_config, "condense_every_n_steps", None) is None:
            loop_config.condense_every_n_steps = (
                self._config.condense_every_n_steps
            )


__all__ = [
    "SWEBenchVerified",
    "SWEBenchConfig",
    "SWEBenchInstance",
    "DEFAULT_LITE_MAX_STEPS",
    "DEFAULT_VERIFIED_MAX_STEPS",
    "DEFAULT_CONDENSE_EVERY_N_STEPS",
]
