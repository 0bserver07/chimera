"""Tests for chimera.training.fault_localization."""

from chimera.training.fault_localization import FaultLocalizer, SuspiciousLocation


def test_localize_simple():
    """Single failure pointing to one location."""
    output = """
FAILED tests/test_calc.py::test_add
    calculator.py:5: in add
        return a - b
    assert add(2, 3) == 5
    AssertionError: assert -1 == 5
"""
    localizer = FaultLocalizer()
    locations = localizer.localize(output)
    assert len(locations) >= 1
    assert locations[0].file == "calculator.py"
    assert locations[0].line == 5


def test_localize_multiple():
    """Multiple failures ranking locations by frequency."""
    output = """
FAILED test_a - utils.py:10: error
FAILED test_b - utils.py:10: error
FAILED test_c - other.py:5: error
"""
    localizer = FaultLocalizer()
    locations = localizer.localize(output)
    # utils.py:10 appears in 2/3 failures, should rank higher
    assert locations[0].file == "utils.py"


def test_localize_filters_test_files():
    """Test file references are excluded from results."""
    output = """
FAILED tests/test_foo.py::test_bar
    tests/test_foo.py:10: assert ...
    src/foo.py:5: actual bug
"""
    localizer = FaultLocalizer()
    locations = localizer.localize(output)
    assert all("test_" not in loc.file for loc in locations)


def test_augment_prompt():
    """Augmented prompt includes suspected bug locations."""
    locations = [SuspiciousLocation("utils.py", "calc", 10, 0.9, "2/2 failures")]
    localizer = FaultLocalizer()
    result = localizer.augment_prompt("Fix the bug.", locations)
    assert "utils.py:10" in result
    assert "Suspected Bug Locations" in result


def test_localize_no_failures():
    """No failures returns empty list."""
    localizer = FaultLocalizer()
    assert localizer.localize("all 5 passed") == []
