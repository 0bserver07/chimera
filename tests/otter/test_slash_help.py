"""Tests for the otter ``/help`` rendering with grouped sections.

W3-F8 split the otter ``/help`` output into "Built-in commands",
"Custom commands", and "Plugin commands" so users can tell at a glance
which entries ship with the binary versus which are sourced from
``.opencode/command/*.md`` (custom) or
``.opencode/plugin/<name>/command/*.md`` (plugin).

We pin three things here:

1. **Origin tagging.** Built-ins land via :func:`register_otter_slash`,
   customs via :func:`register_custom_commands`, and plugins via
   :func:`register_plugin_commands`. Each call updates
   :data:`chimera.otter.slash._COMMAND_ORIGINS` and the public
   :func:`get_command_origin` accessor surfaces the tag.
2. **Section ordering.** The help output emits sections in the canonical
   order Built-in -> Custom -> Plugin. Empty sections are skipped so
   the output stays compact when the user has no extensions.
3. **Per-section sorting.** Inside each section, commands are listed
   alphabetically with their description (custom + plugin descriptions
   come from the source ``CustomCommand`` / plugin record).
"""
from __future__ import annotations

import pytest

from chimera.otter.commands import CustomCommand


@pytest.fixture(autouse=True)
def _reset_origins() -> None:
    """Drain the otter slash origin map between tests.

    The origin registry is module-level (intentional — :func:`cmd_help`
    needs to see prior registrations across calls), so tests need an
    isolation hook. We mutate the private dicts in place rather than
    :func:`importlib.reload` because the wider corpus imports
    ``OTTER_SLASH_COMMANDS`` and friends by reference; reloading would
    invalidate those existing references and break ``test_slash.py``.
    """
    import chimera.otter.slash as slash_mod

    slash_mod._COMMAND_ORIGINS.clear()
    slash_mod._COMMAND_HELP.clear()


