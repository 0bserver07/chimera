"""LSP manager -- manages multiple language server sessions."""
from __future__ import annotations

import shutil
from pathlib import Path

from chimera.lsp.base import Diagnostic
from chimera.lsp.servers import BUILTIN_SERVERS, LanguageServerConfig
from chimera.lsp.session import LSPSession


class LSPManager:
    """Manages language server lifecycles and routes requests by file extension.

    Example:
        ```python
        lsp = LSPManager.for_project("./myapp")
        lsp.start("./myapp")
        diagnostics = lsp.get_diagnostics("src/main.py")
        lsp.stop()
        ```
    """

    def __init__(self) -> None:
        self._configs: dict[str, LanguageServerConfig] = {}
        self._sessions: dict[str, LSPSession] = {}
        self._ext_map: dict[str, str] = {}  # extension -> config name

    @classmethod
    def for_project(cls, path: str) -> LSPManager:
        """Auto-detect languages and configure servers.

        Only adds servers whose commands are available on PATH.

        Args:
            path: Project root directory.

        Returns:
            Configured LSPManager (not yet started).
        """
        manager = cls()
        for config in BUILTIN_SERVERS:
            cmd = config.command[0]
            if shutil.which(cmd) is not None:
                manager.add(config.name, config.command, config.extensions)
        return manager

    def add(self, name: str, command: list[str] | str,
            extensions: tuple[str, ...] | None = None) -> None:
        """Register a language server.

        Args:
            name: Server name (e.g. "python").
            command: Command to start the server (string or list).
            extensions: File extensions this server handles.
        """
        if isinstance(command, str):
            command = command.split()
        config = LanguageServerConfig(name=name, command=command, extensions=extensions or ())
        self._configs[name] = config
        for ext in config.extensions:
            self._ext_map[ext] = name

    def start(self, workdir: str) -> None:
        """Start all configured language servers.

        Args:
            workdir: Project root path.
        """
        for name, config in self._configs.items():
            session = LSPSession(config.command)
            try:
                session.start(workdir)
                self._sessions[name] = session
            except (FileNotFoundError, OSError):
                pass  # Server not available, skip silently

    def stop(self) -> None:
        """Stop all running language servers."""
        for session in self._sessions.values():
            session.stop()
        self._sessions.clear()

    def get_session(self, file_path: str) -> LSPSession | None:
        """Get the LSP session for a file based on its extension.

        Args:
            file_path: File path to look up.

        Returns:
            LSPSession if a server handles this file type, None otherwise.
        """
        ext = Path(file_path).suffix
        name = self._ext_map.get(ext)
        if name is None:
            return None
        return self._sessions.get(name)

    def get_diagnostics(self, file_path: str, wait: float = 0.5) -> list[Diagnostic]:
        """Get diagnostics for a file.

        Opens the file in the language server (if not already open),
        then waits briefly for ``publishDiagnostics`` notifications
        before returning cached results.

        Args:
            file_path: Path to the file.
            wait: Seconds to wait for diagnostics to arrive (default 0.5).

        Returns:
            List of Diagnostic objects.
        """
        import time

        session = self.get_session(file_path)
        if session is None:
            return []
        path = Path(file_path)
        uri = path.resolve().as_uri()
        lang_id = self._detect_language(path.suffix)
        try:
            text = path.read_text()
        except (FileNotFoundError, OSError):
            return []
        session.did_open(uri, lang_id, text)
        # Give the server time to publish diagnostics
        time.sleep(wait)
        return session.get_diagnostics(uri)

    def _detect_language(self, ext: str) -> str:
        """Map file extension to LSP language ID."""
        mapping = {
            ".py": "python", ".ts": "typescript", ".tsx": "typescriptreact",
            ".js": "javascript", ".jsx": "javascriptreact",
            ".go": "go", ".rs": "rust",
        }
        return mapping.get(ext, "plaintext")

    def __enter__(self) -> LSPManager:
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
