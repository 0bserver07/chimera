"""Load permission rules from on-disk settings files."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from chimera.permissions.context import PermissionContext
from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import RuleSource

__all__ = ["PermissionRuleLoader"]

_log = logging.getLogger(__name__)


class PermissionRuleLoader:
    """Read ``<dir>/.chimera/settings.json`` and build a :class:`PermissionContext`.

    Parameters:
        project_dir: Path to the project root (contains ``.chimera/``).
        user_dir:    Optional path to a user-level config directory.
    """

    def __init__(self, project_dir: str, user_dir: str | None = None) -> None:
        self._project_dir = Path(project_dir)
        self._user_dir = Path(user_dir) if user_dir else None

    # ------------------------------------------------------------------

    def load(self) -> PermissionContext:
        """Read settings files and return an assembled :class:`PermissionContext`."""
        allow_rules: dict[RuleSource, list[str]] = {}
        deny_rules: dict[RuleSource, list[str]] = {}
        ask_rules: dict[RuleSource, list[str]] = {}
        mode = PermissionMode.DEFAULT

        # Project-level settings
        project_perms = self._read_permissions(self._project_dir)
        if project_perms is not None:
            mode = self._extract_mode(project_perms, mode)
            self._collect_rules(project_perms, RuleSource.PROJECT,
                                allow_rules, deny_rules, ask_rules)

        # User-level settings
        if self._user_dir is not None:
            user_perms = self._read_permissions(self._user_dir)
            if user_perms is not None:
                mode = self._extract_mode(user_perms, mode)
                self._collect_rules(user_perms, RuleSource.USER,
                                    allow_rules, deny_rules, ask_rules)

        return PermissionContext(
            mode=mode,
            allow_rules=allow_rules,
            deny_rules=deny_rules,
            ask_rules=ask_rules,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_permissions(self, base_dir: Path) -> dict[str, Any] | None:
        """Return the ``permissions`` dict from ``<base>/.chimera/settings.json``,
        or ``None`` if unavailable."""
        settings_path = base_dir / ".chimera" / "settings.json"
        if not settings_path.is_file():
            return None
        try:
            data = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            _log.warning("Failed to read %s: %s", settings_path, exc)
            return None
        return data.get("permissions") if isinstance(data, dict) else None

    @staticmethod
    def _extract_mode(
        perms: dict[str, Any],
        fallback: PermissionMode,
    ) -> PermissionMode:
        mode_str = perms.get("mode")
        if mode_str is None:
            return fallback
        try:
            return PermissionMode(mode_str)
        except ValueError:
            _log.warning("Unknown permission mode %r, using fallback", mode_str)
            return fallback

    @staticmethod
    def _collect_rules(
        perms: dict[str, Any],
        source: RuleSource,
        allow_rules: dict[RuleSource, list[str]],
        deny_rules: dict[RuleSource, list[str]],
        ask_rules: dict[RuleSource, list[str]],
    ) -> None:
        for key, target in (
            ("allow", allow_rules),
            ("deny", deny_rules),
            ("ask", ask_rules),
        ):
            entries = perms.get(key)
            if isinstance(entries, list) and entries:
                target[source] = list(entries)
