"""Tests for TreeSitterParser — works whether or not tree-sitter is installed."""
from __future__ import annotations

import pytest

from chimera.tools.parsers.tree_sitter import (
    TreeSitterParser,
    _TS_LANGUAGES,
    get_parser,
    tree_sitter_available,
)


# ---------------------------------------------------------------------------
# Language map
# ---------------------------------------------------------------------------

def test_language_map_has_common_extensions():
    assert ".py" in _TS_LANGUAGES
    assert ".js" in _TS_LANGUAGES
    assert ".ts" in _TS_LANGUAGES
    assert ".go" in _TS_LANGUAGES
    assert ".rs" in _TS_LANGUAGES
    assert ".java" in _TS_LANGUAGES
    assert ".c" in _TS_LANGUAGES
    assert ".cpp" in _TS_LANGUAGES
    assert ".rb" in _TS_LANGUAGES


def test_language_map_values_are_strings():
    for ext, lang in _TS_LANGUAGES.items():
        assert isinstance(ext, str) and ext.startswith(".")
        assert isinstance(lang, str) and len(lang) > 0


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def test_tree_sitter_available_returns_bool():
    result = tree_sitter_available()
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

def test_parser_graceful_without_tree_sitter():
    """If tree-sitter not installed, parse returns empty list."""
    parser = TreeSitterParser()
    if not parser._available:
        result = parser.parse("def foo(): pass", ".py")
        assert result == []


def test_can_parse_rejects_unknown_extension():
    parser = TreeSitterParser()
    assert not parser.can_parse(".xyz")
    assert not parser.can_parse(".unknown")
    assert not parser.can_parse("")


def test_can_parse_known_extensions():
    parser = TreeSitterParser()
    if parser._available:
        assert parser.can_parse(".py")
        assert parser.can_parse(".js")
        assert parser.can_parse(".ts")
        assert parser.can_parse(".go")
        assert parser.can_parse(".rs")


def test_parse_unknown_extension_returns_empty():
    parser = TreeSitterParser()
    result = parser.parse("some code", ".xyz")
    assert result == []


# ---------------------------------------------------------------------------
# Fallback helper
# ---------------------------------------------------------------------------

def test_get_parser_fallback():
    """get_parser returns a parser for known extensions even without tree-sitter."""
    parser = get_parser(".py")
    assert parser is not None

    parser = get_parser(".ts")
    assert parser is not None

    parser = get_parser(".go")
    assert parser is not None

    parser = get_parser(".rs")
    assert parser is not None

    parser = get_parser(".xyz")
    assert parser is None


def test_get_parser_js_jsx_fallback():
    """get_parser covers .js and .jsx via TypeScript fallback."""
    parser = get_parser(".js")
    assert parser is not None
    parser = get_parser(".jsx")
    assert parser is not None


# ---------------------------------------------------------------------------
# TreeSitterParser attributes
# ---------------------------------------------------------------------------

def test_extensions_tuple():
    parser = TreeSitterParser()
    assert isinstance(parser.extensions, tuple)
    assert ".py" in parser.extensions
    assert ".rs" in parser.extensions


# ---------------------------------------------------------------------------
# tree-sitter integration (only when installed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not tree_sitter_available(), reason="tree-sitter not installed"
)
def test_parse_python_with_tree_sitter():
    parser = TreeSitterParser()
    source = (
        "def hello():\n"
        "    pass\n"
        "\n"
        "class Foo:\n"
        "    def bar(self):\n"
        "        pass\n"
    )
    symbols = parser.parse(source, ".py")
    names = [s.name for s in symbols]
    assert "hello" in names
    assert "Foo" in names
    # Foo should have a child method 'bar'
    foo = [s for s in symbols if s.name == "Foo"][0]
    assert foo.kind == "class"
    child_names = [c.name for c in foo.children]
    assert "bar" in child_names


@pytest.mark.skipif(
    not tree_sitter_available(), reason="tree-sitter not installed"
)
def test_parse_javascript_with_tree_sitter():
    parser = TreeSitterParser()
    source = "function greet(name) { return name; }\n"
    symbols = parser.parse(source, ".js")
    names = [s.name for s in symbols]
    assert "greet" in names


@pytest.mark.skipif(
    not tree_sitter_available(), reason="tree-sitter not installed"
)
def test_parse_empty_source():
    parser = TreeSitterParser()
    symbols = parser.parse("", ".py")
    assert symbols == []
