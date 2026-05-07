"""``chimera stoat`` interactive REPL with the shell-mode toggle.

The stoat REPL combines three ergonomic layers:

* A small slash palette (``/help``, ``/exit``, ``/clear``, ``/model``,
  ``/shell``, ``/plan``, ``/cost``, ``/history``) — implemented in
  :mod:`chimera.stoat.slash`.
* A **shell-mode toggle** — the user can flip the REPL into shell mode
  (every input runs as ``bash -c <input>``) and back without leaving the
  REPL. The state machine lives in :mod:`chimera.stoat.shell_mode`.
* A **plan mode** — third posture beyond agent/shell where the agent
  produces a plan and asks for confirmation rather than acting. State
  + persistence live in :mod:`chimera.stoat.plan_mode`; on-buffer chord
  toggling (``Ctrl-X p``) lives in :mod:`chimera.stoat.keybindings`.

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

from chimera.stoat.hooks import (
    build_emitter_from_path,
    fire_session_end,
    fire_session_start,
    fire_user_prompt_submit,
)
from chimera.stoat.keybindings import ChordCallbacks, build_input_adapter
from chimera.stoat.plan_mode import (
    Plan,
    PlanModeManager,
    build_plan_system_prompt,
    save_plan,
)
from chimera.stoat.shell_mode import (
    MODE_AGENT,
    MODE_SHELL,
    ShellModeManager,
)
from chimera.stoat.slash import SlashPalette, build_default_palette

if TYPE_CHECKING:
    from chimera.hooks.emitter import HookEmitter
    from chimera.providers.base import Provider

__all__ = [
    "BRACKETED_PASTE_BEGIN",
    "BRACKETED_PASTE_END",
    "StoatRepl",
    "coalesce_bracketed_paste",
    "run",
]


# WHY: bracketed-paste mode (xterm) wraps multi-line pastes in these
# escape sequences. Modern terminals enable it by default; the REPL
# detects the markers and accumulates the wrapped chunk into a single
# input event so users don't see one shell-mode subprocess per pasted
# line.
BRACKETED_PASTE_BEGIN = "\x1b[200~"
BRACKETED_PASTE_END = "\x1b[201~"


_BANNER_TEMPLATE = (
    "stoat — Chimera coding agent (toggles: /shell · /plan)\n"
    "model: {model}  ·  posture: {posture}  ·  cwd: {cwd}\n"
    "Type /help for commands, /exit to quit. "
    "Ctrl-X p / s / h for chord bindings (when prompt_toolkit installed).\n"
)


def coalesce_bracketed_paste(
    line: str,
    *,
    read_more: Callable[[], str],
) -> str:
    """Collapse a bracketed-paste sequence into one logical input string.

    When prompt_toolkit is unavailable we fall back to ``input()``,
    which delivers one line per ``\\n`` even inside a paste. xterm-style
    terminals wrap the whole paste in :data:`BRACKETED_PASTE_BEGIN` /
    :data:`BRACKETED_PASTE_END` escape sequences, so we can detect the
    start marker on the first line and keep calling ``read_more`` until
    we see the end marker, then return the joined block (markers
    stripped, internal newlines preserved).

    The markers can also appear *inside* a single line when the entire
    paste is short enough to land on one line — we handle that case
    too. Lines without any markers are returned as-is.

    Args:
        line: The first line read from the user (typically the return
            value of ``input(prompt)``).
        read_more: Callable returning the next line of stdin. Invoked
            until the closing marker is seen. ``EOFError`` propagates
            so the REPL can shut down cleanly mid-paste.

    Returns:
        The coalesced input string. When ``line`` does not start with a
        bracketed-paste marker, returns ``line`` unchanged.
    """
    # Fast path: no opening marker anywhere -> not a paste.
    if BRACKETED_PASTE_BEGIN not in line:
        return line

    pre, _, after_begin = line.partition(BRACKETED_PASTE_BEGIN)
    # Same line contains both markers (short paste fit on one line).
    if BRACKETED_PASTE_END in after_begin:
        body, _, post = after_begin.partition(BRACKETED_PASTE_END)
        return f"{pre}{body}{post}"

    chunks: list[str] = [after_begin]
    trailing: str = ""
    while True:
        try:
            nxt = read_more()
        except EOFError:
            # Treat unterminated pastes as "we got what we got" — better
            # than swallowing a partial paste silently.
            break
        if BRACKETED_PASTE_END in nxt:
            body, _, post = nxt.partition(BRACKETED_PASTE_END)
            chunks.append(body)
            # Post-marker text rejoins the line that contained the end
            # marker rather than starting a new logical line, so
            # ``BEFORE\x1b[200~line one\nline two\x1b[201~AFTER``
            # collapses to ``BEFOREline one\nline twoAFTER`` (mirrors
            # how the user sees the paste rendered).
            trailing = post
            break
        chunks.append(nxt)
    return pre + "\n".join(chunks) + trailing


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
        start_in_plan_mode: bool = False,
        stderr: TextIO | None = None,
        hook_emitter: HookEmitter | None = None,
        session_id: str = "",
        bracketed_paste: bool = True,
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
                When provided, the chord input adapter is bypassed and
                the legacy ``input(prompt)`` flow runs (tests inject a
                fake here). When ``None``, a chord-aware input adapter
                is built — prompt_toolkit when installed, line-oriented
                stdlib ``input()`` otherwise.
            start_in_shell_mode: When ``True``, the REPL boots in shell
                mode (mirrors ``--shell-mode`` on the CLI).
            start_in_plan_mode: When ``True``, the REPL boots in plan
                mode (mirrors ``--plan-mode`` / ``Ctrl-X p`` on first
                input).
            stderr: Stream the chord fallback hint is written to.
                Defaults to :data:`sys.stderr`.
        """
        self.model = model
        self.workdir = os.path.abspath(workdir or os.getcwd())
        self.max_steps = int(max_steps)
        self.out: TextIO = out if out is not None else sys.stdout
        self.stderr: TextIO = stderr if stderr is not None else sys.stderr
        self._user_input_fn: Callable[[str], str] | None = input_fn
        self.history: list[tuple[str, str]] = []
        self.bracketed_paste: bool = bool(bracketed_paste)
        self.session_id: str = session_id
        # WHY: when no emitter is wired explicitly, autoload from
        # ~/.chimera/stoat/hooks.json so users get the documented hook
        # surface without extra setup. Tests pass an explicit emitter
        # (or None) to opt out.
        self._hook_emitter: HookEmitter | None
        if hook_emitter is None:
            self._hook_emitter = build_emitter_from_path()
        else:
            self._hook_emitter = hook_emitter

        initial_mode = MODE_SHELL if start_in_shell_mode else MODE_AGENT
        self.shell_mode = ShellModeManager(mode=initial_mode)
        self.plan_mode = PlanModeManager(active=bool(start_in_plan_mode))
        self.palette: SlashPalette = build_default_palette(
            shell_mode=self.shell_mode,
            model=model,
            on_clear=self._on_clear,
            plan_mode=self.plan_mode,
        )

        # WHY: when tests pass an ``input_fn``, we honour it as-is — no
        # chord wiring, no prompt_toolkit. For real interactive use we
        # build the chord adapter so Ctrl-X p / s / h fire. The adapter
        # itself decides whether prompt_toolkit is available and emits
        # a one-line stderr hint when it falls back.
        self._adapter: Any = None
        if input_fn is None:
            callbacks = ChordCallbacks(
                on_plan_toggle=self._on_chord_plan,
                on_shell_toggle=self._on_chord_shell,
                on_help=self._on_chord_help,
            )
            self._adapter = build_input_adapter(
                shell_mode=self.shell_mode,
                plan_mode=self.plan_mode,
                callbacks=callbacks,
                stderr=self.stderr,
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
    # Chord callbacks (Ctrl-X p / Ctrl-X s / Ctrl-X h)
    # ------------------------------------------------------------------

    def _on_chord_plan(self, active: bool) -> None:
        """Render a banner when ``Ctrl-X p`` toggles plan mode."""
        if active:
            self._write(
                "(plan mode: each input asks for a plan + confirmation. "
                "Type /plan or Ctrl-X p to leave.)"
            )
        else:
            self._write("(plan mode off)")

    def _on_chord_shell(self, mode: str) -> None:
        """Render a banner when ``Ctrl-X s`` toggles shell mode."""
        if mode == MODE_SHELL:
            self._write(
                "(shell mode: each input runs as 'bash -c <input>'. "
                "Type /shell or Ctrl-X s to return to agent mode.)"
            )
        else:
            self._write("(agent mode)")

    def _on_chord_help(self, text: str) -> None:
        """Render the chord help blurb when ``Ctrl-X h`` fires."""
        self._write(text)

    # ------------------------------------------------------------------
    # Input dispatch
    # ------------------------------------------------------------------

    def _read_input(self, prompt: str) -> str:
        """Return the next line of user input.

        Routes through the chord-aware :class:`InputAdapter` for real
        interactive use, or the test-injected ``input_fn`` when present.
        Bracketed-paste sequences are coalesced into a single logical
        input when :attr:`bracketed_paste` is on, so a multi-line paste
        doesn't dispatch one shell-mode subprocess per line.
        """
        if self._user_input_fn is not None:
            line = self._user_input_fn(prompt)
        else:
            assert self._adapter is not None  # set when input_fn is None
            line = str(self._adapter.read_line())
        if self.bracketed_paste and BRACKETED_PASTE_BEGIN in line:
            line = coalesce_bracketed_paste(
                line, read_more=lambda: self._raw_read_line(prompt)
            )
        return line

    def _raw_read_line(self, prompt: str) -> str:
        """Read one more line for paste-coalescing (no recursion through paste detection)."""
        if self._user_input_fn is not None:
            return self._user_input_fn(prompt)
        assert self._adapter is not None
        return str(self._adapter.read_line())

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
        config = LoopConfig(
            cancellation=cancel,
            hook_emitter=self._hook_emitter,
        )
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
    # Plan-mode turn
    # ------------------------------------------------------------------

    def run_plan_turn(self, prompt: str) -> str:
        """Run a single plan-only turn and persist the plan.

        Plan mode swaps the system prompt for the plan-mode addendum
        (see :data:`chimera.stoat.plan_mode.PLAN_SYSTEM_PROMPT`) so the
        LLM is asked to *plan* — emit a step-by-step plan and ask for
        confirmation — rather than to act. The resulting plan is saved
        to ``~/.chimera/plans/`` so it can be reviewed or resumed later.

        Errors fall back to a degraded textual plan so the REPL is still
        usable when the agent stack or provider isn't available.

        Args:
            prompt: User message describing what to plan.

        Returns:
            Rendered plan text plus a footer indicating where the plan
            was saved.
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
            return f"stoat: plan-mode stack unavailable: {exc}"

        try:
            provider = self._build_provider()
        except Exception as exc:  # noqa: BLE001
            return f"stoat: provider error: {exc}"

        env = LocalEnvironment(workdir=self.workdir)
        env.setup()

        cancel = CancellationToken()
        config = LoopConfig(
            cancellation=cancel,
            hook_emitter=self._hook_emitter,
        )
        loop = ReAct(max_steps=self.max_steps, config=config)
        sys_prompt = Prompt.from_string(
            build_plan_system_prompt(
                "You are Stoat, a Chimera coding agent with a shell-mode toggle. "
                "Use tools to inspect the user's repo. Be concise."
            )
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
        except Exception as exc:  # noqa: BLE001
            return f"stoat: plan turn failed: {exc}"
        finally:
            env.cleanup()

        text = str(getattr(result, "output", "") or "")

        # Persist the plan, even when the agent emitted an empty body —
        # the prompt itself is signal worth keeping for /resume.
        plan = Plan.new(
            prompt=prompt,
            content=text,
            model=self.palette.model or self.model,
            cwd=self.workdir,
        )
        try:
            saved = save_plan(plan)
            self.plan_mode.last_plan_id = plan.plan_id
            footer = f"\n\n[plan saved: {saved}]"
        except OSError as exc:
            footer = f"\n\n[stoat: plan save failed: {exc}]"

        self.history.append(("user", f"[plan] {prompt}"))
        self.history.append(("assistant", text))
        return text + footer

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
        prompt = self._current_prompt()
        try:
            line = self._read_input(prompt)
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

        # WHY (W14-3): UserPromptSubmit fires for every non-slash line
        # the user types — slash commands are a CLI-internal surface so
        # firing the hook for them would surprise users who declared
        # the hook expecting prompt-style input.
        fire_user_prompt_submit(
            self._hook_emitter,
            user_prompt=line,
            session_id=self.session_id,
        )

        self.shell_mode.record(line)
        # Posture precedence: plan > shell > agent. Plan wins because
        # it's the explicit "deliberate, don't act" override; shell
        # comes next because it's a posture toggle; everything else is
        # an agent turn.
        if self.plan_mode.is_active():
            text = self.run_plan_turn(line)
        elif self.shell_mode.is_shell_mode():
            text = self.run_shell_turn(line)
        else:
            text = self.run_agent_turn(line)
        if text:
            self._write(text)
        return True

    def _current_prompt(self) -> str:
        """Return the prompt prefix for the active posture (plan > shell)."""
        if self.plan_mode.is_active():
            return self.plan_mode.plan_prompt
        return self.shell_mode.prompt

    def run(self) -> int:
        """Drive the REPL until ``/exit`` or EOF.

        Returns:
            Process exit code. Always ``0`` for normal exits.
        """
        posture = "plan" if self.plan_mode.is_active() else self.shell_mode.mode
        banner = _BANNER_TEMPLATE.format(
            model=self.palette.model or self.model or "(unresolved)",
            posture=posture,
            cwd=self.workdir,
        )
        self._write(banner)
        # WHY (W14-3): SessionStart fires once before the first prompt is
        # rendered to the user. SessionEnd fires on /exit or EOF in a
        # try/finally so a hook always sees the matching pair even
        # when the user crashes out with Ctrl-D mid-turn.
        fire_session_start(self._hook_emitter, session_id=self.session_id)
        try:
            while True:
                keep_going = self.loop_once()
                if not keep_going:
                    return 0
        finally:
            fire_session_end(self._hook_emitter, session_id=self.session_id)


def resolve_resume_session_id(args: argparse.Namespace) -> str | None:
    """Resolve ``--session`` / ``--continue`` into a stoat session id.

    Returns ``None`` when neither flag is set or no candidate session
    exists for the current cwd. ``--session`` (explicit id) wins over
    ``--continue``; this mirrors otter / ferret / weasel resume order.

    Args:
        args: Parsed stoat CLI namespace.

    Returns:
        Resume target session id (newest stoat-* dir for cwd when
        ``--continue``; the explicit value of ``--session`` otherwise).
    """
    explicit = getattr(args, "resume_session", None)
    if explicit:
        return str(explicit)
    if not getattr(args, "continue_latest", False):
        return None
    # Late-import: keep stoat REPL import light when no resume happens.
    from chimera.sessions.eventlog.resume_helpers import find_latest_run

    cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())
    return find_latest_run("stoat-", cwd=cwd)


def run(args: argparse.Namespace) -> int:
    """Entry point invoked by :mod:`chimera.stoat.cli`.

    Builds a :class:`StoatRepl` from the parsed CLI namespace and runs
    it against ``sys.stdin`` / ``sys.stdout``. ``--session <id>`` and
    ``--continue`` / ``-c`` resolve to a prior stoat session and
    pre-render its prompt + last assistant turn into the REPL banner so
    the user has context for their next message.

    Args:
        args: Parsed stoat CLI namespace. Reads ``model``, ``cwd``,
            ``max_steps``, ``shell_mode``; tolerates missing attributes
            via :func:`getattr`.

    Returns:
        Process exit code.
    """
    workdir = getattr(args, "cwd", None) or os.getcwd()
    max_steps_raw = getattr(args, "max_steps", 50) or 50

    # W14-3: --continue / --session resume.
    resume_id = resolve_resume_session_id(args)
    resumed_session_id = ""
    resume_banner: str = ""
    if resume_id:
        try:
            from chimera.stoat.sessions import get_session as _get_session

            detail = _get_session(resume_id)
            resumed_session_id = resume_id
            s = detail.summary
            resume_banner = (
                f"[resumed session {resume_id}]\n"
                f"  model: {s.get('model', '')}\n"
                f"  prompt: {s.get('prompt', '')}\n"
            )
        except Exception as exc:  # noqa: BLE001 — never block REPL on resume err
            sys.stderr.write(
                f"stoat: --session resume failed for {resume_id!r}: {exc}\n"
            )
            resumed_session_id = ""

    repl = StoatRepl(
        model=getattr(args, "model", None),
        workdir=workdir,
        max_steps=int(max_steps_raw),
        start_in_shell_mode=bool(getattr(args, "shell_mode", False)),
        start_in_plan_mode=bool(getattr(args, "plan_mode", False)),
        session_id=resumed_session_id,
    )
    if resume_banner:
        repl.out.write(resume_banner)
        repl.out.flush()
    return repl.run()
