"""Tests for the universal fuzzy-select dialog (chimera/tui/select.py, R-OVER-2)."""
import pytest

textual = pytest.importorskip("textual")  # skip if the [tui] extra isn't installed

from textual.app import App, ComposeResult  # noqa: E402
from textual.widgets import Input, OptionList  # noqa: E402

from chimera.tui.select import (  # noqa: E402
    FuzzySelectScreen,
    SelectItem,
    hints_from_bindings,
    rank_items,
    score_item,
    score_text,
)


# -- scorer: tiers, stability, weighting ----------------------------------

def test_score_text_tiers_are_ordered():
    exact = score_text("bash", "bash")
    prefix = score_text("bas", "bash tool")
    word = score_text("tool", "bash tool")
    substring = score_text("ash", "bash")
    assert exact is not None and prefix is not None
    assert word is not None and substring is not None
    assert exact > prefix > word > substring


def test_score_text_miss_returns_none_and_empty_matches_all():
    assert score_text("zzz", "bash") is None
    assert score_text("", "anything") == 0
    assert score_text("x", "") is None


def test_score_text_is_case_insensitive():
    assert score_text("BASH", "bash") == score_text("bash", "BASH")
    assert score_text("Th", "theme picker") == score_text("th", "Theme picker")


def test_score_text_word_boundary_beats_mid_word():
    # "res" at a word boundary in "show results" vs buried in "compressed".
    boundary = score_text("res", "show results")
    buried = score_text("res", "compressed")
    assert boundary is not None and buried is not None
    assert boundary > buried


def test_score_item_multi_token_requires_all_tokens():
    item = SelectItem(value=1, label="switch model", description="pick a provider model")
    assert score_item("switch model", item) is not None
    assert score_item("switch nope", item) is None


def test_score_item_label_outranks_description():
    in_label = SelectItem(value=1, label="theme", description="whatever")
    in_description = SelectItem(value=2, label="whatever", description="theme")
    s_label = score_item("theme", in_label)
    s_description = score_item("theme", in_description)
    assert s_label is not None and s_description is not None
    assert s_label > s_description


def test_score_item_searches_category_and_search_text():
    item = SelectItem(value=1, label="opt", category="session", search_text="alias-xyz")
    assert score_item("session", item) is not None
    assert score_item("alias-xyz", item) is not None


def test_rank_items_orders_by_score_then_insertion():
    items = [
        SelectItem(value="a", label="alpha bash"),   # word-boundary for "bash"
        SelectItem(value="b", label="bash"),         # exact
        SelectItem(value="c", label="bashful"),      # prefix
        SelectItem(value="d", label="rebash"),       # substring
        SelectItem(value="e", label="unrelated"),    # excluded
    ]
    ranked = rank_items(items, "bash")
    assert [i.value for i in ranked] == ["b", "c", "a", "d"]


def test_rank_items_ties_keep_insertion_order():
    items = [SelectItem(value=k, label=f"prefix-{k}") for k in ("one", "two", "three")]
    # every label matches "prefix" identically -> insertion order preserved
    ranked = rank_items(items, "prefix")
    assert [i.value for i in ranked] == ["one", "two", "three"]
    # empty query keeps everything, original order
    assert [i.value for i in rank_items(items, "")] == ["one", "two", "three"]


def test_hints_from_bindings_derives_and_merges_keys():
    hints = hints_from_bindings(FuzzySelectScreen.BINDINGS)
    assert "↑/↓ move" in hints
    assert "Esc cancel" in hints
    assert "Enter select" in hints  # structural extra


# -- pilot-driven dialog behaviour -----------------------------------------

ITEMS = [
    SelectItem(value="co-1", label="cohort-one", description="first race", hint="10:00"),
    SelectItem(value="co-2", label="cohort-two", description="second race", hint="11:00"),
    SelectItem(value="mo-1", label="model-pick", description="switch model", hint=""),
]


class SelectHost(App):
    """Bare host app with one focusable widget so focus-restore is observable."""

    def __init__(self, items=None, **screen_kwargs):
        super().__init__()
        self._items = ITEMS if items is None else items
        self._screen_kwargs = screen_kwargs
        self.picked: list = []

    def compose(self) -> ComposeResult:
        yield Input(id="host-input")

    def open_select(self) -> None:
        self.push_screen(
            FuzzySelectScreen(self._items, title="Pick", **self._screen_kwargs),
            self.picked.append,
        )


@pytest.mark.asyncio
async def test_filter_then_enter_returns_value():
    app = SelectHost()
    async with app.run_test() as pilot:
        app.open_select()
        await pilot.pause()
        assert isinstance(app.screen, FuzzySelectScreen)
        option_list = app.screen.query_one(OptionList)
        assert option_list.option_count == 3
        await pilot.press(*"model")
        await pilot.pause()
        assert option_list.option_count == 1
        await pilot.press("enter")
        await pilot.pause()
        assert app.picked == ["mo-1"]
        assert not isinstance(app.screen, FuzzySelectScreen)


