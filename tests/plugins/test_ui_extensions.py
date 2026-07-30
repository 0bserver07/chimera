"""Tests for the third-party UI-surface registration API (:mod:`chimera.plugins.ui`).

Covers the typed contribution model (UICommand/UIPanel/UIStatusline), the
process-global :class:`UIExtensionRegistry` (imperative + ``on``-style
decorator registration, enumeration accessors, validation, replace-on-reregister),
and — the headline proof — that the *real* ``chimera code`` REPL command
registry enumerates and dispatches a plugin-registered command once
:func:`install_into_repl` bridges it across.
"""
from __future__ import annotations

import pytest

import chimera.cli.slash_commands as slash_commands
from chimera.plugins.ui import (
    PanelPlacement,
    StatuslineSection,
    UICommand,
    UIExtensionRegistry,
    UIPanel,
    UIStatusline,
    install_into_repl,
)


@pytest.fixture(autouse=True)
def ui_reset():
    """Isolate every test from the process-global UI registry state."""
    UIExtensionRegistry._reset()
    yield
    UIExtensionRegistry._reset()


@pytest.fixture
def repl_registry_snapshot():
    """Snapshot and restore the real REPL slash-command registry.

    ``chimera.cli.slash_commands`` keeps its command table in module-level
    globals. Tests that bridge into it must not leak entries into other
    tests, so we snapshot ``_REGISTRY`` / ``COMMAND_NAMES`` and restore them
    afterward.
    """
    before_registry = dict(slash_commands._REGISTRY)
    before_names = list(slash_commands.COMMAND_NAMES)
    yield slash_commands
    slash_commands._REGISTRY.clear()
    slash_commands._REGISTRY.update(before_registry)
    slash_commands.COMMAND_NAMES[:] = before_names


def _noop_handler(session, env, args, out):
    """A command handler matching the REPL contract that does nothing."""


def _noop_renderer(ctx=None):
    """A panel/statusline renderer that returns an empty string."""
    return ""


# ---------------------------------------------------------------------------
# Contribution model
# ---------------------------------------------------------------------------


class TestContributionModel:
    def test_uicommand_defaults(self):
        cmd = UICommand(name="greet", handler=_noop_handler)
        assert cmd.help == ""
        assert cmd.aliases == ()
        assert cmd.plugin is None

    def test_uicommand_is_frozen(self):
        cmd = UICommand(name="greet", handler=_noop_handler)
        with pytest.raises(Exception):
            cmd.name = "other"  # type: ignore[misc]

    def test_uipanel_defaults(self):
        panel = UIPanel(id="files", renderer=_noop_renderer)
        assert panel.title == ""
        assert panel.placement == "sidebar"
        assert panel.order == 100
        assert panel.plugin is None

    def test_uistatusline_defaults(self):
        seg = UIStatusline(id="cost", renderer=_noop_renderer)
        assert seg.section == "right"
        assert seg.order == 100

    def test_placement_enum_equals_string(self):
        assert PanelPlacement.SIDEBAR == "sidebar"
        assert PanelPlacement.BOTTOM == "bottom"
        assert PanelPlacement.OVERLAY == "overlay"

    def test_section_enum_equals_string(self):
        assert StatuslineSection.LEFT == "left"
        assert StatuslineSection.CENTER == "center"
        assert StatuslineSection.RIGHT == "right"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


