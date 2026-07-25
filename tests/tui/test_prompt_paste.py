"""Paste chips in the composer (R-FOLD-6).

The contract has three parts, and each is pinned here: an oversized paste
collapses to a chip; the chip is an **atomic edit unit** (movement, word-nav
and delete treat it as one token, never exposing its interior); and the full
text rides with the message on submit while history keeps the chip.
"""
import pytest

pytest.importorskip("rich")
pytest.importorskip("textual")

from chimera.tui.prompt import (  # noqa: E402
    CHIP_RE,
    PasteSettings,
    PromptArea,
    atomic_delete,
    atomic_move,
    chip_label,
    chip_spans,
    expand_chips,
    load_paste_settings,
    paste_settings_from_config,
    should_collapse,
)

BIG = "\n".join(f"line {i}" for i in range(40))
CHIP = "[Pasted #1 ~40 lines]"


# -- thresholds ------------------------------------------------------------
def test_should_collapse_binds_on_lines_and_chars():
    s = PasteSettings(lines=8, chars=1000)
    assert should_collapse(BIG, s) is True                     # 40 lines
    assert should_collapse("a\nb\nc", s) is False              # small both ways
    assert should_collapse("x" * 1001, s) is True              # one huge line
    assert should_collapse("x" * 1000, s) is False             # exactly at the cap


def test_zero_thresholds_disable_collapsing_entirely():
    off = PasteSettings(lines=0, chars=0)
    assert should_collapse(BIG, off) is False
    assert should_collapse("x" * 100_000, off) is False


def test_paste_settings_from_config_survives_junk():
    assert paste_settings_from_config({}) == PasteSettings()
    assert paste_settings_from_config(
        {"paste_chip_lines": 3, "paste_chip_chars": 0}
    ) == PasteSettings(lines=3, chars=0)
    assert paste_settings_from_config({"paste_chip_lines": "nope"}).lines == 8
    assert paste_settings_from_config({"paste_chip_chars": True}).chars == 1000
    assert paste_settings_from_config({"paste_chip_lines": -4}).lines == 0


def test_load_paste_settings_reads_the_unified_config_chain(tmp_path, monkeypatch):
    monkeypatch.delenv("CHIMERA_CONFIG_HOME", raising=False)
    scope = tmp_path / ".chimera"
    scope.mkdir()
    (scope / "config.toml").write_text(
        "[tui]\npaste_chip_lines = 3\npaste_chip_chars = 77\n"
    )
    settings = load_paste_settings(str(tmp_path / "project"), home=str(tmp_path))
    assert settings == PasteSettings(lines=3, chars=77)


def test_load_paste_settings_defaults_without_config(tmp_path, monkeypatch):
    monkeypatch.delenv("CHIMERA_CONFIG_HOME", raising=False)
    assert load_paste_settings(str(tmp_path), home=str(tmp_path)) == PasteSettings()


# -- the chip grammar ------------------------------------------------------
def test_chip_label_measures_lines_then_chars():
    assert chip_label(1, BIG) == CHIP
    assert chip_label(2, "x" * 4000) == "[Pasted #2 ~4000 chars]"
    assert CHIP_RE.fullmatch(chip_label(3, BIG))


def test_chip_identity_keeps_same_shaped_pastes_apart():
    a, b = chip_label(1, BIG), chip_label(2, BIG)
    assert a != b
    assert expand_chips(f"{a} and {b}", {a: "AAA", b: "BBB"}) == "AAA and BBB"


def test_expand_chips_leaves_unknown_and_broken_chips_alone():
    assert expand_chips(f"before {CHIP} after", {}) == f"before {CHIP} after"
    assert expand_chips("[Pasted #1 ~40 lin", {CHIP: BIG}) == "[Pasted #1 ~40 lin"


def test_chip_spans_locates_every_chip_on_a_line():
    line = f"see {CHIP} ok"
    assert chip_spans(line) == [(4, 4 + len(CHIP))]


def test_atomic_move_hops_a_chip_whole():
    line = f"a{CHIP}b"
    start, end = chip_spans(line)[0]
    assert atomic_move(line, end, -1) == start          # left from the far edge
    assert atomic_move(line, start, +1) == end          # right from the near edge
    assert atomic_move(line, start + 3, -1) == start    # a cursor inside snaps out
    assert atomic_move(line, start + 3, +1) == end
    assert atomic_move(line, 0, +1) is None             # no chip in the way
    assert atomic_move(line, len(line), +1) is None


def test_atomic_delete_removes_the_whole_chip_or_defers():
    line = f"a{CHIP}b"
    span = chip_spans(line)[0]
    assert atomic_delete(line, span[1], -1) == span      # backspace at the edge
    assert atomic_delete(line, span[0], +1) == span      # delete at the edge
    assert atomic_delete(line, span[0] + 2, -1) == span  # from inside
    assert atomic_delete(line, 0, -1) is None            # ordinary character
    assert atomic_delete(line, len(line), +1) is None


# -- the widget ------------------------------------------------------------
def _paste_host(**kwargs):
    from textual.app import App, ComposeResult

    class Host(App):
        def compose(self) -> ComposeResult:
            yield PromptArea(id="prompt", **kwargs)

    return Host()


