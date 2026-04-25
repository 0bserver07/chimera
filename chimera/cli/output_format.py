# chimera/cli/output_format.py
"""Output-format handlers for ``chimera mink``.

Provides line-oriented and aggregated JSON encodings of the
:class:`~chimera.events.base.Event` (LoopEvent) stream so that IDE / editor
front-ends can consume a Chimera session without parsing the human-readable
TUI output.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import IO, Any

from chimera.events.base import Event
from chimera.events.types import (
    AgentEndEvent,
    AgentStartEvent,
    StepEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from chimera.secrets.detector import SecretDetector
from chimera.secrets.redactor import RedactionMiddleware
from chimera.secrets.registry import SecretRegistry
from chimera.streaming.base import StreamHandler

__all__ = ["StreamJsonHandler", "JsonHandler", "select_handler"]


def _event_to_dict(event: Event) -> dict[str, Any]:
    """Serialize a LoopEvent into a JSON-friendly dict.

    Args:
        event: The event to serialize.

    Returns:
        Mapping with ``type``, ``ts`` and the event's dataclass fields.
    """
    payload: dict[str, Any] = {"type": event.type, "ts": float(event.timestamp)}
    if dataclasses.is_dataclass(event):
        for f in dataclasses.fields(event):
            if f.name in ("type", "timestamp"):
                continue
            payload[f.name] = getattr(event, f.name)
    return payload


class _BaseLoopJsonHandler(StreamHandler):
    """Shared StreamHandler glue for LoopEvent-oriented JSON encoders.

    Subclasses StreamHandler so it can be plugged into existing streaming
    plumbing, but the load-bearing entry point is :meth:`handle_loop_event`.
    The legacy ``on_*`` hooks are wired to synthesize equivalent events.
    """

    def __init__(self, redaction: RedactionMiddleware | None = None) -> None:
        if redaction is None:
            registry = SecretRegistry()
            registry.register_from_env(
                "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
            )
            redaction = RedactionMiddleware(
                registry=registry,
                detector=SecretDetector(),
                detect_unknown=True,
            )
        self._redaction = redaction

    # -- LoopEvent ingress ---------------------------------------------------

    def handle_loop_event(self, event: Event) -> None:
        """Run *event* through the redaction middleware then emit it."""
        self._redaction.process(event, self._emit)

    def _emit(self, event: Event) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- StreamHandler ABC (synthesize LoopEvents) ---------------------------

    def on_text(self, text: str) -> None:
        self.handle_loop_event(TextDeltaEvent(content=text))

    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        self.handle_loop_event(ToolCallEvent(tool_name=tool_name, call_id=call_id))

    def on_tool_end(self, call_id: str, output: str) -> None:
        self.handle_loop_event(ToolResultEvent(call_id=call_id, output=output))

    def on_step_start(self, step: int) -> None:
        self.handle_loop_event(StepEvent(step_number=step))

    def on_step_end(self, step: int) -> None:
        pass

    def on_done(self) -> None:
        self.handle_loop_event(AgentEndEvent(success=True))


class StreamJsonHandler(_BaseLoopJsonHandler):
    """Emit one JSON object per LoopEvent, newline-terminated.

    Each line follows the schema ``{"type": <event_type>, "ts": <float>, ...}``.
    Secrets are redacted via :class:`RedactionMiddleware` before serialization.

    Args:
        out: Writable text stream (default ``sys.stdout``).
        redaction: Pre-configured redaction middleware. If ``None``, a default
            registry+detector pair is constructed.
    """

    def __init__(
        self,
        out: IO[str] | None = None,
        redaction: RedactionMiddleware | None = None,
    ) -> None:
        super().__init__(redaction=redaction)
        self._out: IO[str] = out if out is not None else sys.stdout

    def _emit(self, event: Event) -> None:
        line = json.dumps(_event_to_dict(event), default=str, sort_keys=True)
        self._out.write(line + "\n")
        try:
            self._out.flush()
        except (AttributeError, ValueError):
            pass


class JsonHandler(_BaseLoopJsonHandler):
    """Buffer all LoopEvents and emit a single aggregated JSON document.

    Call :meth:`finalize` at session end to write
    ``{"events": [...], "result": {...}, "cost": {...}}``.

    Args:
        out: Writable text stream (default ``sys.stdout``).
        redaction: Optional pre-configured redaction middleware.
    """

    def __init__(
        self,
        out: IO[str] | None = None,
        redaction: RedactionMiddleware | None = None,
    ) -> None:
        super().__init__(redaction=redaction)
        self._out: IO[str] = out if out is not None else sys.stdout
        self._events: list[dict[str, Any]] = []
        self._result: dict[str, Any] = {}
        self._cost: dict[str, Any] = {}
        self._finalized = False

    def _emit(self, event: Event) -> None:
        self._events.append(_event_to_dict(event))
        if isinstance(event, AgentEndEvent):
            self._result = {"success": event.success, "steps": event.steps}
            self._cost = {"total_cost": float(event.total_cost)}

    def finalize(self) -> None:
        """Flush the aggregated document to the output stream (idempotent)."""
        if self._finalized:
            return
        self._finalized = True
        doc = {"events": self._events, "result": self._result, "cost": self._cost}
        self._out.write(json.dumps(doc, default=str, sort_keys=True) + "\n")
        try:
            self._out.flush()
        except (AttributeError, ValueError):
            pass

    def on_done(self) -> None:
        super().on_done()
        self.finalize()


def select_handler(format: str, out: IO[str] | None = None) -> StreamHandler:
    """Return the StreamHandler implementation for *format*.

    Args:
        format: One of ``"stream-json"``, ``"json"``, or ``"text"``.
        out: Optional output stream forwarded to JSON handlers.

    Returns:
        A configured :class:`StreamHandler`.

    Raises:
        ValueError: If *format* is not recognized.
    """
    fmt = format.lower().replace("_", "-")
    if fmt == "stream-json":
        return StreamJsonHandler(out=out)
    if fmt == "json":
        return JsonHandler(out=out)
    if fmt == "text":
        from chimera.streaming.handlers import ConsoleStreamHandler
        return ConsoleStreamHandler()
    raise ValueError(f"Unknown output format: {format!r}")


def _demo() -> None:
    """Emit five fake LoopEvents through StreamJsonHandler for visual checks."""
    handler = StreamJsonHandler()
    handler.handle_loop_event(AgentStartEvent(max_steps=4))
    handler.handle_loop_event(StepEvent(step_number=1, content="planning"))
    handler.handle_loop_event(
        ToolCallEvent(tool_name="bash", arguments={"cmd": "ls"}, call_id="c1"),
    )
    handler.handle_loop_event(
        ToolResultEvent(call_id="c1", output="README.md\nchimera/", success=True),
    )
    handler.handle_loop_event(AgentEndEvent(steps=1, success=True, total_cost=0.0))


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: ``python -m chimera.cli.output_format --demo``."""
    parser = argparse.ArgumentParser(prog="chimera.cli.output_format")
    parser.add_argument("--demo", action="store_true", help="Emit 5 fake events.")
    args = parser.parse_args(argv)
    if args.demo:
        _demo()
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
