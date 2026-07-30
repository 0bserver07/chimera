"""The slash-command registry: catalog, context filtering, /help derivation,
and the dynamic composition layer that folds plugin commands in.

Pure (stdlib-only) except the autocomplete round-trip tests, which use the
widget-free helpers from :mod:`chimera.tui.prompt`.
"""
import pytest

from chimera.plugins.ui import UIExtensionRegistry
from chimera.tui.commands import (
    COMMAND_DEFS,
    TUICommandContext,
    canonical,
    commands_for,
    completion_catalog,
    dispatch_plugin_command,
    help_lines,
    plugin_command_defs,
    plugin_command_rejections,
)
from chimera.tui.keys import KEY_ACTIONS, resolve_keymap


@pytest.fixture(autouse=True)
def ui_reset():
    """Isolate every test from the process-global plugin UI registry.

    The catalog is now composed dynamically (built-ins + plugin commands),
    so the parity pins below only hold against a clean registry — and the
    dynamic tests must not leak registrations into each other.
    """
    UIExtensionRegistry._reset()
    yield
    UIExtensionRegistry._reset()


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
    # /budget (#170), /theme (R-THEME-3), /resync (the hot-swap seam).
    # With a clean plugin registry the dynamic layer adds nothing.
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


# ===========================================================================
# Dynamic composition: plugin commands folded into the catalog
# ===========================================================================

def _register(name, handler=None, **kwargs):
    """Register a plugin command in the (reset) global UI registry."""
    def _default(session, env, args, out):
        out(f"{name}:{args}")
    return UIExtensionRegistry.register_command(
        name, handler or _default, **kwargs,
    )


def test_plugin_command_appears_in_catalog_on_both_surfaces():
    _register("pilot-hello", help="say hello", aliases=("ph",), plugin="pilot")
    for single in (True, False, None):
        catalog = completion_catalog(single=single)
        assert "/pilot-hello" in catalog and "/ph" in catalog
    defs = plugin_command_defs()
    assert [(d.name, d.source, d.plugin) for d in defs] == [
        ("pilot-hello", "plugin", "pilot"),
    ]
    assert defs[0].context == "both"
    assert plugin_command_rejections() == ()


def test_plugin_commands_can_be_excluded_deliberately():
    _register("pilot-hello", plugin="pilot")
    assert "/pilot-hello" not in completion_catalog(include_plugins=False)
    names = {c.name for c in commands_for(include_plugins=False)}
    assert "pilot-hello" not in names
    assert canonical("/pilot-hello", include_plugins=False) is None


def test_canonical_resolves_plugin_names_and_aliases():
    _register("pilot-hello", aliases=("ph",), plugin="pilot")
    assert canonical("/pilot-hello").source == "plugin"
    assert canonical("ph").name == "pilot-hello"
    assert canonical("/nope") is None


def test_plugin_name_colliding_with_builtin_is_rejected_whole():
    _register("help", plugin="pilot")
    assert plugin_command_defs() == ()
    assert plugin_command_rejections() == (
        ("help", "shadows the built-in /help — built-ins win"),
    )
    # dispatch resolution still lands on the built-in, untouched
    assert canonical("/help").source == "builtin"


def test_plugin_name_colliding_with_builtin_alias_is_rejected_whole():
    _register("quit", plugin="pilot")  # /quit is the built-in alias of /exit
    assert plugin_command_defs() == ()
    (token, why), = plugin_command_rejections()
    assert token == "quit" and "shadows the built-in /exit" in why
    assert canonical("/quit").name == "exit"


def test_plugin_alias_colliding_with_builtin_drops_only_the_alias():
    _register("greet", aliases=("cost", "gr"), plugin="pilot")
    (cmd,) = plugin_command_defs()
    assert cmd.name == "greet" and cmd.aliases == ("gr",)
    (token, why), = plugin_command_rejections()
    assert token == "cost"
    assert "alias of plugin command /greet" in why
    assert "shadows the built-in /cost" in why
    assert canonical("/cost").source == "builtin"


