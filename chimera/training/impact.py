from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field


@dataclass
class CallerInfo:
    """A function that calls the target symbol."""
    file: str
    function: str
    line: int


@dataclass
class ImpactReport:
    """Impact analysis of changing a symbol."""
    symbol: str
    file_path: str
    callers: list[CallerInfo] = field(default_factory=list)
    importers: list[str] = field(default_factory=list)  # files that import from this module
    tests: list[str] = field(default_factory=list)  # test files that might exercise this

    def to_prompt_section(self) -> str:
        """Format as a warning for the agent prompt."""
        lines = [f"\n## Impact Analysis: {self.symbol}\n"]
        if self.callers:
            lines.append("**Callers** (will be affected by changes):")
            for c in self.callers[:5]:
                lines.append(f"  - `{c.function}()` in `{c.file}` line {c.line}")
        if self.importers:
            lines.append(f"\n**Imported by:** {', '.join(f'`{f}`' for f in self.importers[:5])}")
        if self.tests:
            lines.append(f"\n**Related tests:** {', '.join(f'`{f}`' for f in self.tests[:5])}")
        if not self.callers and not self.importers:
            lines.append("No known callers or importers — safe to modify.")
        else:
            lines.append("\nChange carefully — these depend on this code.")
        return "\n".join(lines)


class ImpactAnalyzer:
    """Analyze the blast radius of changing a function/class."""

    def __init__(self, workdir: str) -> None:
        self._workdir = workdir

    def analyze(self, file_path: str, symbol_name: str) -> ImpactReport:
        """Find everything that depends on this symbol."""
        report = ImpactReport(symbol=symbol_name, file_path=file_path)

        module_name = self._file_to_module(file_path)

        # Walk all .py files
        for root, dirs, files in os.walk(self._workdir):
            for fname in files:
                if not fname.endswith('.py'):
                    continue
                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, self._workdir)

                if rel_path == file_path:
                    continue  # skip the file itself

                try:
                    with open(fpath) as f:
                        source = f.read()
                    tree = ast.parse(source)
                except (SyntaxError, OSError):
                    continue

                # Check imports
                if self._imports_module(tree, module_name, symbol_name):
                    report.importers.append(rel_path)

                # Check call sites
                callers = self._find_callers(tree, symbol_name, rel_path)
                report.callers.extend(callers)

                # Check if it's a test file
                if fname.startswith('test_') or '/tests/' in rel_path:
                    if self._references_symbol(tree, symbol_name):
                        report.tests.append(rel_path)

        return report

    def _file_to_module(self, file_path: str) -> str:
        """Convert file path to module name: 'src/utils.py' -> 'src.utils'"""
        return file_path.replace('/', '.').replace('.py', '')

    def _imports_module(self, tree: ast.AST, module_name: str, symbol: str) -> bool:
        """Check if the AST imports from the target module."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and (node.module == module_name or node.module.endswith('.' + module_name.split('.')[-1])):
                    return True
                if node.names:
                    for alias in node.names:
                        if alias.name == symbol:
                            return True
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == module_name:
                        return True
        return False

    def _find_callers(self, tree: ast.AST, symbol: str, file_path: str) -> list[CallerInfo]:
        """Find function calls to the target symbol."""
        callers = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        if self._call_matches(child, symbol):
                            callers.append(CallerInfo(
                                file=file_path,
                                function=node.name,
                                line=child.lineno,
                            ))
                            break  # one per function
        return callers

    def _call_matches(self, call: ast.Call, symbol: str) -> bool:
        """Check if an ast.Call node calls the target symbol."""
        if isinstance(call.func, ast.Name) and call.func.id == symbol:
            return True
        if isinstance(call.func, ast.Attribute) and call.func.attr == symbol:
            return True
        return False

    def _references_symbol(self, tree: ast.AST, symbol: str) -> bool:
        """Check if the AST references the symbol anywhere."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == symbol:
                return True
            if isinstance(node, ast.Attribute) and node.attr == symbol:
                return True
        return False