class TestRegisterCommand:
    def test_register_and_enumerate(self):
        UIExtensionRegistry.register_command("greet", _noop_handler, help="hi")
        cmds = UIExtensionRegistry.get_all_commands()
        assert len(cmds) == 1
        assert cmds[0].name == "greet"
        assert cmds[0].help == "hi"

    def test_returns_the_stored_command(self):
        result = UIExtensionRegistry.register_command("greet", _noop_handler)
        assert isinstance(result, UICommand)
        assert UIExtensionRegistry.get_command("greet") is result

    def test_leading_slash_is_stripped(self):
        UIExtensionRegistry.register_command("/greet", _noop_handler)
        assert UIExtensionRegistry.get_command("greet") is not None
        assert UIExtensionRegistry.get_all_commands()[0].name == "greet"

    def test_aliases_resolve(self):
        UIExtensionRegistry.register_command(
            "greet", _noop_handler, aliases=["hello", "/hi"]
        )
        via_hello = UIExtensionRegistry.get_command("hello")
        via_hi = UIExtensionRegistry.get_command("hi")
        assert via_hello is not None and via_hello.name == "greet"
        assert via_hi is not None and via_hi.name == "greet"

    def test_empty_name_raises(self):
        with pytest.raises(ValueError):
            UIExtensionRegistry.register_command("   ", _noop_handler)

    def test_non_callable_handler_raises(self):
        with pytest.raises(ValueError):
            UIExtensionRegistry.register_command("greet", "not-callable")  # type: ignore[arg-type]

    def test_get_all_commands_sorted_by_name(self):
        UIExtensionRegistry.register_command("zeta", _noop_handler)
        UIExtensionRegistry.register_command("alpha", _noop_handler)
        assert [c.name for c in UIExtensionRegistry.get_all_commands()] == [
            "alpha",
            "zeta",
        ]

    def test_reregister_replaces_and_drops_stale_aliases(self):
        UIExtensionRegistry.register_command("greet", _noop_handler, aliases=["hi"])

        def other(session, env, args, out):
            return "other"

        UIExtensionRegistry.register_command("greet", other, aliases=["yo"])
        assert len(UIExtensionRegistry.get_all_commands()) == 1
        greet = UIExtensionRegistry.get_command("greet")
        assert greet is not None and greet.handler is other
        # The stale alias no longer resolves; the new one does.
        assert UIExtensionRegistry.get_command("hi") is None
        via_yo = UIExtensionRegistry.get_command("yo")
        assert via_yo is not None and via_yo.name == "greet"

    def test_get_command_unknown_returns_none(self):
        assert UIExtensionRegistry.get_command("nope") is None

    def test_no_commands_returns_empty(self):
        assert UIExtensionRegistry.get_all_commands() == []


class TestOnCommandDecorator:
    def test_decorator_registers_and_returns_function(self):
        @UIExtensionRegistry.on_command("greet", help="hi", plugin="demo")
        def greet(session, env, args, out):
            return "greeted"

        # Returned unchanged: still directly callable.
        assert greet(None, None, "", lambda _s: None) == "greeted"
        stored = UIExtensionRegistry.get_command("greet")
        assert stored is not None
        assert stored.plugin == "demo"
        assert stored.handler is greet


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------


class TestRegisterPanel:
    def test_register_and_enumerate(self):
        UIExtensionRegistry.register_panel("files", _noop_renderer, title="Files")
        panels = UIExtensionRegistry.get_all_panels()
        assert len(panels) == 1
        assert panels[0].id == "files"
        assert panels[0].title == "Files"

    def test_placement_enum_accepted(self):
        UIExtensionRegistry.register_panel(
            "files", _noop_renderer, placement=PanelPlacement.BOTTOM
        )
        panel = UIExtensionRegistry.get_panel("files")
        assert panel is not None and panel.placement == "bottom"

    def test_get_all_panels_sorted_by_order_then_id(self):
        UIExtensionRegistry.register_panel("b", _noop_renderer, order=10)
        UIExtensionRegistry.register_panel("a", _noop_renderer, order=10)
        UIExtensionRegistry.register_panel("c", _noop_renderer, order=1)
        assert [p.id for p in UIExtensionRegistry.get_all_panels()] == ["c", "a", "b"]

    def test_filter_by_placement(self):
        UIExtensionRegistry.register_panel("side", _noop_renderer, placement="sidebar")
        UIExtensionRegistry.register_panel("bot", _noop_renderer, placement="bottom")
        got = UIExtensionRegistry.get_all_panels(placement=PanelPlacement.BOTTOM)
        assert [p.id for p in got] == ["bot"]

    def test_empty_id_raises(self):
        with pytest.raises(ValueError):
            UIExtensionRegistry.register_panel("  ", _noop_renderer)

    def test_non_callable_renderer_raises(self):
        with pytest.raises(ValueError):
            UIExtensionRegistry.register_panel("files", "nope")  # type: ignore[arg-type]

    def test_on_panel_decorator(self):
        @UIExtensionRegistry.on_panel("files", title="Files", order=5, plugin="demo")
        def render(ctx=None):
            return ["a.py"]

        assert render() == ["a.py"]
        stored = UIExtensionRegistry.get_panel("files")
        assert stored is not None
        assert stored.order == 5
        assert stored.plugin == "demo"
        assert stored.renderer is render


