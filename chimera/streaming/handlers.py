# chimera/streaming/handlers.py
"""Concrete StreamHandler implementations."""
from __future__ import annotations

import os
import sys
from typing import IO, Any

from chimera.streaming.base import StreamHandler

__all__ = [
    "ConsoleStreamHandler",
    "CollectStreamHandler",
    "NullStreamHandler",
    "ansi_enabled",
]


def ansi_enabled(stream: IO[str] | None = None) -> bool:
    """Return True iff ANSI color/style codes should be emitted to ``stream``.

    Honors the de-facto `NO_COLOR <https://no-color.org/>`_ convention: when
    ``NO_COLOR`` is set in the environment (to any value, including empty
    string), color is disabled. ``FORCE_COLOR`` overrides and forces color
    on. Otherwise color is only enabled when the stream is a tty.

    The default ``stream`` is ``sys.stdout``. Pass an explicit handle for
    stderr renderers or for tests.
    """
    if stream is None:
        stream = sys.stdout
    # FORCE_COLOR wins over NO_COLOR per common CLI conventions (rich, click,
    # npm, cargo). Both are checked for membership, not value, to match
    # https://no-color.org/ and the FORCE_COLOR informal spec.
    if "FORCE_COLOR" in os.environ:
        return True
    if "NO_COLOR" in os.environ:
        return False
    isatty = getattr(stream, "isatty", None)
    try:
        return bool(isatty()) if callable(isatty) else False
    except Exception:  # noqa: BLE001
        return False


class ConsoleStreamHandler(StreamHandler):
    """Prints streaming output to stdout in a human-readable format.

    Args:
        quiet: When True, suppresses the per-tool ``[Tool: …]`` /
            ``[Result: …]`` framing and per-step ``--- Step N ---``
            markers; only assistant text + the final newline are
            printed. Useful when the CLI is being piped into another
            program. (Audit H-2.)
        stream: Where to write. Defaults to ``sys.stdout``. Tests pass
            ``io.StringIO``.
    """

    def __init__(
        self,
        quiet: bool = False,
        stream: IO[str] | None = None,
    ) -> None:
        self.quiet = quiet
        self._stream = stream if stream is not None else sys.stdout
        # Pre-compute color decision once so we don't pay env-lookup
        # cost per chunk. Currently the handler is plain text, but
        # keeping this hook ready means render integration (B-2) can
        # check `self._color` without re-deriving the policy.
        self._color = ansi_enabled(self._stream)

    def _print(self, msg: str, end: str = "\n") -> None:
        # Single funnel so the quiet/stream choices stay consistent.
        print(msg, end=end, flush=True, file=self._stream)

    def on_text(self, text: str) -> None:
        # Assistant text is always printed (even in quiet mode); quiet
        # only suppresses the framing chrome.
        self._print(text, end="")

    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        if self.quiet:
            return
        self._print(f"\n[Tool: {tool_name}]")

    def on_tool_end(self, call_id: str, output: str) -> None:
        if self.quiet:
            return
        self._print(f"[Result: {output[:200]}]")

    def on_step_start(self, step: int) -> None:
        if self.quiet:
            return
        self._print(f"\n--- Step {step} ---")

    def on_step_end(self, step: int) -> None:
        pass

    def on_done(self) -> None:
        if self.quiet:
            return
        self._print("")


class CollectStreamHandler(StreamHandler):
    """Collects all events into a list for testing and inspection."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def on_text(self, text: str) -> None:
        self.events.append({"type": "text", "content": text})

    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        self.events.append({
            "type": "tool_start",
            "tool_name": tool_name,
            "call_id": call_id,
        })

    def on_tool_end(self, call_id: str, output: str) -> None:
        self.events.append({
            "type": "tool_end",
            "call_id": call_id,
            "output": output,
        })

    def on_step_start(self, step: int) -> None:
        self.events.append({"type": "step_start", "step": step})

    def on_step_end(self, step: int) -> None:
        self.events.append({"type": "step_end", "step": step})

    def on_done(self) -> None:
        self.events.append({"type": "done"})


class NullStreamHandler(StreamHandler):
    """No-op handler -- silently discards every event."""

    def on_text(self, text: str) -> None:
        pass

    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        pass

    def on_tool_end(self, call_id: str, output: str) -> None:
        pass

    def on_step_start(self, step: int) -> None:
        pass

    def on_step_end(self, step: int) -> None:
        pass

    def on_done(self) -> None:
        pass
