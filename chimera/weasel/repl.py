"""``chimera weasel`` interactive REPL — minimal slash palette (agent W5).

The weasel REPL is deliberately tiny. Where the shared ``chimera code`` REPL
ships 19 slash commands, weasel ships **four** — ``/help``, ``/exit``,
``/clear``, ``/model`` — and nothing else. No ``/agent`` (sub-agents are out
of scope), no ``/share``, no ``/init`` (project bootstrap is handled by
other CLIs). Minimalism is the feature.

Architecture:

* :func:`run` is the public entry point invoked by :mod:`chimera.weasel.cli`.
* :class:`MinimalRepl` owns the read-eval-print loop. Slash commands are
  dispatched via :data:`MinimalRepl._SLASH_COMMANDS`; everything else is
  forwarded to a minimal :class:`Agent` constructed from
  :func:`chimera.weasel.providers.build_provider`.
* The class is exposed for testability — tests can drive it with a stubbed
  ``input`` / ``output`` pair to verify slash-command behaviour without
  touching a live provider.

The class also keeps a writable ``model`` field so ``/model <id>`` can swap
the active model id mid-session for the *next* turn (the running provider
isn't recreated until the user actually fires a prompt — keeps the slash
command cheap and side-effect-free).

Trademark hygiene: never names the upstream brand.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING, Any, Callable, TextIO

if TYPE_CHECKING:
    from chimera.providers.base import Provider

# WHY: stdlib only at import time. Provider construction is deferred until
# the user actually fires a prompt so ``chimera weasel`` (interactive) can
# still print the banner / handle ``/help`` even when no API key is set.

_BANNER = (
    "weasel — minimal Chimera coding agent\n"
    "Type /help for commands, /exit to quit.\n"
)
_HELP_TEXT = (
    "Slash commands:\n"
    "  /help            Show this help message.\n"
    "  /exit            Exit the REPL.\n"
    "  /clear           Reset conversation history.\n"
    "  /model [<id>]    Show or set the active model id.\n"
    "Anything else is sent to the model as a turn."
)
_PROMPT = "weasel> "


class MinimalRepl:
    """Tiny stateful REPL bridging stdin <-> a Chimera :class:`Agent`.

    Designed to be driven from :func:`run` with the real ``stdin`` /
    ``stdout`` plus :func:`input`, or from tests with a string-based
    fake.

    Attributes:
        model: The active model id. Mutated by ``/model <id>``.
        workdir: Absolute working directory passed to :class:`LocalEnvironment`.
        max_steps: Per-turn step cap forwarded to :class:`ReAct`.
        out: Output stream (defaults to ``sys.stdout``).
        history: List of ``(role, content)`` pairs maintained across turns.
            ``/clear`` resets this list.
    """

    # WHY: keep the dispatch table on the class, not as a module-level dict,
    # so ``MinimalRepl`` instances can be subclassed without leaking the
    # base palette. Tests rely on this.
    _SLASH_COMMANDS: tuple[str, ...] = ("/help", "/exit", "/clear", "/model")

    def __init__(
        self,
        model: str | None,
        workdir: str | None = None,
        max_steps: int = 50,
        out: TextIO | None = None,
        input_fn: Callable[[str], str] | None = None,
        legacy_react: bool = False,
    ) -> None:
        """Initialise the REPL state.

        Args:
            model: Initial model id. ``None`` means "let
                :func:`chimera.weasel.providers.build_provider` resolve it
                from the env-var chain".
            workdir: Working directory for the agent's :class:`LocalEnvironment`.
                ``None`` falls back to :func:`os.getcwd`.
            max_steps: Per-turn step cap forwarded to :class:`ReAct`.
            out: Output stream. Defaults to :data:`sys.stdout`.
            input_fn: Callable that returns the next line of user input.
                Defaults to the builtin :func:`input` (which wires up
                readline editing on most terminals). Tests inject a fake.
            legacy_react: When ``False`` (default), free-text turns are
                dispatched to :class:`chimera.assembly.coding_agent.CodingAgent`
                with ``preset="coding_agent"`` — the wave-10 G3 default
                flip applied to weasel by wave-11 B1. When ``True``,
                free-text turns use the legacy bare :class:`ReAct` stack
                (the original W5 path). The CodingAgent path falls back
                to the legacy ReAct path automatically when the assembly
                module is not importable, so this flag rarely needs to
                be flipped manually.
        """
        self.model = model
        self.workdir = os.path.abspath(workdir or os.getcwd())
        self.max_steps = int(max_steps)
        self.out: TextIO = out if out is not None else sys.stdout
        self._input: Callable[[str], str] = input_fn if input_fn is not None else input
        self.legacy_react = bool(legacy_react)
        # WHY: history is the single source of truth for ``/clear``. We
        # don't reuse :class:`chimera.sessions.Session` here because that
        # would pull in the full session-tree/eventlog stack — which is
        # explicitly *not* the weasel feature set.
        self.history: list[tuple[str, str]] = []

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _write(self, text: str) -> None:
        """Write ``text`` to :attr:`out` without buffering surprises."""
        self.out.write(text)
        if not text.endswith("\n"):
            self.out.write("\n")
        self.out.flush()

    # ------------------------------------------------------------------
    # Slash command handlers
    # ------------------------------------------------------------------

    def cmd_help(self, _arg: str) -> bool:
        """Render the ``/help`` palette. Returns ``True`` to keep looping."""
        self._write(_HELP_TEXT)
        return True

    def cmd_exit(self, _arg: str) -> bool:
        """``/exit`` — break out of the loop."""
        return False

    def cmd_clear(self, _arg: str) -> bool:
        """``/clear`` — drop conversation history."""
        self.history.clear()
        self._write("(history cleared)")
        return True

    def cmd_model(self, arg: str) -> bool:
        """``/model`` — show the current model. ``/model <id>`` sets it."""
        target = arg.strip()
        if not target:
            self._write(f"model: {self.model or '(unresolved)'}")
            return True
        self.model = target
        self._write(f"model set: {self.model}")
        return True

    # ------------------------------------------------------------------
    # Slash dispatcher
    # ------------------------------------------------------------------

    def dispatch_slash(self, line: str) -> bool:
        """Dispatch a slash-prefixed line.

        Args:
            line: Raw user input including the leading ``/``.

        Returns:
            ``True`` to keep looping, ``False`` to exit.
        """
        head, _, tail = line.partition(" ")
        head = head.strip()
        # WHY: explicit dispatch table over reflection (``getattr``) so
        # adding a new command requires touching the registry — keeps the
        # slash palette enumerable for ``/help`` and tests.
        handlers: dict[str, Callable[[str], bool]] = {
            "/help": self.cmd_help,
            "/exit": self.cmd_exit,
            "/clear": self.cmd_clear,
            "/model": self.cmd_model,
        }
        handler = handlers.get(head)
        if handler is None:
            self._write(
                f"unknown command: {head} "
                f"(known: {', '.join(self._SLASH_COMMANDS)})"
            )
            return True
        return handler(tail)

    # ------------------------------------------------------------------
    # Provider / agent invocation (one turn)
    # ------------------------------------------------------------------

    def _build_provider(self) -> Provider:
        """Construct a provider for the next turn.

        Lazy: only called when the user actually fires a prompt. This
        keeps ``chimera weasel`` (interactive) bootable even when no API
        key is configured, so users can read ``/help`` first.
        """
        from chimera.weasel.providers import build_provider

        ns = argparse.Namespace(model=self.model)
        return build_provider(ns)

    def run_turn(self, prompt: str) -> str:
        """Run a single agent turn and return the textual output.

        Dispatches to :meth:`_run_turn_coding_agent` (the wave-10 G3
        default — :class:`chimera.assembly.coding_agent.CodingAgent` with
        ``preset="coding_agent"``) or :meth:`_run_turn_react` (the
        legacy bare :class:`ReAct` stack) based on :attr:`legacy_react`.

        The CodingAgent path falls back to the ReAct path automatically
        when the assembly module is not importable, so a missing optional
        dependency never crashes the REPL.

        Args:
            prompt: User message to send.

        Returns:
            The agent's textual output, or an error message prefixed with
            ``"weasel:"`` when provider construction or the turn itself
            fails. Errors do *not* propagate — REPLs that bubble exceptions
            to the user are unfriendly.
        """
        if not self.legacy_react:
            # Late-binding import: a missing chimera.assembly module (e.g. a
            # trimmed install or import-time failure inside the assembly
            # stack) must not break the REPL — fall through to legacy ReAct.
            try:
                from chimera.assembly.coding_agent import (  # noqa: F401
                    CodingAgent,
                )
            except Exception:  # noqa: BLE001 — defensive: any import failure falls back
                pass
            else:
                return self._run_turn_coding_agent(prompt)
        return self._run_turn_react(prompt)

    def _run_turn_coding_agent(self, prompt: str) -> str:
        """Run a single turn through the assembled :class:`CodingAgent` stack.

        Mirrors the dispatch loop in :func:`chimera.cli.code._run_new_stack`
        — iterates the async event stream and concatenates the textual
        ``assistant`` content into a single string return value so the
        REPL's existing ``self._write(text)`` plumbing stays untouched.

        Args:
            prompt: User message to send.

        Returns:
            The agent's textual output, or an error message prefixed with
            ``"weasel:"`` on failure. Errors never propagate.
        """
        try:
            import asyncio

            from chimera.assembly.coding_agent import CodingAgent
            from chimera.core.loop_events import LoopEventType
        except Exception as exc:  # noqa: BLE001 — import drift shouldn't crash the REPL
            return f"weasel: coding-agent stack unavailable: {exc}"

        try:
            agent = CodingAgent(
                model=self.model or "claude-sonnet-4-20250514",
                preset="coding_agent",
                project_dir=self.workdir,
            )
        except Exception as exc:  # noqa: BLE001 — surface auth / build errors politely
            return f"weasel: coding-agent error: {exc}"

        chunks: list[str] = []

        async def _drive() -> None:
            agent.reset_abort()
            async for event in agent.run(prompt):
                t = event.type
                if t == LoopEventType.assistant:
                    content = getattr(event.data, "content", str(event.data))
                    if content and content.strip():
                        chunks.append(content)
                elif t == LoopEventType.assistant_chunk:
                    chunks.append(str(event.data))
                elif t == LoopEventType.system:
                    if event.data:
                        chunks.append(str(event.data))

        try:
            asyncio.run(_drive())
        except KeyboardInterrupt:
            try:
                agent.abort()
            except Exception:  # noqa: BLE001 — abort is best-effort
                pass
            return "[cancelled]"
        except Exception as exc:  # noqa: BLE001 — REPL should never crash
            return f"weasel: turn failed: {exc}"

        text = "".join(chunks).strip()
        # Track in history regardless of success so /clear has something
        # to drop. Mirrors :meth:`_run_turn_react`.
        self.history.append(("user", prompt))
        self.history.append(("assistant", text))
        return text

    def _run_turn_react(self, prompt: str) -> str:
        """Legacy single-turn dispatch through the bare :class:`ReAct` loop.

        Pre-wave-11 weasel REPL path. Kept behind ``legacy_react=True``
        for users who want the original behaviour (or as a fallback when
        the assembly module is not importable).

        Args:
            prompt: User message to send.

        Returns:
            The agent's textual output, or an error message prefixed with
            ``"weasel:"`` on failure.
        """
        try:
            import asyncio

            from chimera.core.agent import Agent
            from chimera.core.cancellation import CancellationToken
            from chimera.core.loop import ReAct
            from chimera.core.loop_config import LoopConfig
            from chimera.core.prompt import Prompt
            from chimera.core.tool_group import AGENT_TOOLS
            from chimera.env.local import LocalEnvironment
        except Exception as exc:  # noqa: BLE001 — import drift shouldn't crash the REPL
            return f"weasel: agent stack unavailable: {exc}"

        try:
            provider = self._build_provider()
        except Exception as exc:  # noqa: BLE001 — surface auth errors politely
            return f"weasel: provider error: {exc}"

        env = LocalEnvironment(workdir=self.workdir)
        env.setup()

        cancel = CancellationToken()
        config = LoopConfig(cancellation=cancel)
        loop = ReAct(max_steps=self.max_steps, config=config)
        sys_prompt = Prompt.from_string(
            "You are Weasel, a minimal Chimera coding agent. "
            "Use tools to inspect and modify the user's repo. Be concise."
        )
        agent = Agent(
            provider=provider,
            tools=list(AGENT_TOOLS),
            loop=loop,
            prompt=sys_prompt,
        )

        try:
            result: Any = asyncio.run(agent.async_run(prompt, env=env))
        except KeyboardInterrupt:
            cancel.cancel()
            return "[cancelled]"
        except Exception as exc:  # noqa: BLE001 — REPL should never crash
            return f"weasel: turn failed: {exc}"
        finally:
            env.cleanup()

        text = str(getattr(result, "output", "") or "")
        # Track in history regardless of success so /clear has something
        # to drop.
        self.history.append(("user", prompt))
        self.history.append(("assistant", text))
        return text

    # ------------------------------------------------------------------
    # Loop driver
    # ------------------------------------------------------------------

    def loop_once(self) -> bool:
        """Run a single REPL iteration.

        Returns:
            ``True`` to keep looping; ``False`` when the user typed
            ``/exit`` or signalled EOF.
        """
        try:
            line = self._input(_PROMPT)
        except EOFError:
            self._write("")  # newline so the next shell prompt isn't glued
            return False
        except KeyboardInterrupt:
            # Ctrl-C at the prompt is "cancel this line", not "exit".
            self._write("")
            return True

        line = line.strip()
        if not line:
            return True

        if line.startswith("/"):
            return self.dispatch_slash(line)

        text = self.run_turn(line)
        if text:
            self._write(text)
        return True

    def run(self) -> int:
        """Drive the REPL until ``/exit`` or EOF.

        Returns:
            Process exit code. Always ``0`` for normal exits.
        """
        self._write(_BANNER)
        while True:
            keep_going = self.loop_once()
            if not keep_going:
                return 0


def run(args: argparse.Namespace) -> int:
    """Entry point invoked by :mod:`chimera.weasel.cli`.

    Builds a :class:`MinimalRepl` from the parsed CLI namespace and runs
    it against ``sys.stdin`` / ``sys.stdout``.

    Args:
        args: Parsed weasel CLI namespace. Reads ``model``, ``cwd``,
            ``max_steps``, and ``legacy_react``; tolerates missing
            attributes via ``getattr``.

    Returns:
        Process exit code.
    """
    workdir = getattr(args, "cwd", None) or os.getcwd()
    max_steps_raw = getattr(args, "max_steps", 50) or 50
    repl = MinimalRepl(
        model=getattr(args, "model", None),
        workdir=workdir,
        max_steps=int(max_steps_raw),
        legacy_react=bool(getattr(args, "legacy_react", False)),
    )
    return repl.run()


__all__ = ["MinimalRepl", "run"]
