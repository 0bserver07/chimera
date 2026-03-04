"""Tests for test generation agent."""
from __future__ import annotations

import os
import tempfile

import pytest

from chimera.testgen.generator import TestCase, TestGenerator
from chimera.testgen.coverage import CoverageReport, parse_coverage


class TestTestGenerator:
    def test_analyze_function(self):
        source = "def add(a, b):\n    return a + b\n"
        gen = TestGenerator()
        cases = gen.analyze_source(source, "math.py")
        assert len(cases) >= 1
        assert any("test_add" in c.name for c in cases)

    def test_analyze_class_method(self):
        source = "class Calc:\n    def multiply(self, x, y):\n        return x * y\n"
        gen = TestGenerator()
        cases = gen.analyze_source(source, "calc.py")
        assert any("Calc_multiply" in c.name for c in cases)

    def test_analyze_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def greet(name):\n    return f'Hello {name}'\n")
            path = f.name
        try:
            gen = TestGenerator()
            cases = gen.analyze(path)
            assert len(cases) >= 1
        finally:
            os.unlink(path)

    def test_edge_case_generated(self):
        source = "def process(data, flag):\n    pass\n"
        gen = TestGenerator()
        cases = gen.analyze_source(source)
        edge_cases = [c for c in cases if c.category == "edge"]
        assert len(edge_cases) >= 1

    def test_error_case_generated(self):
        source = "def compute(x):\n    pass\n"
        gen = TestGenerator()
        cases = gen.analyze_source(source)
        error_cases = [c for c in cases if c.category == "error"]
        assert len(error_cases) >= 1

    def test_private_functions_skipped(self):
        source = "def _internal():\n    pass\ndef public():\n    pass\n"
        gen = TestGenerator()
        cases = gen.analyze_source(source)
        assert not any("_internal" in c.name for c in cases)
        assert any("public" in c.name for c in cases)

    def test_syntax_error(self):
        gen = TestGenerator()
        cases = gen.analyze_source("def broken(\n")
        assert cases == []

    def test_clear(self):
        gen = TestGenerator()
        gen.analyze_source("def foo(): pass\n")
        assert len(gen.test_cases) > 0
        gen.clear()
        assert len(gen.test_cases) == 0

    def test_test_code_content(self):
        source = "def add(a, b):\n    return a + b\n"
        gen = TestGenerator()
        cases = gen.analyze_source(source)
        unit = [c for c in cases if c.category == "unit"][0]
        assert "def test_add" in unit.test_code


class TestCoverageReport:
    def test_parse_coverage_output(self):
        output = """Name               Stmts   Miss  Cover   Missing
-----------------------------------------------
src/foo.py            50     10    80%   12-15, 30
src/bar.py            30      0   100%
-----------------------------------------------
TOTAL                 80     10    88%"""
        report = parse_coverage(output)
        assert report.total_statements == 80
        assert report.total_missing == 10
        assert report.coverage_percent == 88.0
        assert report.file_coverage["src/foo.py"] == 80.0
        assert report.file_coverage["src/bar.py"] == 100.0

    def test_uncovered_lines(self):
        output = "src/foo.py            50     10    80%   12-15, 30\nTOTAL                 50     10    80%"
        report = parse_coverage(output)
        lines = report.uncovered_lines.get("src/foo.py", [])
        assert 12 in lines
        assert 13 in lines
        assert 14 in lines
        assert 15 in lines
        assert 30 in lines

    def test_files_below_threshold(self):
        report = CoverageReport(file_coverage={"a.py": 60.0, "b.py": 90.0, "c.py": 50.0})
        below = report.files_below(80.0)
        assert "a.py" in below
        assert "c.py" in below
        assert "b.py" not in below

    def test_covered_statements(self):
        report = CoverageReport(total_statements=100, total_missing=25)
        assert report.covered_statements == 75

    def test_empty_output(self):
        report = parse_coverage("")
        assert report.total_statements == 0
