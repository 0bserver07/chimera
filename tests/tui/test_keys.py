"""The declarative keybinding registry (R-KEY-1..4) and its app wiring.

Pure registry tests run without the [tui] extra; tests that build framework
``Binding`` objects or drive the app import textual per-test via importorskip.
"""
import pytest

from chimera.tui.keys import (
    KEY_ACTIONS,
    LEGACY_ACTION_NAMES,
    KeymapError,
    display_key,
    hidden_actions,
    key_for,
    keymap_table,
    load_user_keybinds,
    resolve_keymap,
)

# The multiplexer's BINDINGS list as shipped before the registry existed
# (multiplex.py, #172 era): (key, action, priority). Pinned literally so a
# registry regression can never silently drop or rebind a shipped default.
PREVIOUS_BINDINGS = [
    ("ctrl+c", "cancel_all", True),
    ("ctrl+d", "quit", False),
    ("tab", "smart_tab", True),
    ("shift+tab", "focus_prev_lane", True),
    ("ctrl+b", "toggle_broadcast", False),
    ("ctrl+g", "cancel_focused", False),
    ("ctrl+o", "clear_focused", False),
    ("ctrl+l", "clear_lane", False),
    ("ctrl+r", "show_results", False),
    ("ctrl+e", "toggle_reasoning", True),
    ("ctrl+t", "toggle_sidebar", False),
]
# CohortPickerScreen's BINDINGS as previously shipped.
PREVIOUS_PAGER_BINDINGS = [("escape", "close"), ("q", "close")]
# The #172 lane-mode gating sets as previously hardcoded in multiplex.py.
PREVIOUS_SINGLE_HIDDEN = {
    "focus_prev_lane", "toggle_broadcast", "cancel_focused", "clear_focused",
}
PREVIOUS_MULTI_HIDDEN = {"clear_lane"}


# -- registry ↔ previous-BINDINGS parity ----------------------------------
def test_registry_carries_every_previously_bound_key():
    defaults = {a.action_id: a for a in KEY_ACTIONS}
    for key, action, priority in PREVIOUS_BINDINGS:
        assert action in defaults, f"action {action!r} dropped from the registry"
        a = defaults[action]
        assert key in a.default_keys, f"{action}: default key {key!r} lost"
        assert a.priority is priority, f"{action}: priority flag changed"
        assert a.context == "global"
        assert a.show_in_footer is True
    for key, action in PREVIOUS_PAGER_BINDINGS:
        a = defaults[action]
        assert key in a.default_keys and a.context == "pager"


def test_registry_binding_order_matches_previous_footer_order():
    global_ids = [a.action_id for a in KEY_ACTIONS if a.context == "global"]
    previous = [action for _, action, _ in PREVIOUS_BINDINGS]
    assert global_ids[: len(previous)] == previous  # new actions append only


def test_new_expand_action_is_registered_with_priority():
    (a,) = [a for a in KEY_ACTIONS if a.action_id == "toggle_expand"]
    assert a.default_keys == ("ctrl+x",)
    assert a.priority is True  # must beat the composer's own ctrl+x
    assert a.context == "global" and not a.single_only and not a.multi_only


def test_default_keymap_is_conflict_free_per_context():
    seen = set()
    for a in KEY_ACTIONS:
        for key in a.default_keys:
            slot = (a.context, key)
            assert slot not in seen, f"default conflict on {slot}"
            seen.add(slot)


def test_action_ids_are_unique():
    ids = [a.action_id for a in KEY_ACTIONS]
    assert len(ids) == len(set(ids))


# -- #172 lane-mode gating (check_action data) ------------------------------
def test_hidden_actions_match_previous_gating_sets():
    assert hidden_actions(True) == PREVIOUS_SINGLE_HIDDEN
    assert hidden_actions(False) == PREVIOUS_MULTI_HIDDEN


# -- resolve_keymap: overrides ----------------------------------------------
def test_defaults_resolve_with_source_default():
    km = resolve_keymap({})
    assert km["show_results"].keys == ("ctrl+r",)
    assert all(rb.source == "default" for rb in km.values())


