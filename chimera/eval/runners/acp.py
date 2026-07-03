"""ACPRunner — drive an external agent over Agent Client Protocol.

Reuses :class:`chimera.acp.client.ACPClient` (JSON-RPC 2.0 over subprocess
stdio) to attempt one benchmark task and normalize the reply behind the
:class:`~chimera.eval.runners.base.AgentRunner` protocol. This lifts the ACP
capability out of ``ExternalAgentTool`` / ``teammate_runner`` (where it is used
as a *tool* / team driver) and into a first-class matrix row. See
``docs/specs/agent-benchmark-matrix.md`` (A2).

The ACP client is injectable via ``client_factory`` so the runner is
unit-testable without any real subprocess, network, or LLM. These are honest
scaffolds: live execution requires the external ACP server installed, and real
end-to-end verification happens later with real infra.

Cost is taken only from the agent's own ACP ``usageUpdate`` (never fabricated);
when the agent reports no cost, ``cost_usd`` stays ``0.0`` and
``raw["cost"] == "unknown"``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

from chimera.eval.runners.base import AgentRunResult

if TYPE_CHECKING:
    from chimera.acp.types import ACPSessionConfig
    from chimera.env.base import Environment

_PROMPT_KEYS = ("prompt", "problem", "question", "instruction", "task")

#: A factory that returns an ACP-client-like object. The real client is
#: :class:`chimera.acp.client.ACPClient`; tests pass a fake exposing the same
#: ``start`` / ``send_message`` / ``stop`` methods.
ACPClientFactory = Callable[[], Any]


def _prompt_of(task: Any) -> str:
    """Extract the prompt string from a benchmark task.

    Args:
        task: A prompt string, a dict with a ``prompt``/``problem`` (etc.)
            key, or an object exposing one of those attributes.

    Returns:
        The prompt text, or ``str(task)`` as a last resort.
    """
    if isinstance(task, str):
        return task
    if isinstance(task, dict):
        for key in _PROMPT_KEYS:
            val = task.get(key)
            if isinstance(val, str) and val:
                return val
        return str(task)
    for attr in _PROMPT_KEYS:
        val = getattr(task, attr, None)
        if isinstance(val, str) and val:
            return val
    return str(task)


class ACPRunner:
    """Attempt a task by driving an external agent over ACP.

    Args:
        id: Row label for the matrix.
        config: The :class:`~chimera.acp.types.ACPSessionConfig` describing the
            ACP server command to spawn (e.g. ``command=["opencode", "acp"]``).
        client_factory: Zero-argument callable returning a live ACP client.
            Defaults to ``lambda: ACPClient(config)``. Injectable so tests pass
            a fake that records ``start`` / ``stop`` and returns a canned reply.
    """

    def __init__(
        self,
        id: str,
        config: ACPSessionConfig,
        client_factory: ACPClientFactory | None = None,
    ) -> None:
        self.id = id
        self.config = config
        self._client_factory = client_factory

    def _make_client(self) -> Any:
        """Construct the ACP client, defaulting to the real ``ACPClient``."""
        if self._client_factory is not None:
            return self._client_factory()
        from chimera.acp.client import ACPClient

        return ACPClient(self.config)

    def run(
        self,
        task: Any,
        env: Environment | None = None,
        budget: Any = None,
    ) -> AgentRunResult:
        """Start the ACP session, send the prompt, and normalize the reply.

        The client is always stopped (in a ``finally``), even when ``start`` or
        ``send_message`` raises — no orphaned subprocess survives an error.

        Args:
            task: A benchmark task (prompt string, dict, or object).
            env: Optional environment; recorded in ``raw`` but not used to place
                execution (the ACP server manages its own workspace).
            budget: Optional budget spec. This runner cannot honor a tool-call
                budget (the external agent does not route through Chimera's tool
                executor), so it is recorded in ``raw`` but not enforced.

        Returns:
            An :class:`AgentRunResult` with ``answer`` from the ACP text,
            ``tool_calls`` from the reply's tool-call count, ``status``
            ``completed`` on success or ``error`` on any exception, and
            ``cost_usd`` from the agent's reported cost (``0.0`` +
            ``raw["cost"] == "unknown"`` when unreported).
        """
        prompt = _prompt_of(task)
        raw: dict[str, Any] = {
            "budget": None if budget is None else repr(budget),
            "env": None if env is None else type(env).__name__,
        }
        answer = ""
        cost = 0.0
        tool_calls = 0
        status = "completed"

        client = self._make_client()
        started = time.monotonic()
        try:
            client.start()
            response = client.send_message(prompt)
            answer = str(getattr(response, "text", "") or "")
            cost = float(getattr(response, "cost", 0.0) or 0.0)
            tool_calls = len(getattr(response, "tool_calls", None) or [])
            raw.update(
                {
                    "input_tokens": int(getattr(response, "input_tokens", 0) or 0),
                    "output_tokens": int(getattr(response, "output_tokens", 0) or 0),
                    "thoughts": len(getattr(response, "thoughts", None) or []),
                }
            )
        except Exception as exc:  # noqa: BLE001 - map to an honest error result
            status = "error"
            raw["error"] = repr(exc)
        finally:
            try:
                client.stop()
            except Exception as exc:  # noqa: BLE001 - best-effort teardown
                raw.setdefault("stop_error", repr(exc))

        # Never fabricate cost: a non-positive/unreported cost is flagged.
        if cost <= 0.0:
            raw["cost"] = "unknown"

        return AgentRunResult(
            answer=answer,
            cost_usd=cost,
            tool_calls=tool_calls,
            llm_calls=0,  # ACP does not expose an API-turn count
            wall_clock_sec=time.monotonic() - started,
            status=status,
            raw=raw,
        )