# ---------------------------------------------------------------------------
# Status line
# ---------------------------------------------------------------------------


class TestRegisterStatusline:
    def test_register_and_enumerate(self):
        UIExtensionRegistry.register_statusline("cost", _noop_renderer)
        segs = UIExtensionRegistry.get_all_statuslines()
        assert len(segs) == 1
        assert segs[0].id == "cost"
        assert segs[0].section == "right"

    def test_section_enum_accepted(self):
        UIExtensionRegistry.register_statusline(
            "cost", _noop_renderer, section=StatuslineSection.LEFT
        )
        seg = UIExtensionRegistry.get_statusline("cost")
        assert seg is not None and seg.section == "left"

    def test_sorted_and_filtered(self):
        UIExtensionRegistry.register_statusline(
            "b", _noop_renderer, section="left", order=2
        )
        UIExtensionRegistry.register_statusline(
            "a", _noop_renderer, section="left", order=1
        )
        UIExtensionRegistry.register_statusline(
            "c", _noop_renderer, section="right", order=1
        )
        left = UIExtensionRegistry.get_all_statuslines(section="left")
        assert [s.id for s in left] == ["a", "b"]

    def test_on_statusline_decorator(self):
        @UIExtensionRegistry.on_statusline("cost", section="center", plugin="demo")
        def render(ctx=None):
            return "$0.01"

        assert render() == "$0.01"
        stored = UIExtensionRegistry.get_statusline("cost")
        assert stored is not None
        assert stored.section == "center"
        assert stored.plugin == "demo"


# ---------------------------------------------------------------------------
# Aggregation: a plugin registers a command + a panel -> aggregator returns them
# ---------------------------------------------------------------------------


def _activate_demo_plugin():
    """Simulate a plugin's activation contributing UI surfaces.

    A plugin body registers against the process-global registry exactly like
    the directory loader does for agents/hooks/MCP servers.
    """

    def toolbar(session, env, args, out):
        out("demo toolbar")

    UIExtensionRegistry.register_command(
        "demo", toolbar, help="demo command", plugin="demo-plugin"
    )
    UIExtensionRegistry.register_panel(
        "demo-files", _noop_renderer, title="Demo", plugin="demo-plugin"
    )
    UIExtensionRegistry.register_statusline(
        "demo-cost", _noop_renderer, plugin="demo-plugin"
    )


class TestAggregation:
    def test_plugin_contributions_are_aggregated(self):
        _activate_demo_plugin()

        cmds = UIExtensionRegistry.get_all_commands()
        panels = UIExtensionRegistry.get_all_panels()
        segs = UIExtensionRegistry.get_all_statuslines()

        assert [c.name for c in cmds] == ["demo"]
        assert [p.id for p in panels] == ["demo-files"]
        assert [s.id for s in segs] == ["demo-cost"]
        # Provenance is preserved so a UI can attribute the contribution.
        assert cmds[0].plugin == "demo-plugin"
        assert panels[0].plugin == "demo-plugin"
        assert segs[0].plugin == "demo-plugin"

    def test_two_plugins_accumulate(self):
        UIExtensionRegistry.register_command("alpha", _noop_handler, plugin="a")
        UIExtensionRegistry.register_command("beta", _noop_handler, plugin="b")
        assert {c.name for c in UIExtensionRegistry.get_all_commands()} == {
            "alpha",
            "beta",
        }