@pytest.mark.asyncio
async def test_arrow_keys_move_highlight_while_filter_focused():
    app = SelectHost()
    async with app.run_test() as pilot:
        app.open_select()
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert app.picked == ["co-2"]


@pytest.mark.asyncio
async def test_escape_cancels_with_none():
    app = SelectHost()
    async with app.run_test() as pilot:
        app.open_select()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.picked == [None]
        assert not isinstance(app.screen, FuzzySelectScreen)


@pytest.mark.asyncio
async def test_enter_with_no_matches_is_a_noop():
    app = SelectHost()
    async with app.run_test() as pilot:
        app.open_select()
        await pilot.pause()
        await pilot.press(*"zzzz")
        await pilot.pause()
        assert app.screen.query_one(OptionList).option_count == 0
        await pilot.press("enter")
        await pilot.pause()
        assert app.picked == []  # still open, nothing chosen
        assert isinstance(app.screen, FuzzySelectScreen)


@pytest.mark.asyncio
async def test_preselection_highlights_initial_value():
    app = SelectHost(initial="co-2")
    async with app.run_test() as pilot:
        app.open_select()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.picked == ["co-2"]


@pytest.mark.asyncio
async def test_focus_restores_to_prior_widget_after_dismiss():
    app = SelectHost()
    async with app.run_test() as pilot:
        host_input = app.query_one("#host-input", Input)
        host_input.focus()
        await pilot.pause()
        app.open_select()
        await pilot.pause()
        # the modal's filter input owns focus while open
        assert app.focused is not None and app.focused.id == "select-filter"
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is host_input  # R-OVER-1 basics: prior focus restored


CATEGORIZED = [
    SelectItem(value=1, label="glm-5.2", category="models"),
    SelectItem(value=2, label="glm-5.1", category="models"),
    SelectItem(value=3, label="dark", category="themes"),
]


@pytest.mark.asyncio
async def test_categories_render_headers_and_highlight_skips_them():
    app = SelectHost(items=CATEGORIZED)
    async with app.run_test() as pilot:
        app.open_select()
        await pilot.pause()
        option_list = app.screen.query_one(OptionList)
        # 3 items + 2 headers
        assert option_list.option_count == 5
        assert option_list.get_option_at_index(0).disabled  # "models" header
        assert option_list.get_option_at_index(3).disabled  # "themes" header
        # initial highlight is the first *selectable* row, not the header
        assert option_list.highlighted == 1
        # moving down twice lands on "dark" (skipping the second header)
        await pilot.press("down", "down")
        await pilot.press("enter")
        await pilot.pause()
        assert app.picked == [3]


@pytest.mark.asyncio
async def test_filtering_flattens_category_groups():
    app = SelectHost(items=CATEGORIZED)
    async with app.run_test() as pilot:
        app.open_select()
        await pilot.pause()
        await pilot.press(*"glm")
        await pilot.pause()
        option_list = app.screen.query_one(OptionList)
        assert option_list.option_count == 2  # flat: no headers while filtering
        assert not any(
            option_list.get_option_at_index(i).disabled
            for i in range(option_list.option_count)
        )


# -- cohorts-picker parity ---------------------------------------------------

@pytest.mark.asyncio
async def test_cohort_picker_is_a_fuzzy_select_and_filters():
    from chimera.tui.multiplex import CohortPickerScreen

    rows = [
        {
            "cohort_id": "aaaa-1111",
            "created_at": "2026-07-10T10:00:00",
            "task": "fix the bug",
            "lanes": [{"label": "glm-5.2"}],
        },
        {
            "cohort_id": "bbbb-2222",
            "created_at": "2026-07-10T11:00:00",
            "task": "write docs",
            "lanes": [{"label": "glm-5.1"}],
        },
    ]

    class Host(App):
        def __init__(self):
            super().__init__()
            self.picked: list = []

        def compose(self) -> ComposeResult:
            yield Input(id="host-input")

    app = Host()
    async with app.run_test() as pilot:
        app.push_screen(CohortPickerScreen(rows), app.picked.append)
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, FuzzySelectScreen)
        option_list = screen.query_one(OptionList)
        # parity: one row per cohort, option id = cohort id, newest first
        assert option_list.option_count == 2
        assert option_list.get_option_at_index(0).id == "aaaa-1111"
        # type-to-filter reaches lane labels and tasks too (search_text)
        await pilot.press(*"docs")
        await pilot.pause()
        assert option_list.option_count == 1
        await pilot.press("enter")
        await pilot.pause()
        assert app.picked == ["bbbb-2222"]
