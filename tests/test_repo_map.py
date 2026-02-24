# tests/test_repo_map.py
"""Tests for repository mapping."""
from __future__ import annotations

import tempfile
from pathlib import Path

from chimera.tools.repo_map import RepoMap


class TestRepoMap:
    def test_maps_python_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "calc.py").write_text(
                "def add(a: int, b: int) -> int:\n"
                "    return a + b\n"
                "\n"
                "def subtract(a, b):\n"
                "    return a - b\n"
            )
            rm = RepoMap(tmpdir)
            output = rm.generate()
            assert "calc.py" in output
            assert "add(a: int, b: int) -> int" in output
            assert "subtract(a, b)" in output

    def test_maps_classes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "models.py").write_text(
                "class User:\n"
                "    def __init__(self, name: str):\n"
                "        self.name = name\n"
                "\n"
                "    def greet(self) -> str:\n"
                "        return f'Hi {self.name}'\n"
            )
            rm = RepoMap(tmpdir)
            output = rm.generate()
            assert "class User" in output
            assert "__init__(self, name: str)" in output
            assert "greet(self) -> str" in output

    def test_maps_nested_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = Path(tmpdir, "pkg", "sub")
            sub.mkdir(parents=True)
            Path(sub, "helper.py").write_text("def util():\n    pass\n")
            rm = RepoMap(tmpdir)
            output = rm.generate()
            assert "helper.py" in output
            assert "util()" in output

    def test_ignores_non_python(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "data.json").write_text('{"key": "value"}')
            Path(tmpdir, "code.py").write_text("x = 1\n")
            rm = RepoMap(tmpdir)
            output = rm.generate()
            assert "data.json" in output  # Listed but no signatures
            assert "code.py" in output

    def test_respects_max_depth(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            deep = Path(tmpdir, "a", "b", "c")
            deep.mkdir(parents=True)
            Path(deep, "deep.py").write_text("def deep_fn():\n    pass\n")
            Path(tmpdir, "top.py").write_text("def top_fn():\n    pass\n")
            rm = RepoMap(tmpdir, max_depth=1)
            output = rm.generate()
            assert "top.py" in output
            assert "deep.py" not in output

    def test_ignores_hidden_and_venv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, ".git").mkdir()
            Path(tmpdir, ".git", "config").write_text("x")
            Path(tmpdir, "__pycache__").mkdir()
            Path(tmpdir, "__pycache__", "mod.cpython-311.pyc").write_text("x")
            Path(tmpdir, "real.py").write_text("def fn():\n    pass\n")
            rm = RepoMap(tmpdir)
            output = rm.generate()
            assert ".git" not in output
            assert "__pycache__" not in output
            assert "real.py" in output

    def test_handles_syntax_errors(self):
        """Files with syntax errors should be listed but not parsed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "broken.py").write_text("def broken(:\n    pass\n")
            rm = RepoMap(tmpdir)
            output = rm.generate()
            assert "broken.py" in output  # File listed even if unparseable
