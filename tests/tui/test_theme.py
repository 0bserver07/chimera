"""Semantic slot themes (R-THEME-1..4).

``chimera.tui.theme`` is stdlib-only by design (no rich, no textual), so these
run in CI's no-``tui``-extra posture without an importorskip.
"""
from __future__ import annotations

import pytest

from chimera.tui.theme import (
    BUILTIN_THEMES,
    DEFAULT_THEME,
    OPACITY_KNOBS,
    SLOTS,
    Palette,
    Theme,
    ThemeError,
    ThemeSettings,
    color_depth,
    detect_mode,
    discover_themes,
    load_theme_settings,
    luminance,
    quantize,
    slot_ids,
)


# -- schema ---------------------------------------------------------------
def test_slot_schema_is_a_semantic_registry():
    ids = slot_ids()
    assert len(ids) == len(set(ids)), "slot ids must be unique"
    assert 40 <= len(ids) <= 70, f"schema drifted out of the ~40-60 band: {len(ids)}"
    families = {slot.family for slot in SLOTS}
    assert {"base", "status", "chrome", "tool", "markdown", "syntax", "diff"} <= families
    for slot in SLOTS:
        assert slot.description, f"{slot.slot_id} needs a description"


def test_every_builtin_theme_resolves_every_slot_in_both_modes():
    for name, theme in BUILTIN_THEMES.items():
        for mode in ("dark", "light"):
            resolved = theme.resolve(mode)
            assert set(resolved) == set(slot_ids()), name


def test_default_theme_reproduces_the_pre_theme_styles():
    # The additive pin: with no config, renderers paint exactly what they
    # hardcoded before themes existed.
    palette = Palette()
    assert palette.name == DEFAULT_THEME
    assert palette.style("tool.icon") == "yellow"
    assert palette.style("tool.name") == "bold yellow"
    assert palette.style("tool.ok") == "green"
    assert palette.style("tool.error") == "red"
    assert palette.style("status.error") == "red"
    assert palette.style("chrome.elision") == "dim"
    assert palette.style("chrome.gutter-user") == "bold cyan"
    assert palette.style("chrome.reasoning") == "dim italic"
    assert palette.style("diff.add") == "green"
    assert palette.style("diff.remove") == "red"
    assert palette.style("diff.hunk") == "cyan"


def test_unknown_slot_in_a_theme_is_rejected_loudly():
    with pytest.raises(ThemeError) as exc:
        Theme.from_dict({"slots": {"diff": {"nope": "red"}}}, name="broken")
    assert "diff.nope" in str(exc.value)


def test_style_of_unknown_slot_is_empty_not_an_error():
    assert Palette().style("not.a.slot") == ""


# -- vars -----------------------------------------------------------------
def test_vars_resolve_through_slots_with_mode_variants():
    theme = Theme.from_dict(
        {
            "vars": {"ink": {"dark": "#ffffff", "light": "#000000"}, "alias": "$ink"},
            "slots": {"base.text": "$alias", "diff.add": "#00ff00"},
        },
        name="v",
    )
    assert theme.resolve("dark")["base.text"] == "#ffffff"
    assert theme.resolve("light")["base.text"] == "#000000"
    assert theme.resolve("dark")["diff.add"] == "#00ff00"


def test_circular_var_reference_is_detected():
    theme = Theme.from_dict(
        {"vars": {"a": "$b", "b": "$a"}, "slots": {"base.text": "$a"}}, name="loop",
    )
    with pytest.raises(ThemeError) as exc:
        theme.resolve("dark")
    assert "circular" in str(exc.value)


def test_unknown_var_reference_is_reported():
    theme = Theme.from_dict({"slots": {"base.text": "$nope"}}, name="v")
    with pytest.raises(ThemeError) as exc:
        theme.resolve("dark")
    assert "unknown var" in str(exc.value)


def test_dotted_and_nested_slot_tables_are_equivalent():
    flat = Theme.from_dict({"slots": {"diff.add": "cyan"}}, name="a").resolve("dark")
    nested = Theme.from_dict({"slots": {"diff": {"add": "cyan"}}}, name="b").resolve("dark")
    assert flat["diff.add"] == nested["diff.add"] == "cyan"


def test_a_variant_map_is_a_value_not_a_namespace():
    theme = Theme.from_dict(
        {"slots": {"base": {"text": {"dark": "white", "light": "black"}}}}, name="v",
    )
    assert theme.resolve("light")["base.text"] == "black"


