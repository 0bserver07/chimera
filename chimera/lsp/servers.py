"""Built-in language server configurations."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LanguageServerConfig:
    """Configuration for a language server.

    Args:
        name: Language/server name (e.g. "python").
        command: Command to start the server (e.g. ["pyright-langserver", "--stdio"]).
        extensions: File extensions this server handles.
        initialization_options: Extra options passed during LSP initialize.
    """

    name: str
    command: list[str]
    extensions: tuple[str, ...]
    initialization_options: dict = field(default_factory=dict)


BUILTIN_SERVERS: list[LanguageServerConfig] = [
    LanguageServerConfig("python", ["pyright-langserver", "--stdio"], (".py",)),
    LanguageServerConfig("typescript", ["typescript-language-server", "--stdio"], (".ts", ".tsx", ".js", ".jsx")),
    LanguageServerConfig("go", ["gopls", "serve"], (".go",)),
    LanguageServerConfig("rust", ["rust-analyzer"], (".rs",)),
]
