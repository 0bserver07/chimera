"""Tests for import graph extraction and related files."""
from __future__ import annotations

import os
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.import_graph import ImportEdge, ImportGraph, ImportGraphTool


@pytest.fixture
def sample_project():
    """Create a temporary project with multiple languages."""
    with tempfile.TemporaryDirectory() as tmp:
        # Python files
        os.makedirs(os.path.join(tmp, "src"))
        with open(os.path.join(tmp, "src", "main.py"), "w") as f:
            f.write("import os\nfrom src.utils import helper\n\ndef main(): pass\n")
        with open(os.path.join(tmp, "src", "utils.py"), "w") as f:
            f.write("import json\n\ndef helper(): pass\n")
        with open(os.path.join(tmp, "src", "models.py"), "w") as f:
            f.write("from src.utils import helper\nfrom dataclasses import dataclass\n")

        # TypeScript file
        with open(os.path.join(tmp, "app.ts"), "w") as f:
            f.write("import { Component } from 'react';\nimport utils from './utils';\n")

        # Go file
        with open(os.path.join(tmp, "main.go"), "w") as f:
            f.write('package main\n\nimport (\n\t"fmt"\n\t"os"\n)\n\nfunc main() {}\n')

        # Rust file
        with open(os.path.join(tmp, "lib.rs"), "w") as f:
            f.write("use std::collections::HashMap;\nextern crate serde;\n")

        yield tmp


class TestImportGraph:
    def test_build_finds_files(self, sample_project):
        graph = ImportGraph()
        graph.build(sample_project)
        assert len(graph.files) >= 4

    def test_python_imports(self, sample_project):
        graph = ImportGraph()
        graph.build(sample_project)
        imports = graph.imports_of("src/main.py")
        assert "os" in imports
        assert "src.utils" in imports

    def test_python_from_import(self, sample_project):
        graph = ImportGraph()
        graph.build(sample_project)
        imports = graph.imports_of("src/models.py")
        assert "src.utils" in imports

    def test_typescript_imports(self, sample_project):
        graph = ImportGraph()
        graph.build(sample_project)
        imports = graph.imports_of("app.ts")
        assert "react" in imports
        assert "./utils" in imports

    def test_go_imports(self, sample_project):
        graph = ImportGraph()
        graph.build(sample_project)
        imports = graph.imports_of("main.go")
        assert "fmt" in imports
        assert "os" in imports

    def test_rust_imports(self, sample_project):
        graph = ImportGraph()
        graph.build(sample_project)
        imports = graph.imports_of("lib.rs")
        assert "std::collections::HashMap" in imports
        assert "serde" in imports

    def test_all_edges(self, sample_project):
        graph = ImportGraph()
        graph.build(sample_project)
        assert len(graph.all_edges) > 0
        assert all(isinstance(e, ImportEdge) for e in graph.all_edges)

    def test_related_files(self, sample_project):
        graph = ImportGraph()
        graph.build(sample_project)
        related = graph.related_files("src/main.py")
        # models.py also imports src.utils, so it should be related
        assert isinstance(related, list)

    def test_empty_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = ImportGraph()
            graph.build(tmp)
            assert len(graph.files) == 0

    def test_syntax_error_handled(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "bad.py"), "w") as f:
                f.write("def broken(\n")  # SyntaxError
            graph = ImportGraph()
            graph.build(tmp)
            # Should not crash, just skip the file or have no imports
            assert isinstance(graph.files, list)

    def test_importers_of(self, sample_project):
        graph = ImportGraph()
        graph.build(sample_project)
        importers = graph.importers_of("src.utils")
        assert "src/main.py" in importers
        assert "src/models.py" in importers

    def test_max_results(self, sample_project):
        graph = ImportGraph()
        graph.build(sample_project)
        related = graph.related_files("src/main.py", max_results=1)
        assert len(related) <= 1


class TestImportGraphTool:
    """ImportGraphTool is the agent-callable wrapper around ImportGraph."""

    def test_schema_and_name(self):
        tool = ImportGraphTool()
        assert tool.name == "import_graph"
        schema = tool.to_anthropic_schema()
        assert "action" in str(schema)

    def test_summary_action(self, sample_project):
        tool = ImportGraphTool()
        env = LocalEnvironment(workdir=sample_project)
        result = tool.execute({"action": "summary"}, env)
        assert result.error is None
        assert "files scanned" in result.output
        assert result.metadata["files"] >= 4
        assert result.metadata["edges"] > 0

    def test_imports_of_action(self, sample_project):
        tool = ImportGraphTool()
        env = LocalEnvironment(workdir=sample_project)
        result = tool.execute(
            {"action": "imports_of", "target": "src/main.py"}, env
        )
        assert result.error is None
        assert "os" in result.output
        assert "src.utils" in result.output

    def test_importers_of_action(self, sample_project):
        tool = ImportGraphTool()
        env = LocalEnvironment(workdir=sample_project)
        result = tool.execute(
            {"action": "importers_of", "target": "src.utils"}, env
        )
        assert result.error is None
        assert "src/main.py" in result.output
        assert "src/models.py" in result.output

    def test_related_action(self, sample_project):
        tool = ImportGraphTool()
        env = LocalEnvironment(workdir=sample_project)
        result = tool.execute(
            {"action": "related", "target": "src/main.py", "max_results": 5}, env
        )
        # Either finds related files or returns an honest "no results" message.
        assert result.error is None

    def test_unknown_action(self, sample_project):
        tool = ImportGraphTool()
        env = LocalEnvironment(workdir=sample_project)
        result = tool.execute({"action": "explode"}, env)
        assert result.error is not None
        assert "Unknown action" in result.error

    def test_missing_target(self, sample_project):
        tool = ImportGraphTool()
        env = LocalEnvironment(workdir=sample_project)
        result = tool.execute({"action": "imports_of"}, env)
        assert result.error is not None
        assert "target" in result.error

    def test_bad_root(self):
        tool = ImportGraphTool()
        result = tool.execute(
            {"action": "summary", "root": "/no/such/dir/xyzzy"}, None
        )
        assert result.error is not None
        assert "Not a directory" in result.error

    def test_explicit_root_overrides_env(self, sample_project):
        tool = ImportGraphTool()
        # Pass root explicitly; env=None.
        result = tool.execute(
            {"action": "summary", "root": sample_project}, None
        )
        assert result.error is None
        assert result.metadata["files"] >= 4

    def test_imports_of_no_results_message(self, sample_project):
        tool = ImportGraphTool()
        env = LocalEnvironment(workdir=sample_project)
        result = tool.execute(
            {"action": "imports_of", "target": "nonexistent.py"}, env
        )
        assert result.error is None
        assert "No results" in result.output