def test_light_falls_back_to_dark_when_only_one_variant_is_given():
    theme = Theme.from_dict({"slots": {"diff.add": {"dark": "#001100"}}}, name="v")
    assert theme.resolve("light")["diff.add"] == "#001100"


# -- mode detection (R-THEME-2) ------------------------------------------
def test_detect_mode_cascade():
    assert detect_mode({"CHIMERA_THEME_MODE": "light"}) == "light"
    # explicit beats every hint below it
    assert detect_mode({"CHIMERA_THEME_MODE": "dark", "COLORFGBG": "0;15"}) == "dark"
    # terminal background luminance
    assert detect_mode({"CHIMERA_TERM_BG": "#fdfdfd"}) == "light"
    assert detect_mode({"CHIMERA_TERM_BG": "#11141b"}) == "dark"
    # COLORFGBG palette index
    assert detect_mode({"COLORFGBG": "0;15"}) == "light"
    assert detect_mode({"COLORFGBG": "15;0"}) == "dark"
    # nothing known: dark
    assert detect_mode({}) == "dark"


def test_luminance():
    assert luminance("#ffffff") == pytest.approx(1.0)
    assert luminance("#000000") == pytest.approx(0.0)
    assert luminance("fff") == pytest.approx(1.0)
    assert luminance("not-a-color") is None


# -- depth degradation (R-THEME-4) ---------------------------------------
def test_color_depth_detection():
    assert color_depth({"NO_COLOR": "1", "COLORTERM": "truecolor"}) == "none"
    assert color_depth({"TERM": "dumb"}) == "none"
    assert color_depth({"COLORTERM": "truecolor", "TERM": "xterm"}) == "truecolor"
    assert color_depth({"TERM": "xterm-256color"}) == "256"
    assert color_depth({"TERM": "xterm"}) == "16"
    assert color_depth({}) == "16"


def test_quantize_preserves_attributes_and_palette_names():
    assert quantize("bold yellow", "16") == "bold yellow"
    assert quantize("reverse red", "truecolor") == "reverse red"
    # NO_COLOR: colors go, structure stays
    assert quantize("bold #8fd67a", "none") == "bold"
    assert quantize("dim on #202020", "none") == "dim"
    assert quantize("#8fd67a", "none") == ""


def test_quantize_maps_hex_down_to_256_and_16():
    assert quantize("#ff0000", "truecolor") == "#ff0000"
    assert quantize("bold #ff0000", "256").startswith("bold color(")
    assert quantize("#ff0000", "16") in ("red", "bright_red")
    assert quantize("#000000", "16") == "black"
    assert quantize("#ffffff", "16") == "bright_white"


_ANSI16_NAMES = frozenset(
    "black red green yellow blue magenta cyan white bright_black bright_red "
    "bright_green bright_yellow bright_blue bright_magenta bright_cyan "
    "bright_white".split()
)


def test_palette_quantizes_the_whole_theme():
    palette = Palette(BUILTIN_THEMES["chimera"], mode="dark", depth="16")
    assert palette.style("diff.add") in _ANSI16_NAMES
    assert palette.raw("diff.add") == "#8fd67a"  # the unquantized declaration
    mono = Palette(BUILTIN_THEMES["chimera"], mode="dark", depth="none")
    assert "#" not in mono.style("diff.add")


# -- opacity knobs --------------------------------------------------------
def test_opacity_knobs_degrade_to_dim():
    palette = Palette()
    assert palette.opacity("reasoning") == OPACITY_KNOBS["reasoning"]
    assert palette.dim_for("reasoning") == "dim"
    opaque = Palette(Theme.from_dict({"opacity": {"reasoning": 1.0}}, name="o"))
    assert opaque.dim_for("reasoning") == ""
    assert palette.opacity("nope") == 1.0


def test_bad_opacity_value_is_rejected():
    with pytest.raises(ThemeError):
        Theme.from_dict({"opacity": {"reasoning": "very"}}, name="o")


# -- css variables (Textual bridge) --------------------------------------
def test_terminal_palette_theme_exports_no_css_variables():
    # The byte-identity pin: framework chrome is untouched by the default theme.
    assert Palette().css_variables() == {}


def test_hex_theme_exports_css_variables():
    variables = Palette(BUILTIN_THEMES["chimera"], mode="dark").css_variables()
    assert variables["primary"].startswith("#")
    assert variables["success"].startswith("#")
    assert Palette(BUILTIN_THEMES["chimera"], depth="none").css_variables() == {}


