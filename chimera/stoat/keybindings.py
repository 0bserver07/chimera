"""Ctrl-X chord key bindings for the stoat REPL.

Stoat documents ``Ctrl-X`` as the headline shell-mode shortcut, but the
default REPL uses a line-oriented ``input()`` call which can't observe
key chords mid-line — terminals deliver the literal control byte rather
than a structured event. This module wires the *real* chord behaviour by
building a :mod:`prompt_toolkit` :class:`PromptSession` when the optional
dep is installed and falling back to ``input()`` otherwise.

Three chord bindings are exposed (all behind the ``Ctrl-X`` prefix —
nothing fires until the user presses ``Ctrl-X`` then a follow-up key):

* ``Ctrl-X p`` — toggle plan mode (third posture beyond agent/shell).
* ``Ctrl-X s`` — toggle shell mode (parity with the existing ``/shell``
  slash).
* ``Ctrl-X h`` — print a short chord help blurb to stderr.

Anything else after ``Ctrl-X`` is treated as "unknown chord" (a polite
hint is rendered on stderr; the partial chord is discarded).

Keeping the dep optional matters: stoat's CLI must not require
``prompt_toolkit`` to import or run the slash palette / shell-mode
toggle. The *only* feature that requires it is the in-buffer chord; with
``prompt_toolkit`` absent we still ship :class:`InputAdapter` which falls
back to ``input()`` and prints a one-line stderr hint so the user knows
why the chord isn't firing.

Trademark hygiene: the module describes the chord generically as
"Ctrl-X chord prefix"; the upstream brand that pioneered the binding is
never named in source.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, TextIO

from chimera.stoat.plan_mode import MODE_PLAN, PlanModeManager
from chimera.stoat.shell_mode import MODE_AGENT, MODE_SHELL, ShellModeManager

__all__ = [
    "CHORD_HELP_TEXT",
    "ChordCallbacks",
    "InputAdapter",
    "build_input_adapter",
    "prompt_toolkit_available",
]


CHORD_HELP_TEXT = (
    "Ctrl-X chord (release before tap):\n"
    "  Ctrl-X p   toggle plan mode (planner posture)\n"
    "  Ctrl-X s   toggle shell mode (bash -c posture)\n"
    "  Ctrl-X h   show this chord help"
)
"""Help string rendered by ``Ctrl-X h`` and ``/help``. Kept short so it
fits inside a single REPL turn without scrolling."""


_FALLBACK_HINT = (
    "stoat: Ctrl-X chord requires prompt_toolkit; falling back to "
    "line-oriented input. Install with `pip install prompt_toolkit` to "
    "enable Ctrl-X p / Ctrl-X s / Ctrl-X h."
)
"""One-line stderr hint emitted on the first ``read_line`` call when
``prompt_toolkit`` isn't installed. Suppressed thereafter so subsequent
turns don't spam stderr."""


def prompt_toolkit_available() -> bool:
    """Return ``True`` iff :mod:`prompt_toolkit` can be imported.

    Cheap probe — actually imports the module so callers can trust the
    boolean. We don't cache the result because the typical lifetime of
    the REPL process is short and the cost of a second import is dwarfed
    by the cost of the first prompt round-trip.
    """
    try:
        import importlib

        importlib.import_module("prompt_toolkit")
    except Exception:  # noqa: BLE001 — any failure means "not available"
        return False
    return True


@dataclass
class ChordCallbacks:
    """Callbacks the REPL passes into the chord wiring.

    Attributes:
        on_plan_toggle: Invoked when the user presses ``Ctrl-X p``.
            Receives the new plan-mode boolean (``True`` if plan mode
            was just enabled). The REPL renders a banner and refreshes
            the prompt prefix.
        on_shell_toggle: Invoked when the user presses ``Ctrl-X s``.
            Receives the new shell-mode value (``"shell"`` or
            ``"agent"``).
        on_help: Invoked when the user presses ``Ctrl-X h`` — typically
            writes :data:`CHORD_HELP_TEXT` to the REPL output. Receives
            the help string so callers can route to stdout / stderr /
            both.
    """

    on_plan_toggle: Callable[[bool], None] | None = None
    on_shell_toggle: Callable[[str], None] | None = None
    on_help: Callable[[str], None] | None = None


