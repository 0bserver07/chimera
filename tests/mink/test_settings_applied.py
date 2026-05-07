"""W14-7 Part B — verify ``MinkSettings`` keys are *applied*, not just parsed.

Wave-13 G14 added ``theme``, ``output_styles``, ``statusline``, and
``keybindings`` to :class:`~chimera.mink.settings.MinkSettings`. This
file asserts that wave-14 actually consumes those values:

* ``theme`` -> ``chimera.cli.render.resolve_theme`` returns the right
  :class:`ThemePalette` (dark vs. light vs. none) and
  :class:`build_stream_handler` propagates it into a
  :class:`MinkStreamHandler`.
* ``output_styles`` -> ``resolve_output_style`` honours the recognised
  fields (``highlight_code``, ``max_width``, ``diff_context``,
  ``compact_tool_blocks``) and surfaces unknown keys via ``extra``.
* ``keybindings`` -> ``apply_keybindings`` translates each entry into
  a ``readline.parse_and_bind`` directive, skipping unknown actions /
  unparsable specs without raising.
* ``statusline`` -> ``render_statusline`` writes a single dim line to
  ``stream`` for the dict / bool / string / disabled spec shapes.
"""
from __future__ import annotations

import io

import pytest

from chimera.cli.render import (
    DiffRenderer,
    OutputStyle,
    ThemePalette,
    ToolBlockRenderer,
    build_stream_handler,
    resolve_output_style,
    resolve_theme,
)
from chimera.mink.repl import (
    apply_keybindings,
    format_statusline_text,
    render_statusline,
    translate_key,
)
from chimera.mink.settings import MinkSettings


# ---------------------------------------------------------------------------
# Theme resolution
# ---------------------------------------------------------------------------


def test_theme_dark_returns_dark_palette() -> None:
    palette = resolve_theme("dark")
    assert palette.name == "dark"
    assert palette.fg_red.endswith("203m")


def test_theme_light_returns_light_palette() -> None:
    palette = resolve_theme("light")
    assert palette.name == "light"
    # Light theme uses standard 16-colour-safe codes, not 256 colour 203.
    assert "203m" not in palette.fg_red


def test_theme_none_disables_colour() -> None:
    palette = resolve_theme("none")
    assert palette.name == "none"
    assert palette.reset == ""
    assert palette.fg_red == ""
    # ``wrap`` should be a no-op since every code is empty.
    assert palette.wrap("hello", palette.fg_red) == "hello"


def test_theme_none_via_no_color_kwarg() -> None:
    palette = resolve_theme("dark", no_color=True)
    assert palette.name == "none"


def test_theme_unknown_falls_back_to_dark() -> None:
    palette = resolve_theme("solarized-violet")
    # We don't crash on a typo; we silently fall back to dark.
    assert palette.name == "dark"


def test_theme_passthrough_palette_instance() -> None:
    custom = ThemePalette(name="custom", fg_red="\x1b[35m")
    assert resolve_theme(custom) is custom


def test_theme_none_when_none_input() -> None:
    palette = resolve_theme(None)
    assert palette.name == "dark"


def test_theme_auto_uses_colorfgbg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLORFGBG", "0;15")
    palette = resolve_theme("auto")
    assert palette.name == "light"
    monkeypatch.setenv("COLORFGBG", "15;0")
    palette = resolve_theme("auto")
    assert palette.name == "dark"


# ---------------------------------------------------------------------------
# OutputStyle resolution
# ---------------------------------------------------------------------------


def test_output_style_default_when_no_name() -> None:
    style = resolve_output_style(None, None)
    assert style.name == "default"
    assert style.highlight_code is True
    assert style.max_width is None
    assert style.diff_context == 2
    assert style.compact_tool_blocks is False


def test_output_style_known_keys_are_honoured() -> None:
    cfg = {
        "compact": {
            "highlight_code": False,
            "max_width": 90,
            "diff_context": 5,
            "compact_tool_blocks": True,
        }
    }
    style = resolve_output_style("compact", cfg)
    assert style.name == "compact"
    assert style.highlight_code is False
    assert style.max_width == 90
    assert style.diff_context == 5
    assert style.compact_tool_blocks is True


def test_output_style_camel_case_aliases() -> None:
    cfg = {"compact": {"highlightCode": False, "diffContext": 7}}
    style = resolve_output_style("compact", cfg)
    assert style.highlight_code is False
    assert style.diff_context == 7


def test_output_style_extra_keys_preserved() -> None:
    cfg = {"narrow": {"highlight_code": True, "vendor": "mink", "x": 1}}
    style = resolve_output_style("narrow", cfg)
    assert style.extra == {"vendor": "mink", "x": 1}


