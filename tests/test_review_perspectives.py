"""Tests for review perspectives and perspective registry."""
from __future__ import annotations

from unittest.mock import MagicMock

from chimera.review.perspective import BUILTIN_PERSPECTIVES, ReviewPerspective
from chimera.review.registry import PerspectiveRegistry
from chimera.review.orchestrator import ReviewOrchestrator


def test_builtin_perspectives_loaded() -> None:
    """PerspectiveRegistry has 8 built-ins on init."""
    registry = PerspectiveRegistry()
    names = registry.list()
    assert len(names) == 8
    expected = {
        "logic", "security", "tests", "architecture",
        "concurrency", "performance", "type_safety", "error_handling",
    }
    assert set(names) == expected


def test_register_custom() -> None:
    """Register new perspective, get returns it."""
    registry = PerspectiveRegistry()
    custom = ReviewPerspective(
        name="accessibility",
        focus_area="WCAG compliance, screen reader support, color contrast",
        prompt_template="Review this diff for accessibility...\n\n{diff}",
    )
    registry.register(custom)
    result = registry.get("accessibility")
    assert result is custom
    assert result.name == "accessibility"
    assert "accessibility" in registry.list()


def test_get_unknown_raises() -> None:
    """KeyError for unknown name."""
    registry = PerspectiveRegistry()
    try:
        registry.get("nonexistent")
        assert False, "Expected KeyError"
    except KeyError:
        pass


def test_list_returns_names() -> None:
    """list() returns all names sorted."""
    registry = PerspectiveRegistry()
    names = registry.list()
    assert names == sorted(names)
    assert len(names) == 8


def test_for_language_filters() -> None:
    """Perspective with languages=['python'] only matches python."""
    registry = PerspectiveRegistry()
    python_only = ReviewPerspective(
        name="pythonic",
        focus_area="PEP 8, idiomatic Python, Pythonic patterns",
        prompt_template="Review for Pythonic style...\n\n{diff}",
        languages=["python"],
    )
    registry.register(python_only)

    python_perspectives = registry.for_language("python")
    names = [p.name for p in python_perspectives]
    assert "pythonic" in names

    js_perspectives = registry.for_language("javascript")
    js_names = [p.name for p in js_perspectives]
    assert "pythonic" not in js_names


def test_for_language_none_matches_all() -> None:
    """Perspectives with languages=None match everything."""
    registry = PerspectiveRegistry()
    # All built-ins have languages=None
    python_perspectives = registry.for_language("python")
    assert len(python_perspectives) == 8

    rust_perspectives = registry.for_language("rust")
    assert len(rust_perspectives) == 8


def test_orchestrator_uses_registry() -> None:
    """Orchestrator with custom perspectives uses those from the registry."""
    registry = PerspectiveRegistry()
    custom = ReviewPerspective(
        name="custom_review",
        focus_area="Custom review focus",
        prompt_template="Custom review prompt for:\n\n{diff}",
    )
    registry.register(custom)

    orchestrator = ReviewOrchestrator(
        perspectives=["custom_review", "security"],
        registry=registry,
    )

    perspective_names = [p.name for p in orchestrator.perspectives]
    assert perspective_names == ["custom_review", "security"]
    assert len(orchestrator.perspectives) == 2


def test_orchestrator_backward_compat() -> None:
    """No perspectives param = default 4 perspectives."""
    orchestrator = ReviewOrchestrator()
    perspective_names = [p.name for p in orchestrator.perspectives]
    assert perspective_names == ["logic", "security", "tests", "architecture"]
    assert len(orchestrator.perspectives) == 4


def test_builtin_prompt_templates_are_substantive() -> None:
    """All built-in prompt templates should be substantive (~200 words)."""
    for name, perspective in BUILTIN_PERSPECTIVES.items():
        word_count = len(perspective.prompt_template.split())
        assert word_count >= 100, (
            f"Perspective '{name}' prompt template has only {word_count} words, "
            f"expected at least 100"
        )
        assert "{diff}" in perspective.prompt_template, (
            f"Perspective '{name}' prompt template missing {{diff}} placeholder"
        )


def test_register_overrides_builtin() -> None:
    """Registering a perspective with a built-in name overrides it."""
    registry = PerspectiveRegistry()
    original = registry.get("logic")
    override = ReviewPerspective(
        name="logic",
        focus_area="Custom logic focus",
        prompt_template="Custom logic prompt.\n\n{diff}",
    )
    registry.register(override)
    result = registry.get("logic")
    assert result is override
    assert result is not original


def test_for_language_case_insensitive() -> None:
    """Language matching is case-insensitive."""
    registry = PerspectiveRegistry()
    py_only = ReviewPerspective(
        name="py_style",
        focus_area="Python style",
        prompt_template="Style review.\n\n{diff}",
        languages=["Python"],
    )
    registry.register(py_only)

    results = registry.for_language("python")
    names = [p.name for p in results]
    assert "py_style" in names

    results_upper = registry.for_language("PYTHON")
    names_upper = [p.name for p in results_upper]
    assert "py_style" in names_upper
