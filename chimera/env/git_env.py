"""Git-based environment with commit-based checkpointing."""
from __future__ import annotations

from pathlib import Path

from chimera.env.local import LocalEnvironment
from chimera.types import CommandResult


class GitEnvironment(LocalEnvironment):
    """LocalEnvironment with git-based checkpointing.

    Uses git commits instead of file copies for checkpoint/restore,
    giving you the full power of version control during synthesis.
    """

    def setup(self) -> None:
        super().setup()
        # Initialize git repo if not already present
        if not (self.workdir / ".git").exists():
            self._git("init")
            self._git("config user.email 'chimera@local'")
            self._git("config user.name 'Chimera'")
            # Initial commit so we have a HEAD
            (self.workdir / ".gitkeep").touch()
            self._git("add .")
            self._git("commit -m 'initial' --allow-empty")

    def checkpoint(self) -> str:
        self._git("add .")
        self._git("commit -m 'checkpoint' --allow-empty")
        result = self._git("rev-parse HEAD")
        return result.stdout.strip()

    def restore(self, checkpoint_id: str) -> None:
        self._git(f"checkout {checkpoint_id} -- .")
        self._git("clean -fd")

    def _git(self, cmd: str) -> CommandResult:
        return self.run_command(f"git {cmd}")
