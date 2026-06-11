"""ATIFEmitter — subscribe to an EventBus and emit an ATIF v1.7 trajectory.

Event mapping (one ATIF step per API turn):

    ModelResponseEvent  -> seals the previous agent step, opens a new one
                           (metrics, model, llm_call_count)
    StepEvent           -> assistant message text for the open step
    ToolCallEvent       -> step.tool_calls[] entry
    ToolResultEvent     -> step.observation.results[] entry
    CompactionEvent     -> summarization_count += 1 (final_metrics.extra)
    AgentEndEvent       -> close() — seal, aggregate, write the file

The emitter never fabricates assistant text: ``step.message`` is exactly
the ``StepEvent.content`` the loop published (the model's own output),
and reasoning is never inlined into the message. Timestamps are ISO 8601
at event receipt — Chimera providers do not surface upstream response
timestamps, so receipt time is the closest non-fabricated source.

Usage::

    bus = EventBus()
    emitter = ATIFEmitter("run.atif.json", agent_name="chimera-react",
                          model_name="glm-5")
    emitter.attach(bus)
    emitter.record_user_message(task_prompt)   # step 1: the instruction
    agent.run(task_prompt, env)                # LoopConfig(event_bus=bus)
    emitter.close()
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from chimera.events.base import Event, EventBus

ATIF_VERSION = "ATIF-v1.7"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ATIFEmitter:
    """Accumulate Chimera run events into one ATIF v1.7 trajectory file.

    Args:
        output_path: Where the trajectory JSON is written on :meth:`close`.
        agent_name: ATIF ``agent.name`` (e.g. ``"chimera-react"``).
        agent_version: ATIF ``agent.version``; defaults to the installed
            chimera version.
        model_name: Root-level model identifier.
        session_id: Run identifier; recommended for root trajectories.
        notes: Optional free-form notes stored on the trajectory.
    """

    def __init__(
        self,
        output_path: str | Path,
        agent_name: str = "chimera",
        agent_version: str | None = None,
        model_name: str | None = None,
        session_id: str | None = None,
        notes: str | None = None,
    ) -> None:
        self.output_path = Path(output_path)
        self._agent: dict[str, Any] = {
            "name": agent_name,
            "version": agent_version or _chimera_version(),
        }
        if model_name:
            self._agent["model_name"] = model_name
        self._session_id = session_id
        self._notes = notes
        self._steps: list[dict[str, Any]] = []
        self._draft: dict[str, Any] | None = None
        self._summarization_count = 0
        self._total_cost = 0.0
        self._closed = False
        self._unsubscribes: list[Callable[[], None]] = []

    # ---- wiring -----------------------------------------------------------

    def attach(self, bus: EventBus) -> None:
        """Subscribe to the run events on ``bus``."""
        for event_type, handler in (
            ("model_response", self._on_model_response),
            ("step", self._on_step),
            ("tool_call", self._on_tool_call),
            ("tool_result", self._on_tool_result),
            ("compaction", self._on_compaction),
            ("step_cost", self._on_step_cost),
            ("agent_end", self._on_agent_end),
        ):
            self._unsubscribes.append(bus.subscribe(event_type, handler))

    def detach(self) -> None:
        """Unsubscribe from the bus (idempotent)."""
        for unsub in self._unsubscribes:
            unsub()
        self._unsubscribes = []

    # ---- manual records ----------------------------------------------------

    def record_user_message(self, text: str) -> None:
        """Record a ``source: "user"`` step (the task instruction)."""
        self._seal_draft()
        self._steps.append(
            {
                "step_id": len(self._steps) + 1,
                "timestamp": _now_iso(),
                "source": "user",
                "message": text,
            }
        )

    def record_system_message(self, text: str) -> None:
        """Record a ``source: "system"`` step (e.g. environment notices)."""
        self._seal_draft()
        self._steps.append(
            {
                "step_id": len(self._steps) + 1,
                "timestamp": _now_iso(),
                "source": "system",
                "message": text,
            }
        )

    # ---- event handlers ----------------------------------------------------

    def _on_model_response(self, event: Event) -> None:
        # One ATIF step per API turn: a new model response seals the
        # previous agent step and opens the next draft.
        self._seal_draft()
        metrics: dict[str, Any] = {}
        input_tokens = getattr(event, "input_tokens", 0)
        output_tokens = getattr(event, "output_tokens", 0)
        if input_tokens:
            metrics["prompt_tokens"] = input_tokens
        if output_tokens:
            metrics["completion_tokens"] = output_tokens
        self._draft = {
            "step_id": len(self._steps) + 1,
            "timestamp": _now_iso(),
            "source": "agent",
            "message": "",
            "llm_call_count": 1,
        }
        model = getattr(event, "model", "")
        if model and model != self._agent.get("model_name"):
            self._draft["model_name"] = model
        if metrics:
            self._draft["metrics"] = metrics

    def _on_step(self, event: Event) -> None:
        # The loop's own record of the assistant text for this turn. A
        # second StepEvent without an intervening model-response boundary
        # means a new turn (loops that don't publish ModelResponseEvent) —
        # seal the previous draft instead of overwriting its message.
        if self._draft is not None and self._draft.get("message"):
            self._seal_draft()
        if self._draft is None:
            self._draft = {
                "step_id": len(self._steps) + 1,
                "timestamp": _now_iso(),
                "source": "agent",
                "message": "",
            }
        self._draft["message"] = getattr(event, "content", "") or ""

    def _on_tool_call(self, event: Event) -> None:
        if self._draft is None:
            self._draft = {
                "step_id": len(self._steps) + 1,
                "timestamp": _now_iso(),
                "source": "agent",
                "message": "",
            }
        self._draft.setdefault("tool_calls", []).append(
            {
                "tool_call_id": getattr(event, "call_id", "") or f"call-{len(self._steps)}",
                "function_name": getattr(event, "tool_name", ""),
                "arguments": dict(getattr(event, "arguments", {}) or {}),
            }
        )

    def _on_tool_result(self, event: Event) -> None:
        if self._draft is None:
            return
        result: dict[str, Any] = {
            "content": getattr(event, "output", ""),
        }
        call_id = getattr(event, "call_id", "")
        if call_id:
            result["source_call_id"] = call_id
        success = getattr(event, "success", True)
        if not success:
            result["extra"] = {"success": False}
        self._draft.setdefault("observation", {"results": []})["results"].append(result)

    def _on_compaction(self, event: Event) -> None:
        self._summarization_count += 1

    def _on_step_cost(self, event: Event) -> None:
        self._total_cost += float(getattr(event, "cost", 0.0) or 0.0)

    def _on_agent_end(self, event: Event) -> None:
        self.close()

    # ---- finalisation ------------------------------------------------------

    def _seal_draft(self) -> None:
        if self._draft is not None:
            self._steps.append(self._draft)
            self._draft = None

    def to_trajectory(self) -> dict[str, Any]:
        """Render the accumulated state as an ATIF trajectory dict."""
        steps = list(self._steps)
        if self._draft is not None:
            steps.append(dict(self._draft))

        prompt_total = 0
        completion_total = 0
        peak_context: int | None = None
        for step in steps:
            metrics = step.get("metrics") or {}
            prompt = metrics.get("prompt_tokens")
            if prompt is not None:
                prompt_total += prompt
                peak_context = prompt if peak_context is None else max(peak_context, prompt)
            completion_total += metrics.get("completion_tokens") or 0

        extra: dict[str, Any] = {"summarization_count": self._summarization_count}
        if peak_context is not None:
            extra["peak_context_tokens"] = peak_context

        final_metrics: dict[str, Any] = {
            "total_steps": len(steps),
            "extra": extra,
        }
        if prompt_total:
            final_metrics["total_prompt_tokens"] = prompt_total
        if completion_total:
            final_metrics["total_completion_tokens"] = completion_total
        if self._total_cost:
            final_metrics["total_cost_usd"] = round(self._total_cost, 6)

        trajectory: dict[str, Any] = {
            "schema_version": ATIF_VERSION,
            "agent": dict(self._agent),
            "steps": steps,
            "final_metrics": final_metrics,
        }
        if self._session_id:
            trajectory["session_id"] = self._session_id
        if self._notes:
            trajectory["notes"] = self._notes
        return trajectory

    def close(self) -> Path:
        """Seal the trajectory, write it to disk, and detach (idempotent).

        Returns:
            The path the trajectory was written to.
        """
        if self._closed:
            return self.output_path
        self._seal_draft()
        self._closed = True
        self.detach()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(self.to_trajectory(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return self.output_path


def _chimera_version() -> str:
    try:
        from importlib.metadata import version

        return version("chimera-run")
    except Exception:
        return "0.0.0-dev"
