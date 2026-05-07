"""Stoat slash palette — minimal commands plus the headline ``/shell`` toggle.

Stoat keeps the slash surface small (mirrors weasel's minimalism), with one
distinguishing entry: ``/shell`` flips the REPL into shell mode (every
subsequent input runs as ``bash -c <input>``) until the user toggles back.
The keyboard equivalent ``Ctrl-X`` is documented as the "shell-mode toggle"
in :mod:`chimera.stoat.shell_mode`; ``/shell`` is the slash-typeable form
for terminals that intercept ``Ctrl-X``.

A second posture, ``plan`` mode, is reachable via ``/plan`` (or the
``Ctrl-X p`` chord) — it asks the agent to produce a plan and request
confirmation rather than acting. Plan persistence lives in
:mod:`chimera.stoat.plan_mode`.

The dispatcher returns a :class:`SlashResult` per command so callers (the
REPL) can decide whether to keep looping, render text, or perform a side
effect like clearing history.

Trademark hygiene: ``/shell`` is described as "shell mode toggle"; the
upstream brand that pioneered the ergonomic is never named in source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from chimera.stoat.plan_mode import PlanModeManager
from chimera.stoat.shell_mode import MODE_AGENT, MODE_SHELL, ShellModeManager

__all__ = [
    "SlashResult",
    "SlashPalette",
    "SLASH_COMMANDS",
    "build_default_palette",
]


SLASH_COMMANDS: tuple[str, ...] = (
    "/help",
    "/exit",
    "/clear",
    "/model",
    "/shell",
    "/plan",
    "/cost",
    "/history",
)
"""Canonical slash command list. Mirrored in tests and ``/help``."""


@dataclass
class SlashResult:
    """Outcome of dispatching one slash line.

    Attributes:
        keep_going: ``False`` when the caller (REPL) should exit the
            outer loop. ``True`` for "render output and continue".
        text: Optional textual output the caller should print. ``None``
            means "render nothing" (e.g. :meth:`SlashPalette.cmd_clear`
            already wrote its own confirmation through the side effect).
        handled: ``True`` when the slash was recognised. ``False`` for
            unknown commands so the caller can render its own error.
    """

    keep_going: bool = True
    text: str | None = None
    handled: bool = True


_HELP_TEXT = (
    "Slash commands:\n"
    "  /help            Show this help message.\n"
    "  /exit            Exit the REPL.\n"
    "  /clear           Reset conversation history.\n"
    "  /model [<id>]    Show or set the active model id.\n"
    "  /shell           Toggle between agent mode and shell mode.\n"
    "  /plan            Toggle plan mode (planner posture, no actions).\n"
    "  /cost            Show running cost for the current session.\n"
    "  /history [<n>]   Show the last n submitted lines (default 10).\n"
    "Chord (when prompt_toolkit is installed): Ctrl-X p / s / h.\n"
    "Anything else is sent to the model (agent mode), run as bash -c "
    "<line> (shell mode), or planned (plan mode)."
)


@dataclass
class SlashPalette:
    """Mutable slash command dispatcher backed by a shell-mode manager.

    The palette is intentionally small. Tests and the REPL share one
    instance; mutating ``model`` / ``cost_usd`` between turns is the
    expected pattern.

    Attributes:
        shell_mode: The :class:`ShellModeManager` whose state ``/shell``
            and ``/history`` consult.
        model: Current model id. Mutated by ``/model <id>``.
        cost_usd: Running cost for the active session (USD). Updated by
            the REPL after each successful turn.
        on_clear: Optional callback fired when ``/clear`` is dispatched.
            Receives no arguments. Used by the REPL to drop its
            conversation-history buffer.
        plan_mode: Optional :class:`PlanModeManager` consulted by
            ``/plan``. When ``None``, ``/plan`` reports that plan-mode
            wiring is unavailable rather than erroring.
    """

    shell_mode: ShellModeManager
    model: str | None = None
    cost_usd: float = 0.0
    on_clear: Callable[[], None] | None = None
    plan_mode: PlanModeManager | None = None

    # ------------------------------------------------------------------
    # Per-command handlers
    # ------------------------------------------------------------------

    def cmd_help(self, _arg: str) -> SlashResult:
        """``/help`` — render the slash palette."""
        return SlashResult(text=_HELP_TEXT)

    def cmd_exit(self, _arg: str) -> SlashResult:
        """``/exit`` — break out of the REPL loop."""
        return SlashResult(keep_going=False, text=None)

    def cmd_clear(self, _arg: str) -> SlashResult:
        """``/clear`` — drop conversation history (via :attr:`on_clear`)."""
        if self.on_clear is not None:
            self.on_clear()
        return SlashResult(text="(history cleared)")

    def cmd_model(self, arg: str) -> SlashResult:
        """``/model`` — show the current model. ``/model <id>`` sets it."""
        target = arg.strip()
        if not target:
            return SlashResult(text=f"model: {self.model or '(unresolved)'}")
        self.model = target
        return SlashResult(text=f"model set: {self.model}")

    def cmd_shell(self, _arg: str) -> SlashResult:
        """``/shell`` — toggle between agent and shell mode.

        Leaves plan mode (if active) so the agent/shell toggle remains
        the user's primary mental model — plan mode is the explicit
        third posture, never an implicit overlay.
        """
        if self.plan_mode is not None and self.plan_mode.is_active():
            self.plan_mode.disable()
        new_mode = self.shell_mode.toggle()
        if new_mode == MODE_SHELL:
            return SlashResult(
                text=(
                    "(shell mode: each input runs as 'bash -c <input>'. "
                    "Type /shell to return to agent mode.)"
                )
            )
        return SlashResult(text="(agent mode)")

    def cmd_plan(self, _arg: str) -> SlashResult:
        """``/plan`` — toggle plan mode (agent emits a plan, doesn't act)."""
        if self.plan_mode is None:
            return SlashResult(
                text=(
                    "/plan: plan mode unavailable in this REPL "
                    "(no PlanModeManager wired)."
                )
            )
        new_state = self.plan_mode.toggle()
        if new_state:
            return SlashResult(
                text=(
                    "(plan mode: each input asks for a plan + confirmation. "
                    "Type /plan to leave plan mode.)"
                )
            )
        return SlashResult(text="(plan mode off)")

    def cmd_cost(self, _arg: str) -> SlashResult:
        """``/cost`` — render running cost for the active session."""
        return SlashResult(text=f"cost: ${self.cost_usd:.4f}")

    def cmd_history(self, arg: str) -> SlashResult:
        """``/history`` — render the last ``n`` submitted lines."""
        target = arg.strip()
        try:
            n = int(target) if target else 10
        except ValueError:
            return SlashResult(
                text=f"/history: expected an integer, got {target!r}"
            )
        if n <= 0:
            return SlashResult(text="(no history)")
        rows = self.shell_mode.recent(n)
        if not rows:
            return SlashResult(text="(no history)")
        rendered_lines: list[str] = []
        for mode, line in rows:
            tag = "$" if mode == MODE_SHELL else ">"
            rendered_lines.append(f"{tag} {line}")
        return SlashResult(text="\n".join(rendered_lines))

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def dispatch(self, line: str) -> SlashResult:
        """Dispatch one slash-prefixed line.

        Args:
            line: Raw user input including the leading ``/``.

        Returns:
            A :class:`SlashResult`. ``handled=False`` when the command
            wasn't recognised — the REPL renders its own "unknown
            command" error in that case.
        """
        head, _, tail = line.partition(" ")
        head = head.strip()
        handlers: dict[str, Callable[[str], SlashResult]] = {
            "/help": self.cmd_help,
            "/exit": self.cmd_exit,
            "/quit": self.cmd_exit,
            "/clear": self.cmd_clear,
            "/model": self.cmd_model,
            "/shell": self.cmd_shell,
            "/plan": self.cmd_plan,
            "/cost": self.cmd_cost,
            "/history": self.cmd_history,
        }
        handler = handlers.get(head)
        if handler is None:
            return SlashResult(
                text=(
                    f"unknown command: {head} "
                    f"(known: {', '.join(SLASH_COMMANDS)})"
                ),
                handled=False,
            )
        return handler(tail)


def build_default_palette(
    *,
    shell_mode: ShellModeManager | None = None,
    model: str | None = None,
    on_clear: Callable[[], None] | None = None,
    plan_mode: PlanModeManager | None = None,
) -> SlashPalette:
    """Construct a :class:`SlashPalette` with defaults for the stoat REPL.

    Args:
        shell_mode: Optional pre-built :class:`ShellModeManager`. When
            ``None``, a fresh manager is created in agent mode.
        model: Initial model id rendered by ``/model``.
        on_clear: Callback fired when ``/clear`` runs.
        plan_mode: Optional :class:`PlanModeManager` consulted by
            ``/plan``. ``None`` is allowed (the palette stays
            backwards-compatible with the agent/shell-only REPL).

    Returns:
        A configured :class:`SlashPalette`.
    """
    manager = shell_mode if shell_mode is not None else ShellModeManager(mode=MODE_AGENT)
    return SlashPalette(
        shell_mode=manager,
        model=model,
        on_clear=on_clear,
        plan_mode=plan_mode,
    )