class InputAdapter:
    """Read one line from the user, optionally honouring the chord prefix.

    The adapter is constructed once per REPL session by
    :func:`build_input_adapter` and re-used across turns. Two backends:

    * **prompt_toolkit** — when the dep is installed, a
      :class:`prompt_toolkit.PromptSession` is built with key bindings
      for ``Ctrl-X p / s / h``. Chord activations fire the matching
      callback and update the prompt prefix; the regular ``Enter`` flow
      returns the typed line.

    * **stdlib** — when the dep is absent, the adapter falls back to
      ``input()`` and prints a one-line stderr hint *once* explaining
      why the chord isn't active.

    Both backends honour :class:`ShellModeManager` / :class:`PlanModeManager`
    so the rendered prompt prefix follows the active posture.

    Attributes:
        shell_mode: Manager whose ``prompt`` property answers
            "agent vs shell prompt prefix".
        plan_mode: Manager whose ``plan_prompt`` is rendered while plan
            mode is active.
        callbacks: User-supplied chord callbacks.
        stderr: Stream the fallback hint and chord errors are written
            to. Defaults to :data:`sys.stderr`.
    """

    def __init__(
        self,
        *,
        shell_mode: ShellModeManager,
        plan_mode: PlanModeManager,
        callbacks: ChordCallbacks | None = None,
        stderr: TextIO | None = None,
        force_fallback: bool = False,
    ) -> None:
        self.shell_mode = shell_mode
        self.plan_mode = plan_mode
        self.callbacks = callbacks if callbacks is not None else ChordCallbacks()
        self.stderr: TextIO = stderr if stderr is not None else sys.stderr
        self._fallback_hint_emitted = False
        self._session: Any = None
        self._kb: Any = None

        # WHY: we probe + build the prompt_toolkit session eagerly so a
        # late ImportError doesn't surprise mid-loop. ``force_fallback``
        # lets tests pin the stdlib path even when prompt_toolkit is
        # installed in the dev env.
        if force_fallback:
            self._available = False
        else:
            self._available = prompt_toolkit_available()
            if self._available:
                try:
                    self._session, self._kb = self._build_session()
                except Exception:  # noqa: BLE001 — degrade gracefully
                    self._available = False
                    self._session = None
                    self._kb = None

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def current_prompt(self) -> str:
        """Return the prompt prefix for the active posture.

        Plan mode wins over shell mode (a plan turn outranks the
        shell-mode toggle so the user always knows which posture
        consumes their input).
        """
        if self.plan_mode.is_active():
            return self.plan_mode.plan_prompt
        return self.shell_mode.prompt

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    def read_line(self) -> str:
        """Return the next line of user input.

        Raises:
            EOFError: When the user signals EOF (Ctrl-D at start of line
                or stdin closed).
            KeyboardInterrupt: When the user signals Ctrl-C.
        """
        if self._available and self._session is not None:
            return self._read_via_prompt_toolkit()
        return self._read_via_stdlib()

    # ------------------------------------------------------------------
    # Backends
    # ------------------------------------------------------------------

    def _read_via_stdlib(self) -> str:
        """Stdlib fallback path."""
        if not self._fallback_hint_emitted and not prompt_toolkit_available():
            # WHY: only print the hint when prompt_toolkit is actually
            # missing — ``force_fallback`` tests don't want noise.
            self._emit_hint(_FALLBACK_HINT)
            self._fallback_hint_emitted = True
        return input(self.current_prompt())

    def _read_via_prompt_toolkit(self) -> str:
        """prompt_toolkit-backed path."""
        from prompt_toolkit.shortcuts import PromptSession  # type: ignore[import-not-found]

        # ``prompt`` arg is a callable so the prefix is recomputed every
        # render — chord callbacks mutate the manager state and we want
        # the next prompt frame to reflect that immediately.
        assert isinstance(self._session, PromptSession)
        return str(
            self._session.prompt(
                lambda: self.current_prompt(),
                key_bindings=self._kb,
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit_hint(self, text: str) -> None:
        """Write ``text`` to ``stderr`` with a trailing newline."""
        try:
            self.stderr.write(text)
            if not text.endswith("\n"):
                self.stderr.write("\n")
            self.stderr.flush()
        except Exception:  # noqa: BLE001 — best-effort hint only
            pass

    def _build_session(self) -> tuple[Any, Any]:
        """Construct the :class:`PromptSession` + key bindings.

        Raises:
            ImportError: When ``prompt_toolkit`` cannot be imported. The
                caller catches this and degrades to the stdlib path.
        """
        from prompt_toolkit.key_binding import KeyBindings  # type: ignore[import-not-found]
        from prompt_toolkit.shortcuts import PromptSession  # type: ignore[import-not-found]

        kb = KeyBindings()

        # WHY: prompt_toolkit's KeyBindings.add returns an untyped
        # decorator (no PEP 561 stubs), so mypy flags every decorated
        # body as ``untyped-decorator``. The cast loop preserves the
        # registration semantics while keeping ``mypy --strict`` clean.
        def _on_plan(_event: Any) -> None:  # pragma: no cover — prompt_toolkit-only
            self._handle_plan_chord()

        def _on_shell(_event: Any) -> None:  # pragma: no cover — prompt_toolkit-only
            self._handle_shell_chord()

        def _on_help(_event: Any) -> None:  # pragma: no cover — prompt_toolkit-only
            self._handle_help_chord()

        kb.add("c-x", "p")(_on_plan)
        kb.add("c-x", "s")(_on_shell)
        kb.add("c-x", "h")(_on_help)

        session = PromptSession()
        return session, kb

    # ------------------------------------------------------------------
    # Chord handlers (also unit-testable directly)
    # ------------------------------------------------------------------

    def _handle_plan_chord(self) -> None:
        """``Ctrl-X p`` handler — toggle plan mode + fire callback."""
        # WHY: plan mode is independent of shell mode so we don't touch
        # ``shell_mode`` here. The REPL still routes input through the
        # shell-mode manager; plan mode just biases the agent turn.
        new_state = self.plan_mode.toggle()
        cb = self.callbacks.on_plan_toggle
        if cb is not None:
            cb(new_state)

    def _handle_shell_chord(self) -> None:
        """``Ctrl-X s`` handler — toggle shell mode + fire callback."""
        # WHY: leaving plan mode when entering shell mode keeps the
        # "one posture wins" invariant. The user can always hit Ctrl-X p
        # again to come back.
        if self.plan_mode.is_active():
            self.plan_mode.disable()
        new_mode = self.shell_mode.toggle()
        cb = self.callbacks.on_shell_toggle
        if cb is not None:
            cb(new_mode)

    def _handle_help_chord(self) -> None:
        """``Ctrl-X h`` handler — emit the chord help blurb."""
        cb = self.callbacks.on_help
        if cb is not None:
            cb(CHORD_HELP_TEXT)
        else:
            self._emit_hint(CHORD_HELP_TEXT)


def build_input_adapter(
    *,
    shell_mode: ShellModeManager,
    plan_mode: PlanModeManager | None = None,
    callbacks: ChordCallbacks | None = None,
    stderr: TextIO | None = None,
    force_fallback: bool | None = None,
) -> InputAdapter:
    """Construct an :class:`InputAdapter` with sensible defaults.

    Args:
        shell_mode: The REPL's :class:`ShellModeManager`.
        plan_mode: Optional :class:`PlanModeManager`. When ``None``, a
            fresh inactive manager is created.
        callbacks: Chord callbacks. When ``None``, an empty bundle is
            used (chords still toggle managers but no banner fires).
        stderr: Override the stream the fallback hint is written to.
        force_fallback: When ``True``, skip the prompt_toolkit probe
            and pin the stdlib backend. ``False`` forces the
            prompt_toolkit path. ``None`` (default) honors the env-var
            ``STOAT_NO_CHORD=1`` then auto-detects.

    Returns:
        A configured :class:`InputAdapter`.
    """
    if plan_mode is None:
        plan_mode = PlanModeManager()
    if force_fallback is None:
        force_fallback = bool(os.environ.get("STOAT_NO_CHORD"))
    return InputAdapter(
        shell_mode=shell_mode,
        plan_mode=plan_mode,
        callbacks=callbacks,
        stderr=stderr,
        force_fallback=force_fallback,
    )


# Re-export for convenience — keeps ``from chimera.stoat.keybindings import *``
# round-trippable without leaking the constant module.
_ = (MODE_AGENT, MODE_SHELL, MODE_PLAN)
