"""The slash-command registry: catalog, context filtering, /help derivation.

Pure (stdlib-only) except the autocomplete round-trip tests, which use the
widget-free helpers from :mod:`chimera.tui.prompt`.
"""
import pytest

from chimera.tui.commands import (
    COMMAND_DEFS,
    canonical,
    commands_for,
    completion_catalog,
    help_lines,
)
from chimera.tui.keys import KEY_ACTIONS, resolve_keymap

# The multiplexer's slash catalog as shipped before the registry existed
# (multiplex.py SLASH_COMMANDS): pinned so no command silently disappears.
PREVIOUS_SLASH_COMMANDS = [
    "/broadcast", "/clear", "/cohorts", "/cost", "/exit", "/export",
    "/help", "/model", "/quit", "/resume", "/results", "/summary",
    "/target", "/tools",
]
# The #172 single-lane filtering as previously hardcoded.
PREVIOUS_MULTI_ONLY = {"/broadcast", "/target"}


# -- catalog parity ----------------------------------------------------------
def test_catalog_carries_every_previous_command():
    catalog = completion_catalog()
    for cmd in PREVIOUS_SLASH_COMMANDS:
        assert cmd in catalog, f"{cmd} dropped from the registry catalog"
    # deliberate additions only: /keys (R-KEY-1), /statusline (R-STAT-1),
    # /budget (#170), /theme (R-THEME-3), /resync (the hot-swap seam)
    assert set(catalog) - set(PREVIOUS_SLASH_COMMANDS) == {
        "/keys", "/statusline", "/budget", "/theme", "/resync",
    }


def test_catalog_is_sorted_slash_prefixed_strings():
    catalog = completion_catalog()
    assert catalog == sorted(catalog)
    assert all(c.startswith("/") for c in catalog)


def test_single_surface_drops_routing_modes_only():
    single = set(completion_catalog(single=True))
    both = set(completion_catalog())
    assert both - single == PREVIOUS_MULTI_ONLY
    assert "/quit" in single and "/exit" in single  # aliases still complete


def test_multi_surface_keeps_everything():
    assert set(completion_catalog(single=False)) == set(completion_catalog())


def test_names_and_aliases_are_unique():
    tokens = []
    for cmd in COMMAND_DEFS:
        tokens.append(cmd.name)
        tokens.extend(cmd.aliases)
    assert len(tokens) == len(set(tokens))


def test_contexts_are_valid():
    assert all(c.context in ("single", "multi", "both") for c in COMMAND_DEFS)


def test_commands_for_filters_by_context():
    multi_only = {c.name for c in COMMAND_DEFS if c.context == "multi"}
    assert multi_only == {"broadcast", "target"}
    single_names = {c.name for c in commands_for(single=True)}
    assert single_names.isdisjoint(multi_only)
    assert {c.name for c in commands_for(single=False)} == \
        {c.name for c in COMMAND_DEFS}


# -- canonicalization -----------------------------------------------------------
def test_canonical_resolves_names_and_aliases():
    assert canonical("/exit").name == "exit"
    assert canonical("/quit").name == "exit"       # alias → canonical
    assert canonical("quit").name == "exit"        # slash optional
    assert canonical("/nope") is None


# -- /help derivation ------------------------------------------------------------
def test_help_lists_every_command_for_the_surface():
    text = "\n".join(help_lines(single=False))
    for cmd in commands_for(single=False):
        assert cmd.slash in text
    assert "/resume [id]" in text                  # args hint rendered
    assert "/exit(/quit)" in text                  # alias rendered inline


def test_help_single_omits_multi_only_commands():
    text = "\n".join(help_lines(single=True))
    assert "/broadcast" not in text and "/target" not in text
    assert "/keys" in text


def test_help_keys_section_shows_default_bindings():
    text = "\n".join(help_lines(single=False))
    assert "Ctrl+C" in text                        # cancel_all
    assert "Ctrl+X Expand tool output" in text     # the new R-FOLD-2 toggle
    assert "Ctrl+B" in text                        # multi surface keeps broadcast


def test_help_keys_section_respects_lane_mode():
    single = "\n".join(help_lines(single=True))
    assert "Ctrl+B" not in single                  # broadcast hidden with 1 lane
    assert "Ctrl+L Clear" in single                # single-only clear shown
    multi = "\n".join(help_lines(single=False))
    assert "Ctrl+L Clear" not in multi


def test_help_keys_follow_rebinds():
    # R-KEY-3: after a rebind the help shows the new key, never the default.
    km = resolve_keymap({"toggle_sidebar": "f2"})
    text = "\n".join(help_lines(single=False, keymap=km))
    assert "F2 Sidebar" in text
    assert "Ctrl+T" not in text


def test_help_omits_unbound_actions():
    km = resolve_keymap({"toggle_sidebar": False})
    text = "\n".join(help_lines(single=False, keymap=km))
    assert "Sidebar" not in text                   # no key → no lying hint


def test_help_mentions_composer_basics():
    text = "\n".join(help_lines(single=True))
    assert "Enter submit" in text and "Ctrl+J newline" in text


# -- autocomplete from the registry (R-IN-2, registry half) -----------------------
def test_filter_commands_over_registry_catalog():
    pytest.importorskip("textual")  # prompt.py needs the tui extra
    from chimera.tui.prompt import filter_commands

    catalog = completion_catalog(single=True)
    assert filter_commands("/q", catalog) == ["/quit"]
    assert filter_commands("/re", catalog) == ["/results", "/resume", "/resync"]
    assert filter_commands("/broadcast", catalog) == []   # multi-only, single surface
    assert filter_commands("/b", completion_catalog(single=False)) == ["/broadcast", "/budget"]


def test_complete_command_over_registry_catalog():
    pytest.importorskip("textual")  # prompt.py needs the tui extra
    from chimera.tui.prompt import complete_command

    catalog = completion_catalog(single=False)
    assert complete_command("/ke", catalog) == "/keys "
    assert complete_command("/q", catalog) == "/quit "     # alias completes as itself
    assert complete_command("/re", catalog) == "/res"      # common prefix (results/resume/resync)


# -- registry hygiene shared with the key registry ---------------------------------
def test_slash_and_key_registries_do_not_collide_on_ids():
    # /keys (command) and key actions live in different namespaces; this just
    # pins that every key action referenced by help exists.
    ids = {a.action_id for a in KEY_ACTIONS}
    assert {"cancel_all", "quit", "toggle_expand", "close"} <= ids
