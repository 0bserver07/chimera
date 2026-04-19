"""Multi-language AST parser via tree-sitter.

Tree-sitter is an OPTIONAL dependency. This module gracefully degrades
when tree-sitter is not installed — ``tree_sitter_available()`` returns
``False`` and ``TreeSitterParser.parse()`` returns an empty list.
"""
from __future__ import annotations

from chimera.tools.parsers.base import LanguageParser, Symbol

# tree-sitter language map — maps file extensions to tree-sitter language names
_TS_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".cs": "csharp",
}


def tree_sitter_available() -> bool:
    """Check if tree-sitter is installed."""
    try:
        import tree_sitter  # type: ignore[import-not-found]  # noqa: F401  # optional dep

        return True
    except ImportError:
        return False


class TreeSitterParser(LanguageParser):
    """Parse source files using tree-sitter for accurate AST extraction.

    Falls back gracefully when tree-sitter is not installed.
    Supports 18+ languages via tree-sitter grammars.

    Unlike the regex-based parsers which each handle a single language,
    ``TreeSitterParser`` is a polyglot parser — the target language is
    determined by the ``extension`` argument passed to ``parse()``.
    """

    extensions: tuple[str, ...] = tuple(_TS_LANGUAGES.keys())

    def __init__(self) -> None:
        self._parsers: dict[str, object] = {}  # language name -> Parser
        self._available = tree_sitter_available()

    def can_parse(self, extension: str) -> bool:
        """Check if this extension is supported and tree-sitter is available.

        Args:
            extension: File extension including the dot (e.g. ``".py"``).

        Returns:
            ``True`` when tree-sitter is installed and the extension is
            in the language map.
        """
        return self._available and extension in _TS_LANGUAGES

    def parse(self, source: str, extension: str = ".py") -> list[Symbol]:
        """Parse source code and extract symbols.

        If tree-sitter is not available, returns an empty list
        (callers should fall back to regex parsers).

        Args:
            source: Source code text.
            extension: File extension used to select the tree-sitter
                language grammar (e.g. ``".py"``, ``".rs"``).

        Returns:
            List of top-level symbols with nested children.
        """
        if not self._available:
            return []

        language_name = _TS_LANGUAGES.get(extension)
        if not language_name:
            return []

        try:
            return self._parse_with_tree_sitter(source, language_name)
        except Exception:
            return []  # graceful fallback on any tree-sitter error

    def _parse_with_tree_sitter(
        self, source: str, language: str
    ) -> list[Symbol]:
        """Actual tree-sitter parsing.

        Args:
            source: Source code text.
            language: Tree-sitter language name (e.g. ``"python"``).

        Returns:
            Extracted symbols.
        """
        try:
            import tree_sitter  # type: ignore[import-not-found]  # noqa: F401  # optional dep
        except ImportError:
            return []

        # Try to get or create parser for this language
        parser = self._get_parser(language)
        if parser is None:
            return []

        tree = parser.parse(source.encode())  # type: ignore[union-attr]
        return self._extract_symbols(tree.root_node, source)

    def _get_parser(self, language: str) -> object | None:
        """Get or create a tree-sitter parser for the given language.

        Tries two approaches in order:

        1. ``tree_sitter_languages`` package (``get_parser``).
        2. Per-language ``tree_sitter_<lang>`` packages with the modern
           ``tree_sitter.Language`` / ``tree_sitter.Parser`` API.

        Args:
            language: Tree-sitter language name.

        Returns:
            A tree-sitter ``Parser`` instance, or ``None`` on failure.
        """
        if language in self._parsers:
            return self._parsers[language]

        try:
            import tree_sitter  # type: ignore[import-not-found]  # optional dep

            # Try the tree_sitter_languages convenience package first
            try:
                from tree_sitter_languages import get_parser  # type: ignore[import-not-found]  # optional dep

                parser = get_parser(language)
                self._parsers[language] = parser
                return parser
            except ImportError:
                pass

            # Fallback: per-language tree_sitter_<lang> packages
            try:
                import importlib

                lang_mod = importlib.import_module(f"tree_sitter_{language}")
                lang = tree_sitter.Language(lang_mod.language())
                parser = tree_sitter.Parser(lang)
                self._parsers[language] = parser
                return parser
            except (ImportError, AttributeError):
                pass

            return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # AST symbol extraction
    # ------------------------------------------------------------------

    # Node types that represent definitions across languages
    _DEFINITION_TYPES: frozenset[str] = frozenset(
        {
            # Functions
            "function_definition",
            "function_declaration",
            "method_definition",
            "method_declaration",
            "arrow_function",
            # Classes / types
            "class_definition",
            "class_declaration",
            "struct_item",
            "struct_declaration",
            "interface_declaration",
            "trait_item",
            "enum_item",
            "enum_declaration",
            # Impl blocks (Rust)
            "impl_item",
        }
    )

    _KIND_MAP: dict[str, str] = {
        "function_definition": "function",
        "function_declaration": "function",
        "method_definition": "method",
        "method_declaration": "method",
        "arrow_function": "function",
        "class_definition": "class",
        "class_declaration": "class",
        "struct_item": "struct",
        "struct_declaration": "struct",
        "interface_declaration": "interface",
        "trait_item": "trait",
        "enum_item": "enum",
        "enum_declaration": "enum",
        "impl_item": "impl",
    }

    # Node types that act as transparent containers (we recurse into them
    # when walking top-level definitions, but don't treat them as definitions
    # themselves).
    _CONTAINER_TYPES: frozenset[str] = frozenset(
        {
            "block",
            "class_body",
            "declaration_list",
            "field_declaration_list",
            "enum_body",
            "module",
            "program",
            "source_file",
        }
    )

    def _extract_symbols(self, node: object, source: str) -> list[Symbol]:
        """Extract function/class/method symbols from an AST node.

        Args:
            node: A tree-sitter ``Node``.
            source: The original source text (used to slice identifier names).

        Returns:
            List of extracted ``Symbol`` objects.
        """
        symbols: list[Symbol] = []

        for child in self._walk(node):
            if child.type in self._DEFINITION_TYPES:
                name = self._get_name(child, source)
                if name:
                    kind = self._KIND_MAP.get(child.type, "unknown")
                    children: list[Symbol] = []
                    # For classes/structs/impls/traits, extract nested methods
                    if kind in ("class", "struct", "impl", "trait"):
                        children = self._extract_symbols(child, source)
                    symbols.append(
                        Symbol(name=name, kind=kind, children=children)
                    )

        return symbols

    def _walk(self, node: object) -> list[object]:
        """Walk direct children, recursing into container nodes.

        This yields children breadth-first through transparent containers
        (blocks, class bodies, etc.) but does NOT recurse into nested
        definition nodes — those are handled by ``_extract_symbols``
        recursion.

        Args:
            node: A tree-sitter ``Node``.

        Returns:
            List of descendant nodes to inspect.
        """
        result: list[object] = []
        for child in node.children:  # type: ignore[attr-defined]
            result.append(child)
            if child.type in self._CONTAINER_TYPES:  # type: ignore[attr-defined]
                result.extend(self._walk(child))
        return result

    def _get_name(self, node: object, source: str) -> str | None:
        """Extract the identifier name from a definition node.

        Scans immediate children for ``identifier``, ``name``,
        ``type_identifier``, or ``property_identifier`` nodes.

        Args:
            node: A tree-sitter definition ``Node``.
            source: The original source text.

        Returns:
            The extracted name string, or ``None`` if not found.
        """
        for child in node.children:  # type: ignore[attr-defined]
            if child.type in (  # type: ignore[attr-defined]
                "identifier",
                "name",
                "type_identifier",
                "property_identifier",
            ):
                return source[child.start_byte : child.end_byte]  # type: ignore[attr-defined]
        return None


def get_parser(extension: str) -> LanguageParser | None:
    """Get the best available parser for a file extension.

    Prefers tree-sitter if available, falls back to the built-in regex
    parsers.

    Args:
        extension: File extension including the dot (e.g. ``".py"``).

    Returns:
        A ``LanguageParser`` instance, or ``None`` for unsupported
        extensions.
    """
    if tree_sitter_available():
        ts = TreeSitterParser()
        if ts.can_parse(extension):
            return ts

    # Fall back to existing regex parsers
    from chimera.tools.parsers.go import GoParser
    from chimera.tools.parsers.python_parser import PythonParser
    from chimera.tools.parsers.rust import RustParser
    from chimera.tools.parsers.typescript import TypeScriptParser

    fallbacks: dict[str, LanguageParser] = {
        ".py": PythonParser(),
        ".ts": TypeScriptParser(),
        ".tsx": TypeScriptParser(),
        ".js": TypeScriptParser(),
        ".jsx": TypeScriptParser(),
        ".go": GoParser(),
        ".rs": RustParser(),
    }
    return fallbacks.get(extension)