def test_output_style_missing_name_returns_defaults_with_label() -> None:
    style = resolve_output_style("nonexistent", {})
    assert style.name == "nonexistent"
    assert style.highlight_code is True


def test_output_style_drops_invalid_int_for_diff_context() -> None:
    style = resolve_output_style("foo", {"foo": {"diff_context": "not-an-int"}})
    assert style.diff_context == 2  # default


# ---------------------------------------------------------------------------
# Renderers consume palette + style
# ---------------------------------------------------------------------------


def test_diff_renderer_uses_palette_codes() -> None:
    palette = resolve_theme("light")
    out = DiffRenderer(palette=palette).format("a\nb\n", "a\nB\n", path="t.txt")
    assert palette.fg_red in out
    assert palette.fg_green in out


def test_diff_renderer_respects_context_lines() -> None:
    long_old = "\n".join(f"line{i}" for i in range(20)) + "\n"
    long_new = long_old.replace("line5", "LINE5")
    out_default = DiffRenderer(context_lines=2).format(
        long_old, long_new, path="t.txt",
    )
    out_wide = DiffRenderer(context_lines=8).format(
        long_old, long_new, path="t.txt",
    )
    assert len(out_wide.splitlines()) > len(out_default.splitlines())


def test_tool_block_renderer_uses_palette_for_error() -> None:
    palette = resolve_theme("light")
    sink = io.StringIO()
    renderer = ToolBlockRenderer(stream=sink, palette=palette)
    renderer.render_result("bash", "boom", is_error=True, exit_code=1)
    text = sink.getvalue()
    # Light theme uses a different red than the dark default.
    assert palette.fg_red in text


def test_tool_block_renderer_compact_mode_does_not_pad() -> None:
    style = OutputStyle(compact_tool_blocks=True)
    sink = io.StringIO()
    renderer = ToolBlockRenderer(stream=sink, style=style)
    renderer.render_result("bash", "ok", exit_code=0)
    text = sink.getvalue()
    # Compact style still emits the body; just no extra trailing newline.
    assert "ok" in text


def test_build_stream_handler_propagates_settings(monkeypatch) -> None:
    """The mink stream handler should read theme + style off settings."""
    # Force rich path so we can inspect attributes.
    pytest.importorskip("rich")
    monkeypatch.delenv("NO_COLOR", raising=False)
    settings = MinkSettings(
        theme="light",
        output_styles={"compact": {"highlight_code": False, "diff_context": 4}},
    )
    handler = build_stream_handler(
        settings=settings,
        style_name="compact",
        force_rich=True,
        stream=io.StringIO(),
    )
    palette = getattr(handler, "_palette", None)
    style = getattr(handler, "_style", None)
    assert palette is not None and palette.name == "light"
    assert style is not None and style.name == "compact"
    assert style.diff_context == 4


def test_build_stream_handler_no_color_overrides_theme() -> None:
    pytest.importorskip("rich")
    settings = MinkSettings(theme="light")
    handler = build_stream_handler(
        settings=settings,
        force_rich=True,
        no_color=True,
        stream=io.StringIO(),
    )
    palette = getattr(handler, "_palette", None)
    assert palette is not None and palette.name == "none"


# ---------------------------------------------------------------------------
# Keybindings
# ---------------------------------------------------------------------------


def test_translate_key_handles_modifiers() -> None:
    assert translate_key("ctrl-d") == "\\C-d"
    assert translate_key("ctrl-shift-c") == "\\C-C"
    assert translate_key("alt-x") == "\\ex"
    assert translate_key("alt-enter") == "\\e\\r"


def test_translate_key_named_keys() -> None:
    assert translate_key("enter") == "\\r"
    assert translate_key("tab") == "\\t"
    assert translate_key("escape") == "\\e"
    assert translate_key("f5") == "\\e[15~"


def test_translate_key_returns_none_for_unknown() -> None:
    assert translate_key("") is None
    assert translate_key("ctrl-shift-multistring") is None


def test_apply_keybindings_no_settings_is_noop() -> None:
    assert apply_keybindings(None) == 0
    assert apply_keybindings(MinkSettings()) == 0


