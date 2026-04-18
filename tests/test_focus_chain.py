from chimera.context.focus import FocusChain


def test_select_fits_budget():
    fc = FocusChain(token_budget=100)
    fc.add("a" * 200, "big", relevance=0.9)    # 50 tokens, fits
    fc.add("b" * 800, "huge", relevance=0.8)   # 200 tokens, doesn't fit
    selected = fc.select()
    assert len(selected) == 1
    assert selected[0].source == "big"


def test_relevance_ranking():
    fc = FocusChain(token_budget=1000)
    fc.add("low", "low", relevance=0.1)
    fc.add("high", "high", relevance=0.9)
    fc.add("mid", "mid", relevance=0.5)
    selected = fc.select()
    assert selected[0].source == "high"


def test_add_file():
    from unittest.mock import MagicMock
    env = MagicMock()
    env.read_file.return_value = "file content here"
    fc = FocusChain()
    fc.add_file("utils.py", env)
    assert len(fc.items) == 1
    assert fc.items[0].source == "file:utils.py"


def test_to_prompt_section():
    fc = FocusChain(token_budget=1000)
    fc.add("def foo(): pass", "file:foo.py", relevance=0.8)
    section = fc.to_prompt_section()
    assert "Context" in section
    assert "file:foo.py" in section
    assert "def foo" in section


def test_empty():
    fc = FocusChain()
    assert fc.select() == []
    assert fc.to_prompt_section() == ""


def test_budget_property():
    fc = FocusChain(token_budget=2000)
    assert fc.budget == 2000