# ---------------------------------------------------------------------------
# The bridge into the REPL
# ---------------------------------------------------------------------------


class TestInstallIntoRepl:
    def test_injectable_register_receives_commands_and_aliases(self):
        captured: dict[str, tuple] = {}

        def fake_register(name, handler, help_text=""):
            captured[name] = (handler, help_text)

        UIExtensionRegistry.register_command(
            "greet", _noop_handler, help="hi", aliases=["hello"]
        )
        installed = install_into_repl(fake_register)

        assert installed == ["greet", "hello"]
        assert set(captured) == {"greet", "hello"}
        assert captured["greet"][1] == "hi"
        # Alias forwards the same handler.
        assert captured["hello"][0] is captured["greet"][0]

    def test_repl_enumerates_and_dispatches_plugin_command(
        self, repl_registry_snapshot
    ):
        """Headline proof: a plugin-registered command becomes a live REPL command.

        Register a :class:`UICommand`, bridge it into the *real*
        ``chimera.cli.slash_commands`` registry, then show the REPL both
        enumerates it (``list_commands`` / ``COMMAND_NAMES``) and routes a
        ``/name`` line to the plugin handler via the shared dispatcher.
        """
        sc = repl_registry_snapshot
        calls: list[str] = []

        def handler(session, env, args, out):
            calls.append(args)
            out(f"ran with {args}")

        UIExtensionRegistry.register_command(
            "plugcmd", handler, help="from a plugin", plugin="demo"
        )

        installed = install_into_repl()  # bridges into the real sc.register
        assert "plugcmd" in installed

        # (1) Enumeration: the REPL's own command listing includes it.
        listed = {name for name, _help in sc.list_commands()}
        assert "plugcmd" in listed
        assert "/plugcmd" in sc.COMMAND_NAMES

        # (2) Dispatch: the shared router invokes the plugin handler.
        outputs: list[str] = []
        handled = sc.dispatch(
            "/plugcmd hello world", session=object(), env=None, out=outputs.append
        )
        assert handled is True
        assert calls == ["hello world"]
        assert any("ran with hello world" in line for line in outputs)

    def test_install_is_idempotent_returnwise(self, repl_registry_snapshot):
        UIExtensionRegistry.register_command("plugcmd", _noop_handler)
        first = install_into_repl()
        second = install_into_repl()
        assert first == second == ["plugcmd"]


# ---------------------------------------------------------------------------
# Provenance-scoped removal (the hot-swap seam)
# ---------------------------------------------------------------------------


class TestUnregisterPlugin:
    def test_removes_commands_aliases_panels_and_statuslines_by_provenance(self):
        UIExtensionRegistry.register_command(
            "greet", _noop_handler, aliases=["hello"], plugin="demo"
        )
        UIExtensionRegistry.register_command("bye", _noop_handler, plugin="demo")
        UIExtensionRegistry.register_command("keep", _noop_handler, plugin="other")
        UIExtensionRegistry.register_panel("p1", _noop_renderer, plugin="demo")
        UIExtensionRegistry.register_statusline("s1", _noop_renderer, plugin="demo")

        removed = UIExtensionRegistry.unregister_plugin("demo")

        assert removed == ["bye", "greet"]  # sorted command names
        assert UIExtensionRegistry.get_command("greet") is None
        assert UIExtensionRegistry.get_command("hello") is None  # alias gone too
        assert UIExtensionRegistry.get_command("bye") is None
        assert UIExtensionRegistry.get_panel("p1") is None
        assert UIExtensionRegistry.get_statusline("s1") is None
        # another plugin's contribution is untouched
        assert UIExtensionRegistry.get_command("keep") is not None

    def test_provenance_free_contributions_are_left_in_place(self):
        UIExtensionRegistry.register_command("anon", _noop_handler)  # plugin=None
        assert UIExtensionRegistry.unregister_plugin("demo") == []
        assert UIExtensionRegistry.get_command("anon") is not None

    def test_unknown_plugin_is_a_quiet_noop(self):
        assert UIExtensionRegistry.unregister_plugin("never-loaded") == []
