"""Auto-formatter detection and execution.

Detects project formatters from config files and package dependencies,
then runs them after file edits.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FormatterConfig:
    name: str
    extensions: list[str]
    detect_files: list[str]      # Config files that indicate this formatter
    detect_deps: list[str]       # Package deps that indicate it
    command: list[str]           # {file} is replaced with actual path


FORMATTERS = [
    FormatterConfig(
        "prettier",
        [".js", ".ts", ".tsx", ".jsx", ".json", ".css", ".md"],
        [".prettierrc", ".prettierrc.json", "prettier.config.js"],
        ["prettier"],
        ["npx", "prettier", "--write", "{file}"],
    ),
    FormatterConfig(
        "biome",
        [".js", ".ts", ".tsx", ".jsx", ".json"],
        ["biome.json"],
        ["@biomejs/biome"],
        ["npx", "@biomejs/biome", "format", "--write", "{file}"],
    ),
    FormatterConfig(
        "ruff",
        [".py"],
        ["ruff.toml", ".ruff.toml"],
        ["ruff"],
        ["ruff", "format", "{file}"],
    ),
    FormatterConfig(
        "black",
        [".py"],
        [],
        ["black"],
        ["black", "{file}"],
    ),
    FormatterConfig(
        "gofmt",
        [".go"],
        [],
        [],
        ["gofmt", "-w", "{file}"],
    ),
    FormatterConfig(
        "rustfmt",
        [".rs"],
        ["rustfmt.toml"],
        [],
        ["rustfmt", "{file}"],
    ),
    FormatterConfig(
        "clang-format",
        [".c", ".cpp", ".h", ".hpp"],
        [".clang-format"],
        [],
        ["clang-format", "-i", "{file}"],
    ),
    FormatterConfig(
        "shfmt",
        [".sh", ".bash"],
        [],
        [],
        ["shfmt", "-w", "{file}"],
    ),
]


class AutoFormatter:
    """Detect and run project formatters after file edits."""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir
        self._detected: dict[str, FormatterConfig] = {}
        self._cache_valid = False

    def detect_formatters(self) -> dict[str, FormatterConfig]:
        """Detect available formatters from project config.

        Results are cached; subsequent calls return the same dict object.
        """
        if self._cache_valid:
            return self._detected

        # 1. Check for config files
        for fmt in FORMATTERS:
            for cfg in fmt.detect_files:
                if (self._project_dir / cfg).exists():
                    for ext in fmt.extensions:
                        self._detected.setdefault(ext, fmt)
                    break

        # 2. Check package.json deps
        pkg = self._project_dir / "package.json"
        if pkg.exists():
            data = json.loads(pkg.read_text())
            all_deps = {
                **data.get("dependencies", {}),
                **data.get("devDependencies", {}),
            }
            for fmt in FORMATTERS:
                if any(d in all_deps for d in fmt.detect_deps):
                    for ext in fmt.extensions:
                        self._detected.setdefault(ext, fmt)

        # 3. Always-available formatters (no config needed, no deps needed)
        for fmt in FORMATTERS:
            if not fmt.detect_files and not fmt.detect_deps:
                for ext in fmt.extensions:
                    self._detected.setdefault(ext, fmt)

        self._cache_valid = True
        return self._detected

    async def format_file(self, path: str) -> tuple[bool, str]:
        """Format a file using the detected formatter.

        Returns:
            ``(success, output)`` tuple.  When the formatter binary is not
            installed, returns ``(True, "")`` to skip gracefully.
        """
        ext = Path(path).suffix
        fmt = self.detect_formatters().get(ext)
        if not fmt:
            return True, ""

        cmd = [c.replace("{file}", path) for c in fmt.command]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self._project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return proc.returncode == 0, (stdout or stderr or b"").decode()
        except FileNotFoundError:
            return True, ""  # Formatter not installed — skip gracefully