def test_override_single_key():
    km = resolve_keymap({"toggle_sidebar": "f2"})
    assert km["toggle_sidebar"].keys == ("f2",)
    assert km["toggle_sidebar"].source == "user"
    assert km["show_results"].keys == ("ctrl+r",)  # others untouched


def test_override_key_list_binds_all():
    km = resolve_keymap({"toggle_reasoning": ["ctrl+e", "f3"]})
    assert km["toggle_reasoning"].keys == ("ctrl+e", "f3")


def test_override_normalizes_case_and_whitespace():
    km = resolve_keymap({"toggle_sidebar": " Ctrl+T "})
    assert km["toggle_sidebar"].keys == ("ctrl+t",)


def test_unbind_with_false():
    km = resolve_keymap({"toggle_sidebar": False})
    assert km["toggle_sidebar"].keys == ()
    assert km["toggle_sidebar"].source == "user"


def test_unbind_with_empty_list():
    km = resolve_keymap({"toggle_sidebar": []})
    assert km["toggle_sidebar"].keys == ()


def test_unknown_action_error_lists_valid_ids():
    with pytest.raises(KeymapError) as exc:
        resolve_keymap({"togle_sidebar": "f2"})
    msg = str(exc.value)
    assert "togle_sidebar" in msg
    for a in KEY_ACTIONS:  # the full menu of valid ids is in the error
        assert a.action_id in msg


@pytest.mark.parametrize("bad", [True, 3, {"k": "v"}, [1], "", "ctrl +x"])
def test_malformed_values_are_loud(bad):
    with pytest.raises(KeymapError):
        resolve_keymap({"toggle_sidebar": bad})


# -- conflict detection (R-KEY-2) --------------------------------------------
def test_conflict_with_a_default_names_both_actions_and_key():
    with pytest.raises(KeymapError) as exc:
        resolve_keymap({"toggle_sidebar": "ctrl+r"})  # ctrl+r = show_results
    msg = str(exc.value)
    assert "toggle_sidebar" in msg and "show_results" in msg and "ctrl+r" in msg


def test_conflict_between_two_overrides_detected():
    with pytest.raises(KeymapError):
        resolve_keymap({"toggle_sidebar": "f9", "toggle_reasoning": "f9"})


def test_swap_is_not_a_conflict():
    km = resolve_keymap({"show_results": "ctrl+t", "toggle_sidebar": "ctrl+r"})
    assert km["show_results"].keys == ("ctrl+t",)
    assert km["toggle_sidebar"].keys == ("ctrl+r",)


def test_same_key_in_different_contexts_is_allowed():
    # "q" closes the pager; binding it globally is layering, not a conflict.
    km = resolve_keymap({"toggle_sidebar": "q"})
    assert km["toggle_sidebar"].keys == ("q",)


def test_rebinding_over_a_hidden_mode_action_still_conflicts():
    # ctrl+l belongs to clear_lane (single-only): a global-context user key
    # on it collides regardless of the current lane mode — contexts, not
    # modes, scope conflicts (modes flip at runtime).
    with pytest.raises(KeymapError):
        resolve_keymap({"toggle_sidebar": "ctrl+l"})


# -- legacy-name migration ----------------------------------------------------
def test_legacy_names_migrate_and_are_marked():
    km = resolve_keymap({"cancel": "f12", "clear_convo": "ctrl+n"})
    assert km["cancel_all"].keys == ("f12",)
    assert km["cancel_all"].source == "migrated"
    assert km["clear_lane"].keys == ("ctrl+n",)
    assert km["clear_lane"].source == "migrated"


def test_legacy_table_targets_exist():
    ids = {a.action_id for a in KEY_ACTIONS}
    for old, new in LEGACY_ACTION_NAMES.items():
        assert new in ids
        assert old not in ids  # otherwise migration would shadow a real action


