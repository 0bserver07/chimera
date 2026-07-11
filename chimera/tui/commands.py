"""Slash-command registry for the Chimera TUIs (spec P4: one registry per surface).

One table — :data:`COMMAND_DEFS` — is the single source for the multiplexer's
slash-command *catalog*: what exists (name + aliases + argument hint +
description), where it applies (single-lane vs multi-lane surface, the #172
split), what autocomplete offers (:func:`completion_catalog`, the registry
half of R-IN-2), and what ``/help`` prints (:func:`help_lines` — a commands
section from this registry plus a keys section rendering the *currently
bound* keys via :func:`chimera.tui.keys.key_for`, R-KEY-3).

Dispatch deliberately stays in the frontend (``MultiplexApp._handle_command``)
— this module owns the data, not the handlers. :func:`canonical` maps a typed
token (name or alias, with or without the slash) back to its registry entry
so dispatchers and help can treat ``/quit`` and ``/exit`` as one command.

Stdlib-only, widget-free, exhaustively unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from chimera.tui.keys import KEY_ACTIONS, ResolvedBinding, display_key, key_for

__all__ = [
    "COMMAND_DEFS",
    "SlashCommand",
    "canonical",
    "commands_for",
    "completion_catalog",
    "help_lines",
]


@dataclass(frozen=True)
class SlashCommand:
    """One slash command in the registry.

    Args:
        name: Canonical name, without the leading slash.
        description: One-line ``/help`` text.
        aliases: Alternative names (also completable; dispatch canonicalizes).
        args_hint: Argument placeholder shown in help (e.g. ``"[id]"``).
        context: Where the command exists: ``single`` (one-lane surface),
            ``multi`` (2+ lanes), or ``both``.
    """

    name: str
    description: str
    aliases: tuple[str, ...] = ()
    args_hint: str = ""
    context: str = "both"

    @property
    def slash(self) -> str:
        """The typed form: ``/name``."""
        return "/" + self.name


#: The one table. Alphabetical by name (the completion catalog sorts anyway;
#: keeping the source sorted keeps diffs reviewable).
COMMAND_DEFS: tuple[SlashCommand, ...] = (
    SlashCommand("broadcast", "route new input to every lane", context="multi"),
    SlashCommand("clear", "clear the focused lane's conversation"),
    SlashCommand("cohorts", "pick a saved cohort to resume"),
    SlashCommand("cost", "cumulative cost (per lane when racing)"),
    SlashCommand("exit", "leave the app", aliases=("quit",)),
    SlashCommand("export", "persist the cohort artifact now"),
    SlashCommand("help", "commands and current keybindings"),
    SlashCommand("keys", "effective keybinding table (default/user/migrated)"),
    SlashCommand("model", "model per lane (context window when single)"),
    SlashCommand("resume", "switch to a saved cohort", args_hint="[id]"),
    SlashCommand("results", "open the comparison screen"),
    SlashCommand("summary", "cohort scoreboard"),
    SlashCommand("target", "route input to the focused lane only", context="multi"),
    SlashCommand("tools", "tools available to the focused lane"),
)

_BY_TOKEN: dict[str, SlashCommand] = {}
for _cmd in COMMAND_DEFS:
    _BY_TOKEN[_cmd.name] = _cmd
    for _alias in _cmd.aliases:
        _BY_TOKEN[_alias] = _cmd
del _cmd


def _applies(cmd: SlashCommand, single: bool | None) -> bool:
    if single is None or cmd.context == "both":
        return True
    return cmd.context == ("single" if single else "multi")


def commands_for(*, single: bool | None = None) -> tuple[SlashCommand, ...]:
    """Registry entries for a surface.

    Args:
        single: True for the one-lane surface, False for multi-lane,
            ``None`` for the unfiltered registry.

    Returns:
        The matching commands, in registry order.
    """
    return tuple(c for c in COMMAND_DEFS if _applies(c, single))


def completion_catalog(*, single: bool | None = None) -> list[str]:
    """Slash-prefixed completion candidates: names *and* aliases (R-IN-2).

    Aliases complete as themselves (``/q`` still tab-completes to ``/quit``)
    — dispatch canonicalizes, completion does not rewrite what the user
    typed.

    Args:
        single: Surface filter, as in :func:`commands_for`.

    Returns:
        Sorted candidate strings for the prompt's autocomplete.
    """
    names: list[str] = []
    for cmd in commands_for(single=single):
        names.append(cmd.slash)
        names.extend("/" + a for a in cmd.aliases)
    return sorted(names)


def canonical(token: str) -> SlashCommand | None:
    """Resolve a typed command token (name or alias) to its registry entry.

    Args:
        token: The first word of the input, with or without the leading
            slash (``"/quit"``, ``"quit"``).

    Returns:
        The command, or ``None`` for an unknown token.
    """
    return _BY_TOKEN.get(token.lstrip("/"))


def help_lines(
    *,
    single: bool,
    keymap: Mapping[str, ResolvedBinding] | None = None,
) -> list[str]:
    """Generate ``/help``: a commands section and a keys section.

    Both derive from their registries — commands from :data:`COMMAND_DEFS`
    (aliases and argument hints inline), keys from the keybinding registry
    rendering the *currently bound* key per action (R-KEY-3: after a
    ``tui.keybinds`` rebind the help shows the new key; an unbound action is
    omitted rather than lied about).

    Args:
        single: True for the one-lane surface (filters both sections the
            same way the app gates commands and actions).
        keymap: The app's resolved keymap; ``None`` shows defaults.

    Returns:
        Display lines (commands, keys, and a fixed composer-keys trailer —
        the composer's editing keys are not yet registry-bound).
    """
    parts: list[str] = []
    for cmd in commands_for(single=single):
        entry = cmd.slash
        if cmd.aliases:
            entry += "(" + " ".join("/" + a for a in cmd.aliases) + ")"
        if cmd.args_hint:
            entry += f" {cmd.args_hint}"
        parts.append(entry)
    lines = ["commands: " + " ".join(parts)]

    hints: list[str] = []
    for action in KEY_ACTIONS:
        if action.context != "global" or not action.show_in_footer:
            continue
        if (action.multi_only and single) or (action.single_only and not single):
            continue
        key = key_for(action.action_id, keymap)
        if not key:
            continue  # unbound by the user: no key to advertise
        hints.append(f"{display_key(key)} {action.description}")
    lines.append("keys: " + " · ".join(hints))
    lines.append("input: Enter submit · Ctrl+J newline · type while running to steer")
    return lines
