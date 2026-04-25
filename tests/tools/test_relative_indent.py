# tests/test_relative_indent.py
"""Tests for chimera.tools.relative_indent — robust indent-aware search/replace."""
from chimera.tools.relative_indent import (
    find_with_relative_indent,
    replace_with_relative_indent,
)


class TestFindWithRelativeIndent:
    def test_exact_match(self):
        content = "def foo():\n    return 1\n"
        search = "def foo():\n    return 1\n"
        result = find_with_relative_indent(content, search)
        assert result is not None
        assert result.strategy == "exact"
        assert result.start == 0

    def test_different_base_indent(self):
        content = "class A:\n    def foo(self):\n        return 1\n"
        search = "def foo(self):\n    return 1"
        result = find_with_relative_indent(content, search)
        assert result is not None
        assert result.strategy == "relative_indent"

    def test_tabs_vs_spaces(self):
        content = "\t\tdef bar():\n\t\t\treturn 42\n"
        search = "def bar():\n    return 42"
        result = find_with_relative_indent(content, search)
        assert result is not None
        assert result.strategy == "relative_indent"

    def test_no_match(self):
        content = "def foo():\n    return 1\n"
        search = "def bar():\n    return 2"
        result = find_with_relative_indent(content, search)
        assert result is None


class TestReplaceWithRelativeIndent:
    def test_exact_replace(self):
        content = "x = 1\ny = 2\n"
        result = replace_with_relative_indent(content, "x = 1", "x = 10")
        assert result == "x = 10\ny = 2\n"

    def test_indent_adapted_replace(self):
        content = "class A:\n    def foo(self):\n        return 1\n"
        old = "def foo(self):\n    return 1"
        new = "def foo(self):\n    return 2"
        result = replace_with_relative_indent(content, old, new)
        assert result is not None
        assert "return 2" in result

    def test_no_match_returns_none(self):
        content = "hello world\n"
        result = replace_with_relative_indent(content, "missing", "new")
        assert result is None