# -- reserved actions (R-KEY-4) -----------------------------------------------
@pytest.mark.parametrize("action", ["cancel_all", "quit"])
def test_reserved_actions_cannot_be_unbound(action):
    with pytest.raises(KeymapError) as exc:
        resolve_keymap({action: False})
    assert "reserved" in str(exc.value)


def test_reserved_actions_can_be_rebound():
    km = resolve_keymap({"quit": "ctrl+shift+q"})
    assert km["quit"].keys == ("ctrl+shift+q",)


def test_reserved_flags_cover_interrupt_and_quit_only():
    reserved = {a.action_id for a in KEY_ACTIONS if a.reserved}
    assert reserved == {"cancel_all", "quit"}


# -- key_for hints (R-KEY-3) ---------------------------------------------------
def test_key_for_defaults():
    assert key_for("toggle_expand") == "ctrl+x"
    assert key_for("cancel_all") == "ctrl+c"


def test_key_for_reflects_override():
    km = resolve_keymap({"toggle_expand": "f8"})
    assert key_for("toggle_expand", km) == "f8"


def test_key_for_unbound_returns_empty():
    km = resolve_keymap({"toggle_expand": False})
    assert key_for("toggle_expand", km) == ""


def test_key_for_unknown_action_raises():
    with pytest.raises(KeymapError):
        key_for("does_not_exist")


def test_display_key_forms():
    assert display_key("ctrl+x") == "Ctrl+X"
    assert display_key("shift+tab") == "Shift+Tab"
    assert display_key("escape") == "Esc"
    assert display_key("f2") == "F2"


# -- /keys table -----------------------------------------------------------------
def test_keymap_table_shows_sources_and_reserved():
    km = resolve_keymap({"toggle_sidebar": "f2", "cancel": "f12", "clear_lane": False})
    rows = keymap_table(km)
    assert len(rows) == len(KEY_ACTIONS)
    by_action = {row.split()[0]: row for row in rows}
    assert "f2" in by_action["toggle_sidebar"] and "user" in by_action["toggle_sidebar"]
    assert "migrated" in by_action["cancel_all"] and "reserved" in by_action["cancel_all"]
    assert "(unbound)" in by_action["clear_lane"]
    assert "default" in by_action["show_results"]
    assert "pager" in by_action["close"]


# -- config chain (tui.keybinds) ---------------------------------------------------
def test_load_user_keybinds_reads_config_chain(_isolated_chimera_config):
    (_isolated_chimera_config / "config.toml").write_text(
        '[tui.keybinds]\ntoggle_sidebar = "f2"\nclear_lane = false\n'
        'toggle_reasoning = ["ctrl+e", "f3"]\n'
    )
    kb = load_user_keybinds()
    assert kb == {
        "toggle_sidebar": "f2",
        "clear_lane": False,
        "toggle_reasoning": ["ctrl+e", "f3"],
    }
    km = resolve_keymap(kb)
    assert key_for("toggle_sidebar", km) == "f2"


def test_load_user_keybinds_missing_file_or_section_is_empty(_isolated_chimera_config):
    assert load_user_keybinds() == {}  # no config.toml at all
    (_isolated_chimera_config / "config.toml").write_text('[otter]\nmodel = "glm-5"\n')
    assert load_user_keybinds() == {}  # no [tui.keybinds] table


# -- framework Binding construction (needs textual) -----------------------------
def test_build_bindings_matches_previous_bindings_exactly():
    pytest.importorskip("textual")
    from chimera.tui.keys import build_bindings

    built = [(b.key, b.action, b.priority) for b in build_bindings()]
    for entry in PREVIOUS_BINDINGS:
        assert entry in built, f"previously bound {entry} missing from registry build"
    # ... and the only addition is the R-FOLD-2 expand toggle.
    extras = [e for e in built if e not in PREVIOUS_BINDINGS]
    assert extras == [("ctrl+x", "toggle_expand", True)]


def test_build_bindings_pager_context():
    pytest.importorskip("textual")
    from chimera.tui.keys import build_bindings

    assert [(b.key, b.action) for b in build_bindings(context="pager")] == \
        PREVIOUS_PAGER_BINDINGS


