"""Auto-documentation generator from code analysis."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.config.ignore import is_not_source


@dataclass
class DocSection:
    """A section of generated documentation."""
    title: str
    content: str
    subsections: list[DocSection] = field(default_factory=list)
    source_file: str = ""

    def to_markdown(self, level: int = 1) -> str:
        prefix = "#" * level
        parts = [f"{prefix} {self.title}\n"]
        if self.content:
            parts.append(self.content + "\n")
        for sub in self.subsections:
            parts.append(sub.to_markdown(level + 1))
        return "\n".join(parts)


class DocGenerator:
    """Generates documentation from source code structure.

    Uses RepoMap parsers to extract symbols and generates
    markdown documentation for each module.
    """

    def __init__(self, root: str, output_dir: str = "docs/api") -> None:
        self._root = Path(root)
        self._output_dir = Path(output_dir)
        self._sections: list[DocSection] = []

    def scan(self, extensions: tuple[str, ...] = (".py",)) -> list[DocSection]:
        """Scan source files and generate documentation sections.

        Returns list of DocSection for each scanned file.
        """
        self._sections = []

        for filepath in sorted(self._root.rglob("*")):
            if filepath.suffix not in extensions:
                continue
            if any(part.startswith(".") or is_not_source(part)
                   for part in filepath.parts):
                continue

            rel = str(filepath.relative_to(self._root))
            try:
                source = filepath.read_text(errors="replace")
                section = self._document_file(rel, source, filepath.suffix)
                if section:
                    self._sections.append(section)
            except Exception:
                continue

        return self._sections

    def _document_file(self, filepath: str, source: str, ext: str) -> DocSection | None:
        """Generate documentation for a single file."""
        if ext == ".py":
            return self._document_python(filepath, source)
        return None

    def _document_python(self, filepath: str, source: str) -> DocSection | None:
        """Generate docs from a Python file."""
        import ast
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None

        module_doc = ast.get_docstring(tree) or ""
        subsections: list[DocSection] = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                class_doc = ast.get_docstring(node) or ""
                methods: list[DocSection] = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_doc = ast.get_docstring(item) or ""
                        sig = self._get_function_sig(item)
                        methods.append(DocSection(
                            title=f"`{sig}`",
                            content=method_doc,
                            source_file=filepath,
                        ))
                subsections.append(DocSection(
                    title=f"class `{node.name}`",
                    content=class_doc,
                    subsections=methods,
                    source_file=filepath,
                ))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_doc = ast.get_docstring(node) or ""
                sig = self._get_function_sig(node)
                subsections.append(DocSection(
                    title=f"`{sig}`",
                    content=func_doc,
                    source_file=filepath,
                ))

        if not subsections and not module_doc:
            return None

        module_name = filepath.replace("/", ".").replace("\\", ".")
        if module_name.endswith(".py"):
            module_name = module_name[:-3]

        return DocSection(
            title=module_name,
            content=module_doc,
            subsections=subsections,
            source_file=filepath,
        )

    def _get_function_sig(self, node: Any) -> str:
        """Extract function signature string.

        Includes positional args, default values, *args, keyword-only args,
        **kwargs, and return-type annotations. Type annotations on parameters
        are included when present. ``self`` and ``cls`` are stripped.
        """
        import ast

        def ann(a: ast.expr | None) -> str:
            return ast.unparse(a) if a is not None else ""

        parts: list[str] = []
        pos_args = list(node.args.args)
        # Normal positional args with default-value alignment. Defaults align
        # to the TAIL of node.args.args (the usual AST convention).
        defaults = list(node.args.defaults)
        default_offset = len(pos_args) - len(defaults)

        for i, arg in enumerate(pos_args):
            if arg.arg in ("self", "cls"):
                continue
            piece = arg.arg
            if arg.annotation is not None:
                piece += f": {ann(arg.annotation)}"
            if i >= default_offset:
                default_node = defaults[i - default_offset]
                piece += f" = {ast.unparse(default_node)}"
            parts.append(piece)

        if node.args.vararg is not None:
            vararg = node.args.vararg
            piece = f"*{vararg.arg}"
            if vararg.annotation is not None:
                piece += f": {ann(vararg.annotation)}"
            parts.append(piece)
        elif node.args.kwonlyargs:
            # PEP 3102: bare ``*`` separator before keyword-only args
            parts.append("*")

        kw_defaults = list(node.args.kw_defaults)
        for i, arg in enumerate(node.args.kwonlyargs):
            piece = arg.arg
            if arg.annotation is not None:
                piece += f": {ann(arg.annotation)}"
            # kw_defaults positions align 1:1 with kwonlyargs; None = no default.
            if i < len(kw_defaults) and kw_defaults[i] is not None:
                piece += f" = {ast.unparse(kw_defaults[i])}"
            parts.append(piece)

        if node.args.kwarg is not None:
            kwarg = node.args.kwarg
            piece = f"**{kwarg.arg}"
            if kwarg.annotation is not None:
                piece += f": {ann(kwarg.annotation)}"
            parts.append(piece)

        prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        ret = f" -> {ann(node.returns)}" if node.returns is not None else ""
        return f"{prefix}{node.name}({', '.join(parts)}){ret}"

    def write(self, sections: list[DocSection] | None = None) -> list[str]:
        """Write documentation sections to files.

        Returns list of written file paths.
        """
        sections = sections or self._sections
        self._output_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []

        for section in sections:
            filename = section.source_file.replace("/", "_").replace("\\", "_")
            if filename.endswith(".py"):
                filename = filename[:-3] + ".md"
            else:
                filename += ".md"

            outpath = self._output_dir / filename
            outpath.write_text(section.to_markdown())
            written.append(str(outpath))

        # Write index
        index_path = self._output_dir / "index.md"
        index_lines = ["# API Reference\n"]
        for section in sections:
            filename = section.source_file.replace("/", "_").replace("\\", "_")
            if filename.endswith(".py"):
                filename = filename[:-3] + ".md"
            else:
                filename += ".md"
            index_lines.append(f"- [{section.title}]({filename})")
        index_path.write_text("\n".join(index_lines) + "\n")
        written.append(str(index_path))

        return written

    @property
    def sections(self) -> list[DocSection]:
        return list(self._sections)
