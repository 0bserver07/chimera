"""Tests for multi-language repository parsers."""
from __future__ import annotations

import tempfile
from pathlib import Path

from chimera.tools.parsers.python_parser import PythonParser
from chimera.tools.parsers.typescript import TypeScriptParser
from chimera.tools.parsers.go import GoParser
from chimera.tools.parsers.rust import RustParser
from chimera.tools.parsers.base import Symbol
from chimera.tools.repo_map import RepoMap


# ---------------------------------------------------------------------------
# PythonParser
# ---------------------------------------------------------------------------

class TestPythonParser:
    def setup_method(self):
        self.parser = PythonParser()

    def test_python_function(self):
        syms = self.parser.parse("def foo(): pass")
        assert len(syms) == 1
        assert "foo" in syms[0].name
        assert syms[0].kind == "function"

    def test_python_class(self):
        source = "class Foo:\n    def bar(self): pass\n"
        syms = self.parser.parse(source)
        assert len(syms) == 1
        assert syms[0].name == "Foo"
        assert syms[0].kind == "class"
        assert len(syms[0].children) == 1
        assert "bar" in syms[0].children[0].name
        assert syms[0].children[0].kind == "method"

    def test_python_async(self):
        syms = self.parser.parse("async def fetch(): pass")
        assert len(syms) == 1
        assert "fetch" in syms[0].name
        assert syms[0].kind == "function"

    def test_python_empty(self):
        assert self.parser.parse("") == []

    def test_python_syntax_error(self):
        assert self.parser.parse("def broken(:\n    pass") == []

    def test_python_full_signature(self):
        syms = self.parser.parse("def add(a: int, b: int) -> int:\n    return a + b")
        assert len(syms) == 1
        assert syms[0].name == "add(a: int, b: int) -> int"
        assert syms[0].kind == "function"

    def test_python_extensions(self):
        assert ".py" in PythonParser.extensions


# ---------------------------------------------------------------------------
# TypeScriptParser
# ---------------------------------------------------------------------------

class TestTypeScriptParser:
    def setup_method(self):
        self.parser = TypeScriptParser()

    def test_ts_function(self):
        syms = self.parser.parse("export function greet(name: string): void {}")
        fns = [s for s in syms if s.kind == "function"]
        assert any("greet" in s.name for s in fns)

    def test_ts_class(self):
        source = "class Foo {\n  bar() {}\n}\n"
        syms = self.parser.parse(source)
        classes = [s for s in syms if s.kind == "class"]
        assert len(classes) >= 1
        cls = classes[0]
        assert cls.name == "Foo"
        assert any("bar" in c.name for c in cls.children)

    def test_ts_interface(self):
        source = "interface IFoo {\n  baz(): void;\n}\n"
        syms = self.parser.parse(source)
        interfaces = [s for s in syms if s.kind == "interface"]
        assert len(interfaces) >= 1
        assert interfaces[0].name == "IFoo"

    def test_ts_const(self):
        source = "export const handler = () => {}\n"
        syms = self.parser.parse(source)
        fns = [s for s in syms if s.kind == "function"]
        assert any("handler" in s.name for s in fns)

    def test_ts_empty(self):
        assert self.parser.parse("") == []
        assert self.parser.parse("   \n\n  ") == []

    def test_ts_extensions(self):
        for ext in (".ts", ".tsx", ".js", ".jsx"):
            assert ext in TypeScriptParser.extensions


# ---------------------------------------------------------------------------
# GoParser
# ---------------------------------------------------------------------------

class TestGoParser:
    def setup_method(self):
        self.parser = GoParser()

    def test_go_function(self):
        syms = self.parser.parse("func main() {}")
        fns = [s for s in syms if s.kind == "function"]
        assert any(s.name == "main" for s in fns)

    def test_go_method(self):
        syms = self.parser.parse("func (s *Server) Start() {}")
        methods = [s for s in syms if s.kind == "method"]
        assert any(s.name == "Start" for s in methods)

    def test_go_struct(self):
        syms = self.parser.parse("type Config struct {\n    Host string\n}\n")
        structs = [s for s in syms if s.kind == "struct"]
        assert any(s.name == "Config" for s in structs)

    def test_go_interface(self):
        syms = self.parser.parse("type Handler interface {\n    Handle()\n}\n")
        interfaces = [s for s in syms if s.kind == "interface"]
        assert any(s.name == "Handler" for s in interfaces)

    def test_go_empty(self):
        assert self.parser.parse("") == []

    def test_go_extensions(self):
        assert ".go" in GoParser.extensions


# ---------------------------------------------------------------------------
# RustParser
# ---------------------------------------------------------------------------

class TestRustParser:
    def setup_method(self):
        self.parser = RustParser()

    def test_rust_function(self):
        syms = self.parser.parse("fn main() {}")
        fns = [s for s in syms if s.kind == "function"]
        assert any(s.name == "main" for s in fns)

    def test_rust_pub_function(self):
        syms = self.parser.parse("pub fn serve() {}")
        fns = [s for s in syms if s.kind == "function"]
        assert any(s.name == "serve" for s in fns)

    def test_rust_struct(self):
        syms = self.parser.parse("pub struct Config {}")
        structs = [s for s in syms if s.kind == "struct"]
        assert any(s.name == "Config" for s in structs)

    def test_rust_trait(self):
        syms = self.parser.parse("trait Handler {}")
        traits = [s for s in syms if s.kind == "trait"]
        assert any(s.name == "Handler" for s in traits)

    def test_rust_impl(self):
        source = "impl Config {\n    fn new() {}\n}\n"
        syms = self.parser.parse(source)
        impls = [s for s in syms if s.kind == "impl"]
        assert len(impls) >= 1
        impl_sym = impls[0]
        assert impl_sym.name == "Config"
        methods = [c for c in impl_sym.children if c.kind == "method"]
        assert any(c.name == "new" for c in methods)

    def test_rust_empty(self):
        assert self.parser.parse("") == []

    def test_rust_extensions(self):
        assert ".rs" in RustParser.extensions


# ---------------------------------------------------------------------------
# Integration: RepoMap with multiple languages
# ---------------------------------------------------------------------------

class TestRepoMapMultiLanguage:
    def test_repo_map_mixed_languages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "main.py").write_text(
                "def run():\n    pass\n"
            )
            Path(tmpdir, "app.ts").write_text(
                "export function start(): void {}\n"
            )
            Path(tmpdir, "server.go").write_text(
                "func main() {}\n"
            )
            Path(tmpdir, "lib.rs").write_text(
                "pub fn init() {}\n"
            )
            rm = RepoMap(tmpdir)
            output = rm.generate()

            assert "main.py" in output
            assert "run" in output   # Python function

            assert "app.ts" in output
            assert "start" in output  # TypeScript function

            assert "server.go" in output
            assert "main" in output   # Go function

            assert "lib.rs" in output
            assert "init" in output   # Rust function

    def test_repo_map_python_backward_compat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "calc.py").write_text(
                "def add(a: int, b: int) -> int:\n"
                "    return a + b\n"
                "\n"
                "class Math:\n"
                "    def subtract(self, a, b):\n"
                "        return a - b\n"
            )
            rm = RepoMap(tmpdir)
            output = rm.generate()

            assert "calc.py" in output
            assert "add(a: int, b: int) -> int" in output
            assert "class Math" in output
            assert "subtract(self, a, b)" in output
