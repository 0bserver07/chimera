"""Tests for chimera.commands.registry — Phase 7."""
from __future__ import annotations

import asyncio
from pathlib import Path

from chimera.commands.registry import CommandRegistry
from chimera.commands.types import LocalCommand, PromptCommand


def _write_skill(directory: Path, name: str, body: str = "Do the thing.") -> None:
    """Drop a minimal ``<name>.md`` skill file into *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: skill {name}\n---\n{body}\n"
    )


class TestCommandRegistry:
    """CommandRegistry stores, finds, lists, and filters commands."""

    def test_register_and_find(self):
        reg = CommandRegistry()
        cmd = LocalCommand(name="help", description="Show help", handler=lambda args: "help text")
        reg.register(cmd)
        assert reg.find("help") is cmd

    def test_find_by_alias(self):
        reg = CommandRegistry()
        cmd = LocalCommand(
            name="help",
            description="Show help",
            aliases=["h", "?"],
            handler=lambda args: "help text",
        )
        reg.register(cmd)
        assert reg.find("h") is cmd
        assert reg.find("?") is cmd
        assert reg.find("nonexistent") is None

    def test_list_excludes_hidden(self):
        reg = CommandRegistry()
        reg.register(LocalCommand(name="visible", description="v", handler=lambda a: ""))
        reg.register(LocalCommand(name="secret", description="s", handler=lambda a: "", is_hidden=True))
        visible = reg.list_commands(include_hidden=False)
        assert len(visible) == 1
        assert visible[0].name == "visible"
        all_cmds = reg.list_commands(include_hidden=True)
        assert len(all_cmds) == 2

    def test_list_excludes_disabled(self):
        reg = CommandRegistry()
        reg.register(LocalCommand(name="on", description="on", handler=lambda a: "", is_enabled=lambda: True))
        reg.register(LocalCommand(name="off", description="off", handler=lambda a: "", is_enabled=lambda: False))
        listed = reg.list_commands()
        assert len(listed) == 1
        assert listed[0].name == "on"

    def test_model_invocable_filters_builtin(self):
        reg = CommandRegistry()
        reg.register(PromptCommand(
            name="builtin-cmd",
            description="builtin",
            source="builtin",
            get_prompt=lambda: "hi",
        ))
        reg.register(PromptCommand(
            name="skill-cmd",
            description="skill",
            source="skill",
            get_prompt=lambda: "hi",
        ))
        reg.register(PromptCommand(
            name="disabled-cmd",
            description="disabled",
            source="skill",
            disable_model_invocation=True,
            get_prompt=lambda: "hi",
        ))
        invocable = reg.get_model_invocable()
        names = [c.name for c in invocable]
        assert "skill-cmd" in names
        assert "builtin-cmd" not in names
        assert "disabled-cmd" not in names


class TestLoadAllSkillPaths:
    """``load_all`` resolves the same skill directories production discovery does.

    ``user_dir`` is the user-scope Chimera state directory itself
    (``chimera_home()`` / ``~/.chimera``). It used to have a second
    ``.chimera`` joined onto it, so a caller following the house convention
    landed on ``~/.chimera/.chimera/skills`` and no user skill ever loaded.
    """

    def test_user_scope_skill_is_discovered(self, tmp_path, monkeypatch) -> None:
        """A skill in ``<user_dir>/skills`` becomes a registered command."""
        monkeypatch.delenv("CHIMERA_HOME", raising=False)
        project = tmp_path / "proj"
        user_home = tmp_path / "home" / ".chimera"
        _write_skill(project / ".chimera" / "skills", "proj-skill")
        _write_skill(user_home / "skills", "user-skill")

        reg = CommandRegistry()
        asyncio.run(reg.load_all(project_dir=project, user_dir=user_home))

        assert reg.find("user-skill") is not None, (
            "user-scope skill did not load — user_dir must be the "
            ".chimera directory itself, with no second .chimera joined on"
        )
        assert reg.find("proj-skill") is not None

    def test_no_second_state_dirname_is_joined(self, tmp_path, monkeypatch) -> None:
        """The old ``<user_dir>/.chimera/skills`` spelling must NOT be read."""
        monkeypatch.delenv("CHIMERA_HOME", raising=False)
        project = tmp_path / "proj"
        user_home = tmp_path / "home" / ".chimera"
        _write_skill(user_home / ".chimera" / "skills", "double-joined")

        reg = CommandRegistry()
        asyncio.run(reg.load_all(project_dir=project, user_dir=user_home))

        assert reg.find("double-joined") is None

    def test_user_dir_none_falls_back_to_the_skills_store(
        self, tmp_path, monkeypatch
    ) -> None:
        """Omitting ``user_dir`` uses ``store_path("skills")``, so the default
        agrees with ``discovery.default_search_paths`` and honors
        ``$CHIMERA_HOME``."""
        home = tmp_path / "chimera-home"
        monkeypatch.setenv("CHIMERA_HOME", str(home))
        _write_skill(home / "skills", "env-home-skill")

        reg = CommandRegistry()
        asyncio.run(reg.load_all(project_dir=tmp_path / "proj"))

        assert reg.find("env-home-skill") is not None

    def test_user_skill_overrides_project_skill_of_the_same_name(
        self, tmp_path, monkeypatch
    ) -> None:
        """User scope is searched last, so it wins a name collision."""
        monkeypatch.delenv("CHIMERA_HOME", raising=False)
        project = tmp_path / "proj"
        user_home = tmp_path / "home" / ".chimera"
        _write_skill(project / ".chimera" / "skills", "dup", body="PROJECT BODY")
        _write_skill(user_home / "skills", "dup", body="USER BODY")

        reg = CommandRegistry()
        asyncio.run(reg.load_all(project_dir=project, user_dir=user_home))

        cmd = reg.find("dup")
        assert cmd is not None
        assert isinstance(cmd, PromptCommand)
        assert "USER BODY" in cmd.get_prompt()
