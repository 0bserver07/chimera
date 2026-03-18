# tests/test_definition_lookup.py
"""Tests for the DefinitionLookupTool and DefinitionFinder."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from chimera.tools.definition_lookup import Definition, DefinitionFinder, DefinitionLookupTool
from chimera.types import ToolResult


@pytest.fixture
def project_dir():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "utils.py"), "w") as f:
            f.write(
                "def calculate_tax(amount):\n"
                "    return amount * 0.1\n"
                "\n"
                "class TaxCalculator:\n"
                "    pass\n"
                "\n"
                "TAX_RATE = 0.1\n"
            )
        with open(os.path.join(d, "app.js"), "w") as f:
            f.write(
                "function processOrder(items) {\n"
                "  return items.map(i => i.price);\n"
                "}\n"
                "\n"
                "class OrderService {\n"
                "}\n"
            )
        sub = os.path.join(d, "pkg")
        os.makedirs(sub)
        with open(os.path.join(sub, "helpers.py"), "w") as f:
            f.write(
                "class TaxCalculator:\n"
                "    def calculate_tax(self, amount):\n"
                "        return amount * 0.2\n"
            )
        yield d


# ------------------------------------------------------------------
# DefinitionFinder tests
# ------------------------------------------------------------------

class TestDefinitionFinderPython:
    def test_find_function(self, project_dir):
        finder = DefinitionFinder(project_dir)
        results = finder.find("calculate_tax")
        assert len(results) >= 1
        top = results[0]
        assert top.kind == "function"
        assert top.line == 1
        assert "calculate_tax" in top.source

    def test_find_class(self, project_dir):
        finder = DefinitionFinder(project_dir)
        results = finder.find("TaxCalculator")
        assert len(results) >= 1
        assert any(r.kind == "class" for r in results)

    def test_find_variable(self, project_dir):
        finder = DefinitionFinder(project_dir)
        results = finder.find("TAX_RATE")
        assert len(results) >= 1
        assert results[0].kind == "variable"
        assert results[0].line == 7

    def test_find_method(self, project_dir):
        finder = DefinitionFinder(project_dir)
        results = finder.find("calculate_tax")
        # Should find both the function in utils.py and the method in pkg/helpers.py
        kinds = {r.kind for r in results}
        assert "function" in kinds
        assert "method" in kinds

    def test_function_source_includes_body(self, project_dir):
        finder = DefinitionFinder(project_dir)
        results = finder.find("calculate_tax")
        func_results = [r for r in results if r.kind == "function"]
        assert len(func_results) == 1
        assert "return amount * 0.1" in func_results[0].source


class TestDefinitionFinderJavaScript:
    def test_find_function(self, project_dir):
        finder = DefinitionFinder(project_dir)
        results = finder.find("processOrder")
        assert len(results) >= 1
        assert results[0].kind == "function"
        assert results[0].line == 1

    def test_find_class(self, project_dir):
        finder = DefinitionFinder(project_dir)
        results = finder.find("OrderService")
        assert len(results) >= 1
        assert results[0].kind == "class"


class TestDefinitionFinderEdgeCases:
    def test_not_found(self, project_dir):
        finder = DefinitionFinder(project_dir)
        results = finder.find("nonexistent_symbol_xyz")
        assert results == []

    def test_file_hint_searched_first(self, project_dir):
        finder = DefinitionFinder(project_dir)
        results = finder.find("calculate_tax", file_hint="utils.py")
        assert len(results) >= 1
        assert results[0].file == "utils.py"

    def test_file_hint_nonexistent(self, project_dir):
        """A file_hint that doesn't exist should not cause errors."""
        finder = DefinitionFinder(project_dir)
        results = finder.find("calculate_tax", file_hint="no_such_file.py")
        assert len(results) >= 1  # still finds it by walking

    def test_skips_hidden_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            git_dir = os.path.join(d, ".git")
            os.makedirs(git_dir)
            with open(os.path.join(git_dir, "code.py"), "w") as f:
                f.write("def hidden_fn():\n    pass\n")
            with open(os.path.join(d, "visible.py"), "w") as f:
                f.write("def visible_fn():\n    pass\n")
            finder = DefinitionFinder(d)
            hidden = finder.find("hidden_fn")
            assert hidden == []
            visible = finder.find("visible_fn")
            assert len(visible) == 1

    def test_handles_syntax_errors(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "broken.py"), "w") as f:
                f.write("def broken(:\n    pass\n")
            finder = DefinitionFinder(d)
            # Falls back to regex, which may or may not match
            # but should not raise
            results = finder.find("broken")
            # The regex fallback should still find it via "def broken("
            assert isinstance(results, list)

    def test_handles_binary_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "data.py"), "wb") as f:
                f.write(b"\x00\x01\x02\x03")
            finder = DefinitionFinder(d)
            results = finder.find("anything")
            assert results == []