# -- settings resolution --------------------------------------------------
def test_settings_default_to_the_shipped_behavior():
    settings = ThemeSettings.resolve({}, env={"TERM": "xterm-256color"})
    assert settings.theme == DEFAULT_THEME
    assert settings.mode == "dark"
    assert settings.animations is True
    assert settings.error == ""


def test_settings_read_the_config_keys():
    settings = ThemeSettings.resolve(
        {"theme": "chimera", "theme_mode": "light", "animations": False},
        env={"COLORTERM": "truecolor"},
    )
    assert (settings.theme, settings.mode, settings.depth) == (
        "chimera", "light", "truecolor",
    )
    assert settings.animations is False
    assert settings.palette().name == "chimera"


def test_unknown_theme_name_falls_back_and_reports():
    settings = ThemeSettings.resolve({"theme": "nope"}, env={})
    assert settings.theme == DEFAULT_THEME
    assert "unknown theme" in settings.error


def test_bad_mode_falls_back_and_reports():
    settings = ThemeSettings.resolve({"theme_mode": "sideways"}, env={})
    assert settings.mode_setting == "auto"
    assert "theme_mode" in settings.error


def test_no_color_forces_animations_off():
    settings = ThemeSettings.resolve({"animations": True}, env={"NO_COLOR": "1"})
    assert settings.depth == "none"
    assert settings.animations is False


def test_lock_mode_detects_once():
    settings = ThemeSettings.resolve(
        {"theme_mode": "lock"}, env={"CHIMERA_TERM_BG": "#ffffff"},
    )
    assert settings.mode_setting == "lock"
    assert settings.mode == "light"


# -- user theme files -----------------------------------------------------
def test_discover_themes_layers_user_files_over_builtins(tmp_path):
    xdg = tmp_path / "xdg"
    project = tmp_path / "proj"
    (xdg / "themes").mkdir(parents=True)
    (project / "themes").mkdir(parents=True)
    (xdg / "themes" / "midnight.toml").write_text(
        'description = "cool dark"\n[vars]\nink = "#c8d3f5"\n'
        '[slots]\nbase.text = "$ink"\n'
    )
    (project / "themes" / "midnight.toml").write_text(
        '[slots]\nbase.text = "#ff0000"\n'
    )
    themes = discover_themes([xdg, project])
    assert set(BUILTIN_THEMES) <= set(themes)
    # project scope wins
    assert themes["midnight"].resolve("dark")["base.text"] == "#ff0000"
    assert themes["midnight"].source.endswith("proj/themes/midnight.toml")


def test_broken_theme_file_is_skipped_not_fatal(tmp_path):
    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "junk.toml").write_text("this is not toml {{{")
    (tmp_path / "themes" / "bad-slot.toml").write_text('[slots]\nnope.nope = "red"\n')
    (tmp_path / "themes" / "ok.toml").write_text('[slots]\ndiff.add = "cyan"\n')
    themes = discover_themes([tmp_path])
    assert "junk" not in themes
    assert "bad-slot" not in themes
    assert themes["ok"].resolve("dark")["diff.add"] == "cyan"


def test_load_theme_settings_reads_the_config_chain(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".chimera" / "themes").mkdir(parents=True)
    (home / ".chimera" / "config.toml").write_text('[tui]\ntheme = "midnight"\n')
    (home / ".chimera" / "themes" / "midnight.toml").write_text(
        '[slots]\ndiff.add = "#123456"\n'
    )
    monkeypatch.delenv("CHIMERA_CONFIG_HOME", raising=False)
    settings = load_theme_settings(
        project_dir=tmp_path / "project", home=home, env={"COLORTERM": "truecolor"},
    )
    assert settings.theme == "midnight"
    assert settings.palette().style("diff.add") == "#123456"


def test_load_theme_settings_degrades_when_nothing_is_configured(tmp_path):
    settings = load_theme_settings(project_dir=tmp_path, home=tmp_path, env={})
    assert settings.theme == DEFAULT_THEME
    assert settings.palette().style("tool.name") == "bold yellow"


def test_compound_style_values_expand_their_var_tokens():
    # A slot value is a *style*, not only a color: "bold $amber" must resolve.
    theme = Theme.from_dict(
        {"vars": {"amber": "#e5b567"}, "slots": {"tool.name": "bold $amber"}}, name="v",
    )
    assert theme.resolve("dark")["tool.name"] == "bold #e5b567"
    chimera = Palette(BUILTIN_THEMES["chimera"])
    assert chimera.style("diff.add-word") == "reverse #8fd67a"
    assert "$" not in "".join(chimera.style(slot) for slot in slot_ids())