class _CapturePrinter:
    """Records each line printed by a slash handler for later assertions."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)


class _DictRegistry:
    """A minimal dict-backed slash registry, sufficient for /help dispatch."""

    def __init__(self) -> None:
        self.entries: dict[str, tuple[object, str]] = {}

    def register(self, name: str, handler: object, help_text: str = "") -> None:
        self.entries[name] = (handler, help_text)


def _make_custom(name: str, description: str) -> CustomCommand:
    """Build a CustomCommand with the bare-minimum fields /help inspects."""
    return CustomCommand(name=name, description=description, body_template="echo")


class _PluginCommand:
    """Tiny duck-typed plugin command record (mirrors OtterCommand shape)."""

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description

    def handler(self, _session: object, _env: object, _args: str, out: object) -> None:
        # Plugins ship a callable handler; we don't dispatch in this
        # test but the registrar requires a callable to install.
        if callable(out):
            out(f"plugin {self.name} ran")


# ---------------------------------------------------------------------------
# Origin tagging
# ---------------------------------------------------------------------------


def test_register_otter_slash_marks_builtins() -> None:
    """Every command installed by ``register_otter_slash`` is ``builtin``."""
    from chimera.otter.slash import (
        OTTER_SLASH_COMMANDS,
        ORIGIN_BUILTIN,
        get_command_origin,
        register_otter_slash,
    )

    state = _DictRegistry()
    register_otter_slash(state)

    for name in OTTER_SLASH_COMMANDS:
        assert get_command_origin(name) == ORIGIN_BUILTIN, (
            f"/{name} should be tagged builtin; got {get_command_origin(name)!r}"
        )


def test_register_custom_commands_marks_customs() -> None:
    """``register_custom_commands`` tags entries as ``custom``."""
    from chimera.otter.slash import (
        ORIGIN_CUSTOM,
        get_command_origin,
        register_custom_commands,
    )

    state = _DictRegistry()
    customs = [
        _make_custom("review-pr", "Review the current PR"),
        _make_custom("ship", "Open a release PR"),
    ]
    n = register_custom_commands(state, customs)
    assert n == 2
    assert get_command_origin("review-pr") == ORIGIN_CUSTOM
    assert get_command_origin("ship") == ORIGIN_CUSTOM


def test_register_plugin_commands_marks_plugins() -> None:
    """``register_plugin_commands`` tags entries as ``plugin``."""
    from chimera.otter.slash import (
        ORIGIN_PLUGIN,
        get_command_origin,
        register_plugin_commands,
    )

    state = _DictRegistry()
    plugins = [
        _PluginCommand("deploy", "Deploy via the CI pipeline"),
        _PluginCommand("lint-fix", "Auto-fix linter findings"),
    ]
    n = register_plugin_commands(state, plugins)
    assert n == 2
    assert get_command_origin("deploy") == ORIGIN_PLUGIN
    assert get_command_origin("lint-fix") == ORIGIN_PLUGIN


# ---------------------------------------------------------------------------
# /help section rendering
# ---------------------------------------------------------------------------


def _render_help(state: _DictRegistry) -> tuple[list[str], str]:
    """Dispatch ``/help`` against *state* and return ``(lines, joined)``."""
    from chimera.otter.slash import cmd_help

    out = _CapturePrinter()
    cmd_help(None, None, "", out)
    return out.lines, "\n".join(out.lines)


def test_help_emits_three_sections_in_canonical_order() -> None:
    """Built-in -> Custom -> Plugin: section order is fixed."""
    from chimera.otter.slash import (
        register_custom_commands,
        register_otter_slash,
        register_plugin_commands,
    )

    state = _DictRegistry()
    register_otter_slash(state)
    register_custom_commands(
        state, [_make_custom("ship", "Open a release PR")],
    )
    register_plugin_commands(state, [_PluginCommand("deploy", "Deploy via CI")])

    lines, rendered = _render_help(state)

    # Headers must appear, and in the correct order.
    builtin_idx = rendered.index("Built-in commands:")
    custom_idx = rendered.index("Custom commands:")
    plugin_idx = rendered.index("Plugin commands:")
    assert builtin_idx < custom_idx < plugin_idx, (
        f"section order wrong: builtin={builtin_idx}, "
        f"custom={custom_idx}, plugin={plugin_idx}"
    )

    # The first emitted line is the canonical header so users can grep
    # for it the same way they do against the shared registry.
    assert lines[0] == "Available commands:"


def test_help_lists_builtins_sorted() -> None:
    """All built-ins appear under "Built-in commands" in alphabetical order."""
    from chimera.otter.slash import (
        OTTER_SLASH_COMMANDS,
        register_otter_slash,
    )

    state = _DictRegistry()
    register_otter_slash(state)
    lines, _ = _render_help(state)

    # Pull every line that starts with "  /<name>" between the
    # "Built-in commands:" header and the next blank line.
    start = lines.index("Built-in commands:") + 1
    builtin_block: list[str] = []
    for line in lines[start:]:
        if not line:
            break
        builtin_block.append(line)

    names = [line.strip().split()[0].lstrip("/") for line in builtin_block]
    assert names == sorted(names), f"built-ins not sorted: {names}"
    # And every command in the palette is present.
    expected = set(OTTER_SLASH_COMMANDS.keys())
    assert expected <= set(names), f"missing built-ins: {expected - set(names)}"


def test_help_lists_customs_with_description() -> None:
    """Custom commands carry their .md ``description`` into the help output."""
    from chimera.otter.slash import (
        register_custom_commands,
        register_otter_slash,
    )

    state = _DictRegistry()
    register_otter_slash(state)
    register_custom_commands(
        state,
        [
            _make_custom("review-pr", "Review the current PR"),
            _make_custom("ship", "Open a release PR"),
        ],
    )
    _, rendered = _render_help(state)

    custom_section_start = rendered.index("Custom commands:")
    custom_chunk = rendered[custom_section_start:]
    # Both custom names + descriptions must appear.
    assert "/review-pr" in custom_chunk
    assert "Review the current PR" in custom_chunk
    assert "/ship" in custom_chunk
    assert "Open a release PR" in custom_chunk
    # Sorted alphabetically (review-pr < ship).
    assert custom_chunk.index("/review-pr") < custom_chunk.index("/ship")


def test_help_lists_plugin_commands_with_description() -> None:
    """Plugin commands appear under their own section with descriptions."""
    from chimera.otter.slash import (
        register_otter_slash,
        register_plugin_commands,
    )

    state = _DictRegistry()
    register_otter_slash(state)
    register_plugin_commands(
        state,
        [
            _PluginCommand("deploy", "Deploy via the CI pipeline"),
            _PluginCommand("lint-fix", "Auto-fix linter findings"),
        ],
    )
    _, rendered = _render_help(state)

    plugin_chunk = rendered[rendered.index("Plugin commands:"):]
    assert "/deploy" in plugin_chunk
    assert "Deploy via the CI pipeline" in plugin_chunk
    assert "/lint-fix" in plugin_chunk
    assert "Auto-fix linter findings" in plugin_chunk
    # Sorted (deploy < lint-fix).
    assert plugin_chunk.index("/deploy") < plugin_chunk.index("/lint-fix")


def test_help_skips_empty_sections() -> None:
    """When no customs / plugins are registered, those headers are absent."""
    from chimera.otter.slash import register_otter_slash

    state = _DictRegistry()
    register_otter_slash(state)
    _, rendered = _render_help(state)
    assert "Built-in commands:" in rendered
    assert "Custom commands:" not in rendered
    assert "Plugin commands:" not in rendered


def test_help_falls_back_when_no_origins_recorded() -> None:
    """An empty origin map degrades to the shared flat listing.

    This mirrors ``chimera code``'s ``/help`` so tiny test fixtures that
    never call :func:`register_otter_slash` still see a sensible
    listing.
    """
    from chimera.otter.slash import cmd_help

    out = _CapturePrinter()
    cmd_help(None, None, "", out)

    # The shared cmd_help opens with "Available commands:" and emits
    # at least the canonical /help line for itself.
    rendered = "\n".join(out.lines)
    assert "Available commands:" in rendered
    assert "/help" in rendered
    # No grouped section headers when origins are empty.
    assert "Built-in commands:" not in rendered
    assert "Custom commands:" not in rendered
    assert "Plugin commands:" not in rendered


def test_help_handles_unknown_origin_via_other_section() -> None:
    """Exotic origin tags surface under "Other commands" so they stay visible."""
    from chimera.otter.slash import cmd_help, mark_origin

    # Tag a hypothetical command with an unrecognised origin.
    mark_origin("zzz-experimental", "lab")
    out = _CapturePrinter()
    cmd_help(None, None, "", out)
    rendered = "\n".join(out.lines)
    assert "Other commands:" in rendered
    assert "/zzz-experimental" in rendered


def test_custom_command_overriding_builtin_moves_to_custom_section() -> None:
    """A custom that shadows a built-in name is re-tagged as ``custom``.

    This matches the upstream agent's last-wins precedence: a user-authored
    ``review.md`` should appear in the Custom section, not the Built-in
    one, since it's the active handler the REPL dispatches.
    """
    from chimera.otter.slash import (
        ORIGIN_CUSTOM,
        get_command_origin,
        register_custom_commands,
        register_otter_slash,
    )

    state = _DictRegistry()
    register_otter_slash(state)

    # Pick a name that may or may not exist in the built-in palette;
    # ``init`` is in the canonical wave-1 set so this is a real shadow.
    register_custom_commands(
        state, [_make_custom("init", "Custom project init")],
    )
    assert get_command_origin("init") == ORIGIN_CUSTOM

    _, rendered = _render_help(state)
    # The /init line shows up under Custom commands with the new
    # description, not the built-in one.
    custom_chunk = rendered[rendered.index("Custom commands:"):]
    assert "/init" in custom_chunk
    assert "Custom project init" in custom_chunk
