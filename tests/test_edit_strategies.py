# tests/test_edit_strategies.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.edit import EditFileTool
from chimera.tools.strategies import (
    ExactMatch,
    FuzzyEditor,
    IndentFlexible,
    LevenshteinMatch,
    NormalizeWhitespace,
    StripLines,
)


# ---- Strategy unit tests ----


class TestExactMatch:
    def test_found(self):
        result = ExactMatch().find("hello world", "world")
        assert result is not None
        assert result.strategy_name == "exact"
        assert "hello world"[result.start : result.end] == "world"

    def test_ambiguous(self):
        result = ExactMatch().find("x = 1\nx = 1", "x = 1")
        assert result is None

    def test_not_found(self):
        result = ExactMatch().find("hello world", "missing")
        assert result is None


class TestStripLines:
    def test_match_with_extra_whitespace(self):
        content = "    def foo():\n        return 1\n"
        search = "def foo():\n    return 1"
        result = StripLines().find(content, search)
        assert result is not None
        assert result.strategy_name == "strip_lines"


class TestNormalizeWhitespace:
    def test_collapsed_whitespace(self):
        content = "x  =   1\ny=2\n"
        search = "x = 1\ny=2"
        result = NormalizeWhitespace().find(content, search)
        assert result is not None
        assert result.strategy_name == "normalize_whitespace"


class TestIndentFlexible:
    def test_different_base_indent(self):
        content = "        def foo():\n            return 1\n"
        search = "def foo():\n    return 1"
        result = IndentFlexible().find(content, search)
        assert result is not None
        assert result.strategy_name == "indent_flexible"


class TestLevenshteinMatch:
    def test_small_typo(self):
        content = "def hello_world():\n    return 42\n"
        search = "def helo_world():\n    return 42"
        result = LevenshteinMatch().find(content, search)
        assert result is not None
        assert result.strategy_name == "levenshtein"

    def test_below_threshold(self):
        content = "def hello():\n    return 42\n"
        search = "class Goodbye:\n    pass"
        result = LevenshteinMatch().find(content, search)
        assert result is None


# ---- FuzzyEditor tests ----


class TestFuzzyEditor:
    def test_exact_first(self):
        editor = FuzzyEditor()
        result = editor.find("hello world", "world")
        assert result is not None
        assert result.strategy_name == "exact"

    def test_fallback_to_strip_lines(self):
        editor = FuzzyEditor()
        content = "    def foo():\n        return 1\n"
        search = "def foo():\n    return 1"
        result = editor.find(content, search)
        assert result is not None
        assert result.strategy_name == "strip_lines"

    def test_none_when_nothing_matches(self):
        editor = FuzzyEditor()
        result = editor.find("hello world", "completely different text that won't match anything at all")
        assert result is None


# ---- EditFileTool integration tests ----


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestEditFileToolWithFuzzy:
    def test_without_editor_exact_only(self, env):
        env.write_file("main.py", "def hello():\n    return 'hi'\n")
        tool = EditFileTool()
        result = tool.execute({
            "path": "main.py",
            "old_string": "return 'hi'",
            "new_string": "return 'hello'",
        }, env)
        assert result.success
        assert env.read_file("main.py") == "def hello():\n    return 'hello'\n"

    def test_with_editor_fuzzy_fallback(self, env):
        env.write_file("main.py", "    def foo():\n        return 1\n")
        editor = FuzzyEditor()
        tool = EditFileTool(editor=editor)
        result = tool.execute({
            "path": "main.py",
            "old_string": "def foo():\n    return 1",
            "new_string": "def foo():\n    return 2",
        }, env)
        assert result.success
        assert "return 2" in env.read_file("main.py")

    def test_with_editor_reports_strategy(self, env):
        env.write_file("main.py", "    def foo():\n        return 1\n")
        editor = FuzzyEditor()
        tool = EditFileTool(editor=editor)
        result = tool.execute({
            "path": "main.py",
            "old_string": "def foo():\n    return 1",
            "new_string": "def bar():\n    return 2",
        }, env)
        assert result.success
        assert "match_strategy" in result.metadata
        assert result.metadata["match_strategy"] != "exact"

    def test_without_editor_not_found(self, env):
        env.write_file("main.py", "hello\n")
        tool = EditFileTool()
        result = tool.execute({
            "path": "main.py",
            "old_string": "MISSING",
            "new_string": "something",
        }, env)
        assert not result.success
        assert "not found" in result.error.lower()
