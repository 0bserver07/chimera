"""Import graph extraction and related-file discovery."""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


@dataclass
class ImportEdge:
    """A single import relationship."""
    source: str      # file that imports
    target: str      # module/file being imported
    names: list[str] = field(default_factory=list)  # specific names imported


class ImportGraph:
    """Builds and queries a file-level import dependency graph.

    Extracts imports from Python, TypeScript/JavaScript, Go, and Rust files
    using regex-based parsing (no external dependencies).
    """

    def __init__(self) -> None:
        self._edges: list[ImportEdge] = []
        self._imports_by_file: dict[str, list[ImportEdge]] = defaultdict(list)
        self._imported_by: dict[str, list[str]] = defaultdict(list)  # module -> files that import it

    def build(self, root: str, extensions: tuple[str, ...] = (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs")) -> None:
        """Scan a directory tree and extract all imports."""
        root_path = Path(root)
        for filepath in root_path.rglob("*"):
            if filepath.suffix not in extensions:
                continue
            if any(part.startswith(".") or part in ("node_modules", "__pycache__", "venv", ".git")
                   for part in filepath.parts):
                continue
            rel = str(filepath.relative_to(root_path))
            try:
                source = filepath.read_text(errors="replace")
                edges = self._extract_imports(rel, source, filepath.suffix)
                for edge in edges:
                    self._edges.append(edge)
                    self._imports_by_file[rel].append(edge)
                    self._imported_by[edge.target].append(rel)
            except Exception:
                continue

    def _extract_imports(self, filepath: str, source: str, ext: str) -> list[ImportEdge]:
        """Extract imports based on file extension."""
        if ext == ".py":
            return self._extract_python_imports(filepath, source)
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            return self._extract_ts_imports(filepath, source)
        elif ext == ".go":
            return self._extract_go_imports(filepath, source)
        elif ext == ".rs":
            return self._extract_rust_imports(filepath, source)
        return []

    def _extract_python_imports(self, filepath: str, source: str) -> list[ImportEdge]:
        edges: list[ImportEdge] = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return edges
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    edges.append(ImportEdge(source=filepath, target=alias.name, names=[alias.name]))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [a.name for a in node.names]
                edges.append(ImportEdge(source=filepath, target=module, names=names))
        return edges

    def _extract_ts_imports(self, filepath: str, source: str) -> list[ImportEdge]:
        edges = []
        # import X from 'module' or import { X } from 'module'
        pattern = re.compile(r'''import\s+(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)?\s*(?:,\s*\{[^}]*\})?\s*from\s+['"]([^'"]+)['"]''')
        for match in pattern.finditer(source):
            edges.append(ImportEdge(source=filepath, target=match.group(1)))
        # require('module')
        pattern2 = re.compile(r'''require\s*\(\s*['"]([^'"]+)['"]\s*\)''')
        for match in pattern2.finditer(source):
            edges.append(ImportEdge(source=filepath, target=match.group(1)))
        return edges

    def _extract_go_imports(self, filepath: str, source: str) -> list[ImportEdge]:
        edges = []
        # Single import: import "fmt"
        for match in re.finditer(r'import\s+"([^"]+)"', source):
            edges.append(ImportEdge(source=filepath, target=match.group(1)))
        # Multi import block
        for block in re.finditer(r'import\s*\((.*?)\)', source, re.DOTALL):
            for match in re.finditer(r'"([^"]+)"', block.group(1)):
                edges.append(ImportEdge(source=filepath, target=match.group(1)))
        return edges

    def _extract_rust_imports(self, filepath: str, source: str) -> list[ImportEdge]:
        edges = []
        for match in re.finditer(r'use\s+([\w:]+)', source):
            edges.append(ImportEdge(source=filepath, target=match.group(1)))
        for match in re.finditer(r'extern\s+crate\s+(\w+)', source):
            edges.append(ImportEdge(source=filepath, target=match.group(1)))
        return edges

    # -- Query API --

    def imports_of(self, filepath: str) -> list[str]:
        """Return modules/files imported by the given file."""
        return [e.target for e in self._imports_by_file.get(filepath, [])]

    def importers_of(self, module: str) -> list[str]:
        """Return files that import the given module."""
        return list(self._imported_by.get(module, []))

    def related_files(self, filepath: str, max_results: int = 10) -> list[str]:
        """Find files related to the given file (imports + importers).

        Returns a ranked list of related file paths.
        """
        related: dict[str, int] = defaultdict(int)

        # Files we import from (direct dependencies)
        for edge in self._imports_by_file.get(filepath, []):
            # Try to resolve module to file
            for candidate in self._imports_by_file:
                if candidate == filepath:
                    continue
                # Check if this file's module name matches our import target
                module_path = candidate.replace("/", ".").replace("\\", ".")
                if module_path.endswith(".py"):
                    module_path = module_path[:-3]
                if edge.target in module_path or module_path.endswith(edge.target.replace(".", "/")):
                    related[candidate] += 3  # Direct dependency is high weight

        # Files that import us
        file_module = filepath.replace("/", ".").replace("\\", ".")
        if file_module.endswith(".py"):
            file_module = file_module[:-3]
        for other_file, edges in self._imports_by_file.items():
            if other_file == filepath:
                continue
            for edge in edges:
                if edge.target in file_module or file_module.endswith(edge.target.replace(".", "/")):
                    related[other_file] += 2  # Reverse dependency

        # Sort by score descending
        sorted_files = sorted(related.items(), key=lambda x: -x[1])
        return [f for f, _ in sorted_files[:max_results]]

    @property
    def all_edges(self) -> list[ImportEdge]:
        return list(self._edges)

    @property
    def files(self) -> list[str]:
        return list(self._imports_by_file.keys())


class ImportGraphTool(BaseTool):
    """Expose :class:`ImportGraph` as an agent-callable tool.

    Actions:
        imports_of: List modules imported by the given file.
        importers_of: List files that import the given module.
        related: List files related to the given file (imports + importers).
        summary: Short summary of the graph (file count, edge count).
    """

    name = "import_graph"
    description = (
        "Analyze module import dependencies in the workspace. "
        "Actions: 'imports_of' (what a file imports), 'importers_of' "
        "(who imports a module), 'related' (both directions, ranked), "
        "'summary' (counts)."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["imports_of", "importers_of", "related", "summary"],
                "description": "Which query to perform.",
            },
            "target": {
                "type": "string",
                "description": (
                    "File path (for imports_of / related) or module name "
                    "(for importers_of). Required for all actions except 'summary'."
                ),
            },
            "root": {
                "type": "string",
                "description": "Workspace root to scan. Defaults to environment workdir.",
            },
            "max_results": {
                "type": "integer",
                "description": "Cap on results returned (default 20).",
                "default": 20,
            },
        },
        "required": ["action"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        action = args.get("action")
        if action not in {"imports_of", "importers_of", "related", "summary"}:
            return ToolResult(output="", error=f"Unknown action: {action!r}")

        root = args.get("root")
        if root is None:
            workdir = getattr(env, "workdir", None) if env is not None else None
            if workdir is not None:
                root = str(workdir)
            else:
                root = "."
        root_path = Path(root)
        if not root_path.is_dir():
            return ToolResult(output="", error=f"Not a directory: {root}")

        graph = ImportGraph()
        graph.build(str(root_path))
        max_results = args.get("max_results", 20)

        if action == "summary":
            return ToolResult(
                output=(
                    f"{len(graph.files)} files scanned, "
                    f"{len(graph.all_edges)} import edges"
                ),
                metadata={
                    "files": len(graph.files),
                    "edges": len(graph.all_edges),
                },
            )

        target = args.get("target")
        if not target:
            return ToolResult(
                output="",
                error=f"'target' is required for action {action!r}",
            )

        if action == "imports_of":
            results = graph.imports_of(target)[:max_results]
        elif action == "importers_of":
            results = graph.importers_of(target)[:max_results]
        else:  # related
            results = graph.related_files(target, max_results=max_results)

        if not results:
            return ToolResult(output=f"No results for {action} {target!r}.")
        return ToolResult(
            output="\n".join(results),
            metadata={"count": len(results)},
        )
