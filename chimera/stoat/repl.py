"""``chimera stoat`` interactive REPL with the shell-mode toggle.

The stoat REPL combines two ergonomic layers:

* A small slash palette (``/help``, ``/exit``, ``/clear``, ``/model``,
  ``/shell``, ``/cost``, ``/history``) — implemented in
  :mod:`chimera.stoat.slash`.
* A **shell-mode toggle** — the user can flip the REPL into shell mode
  (every input runs as ``bash -c <input>``) and back without leaving the
  REPL. The state machine lives in :mod:`chimera.stoat.shell_mode`.

The REPL deliberately stays self-contained (mirrors weasel's
:class:`MinimalRepl`) rather than wrapping :func:`chimera.cli.code.run_code`
because the shared code REPL doesn't expose the shell-mode buffer toggle.
A ``run_code``-style integration is documented as a follow-up; the
``--mode rpc`` path already delegates to :mod:`chimera.cli.code` for
process integration.

Trademark hygiene: never names the upstream brand. The toggle is referred
to as "shell mode" everywhere — both the slash command and the docs.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING, Any, Callable, TextIO

from chimera.stoat.shell_mode import (
    MODE_AGENT,
    MODE_SHELL,
    ShellModeManager,
)
from chimera.stoat.slash import SlashPalette, build_default_palette

if TYPE_CHECKING:
    from chimera.providers.base import Provider

__all__ = ["StoatRepl", "run"]


_BANNER_TEMPLATE = (
    "stoat — Chimera coding agent (shell-mode toggle: /shell)\n"
    "model: {model}  ·  mode: {mode}  ·  cwd: {cwd}\n"
    "Type /help for commands, /exit to quit.\n"
)


class StoatRepl:
    """Stateful REPL bridging stdin <-> a Chimera :class:`Agent` plus shell mode.

    Attributes:
        model: The active model id. Mutated via the ``/model`` slash.
        workdir: Absolute working directory passed to :class:`LocalEnvironment`.
        max_steps: Per-turn step cap forwarded to :class:`ReAct`.
        out: Output stream (defaults to :data:`sys.stdout`).
        history: List of ``(role, content)`` pairs maintained across turns.
            ``/clear`` resets this list.
        shell_mode: The :class:`ShellModeManager` driving shell-mode dispatch.
        palette: The :class:`SlashPalette` handling slash commands.
    """

    def __init__(
        self,
        model: str | None,
        workdir: str | None = None,
        max_steps: int = 50,
        out: TextIO | None = None,
        input_fn: Callable[[str], str] | None = None,
        start_in_shell_mode: bool = False,
    ) -> None:
        """Initialise the REPL state.

        Args:
            model: Initial model id. ``None`` means "let
                :func:`chimera.stoat.providers.build_provider` resolve it
                from the env-var chain on the first turn".
            workdir: Working directory for the agent's
                :class:`LocalEnvironment`. ``None`` falls back to
                :func:`os.getcwd`.
            max_steps: Per-turn step cap forwarded to :class:`ReAct`.
            out: Output stream. Defaults to :data:`sys.stdout`.
            input_fn: Callable that returns the next line of user input.
                Defaults to the builtin :func:`input`. Tests inject a fake.
            start_in_shell_mode: When ``True``, the REPL boots in shell
                mode (mirrors ``--shell-mode`` on the CLI).
        """
        self.model = model
        self.workdir = os.path.abspath(workdir or os.getcwd())
        self.max_steps = int(max_steps)
        self.out: TextIO = out if out is not None else sys.stdout
        self._input: Callable[[str], str] = (
            input_fn if input_fn is not None else input
        )
        self.history: list[tuple[str, str]] = []

        initial_mode = MODE_SHELL if start_in_shell_mode else MODE_AGENT
        self.shell_mode = ShellModeManager(mode=initial_mode)
        self.palette: SlashPalette = build_default_palette(
            shell_mode=self.shell_mode,
            model=model,
            on_clear=self._on_clear,
        )

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _write(self, text: str) -> None:
        """Write ``text`` to :attr:`out` with a guaranteed trailing newline."""
        self.out.write(text)
        if not text.endswith("\n"):
            self.out.write("\n")
        self.out.flush()

    def _on_clear(self) -> None:
        """Callback invoked by the slash palette on ``/clear``."""
        self.history.clear()

    # ------------------------------------------------------------------
    # Provider / agent invocation (one agent-mode turn)
    # ------------------------------------------------------------------

    def _build_provider(self) -> Provider:
        """Construct a provider for the next agent-mode turn (lazy)."""
        from chimera.stoat.providers import build_provider

        ns = argparse.Namespace(model=self.palette.model or self.model)
        return build_provider(ns)

    def run_agent_turn(self, prompt: str) -> str:
        """Run a single agent turn and return the textual output.

        Args:
            prompt: User message to send.

        Returns:
            The agent's textual output, or an error message prefixed with
            ``"stoat:"`` when provider construction or the turn itself
            fails. Errors do *not* propagate — REPLs that bubble exceptions
            to the user are unfriendly.
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
        except Exception as exc:  # noqa: BLE001 — never crash the REPL
            return f"stoat: agent stack unavailable: {exc}"

        try:
            provider = self._build_provider()
        except Exception as exc:  # noqa: BLE001 — surface auth errors politely
            return f"stoat: provider error: {exc}"

        env = LocalEnvironment(workdir=self.workdir)
        env.setup()

        cancel = CancellationToken()
        config = LoopConfig(cancellation=cancel)
        loop = ReAct(max_steps=self.max_steps, config=config)
        sys_prompt = Prompt.from_string(
            "You are Stoat, a Chimera coding agent with a shell-mode toggle. "
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
            return f"stoat: turn failed: {exc}"
        finally:
            env.cleanup()

        text = str(getattr(result, "output", "") or "")
        self.history.append(("user", prompt))
        self.history.append(("assistant", text))
        # Best-effort cost rollup for ``/cost``.
        cost = getattr(result, "cost", None)
        if cost is not None:
            try:
                self.palette.cost_usd += float(cost)
            except (TypeError, ValueError):
                pass
        return text

    # ------------------------------------------------------------------
    # Shell-mode turn
    # ------------------------------------------------------------------

    def run_shell_turn(self, command: str) -> str:
        """Execute ``command`` via the shell-mode manager and render the result.

        Args:
            command: The exact command line submitted by the user.

        Returns:
            A multi-line string suitable for the REPL transcript:
            stdout (if any) followed by stderr (if any). When the command
            had no output we surface a brief ``[exit N]`` marker so the
            user knows it ran.
        """
        result = self.shell_mode.run_shell(command, cwd=self.workdir)
        parts: list[str] = []
        if result.stdout:
            parts.append(result.stdout.rstrip("\n"))
        if result.stderr:
            parts.append(result.stderr.rstrip("\n"))
        if not parts:
            parts.append(f"[exit {result.returncode}]")
        elif result.returncode != 0:
            parts.append(f"[exit {result.returncode}]")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Loop driver
    # ------------------------------------------------------------------

    def loop_once(self) -> bool:
        """Run a single REPL iteration.

        Returns:
            ``True`` to keep looping; ``False`` when the user typed
            ``/exit`` or signalled EOF.
        """
        prompt = self.shell_mode.prompt
        try:
            line = self._input(prompt)
        except EOFError:
            self._write("")
            return False
        except KeyboardInterrupt:
            # Ctrl-C at the prompt is "cancel this line", not "exit".
            self._write("")
            return True

        line = line.strip()
        if not line:
            return True

        # Slash commands run regardless of current mode — ``/shell``
        # itself is how the user toggles back, so it must always be
        # reachable.
        if line.startswith("/"):
            result = self.palette.dispatch(line)
            # Sync palette.model back onto the REPL so the next turn
            # picks up the new id.
            if self.palette.model is not None:
                self.model = self.palette.model
            if result.text is not None:
                self._write(result.text)
            return result.keep_going

        self.shell_mode.record(line)
        if self.shell_mode.is_shell_mode():
            text = self.run_shell_turn(line)
        else:
            text = self.run_agent_turn(line)
        if text:
            self._write(text)
        return True

    def run(self) -> int:
        """Drive the REPL until ``/exit`` or EOF.

        Returns:
            Process exit code. Always ``0`` for normal exits.
        """
        banner = _BANNER_TEMPLATE.format(
            model=self.palette.model or self.model or "(unresolved)",
            mode=self.shell_mode.mode,
            cwd=self.workdir,
        )
        self._write(banner)
        while True:
            keep_going = self.loop_once()
            if not keep_going:
                return 0


def run(args: argparse.Namespace) -> int:
    """Entry point invoked by :mod:`chimera.stoat.cli`.

    Builds a :class:`StoatRepl` from the parsed CLI namespace and runs
    it against ``sys.stdin`` / ``sys.stdout``.

    Args:
        args: Parsed stoat CLI namespace. Reads ``model``, ``cwd``,
            ``max_steps``, ``shell_mode``; tolerates missing attributes
            via :func:`getattr`.

    Returns:
        Process exit code.
    """
    workdir = getattr(args, "cwd", None) or os.getcwd()
    max_steps_raw = getattr(args, "max_steps", 50) or 50
    repl = StoatRepl(
        model=getattr(args, "model", None),
        workdir=workdir,
        max_steps=int(max_steps_raw),
        start_in_shell_mode=bool(getattr(args, "shell_mode", False)),
    )
    return repl.run()
