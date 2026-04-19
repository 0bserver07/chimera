import math

import pytest

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


def test_add_rejects_relevance_above_one():
    """Bug fix: relevance above 1.0 must raise ValueError."""
    fc = FocusChain()
    with pytest.raises(ValueError, match="relevance"):
        fc.add("content", "src", relevance=1.5)
    assert len(fc.items) == 0


def test_add_rejects_negative_relevance():
    """Bug fix: negative relevance must raise ValueError."""
    fc = FocusChain()
    with pytest.raises(ValueError, match="relevance"):
        fc.add("content", "src", relevance=-0.1)
    assert len(fc.items) == 0


def test_add_rejects_nan_relevance():
    """Bug fix: NaN relevance must raise (breaks ordering otherwise)."""
    fc = FocusChain()
    with pytest.raises(ValueError, match="relevance"):
        fc.add("content", "src", relevance=math.nan)
    assert len(fc.items) == 0


def test_add_accepts_boundary_values():
    """0.0 and 1.0 are explicitly allowed."""
    fc = FocusChain()
    fc.add("low", "a", relevance=0.0)
    fc.add("high", "b", relevance=1.0)
    assert len(fc.items) == 2