class TestDefinitionFinderMultiLanguage:
    def test_rust_function(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "lib.rs"), "w") as f:
                f.write("pub fn parse_input(data: &str) -> Result<()> {\n    Ok(())\n}\n")
            finder = DefinitionFinder(d)
            results = finder.find("parse_input")
            assert len(results) == 1
            assert results[0].kind == "function"

    def test_rust_struct(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "types.rs"), "w") as f:
                f.write("pub struct Config {\n    pub debug: bool,\n}\n")
            finder = DefinitionFinder(d)
            results = finder.find("Config")
            assert len(results) == 1
            assert results[0].kind == "struct"

    def test_go_function(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "main.go"), "w") as f:
                f.write("func handleRequest(w http.ResponseWriter, r *http.Request) {\n}\n")
            finder = DefinitionFinder(d)
            results = finder.find("handleRequest")
            assert len(results) == 1
            assert results[0].kind == "function"

    def test_go_struct(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "models.go"), "w") as f:
                f.write("type Server struct {\n    Port int\n}\n")
            finder = DefinitionFinder(d)
            results = finder.find("Server")
            assert len(results) == 1
            assert results[0].kind == "struct"

    def test_typescript_interface(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "types.ts"), "w") as f:
                f.write("export interface UserConfig {\n    name: string;\n}\n")
            finder = DefinitionFinder(d)
            results = finder.find("UserConfig")
            assert len(results) == 1
            assert results[0].kind == "interface"

    def test_const_variable(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "config.ts"), "w") as f:
                f.write("export const MAX_RETRIES = 3;\n")
            finder = DefinitionFinder(d)
            results = finder.find("MAX_RETRIES")
            assert len(results) == 1
            assert results[0].kind == "variable"


# ------------------------------------------------------------------
# DefinitionLookupTool tests
# ------------------------------------------------------------------

class TestDefinitionLookupTool:
    def test_tool_schema(self):
        tool = DefinitionLookupTool()
        assert tool.name == "definition_lookup"
        schema = tool.to_anthropic_schema()
        assert schema["name"] == "definition_lookup"
        assert "input_schema" in schema
        assert "symbol" in schema["input_schema"]["properties"]

    def test_execute_found(self, project_dir):
        tool = DefinitionLookupTool()
        env = MagicMock()
        env.workdir = project_dir
        result = tool.execute({"symbol": "calculate_tax"}, env=env)
        assert result.success
        assert "calculate_tax" in result.output
        assert result.metadata["count"] >= 1

    def test_execute_not_found(self, project_dir):
        tool = DefinitionLookupTool()
        env = MagicMock()
        env.workdir = project_dir
        result = tool.execute({"symbol": "xyz_not_here"}, env=env)
        assert result.success  # no error, just "not found" message
        assert "No definition found" in result.output

    def test_execute_with_file_hint(self, project_dir):
        tool = DefinitionLookupTool()
        env = MagicMock()
        env.workdir = project_dir
        result = tool.execute(
            {"symbol": "calculate_tax", "file_hint": "utils.py"}, env=env,
        )
        assert result.success
        assert "utils.py" in result.output

    def test_execute_no_env(self):
        """When env is None, falls back to cwd."""
        tool = DefinitionLookupTool()
        # Should not raise
        result = tool.execute({"symbol": "DefinitionLookupTool"}, env=None)
        assert isinstance(result, ToolResult)

    def test_openai_schema(self):
        tool = DefinitionLookupTool()
        schema = tool.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "definition_lookup"


class TestDefinitionDataclass:
    def test_fields(self):
        d = Definition(
            symbol="foo", kind="function", file="bar.py", line=42, source="def foo(): pass",
        )
        assert d.symbol == "foo"
        assert d.kind == "function"
        assert d.file == "bar.py"
        assert d.line == 42
        assert d.source == "def foo(): pass"
