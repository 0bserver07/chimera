"""Shell-integration (OSC 133) zone marks around committed turns.

Terminals that implement the shell-integration protocol let a user jump
prompt-to-prompt, select "the output of that command", and fold a command's
output — but only if *something* tells them where those zones are. A shell
does it with four zero-width escape sequences; an agent frontend whose
transcript lands in the terminal's own scrollback can do exactly the same,
turning every turn into a navigable zone.

The vocabulary (all zero-width, all inert on terminals that do not implement
it — an unrecognized OSC is consumed to its terminator and drawn as nothing):

===============  =====================================================
``OSC 133 ; A``  prompt starts here
``OSC 133 ; B``  prompt ends / user input starts here
``OSC 133 ; C``  command output starts here
``OSC 133 ; D``  command ended (optionally ``;<exit-code>``)
===============  =====================================================

**Where these are emitted, and where they are not.** Marks only make sense in
the normal buffer, where the terminal owns the rows and keeps them in its
scrollback — that is the inline frontend
(:mod:`chimera.tui.inline_frontend`, over :mod:`chimera.tui.scrollback`).
The full-screen frontend runs in the alternate screen: there is no scrollback
to navigate there and the app repaints the rows itself, so it emits nothing.

**Why marks queue instead of writing.** In the inline hybrid, raw output is
never written at the cursor's resting position — the cursor lives in the
pinned bottom band, and history rows are written inside a scroll-region batch
(:func:`chimera.tui.scrollback.commit_lines`). A mark written at the parked
cursor would attach to the *band*, not to a transcript row. So marks are
queued and drained as the ``prefix`` of the next committed row, which places
each one at column 1 of the row it describes.

**Off by default.** ``[tui] shell_integration = true`` opts in
(:func:`load_shell_integration`). With it off, :meth:`ShellMarks.take` returns
``""`` and every byte the frontend writes is what it wrote before this module
existed.

Stdlib only — no terminal, no framework, nothing to import beyond the config
reader.
"""
from __future__ import annotations

import os
from typing import Any

__all__ = [
    "COMMAND_END",
    "COMMAND_START",
    "OUTPUT_START",
    "PROMPT_START",
    "ShellMarks",
    "command_end",
    "load_shell_integration",
    "mark",
]

#: OSC introducer and the ST (string terminator) this module ends marks with.
#: ST is the standards-correct terminator; terminals that expect BEL accept it
#: too, and every terminal that knows neither simply consumes the sequence.
_OSC = "\x1b]"
_ST = "\x1b\\"


def mark(kind: str) -> str:
    """Build one OSC 133 mark.

    Args:
        kind: The zone letter (``A``, ``B``, ``C``) or a full parameter
            string (``"D;0"``).

    Returns:
        The escape sequence, e.g. ``"\\x1b]133;A\\x1b\\\\"``.
    """
    return f"{_OSC}133;{kind}{_ST}"


#: Prompt starts here — the mark terminals navigate between.
PROMPT_START = mark("A")
#: Prompt ends, user input starts here.
COMMAND_START = mark("B")
#: Command output starts here.
OUTPUT_START = mark("C")
#: Command ended, exit status unknown.
COMMAND_END = mark("D")


def command_end(exit_code: int | None = None) -> str:
    """Build the command-end mark, optionally carrying an exit code.

    Args:
        exit_code: ``0`` for a clean turn, non-zero for a failed one, or
            ``None`` to report no status.

    Returns:
        The escape sequence.
    """
    return COMMAND_END if exit_code is None else mark(f"D;{int(exit_code)}")


class ShellMarks:
    """Queue of pending zone marks, drained onto the next committed row.

    One instance per frontend. Every method is a no-op while disabled, and
    :meth:`take` then returns ``""`` — so the default build emits not one
    extra byte.

    Args:
        enabled: Whether to emit anything at all (the ``[tui]
            shell_integration`` knob).
    """

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = bool(enabled)
        self._pending: list[str] = []

    def _queue(self, sequence: str) -> None:
        if self.enabled:
            self._pending.append(sequence)

    def turn_start(self) -> None:
        """A turn begins: the row about to be committed is the prompt row."""
        self._queue(PROMPT_START)
        self._queue(COMMAND_START)

    def output_start(self) -> None:
        """The prompt row is committed; the next row begins the output zone."""
        self._queue(OUTPUT_START)

    def turn_end(self, *, ok: bool = True) -> None:
        """A turn ended.

        The mark rides the next committed row — which is normally the next
        turn's prompt row, exactly the ``D`` then ``A`` pairing a shell emits
        at its next prompt. A session that quits before another row is
        committed leaves the zone open; the shell's own next prompt mark
        closes it.

        Args:
            ok: False when the turn ended in an error (reported as exit 1).
        """
        self._queue(command_end(0 if ok else 1))

    def take(self) -> str:
        """Drain the queued marks as a prefix for the next committed row.

        Returns:
            The concatenated sequences, or ``""`` when disabled or empty.
        """
        if not self._pending:
            return ""
        out = "".join(self._pending)
        self._pending.clear()
        return out


def load_shell_integration(
    project_dir: str | os.PathLike[str] | None = None,
    *,
    home: str | os.PathLike[str] | None = None,
) -> bool:
    """Read the ``[tui] shell_integration`` knob from the unified config chain.

    Off by default, and best-effort: a broken or missing config reads as off
    rather than blocking a launch.

    Args:
        project_dir: Project root (default: cwd).
        home: Home-directory override (tests).

    Returns:
        True when the user opted in.
    """
    try:
        from chimera.config.user_config import load_tui_config

        tui: dict[str, Any] = load_tui_config(project_dir, home=home)
    except Exception:  # noqa: BLE001 — config discovery must not block a launch
        return False
    return bool(tui.get("shell_integration", False))