def test_build_bindings_honors_keymap_overrides():
    pytest.importorskip("textual")
    from chimera.tui.keys import build_bindings

    km = resolve_keymap({"toggle_sidebar": "f2", "clear_lane": False})
    built = [(b.key, b.action) for b in build_bindings(km)]
    assert ("f2", "toggle_sidebar") in built
    assert ("ctrl+t", "toggle_sidebar") not in built
    assert not any(action == "clear_lane" for _, action in built)


def test_apply_keymap_rewrites_a_live_bindings_map():
    pytest.importorskip("textual")
    from textual.binding import BindingsMap

    from chimera.tui.keys import apply_keymap, build_bindings

    bmap = BindingsMap(build_bindings())
    km = resolve_keymap({"toggle_sidebar": "f2", "clear_lane": False, "quit": "f10"})
    apply_keymap(bmap, km)
    bound = {
        (key, b.action)
        for key, bs in bmap.key_to_bindings.items()
        for b in bs
    }
    assert ("f2", "toggle_sidebar") in bound
    assert ("ctrl+t", "toggle_sidebar") not in bound
    assert not any(a == "clear_lane" for _, a in bound)      # unbound
    assert ("f10", "quit") in bound and ("ctrl+d", "quit") not in bound
    assert ("ctrl+c", "cancel_all") in bound                 # untouched default
    # declared flags survive a rebind
    (f2_binding,) = bmap.key_to_bindings["f2"]
    assert f2_binding.show is True                           # show_in_footer kept
    km2 = resolve_keymap({"toggle_reasoning": "f4"})
    bmap2 = BindingsMap(build_bindings())
    apply_keymap(bmap2, km2)
    (reasoning,) = bmap2.key_to_bindings["f4"]
    assert reasoning.priority is True                        # priority kept


def test_apply_keymap_leaves_foreign_bindings_alone():
    pytest.importorskip("textual")
    from textual.binding import Binding, BindingsMap

    from chimera.tui.keys import apply_keymap

    bmap = BindingsMap([Binding("ctrl+p", "command_palette", "Palette")])
    apply_keymap(bmap, resolve_keymap({"toggle_sidebar": "f2"}))
    assert any(
        b.action == "command_palette"
        for bs in bmap.key_to_bindings.values() for b in bs
    )


# -- app integration -----------------------------------------------------------
def _single_cohort():
    from chimera.tui.cohort import Cohort
    from chimera.tui.lane import Lane, LaneConfig

    class _Driver:
        model = "glm-5.2"
        context_window = 128_000
        tools: list = []
        total_cost = 0.0
        history: list = []

        def cancel(self):  # pragma: no cover - required surface
            pass

        def clear(self):  # pragma: no cover - required surface
            pass

    d = _Driver()
    return Cohort([Lane(LaneConfig(lane_id="A", label="A", model=d.model), d, None)])


@pytest.mark.asyncio
async def test_app_applies_keybind_overrides_and_check_action_still_gates():
    pytest.importorskip("textual")
    from chimera.tui.multiplex import MultiplexApp

    app = MultiplexApp(_single_cohort(), keybinds={"toggle_sidebar": "f2"})
    async with app.run_test() as pilot:
        assert app._sidebar_on is False
        await pilot.press("f2")               # the override fires
        assert app._sidebar_on is True
        await pilot.press("ctrl+t")           # the stale default does not
        assert app._sidebar_on is True
        # the #172 gating still works through the registry
        assert app.check_action("focus_prev_lane", ()) is False
        assert app.check_action("clear_lane", ()) is True


@pytest.mark.asyncio
async def test_app_unbind_disables_the_default_key():
    pytest.importorskip("textual")
    from chimera.tui.multiplex import MultiplexApp

    app = MultiplexApp(_single_cohort(), keybinds={"toggle_sidebar": False})
    async with app.run_test() as pilot:
        await pilot.press("ctrl+t")
        assert app._sidebar_on is False