def test_plugin_vs_plugin_token_conflict_is_first_come_and_loud():
    # "alpha" (with alias "zz") registers first; a later command named "zz"
    # finds its token claimed. get_all_commands() is ascending by name, so
    # "alpha" composes first deterministically.
    _register("alpha", aliases=("zz",), plugin="one")
    _register("zz", plugin="two")
    defs = plugin_command_defs()
    assert [d.name for d in defs] == ["alpha"]
    assert ("zz", "already provided by plugin command /alpha") in \
        plugin_command_rejections()


def test_help_lines_render_plugin_commands_with_provenance():
    _register("pilot-hello", help="say hello", aliases=("ph",), plugin="pilot")
    text = "\n".join(help_lines(single=True))
    assert "plugin commands: /pilot-hello(/ph) — say hello [pilot]" in text


def test_help_lines_name_rejected_tokens():
    _register("help", plugin="pilot")
    text = "\n".join(help_lines(single=True))
    assert "plugin commands rejected (built-ins win): /help" in text


def test_help_lines_have_no_plugin_sections_when_registry_is_clean():
    text = "\n".join(help_lines(single=True))
    assert "plugin commands" not in text


def test_plugin_commands_complete_in_the_prompt_catalog():
    pytest.importorskip("textual")  # prompt.py needs the tui extra
    from chimera.tui.prompt import complete_command, filter_commands

    _register("pilot-hello", aliases=("ph",), plugin="pilot")
    catalog = completion_catalog(single=True)
    assert filter_commands("/pilot-h", catalog) == ["/pilot-hello"]
    assert complete_command("/pilot-h", catalog) == "/pilot-hello "


# -- dispatch: the (session, env, args, out) adaptation ------------------------

def _context(said, **kwargs):
    kwargs.setdefault("driver", object())
    return TUICommandContext(
        say=lambda msg, style="dim": said.append((msg, style)), **kwargs,
    )


def test_dispatch_runs_the_handler_with_the_thin_context():
    seen = {}

    def handler(session, env, args, out):
        seen["session"] = session
        seen["env_workdir"] = env.workdir
        seen["args"] = args
        out("done")

    _register("pilot-hello", handler, plugin="pilot")
    said = []
    ctx = _context(said, busy=True, workdir="/tmp/lane-a", lane_id="A",
                   lane_label="glm-5.2-0", model="glm-5.2", single=False)
    assert dispatch_plugin_command("/pilot-hello", "a b", ctx) is True
    assert seen["session"] is ctx
    assert seen["env_workdir"] == "/tmp/lane-a"
    assert seen["args"] == "a b"
    assert said == [("done", "dim")]
    # the context grants exactly the TUI surface — and says so
    assert ctx.surface == "tui" and ctx.busy is True and ctx.single is False


def test_dispatch_resolves_aliases():
    _register("pilot-hello", aliases=("ph",), plugin="pilot")
    said = []
    assert dispatch_plugin_command("ph", "", _context(said)) is True
    assert said == [("pilot-hello:", "dim")]


def test_dispatch_returns_false_for_unknown_and_rejected_tokens():
    _register("help", plugin="pilot")  # rejected: shadows the built-in
    said = []
    assert dispatch_plugin_command("/help", "", _context(said)) is False
    assert dispatch_plugin_command("/nope", "", _context(said)) is False
    assert said == []  # nothing ran, nothing was said


def test_dispatch_reports_a_raising_handler_as_a_refusal():
    def needs_repl(session, env, args, out):
        session.provider.chat("x")  # a REPL-session ability the TUI can't grant

    _register("pilot-repl", needs_repl, plugin="pilot")
    said = []
    assert dispatch_plugin_command("/pilot-repl", "", _context(said)) is True
    assert len(said) == 1
    msg, style = said[0]
    assert msg.startswith("plugin command /pilot-repl refused:")
    assert style == "red"


def test_context_degrades_like_a_bare_session_for_defensive_handlers():
    # REPL handlers probe with getattr(session, "provider", None) — the thin
    # context must degrade the same way instead of pretending.
    ctx = TUICommandContext()
    assert getattr(ctx, "provider", None) is None
    assert getattr(ctx, "cost_tracker", None) is None
    assert ctx.surface == "tui"