async def _paste(pilot, app, text):
    """Paste the way a terminal does: the app receives it, the focus handles it.

    (Posting straight to the widget is not equivalent — the event bubbles back
    to the app, which forwards it to the focused widget a second time.)
    """
    from textual import events

    app.post_message(events.Paste(text))
    await pilot.pause()


@pytest.mark.asyncio
async def test_small_paste_is_inserted_verbatim():
    """The common case is byte-identical to before chips existed."""
    app = _paste_host()
    async with app.run_test() as pilot:
        p = app.query_one("#prompt", PromptArea)
        p.focus()
        await pilot.pause()
        await _paste(pilot, app, "a\nb\nc")
        assert p.value == "a\nb\nc"
        assert p.pastes == {}


@pytest.mark.asyncio
async def test_big_paste_collapses_to_a_chip_and_expands_on_submit():
    app = _paste_host()
    async with app.run_test() as pilot:
        p = app.query_one("#prompt", PromptArea)
        p.focus()
        await pilot.pause()
        await _paste(pilot, app, BIG)
        assert p.value == CHIP                       # the composer stays readable
        assert p.pastes == {CHIP: BIG}
        p.insert(" summarize this")
        assert p.submitted_text() == BIG + " summarize this"


@pytest.mark.asyncio
async def test_submit_sends_the_full_text_and_history_keeps_the_chip():
    captured = []

    from textual.app import App, ComposeResult

    class Host(App):
        def compose(self) -> ComposeResult:
            yield PromptArea(id="prompt")

        def on_prompt_area_submitted(self, event: PromptArea.Submitted) -> None:
            captured.append((event.value, event.raw))
            event.prompt.remember(event.raw)

    app = Host()
    async with app.run_test() as pilot:
        p = app.query_one("#prompt", PromptArea)
        p.focus()
        await pilot.pause()
        await _paste(pilot, app, BIG)
        await pilot.press("enter")
        await pilot.pause()
        [(value, raw)] = captured
        assert value == BIG                          # the full text is what is sent
        assert raw == CHIP                           # the chip is what was on screen
        p.value = ""
        await pilot.press("up")                      # history recall
        assert p.value == CHIP                       # not a 40-line wall


@pytest.mark.asyncio
async def test_chip_is_atomic_for_movement_and_forward_delete():
    app = _paste_host()
    async with app.run_test() as pilot:
        p = app.query_one("#prompt", PromptArea)
        p.focus()
        await pilot.pause()
        await _paste(pilot, app, BIG)
        assert p.cursor_location == (0, len(CHIP))
        await pilot.press("left")                    # hops the whole chip
        assert p.cursor_location == (0, 0)
        await pilot.press("right")
        assert p.cursor_location == (0, len(CHIP))
        await pilot.press("ctrl+left")               # word-nav obeys the same rule
        assert p.cursor_location == (0, 0)
        await pilot.press("delete")                  # takes the chip whole
        assert p.value == ""


@pytest.mark.asyncio
async def test_backspace_takes_the_chip_whole_never_its_interior():
    app = _paste_host()
    async with app.run_test() as pilot:
        p = app.query_one("#prompt", PromptArea)
        p.focus()
        await pilot.pause()
        p.insert("look: ")
        await _paste(pilot, app, BIG)
        assert p.value == f"look: {CHIP}"
        await pilot.press("backspace")
        assert p.value == "look: "                   # the chip, not a stray "]"
        await pilot.press("backspace")
        assert p.value == "look:"                    # ordinary editing resumes


@pytest.mark.asyncio
async def test_typing_next_to_a_chip_still_works():
    app = _paste_host()
    async with app.run_test() as pilot:
        p = app.query_one("#prompt", PromptArea)
        p.focus()
        await pilot.pause()
        await _paste(pilot, app, BIG)
        p.insert(" go")
        assert p.value == f"{CHIP} go"
        await pilot.press("backspace")               # not adjacent to the chip
        assert p.value == f"{CHIP} g"


@pytest.mark.asyncio
async def test_thresholds_are_configurable_per_widget():
    app = _paste_host(paste=PasteSettings(lines=2, chars=0))
    async with app.run_test() as pilot:
        p = app.query_one("#prompt", PromptArea)
        p.focus()
        await pilot.pause()
        await _paste(pilot, app, "a\nb\nc")            # 3 lines > 2
        assert p.value == "[Pasted #1 ~3 lines]"


@pytest.mark.asyncio
async def test_collapsing_can_be_turned_off_entirely():
    app = _paste_host(paste=PasteSettings(lines=0, chars=0))
    async with app.run_test() as pilot:
        p = app.query_one("#prompt", PromptArea)
        p.focus()
        await pilot.pause()
        await _paste(pilot, app, BIG)
        assert p.value == BIG
        assert p.pastes == {}


@pytest.mark.asyncio
async def test_multiplexer_sends_the_expanded_paste():
    """End to end: the app's submit handler routes the full text to the lane."""
    from chimera.tui.multiplex import MultiplexApp
    from tests.tui.test_multiplex import FakeDriver, _cohort

    app = MultiplexApp(_cohort([FakeDriver("glm-5.2")]))
    async with app.run_test() as pilot:
        p = app.query_one("#prompt", PromptArea)
        p.focus()
        await pilot.pause()
        await _paste(pilot, app, BIG)
        assert p.value == CHIP
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        record = "\n".join(app._cohort.lanes[0].transcript_lines)
        assert "line 39" in record                   # the whole paste was sent
        assert CHIP not in record
