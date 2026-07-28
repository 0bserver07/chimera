"""Central registry for all commands (builtins, skills, plugins)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from chimera.commands.types import Command, PromptCommand
from chimera.config.paths import store_path

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
        """Aggregate builtin commands, skills, and plugins into the registry.

        Args:
            project_dir: Project root. Project skills load from its
                ``project-skills`` store (``<project_dir>/.chimera/skills``).
            user_dir: The **user-scope Chimera state directory** itself —
                i.e. what :func:`chimera.config.paths.chimera_home` returns
                (``~/.chimera``), not its parent. User skills load from
                ``<user_dir>/skills``. When ``None``, the registry's
                ``skills`` store is used, so the default agrees with
                :func:`chimera.skills.discovery.default_search_paths` and
                honors ``$CHIMERA_HOME`` / ``[storage] root``.
        """
        # Builtins
        from chimera.commands.builtins import get_builtin_commands

        for cmd in get_builtin_commands():
            self.register(cmd)

        # Skills
        from chimera.skills.loader import SkillLoader

        # NOTE: two skill-discovery paths read this same directory and must
        # not drift. (1) here: SkillLoader globs flat ``*.md`` files and turns
        # each into an invocable slash command; (2)
        # ``chimera.skills.discovery.discover_all_skills`` rglobs nested
        # ``SKILL.md`` files and turns each into a system-prompt bullet. They
        # read disjoint file layouts out of one directory on purpose, so the
        # *directory* is the shared contract: both resolve it through the
        # ``skills`` / ``project-skills`` stores in ``chimera/config/paths.py``
        # and neither may hand-build a ``~/.chimera`` path. This used to join a
        # second ``.chimera`` onto ``user_dir``, which would have sent any
        # caller passing ``chimera_home()`` to ``~/.chimera/.chimera/skills``.
        user_skills = Path(user_dir) / "skills" if user_dir is not None else store_path("skills")
        search_paths: list[Path] = [
            store_path("project-skills", project_dir),
            user_skills,
        ]

        loader = SkillLoader(search_paths)
        definitions = await loader.load_all()
        for defn in definitions:
            self.register(defn.to_command())

        # Bundled skills
        from chimera.skills.bundled import get_bundled_skills

        for bundled_cmd in get_bundled_skills():
            self.register(bundled_cmd)
