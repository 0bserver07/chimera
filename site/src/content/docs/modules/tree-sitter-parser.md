---
title: "Tree-Sitter Parser"
description: "Tree-Sitter Parser"
---

`chimera.tools.parsers.tree_sitter` provides multi-language AST parsing via
tree-sitter. It extracts function, class, method, struct, trait, and enum
symbols from source files across 18+ languages. Tree-sitter is an optional
dependency -- the parser degrades gracefully when it is not installed.

## Key Classes

| Class / Function | Description |
|------------------|-------------|
| `TreeSitterParser` | Polyglot AST parser supporting 18+ languages |
| `tree_sitter_available()` | Check if tree-sitter is installed |
| `get_parser(extension)` | Get the best available parser (tree-sitter or regex fallback) |

## Supported Languages

Python, JavaScript, TypeScript, TSX, JSX, Go, Rust, Java, C, C++,
Ruby, PHP, Swift, Kotlin, Scala, C# -- determined by file extension.

## Quick Start

```python
from chimera.tools.parsers.tree_sitter import TreeSitterParser, get_parser

parser = TreeSitterParser()
if parser.can_parse(".py"):
    symbols = parser.parse("class Foo:\n    def bar(self): ...", extension=".py")
    for sym in symbols:
        print(f"{sym.kind}: {sym.name}")
        for child in sym.children:
            print(f"  {child.kind}: {child.name}")
```

## Automatic Fallback

`get_parser()` returns tree-sitter when available, otherwise falls back
to the built-in regex parsers for Python, TypeScript, Go, and Rust:

```python
parser = get_parser(".rs")  # TreeSitterParser or RustParser
if parser:
    symbols = parser.parse(source, ".rs")
```

## Extracted Symbol Kinds

| Kind | Node Types |
|------|------------|
| `function` | `function_definition`, `function_declaration`, `arrow_function` |
| `method` | `method_definition`, `method_declaration` |
| `class` | `class_definition`, `class_declaration` |
| `struct` | `struct_item`, `struct_declaration` |
| `interface` | `interface_declaration` |
| `trait` | `trait_item` |
| `enum` | `enum_item`, `enum_declaration` |
| `impl` | `impl_item` |

## Import Reference

```python
from chimera.tools.parsers.tree_sitter import TreeSitterParser, tree_sitter_available, get_parser
from chimera.tools.parsers.base import LanguageParser, Symbol
```

## Related

- [Definition Lookup](/definition-lookup/) -- find symbol definitions across a codebase
- [Agent Tools](/agent-tools/) -- built-in tool catalogue

<!-- W17-3 SOURCE FOOTER -->

## Source

`chimera/tools/parsers/tree_sitter.py` -- verified against current source at wave-17.