@pytest.mark.asyncio
async def test_app_falls_back_to_defaults_on_invalid_keybinds():
    pytest.importorskip("textual")
    from chimera.tui.multiplex import MultiplexApp

    app = MultiplexApp(_single_cohort(), keybinds={"no_such_action": "f2"})
    async with app.run_test() as pilot:
        assert app._keybinds_error is not None
        await pilot.press("ctrl+t")           # defaults still live
        assert app._sidebar_on is True
        assert app.is_running


@pytest.mark.asyncio
async def test_app_reads_tui_keybinds_from_config_chain(_isolated_chimera_config):
    pytest.importorskip("textual")
    from chimera.tui.multiplex import MultiplexApp

    (_isolated_chimera_config / "config.toml").write_text(
        '[tui.keybinds]\ntoggle_sidebar = "f2"\n'
    )
    app = MultiplexApp(_single_cohort())      # no keybinds kwarg: config chain
    async with app.run_test() as pilot:
        await pilot.press("f2")
        assert app._sidebar_on is True


@pytest.mark.asyncio
async def test_expand_toggle_flips_elision_for_new_output():
    pytest.importorskip("textual")
    from chimera.tui.multiplex import LanePane, MultiplexApp

    app = MultiplexApp(_single_cohort())
    async with app.run_test() as pilot:
        pane = app.query_one(LanePane)
        assert pane._transcript is not None
        assert pane._transcript.elide is True           # collapsed by default
        await pilot.press("ctrl+x")
        assert app._tools_expanded is True
        assert pane._transcript.elide is False          # expanded
        await pilot.press("ctrl+x")
        assert pane._transcript.elide is True


@pytest.mark.asyncio
async def test_expand_hint_reaches_the_elision_marker():
    pytest.importorskip("textual")
    from chimera.core.loop_events import LoopEvent, LoopEventType
    from chimera.tui.multiplex import LanePane, MultiplexApp
    from chimera.tui.render import plain
    from chimera.types import ToolCall

    class _Res:
        success = True
        output = "\n".join(f"line {i}" for i in range(100))

    app = MultiplexApp(_single_cohort(), keybinds={"toggle_expand": "f8"})
    async with app.run_test():
        pane = app.query_one(LanePane)
        sink: list = []
        assert pane._transcript is not None
        pane._transcript._sink = sink.append
        pane.feed(LoopEvent(
            LoopEventType.tool_result,
            (ToolCall(id="1", name="bash", arguments={}), _Res()), 0,
        ))
        [r] = sink
        assert "(f8 expands)" in plain(r)               # currently-bound key
        assert "ctrl+x" not in plain(r)                 # not the stale default


@pytest.mark.asyncio
async def test_keys_command_prints_the_effective_table():
    pytest.importorskip("textual")
    from chimera.tui.multiplex import LanePane, MultiplexApp
    from chimera.tui.prompt import PromptArea

    app = MultiplexApp(_single_cohort(), keybinds={"toggle_sidebar": "f2"})
    async with app.run_test() as pilot:
        pane = app.query_one(LanePane)
        app.query_one("#prompt", PromptArea).value = "/keys"
        # /keys writes the table to the focused pane's log via note()
        from chimera.tui.logview import TranscriptLog
        log = pane.query_one(TranscriptLog)
        before = len(log.lines)
        await pilot.press("enter")
        await pilot.pause()
        assert len(log.lines) > before
        text = "\n".join(str(line) for line in log.lines[before:])
        assert "toggle_sidebar" in text and "f2" in text and "user" in text


@pytest.mark.asyncio
async def test_reserved_interrupt_still_bound_after_overrides():
    pytest.importorskip("textual")
    from chimera.tui.multiplex import MultiplexApp

    app = MultiplexApp(_single_cohort(), keybinds={"toggle_sidebar": "f2"})
    async with app.run_test() as pilot:
        await pilot.press("ctrl+c")   # idle: cancel_all exits the app
        await pilot.pause()
        assert not app.is_running