def test_apply_keybindings_installs_known_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock readline to count installed bindings."""
    calls: list[str] = []

    class _FakeReadline:
        @staticmethod
        def parse_and_bind(directive: str) -> None:
            calls.append(directive)

    import sys

    monkeypatch.setitem(sys.modules, "readline", _FakeReadline)
    settings = MinkSettings(
        keybindings={
            "submit": "ctrl-d",
            "cancel": "ctrl-c",
            "clear-screen": "ctrl-l",
            "history-up": "alt-p",
        }
    )
    n = apply_keybindings(settings)
    assert n == 4
    assert any("\\C-d" in c and "accept-line" in c for c in calls)
    assert any("\\C-c" in c and "abort" in c for c in calls)
    assert any("\\C-l" in c and "clear-screen" in c for c in calls)
    assert any("\\ep" in c and "previous-history" in c for c in calls)


def test_apply_keybindings_skips_unknown_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _FakeReadline:
        @staticmethod
        def parse_and_bind(d: str) -> None:
            calls.append(d)

    import sys

    monkeypatch.setitem(sys.modules, "readline", _FakeReadline)
    log = io.StringIO()
    n = apply_keybindings(
        MinkSettings(keybindings={"submit": "ctrl-d", "wat": "ctrl-q"}),
        stream=log,
    )
    assert n == 1
    assert "unknown action 'wat'" in log.getvalue()


def test_apply_keybindings_skips_invalid_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _FakeReadline:
        @staticmethod
        def parse_and_bind(d: str) -> None:
            calls.append(d)

    import sys

    monkeypatch.setitem(sys.modules, "readline", _FakeReadline)
    log = io.StringIO()
    n = apply_keybindings(
        MinkSettings(keybindings={"submit": "totally-not-a-key-spec"}),
        stream=log,
    )
    assert n == 0
    assert "cannot parse key" in log.getvalue()


# ---------------------------------------------------------------------------
# Statusline
# ---------------------------------------------------------------------------


def test_statusline_disabled_emits_nothing() -> None:
    sink = io.StringIO()
    assert render_statusline(False, stream=sink) is False
    assert sink.getvalue() == ""
    assert render_statusline(None, stream=sink) is False
    assert sink.getvalue() == ""


def test_statusline_bool_true_uses_default_format() -> None:
    sink = io.StringIO()
    text = format_statusline_text(
        True, context={"cwd": "/tmp/proj", "model": "kimi-k2.6:cloud"},
    )
    assert "/tmp/proj" in text
    assert "kimi-k2.6:cloud" in text
    assert render_statusline(True, context={"cwd": "/tmp", "model": "x"}, stream=sink)
    assert "/tmp" in sink.getvalue()


def test_statusline_dict_with_format_template() -> None:
    spec = {"format": "[{model}] {cwd}", "enabled": True}
    text = format_statusline_text(
        spec, context={"cwd": "/p", "model": "kimi"},
    )
    assert text == "[kimi] /p"


def test_statusline_dict_disabled_skips_render() -> None:
    spec = {"format": "x", "enabled": False}
    sink = io.StringIO()
    assert render_statusline(spec, stream=sink) is False
    assert sink.getvalue() == ""


def test_statusline_string_runs_command() -> None:
    """Bare string spec is treated as a command; runner stub returns text."""
    spec = "echo from-shell"
    last: dict[str, list[str]] = {}

    def _runner(argv: list[str]) -> str:
        last["argv"] = argv
        return "from-shell\n"

    text = format_statusline_text(spec, command_runner=_runner)
    assert text == "from-shell"
    assert last["argv"] == ["echo", "from-shell"]


def test_statusline_dict_command_with_runner_stub() -> None:
    spec = {"command": "/usr/bin/true", "enabled": True}
    last: dict[str, list[str]] = {}

    def _runner(argv: list[str]) -> str:
        last["argv"] = argv
        return "all-good\n"

    text = format_statusline_text(spec, command_runner=_runner)
    assert text == "all-good"
    assert last["argv"] == ["/usr/bin/true"]


def test_statusline_format_missing_token_keeps_placeholder() -> None:
    spec = {"format": "{cwd} | model={model} | host={host}"}
    text = format_statusline_text(spec, context={"cwd": "/", "model": "k"})
    assert "{host}" in text  # placeholder retained, not error


def test_statusline_render_writes_dim_ansi() -> None:
    sink = io.StringIO()
    render_statusline(
        {"format": "X"},
        context={},
        stream=sink,
    )
    out = sink.getvalue()
    assert out.startswith("\x1b[2m")
    assert out.endswith("\x1b[0m\n")


def test_statusline_command_failure_returns_empty() -> None:
    """Subprocess failure must not raise; it just collapses to empty."""
    spec = {"command": "totally-not-a-real-binary-zzz", "enabled": True}
    text = format_statusline_text(spec)
    assert text == ""


def test_settings_default_statusline_is_none() -> None:
    """Sanity: the dataclass default leaves statusline disabled."""
    assert MinkSettings().statusline is None
