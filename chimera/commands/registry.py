"""Central registry for all commands (builtins, skills, plugins)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from chimera.commands.types import Command, PromptCommand
from chimera.config.paths import STATE_DIRNAME, store_path

if TYPE_CHECKING:
    pass


class CommandRegistry:
    """Stores commands and resolves them by name or alias."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._alias_map: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, command: Command) -> None:
        """Register a command by its canonical name and any aliases."""
        self._commands[command.name] = command
        for alias in command.aliases:
            self._alias_map[alias] = command.name

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def find(self, name: str) -> Command | None:
        """Look up a command by name or alias.  Returns ``None`` if not found."""
        if name in self._commands:
            return self._commands[name]
        canonical = self._alias_map.get(name)
        if canonical is not None:
            return self._commands.get(canonical)
        return None

    def list_commands(self, include_hidden: bool = False) -> list[Command]:
        """Return unique, sorted, enabled commands.

        Hidden commands are excluded unless *include_hidden* is ``True``.
        """
        seen: set[str] = set()
        result: list[Command] = []
        for cmd in self._commands.values():
            if cmd.name in seen:
                continue
            if not cmd.is_enabled():
                continue
            if cmd.is_hidden and not include_hidden:
                continue
            seen.add(cmd.name)
            result.append(cmd)
        result.sort(key=lambda c: c.name)
        return result

    def get_model_invocable(self) -> list[PromptCommand]:
        """Return prompt commands the model may invoke autonomously.

        Filters out builtins and commands with ``disable_model_invocation``.
        """
        return [
            cmd
            for cmd in self._commands.values()
            if isinstance(cmd, PromptCommand)
            and cmd.source != "builtin"
            and not cmd.disable_model_invocation
        ]

    # ------------------------------------------------------------------
    # Bulk loading
    # ------------------------------------------------------------------

    async def load_all(
        self,
        project_dir: Path,
        user_dir: Path | None = None,
    ) -> None:
        """Aggregate builtin commands, skills, and plugins into the registry."""
        # Builtins
        from chimera.commands.builtins import get_builtin_commands

        for cmd in get_builtin_commands():
            self.register(cmd)

        # Skills
        from chimera.skills.loader import SkillLoader

        search_paths: list[Path] = [store_path("project-skills", project_dir)]
        if user_dir is not None:
            # NOTE (M1 storage sweep): every caller passes ``~/.chimera``
            # here, so this joins a *second* ``.chimera`` and user skills
            # have never actually loaded. Preserved verbatim — fixing it
            # would change skill resolution, which a path-plumbing change
            # has no business doing silently.
            search_paths.append(Path(user_dir) / STATE_DIRNAME / "skills")

        loader = SkillLoader(search_paths)
        definitions = await loader.load_all()
        for defn in definitions:
            self.register(defn.to_command())

        # Bundled skills
        from chimera.skills.bundled import get_bundled_skills

        for bundled_cmd in get_bundled_skills():
            self.register(bundled_cmd)
