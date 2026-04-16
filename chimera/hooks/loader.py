"""HookLoader — load and merge hooks from settings files and session."""
from __future__ import annotations

import json
import os
from typing import Any

from chimera.hooks.events import HookEvent
from chimera.hooks.session_hooks import SessionHookManager
from chimera.hooks.hook_types import CommandHook, HookMatcher, PromptHook


class HookLoader:
    """Load hook matchers from settings files and session hooks.

    Settings files are searched in ``<dir>/.chimera/settings.json`` for
    both *project_dir* and *user_dir*.  Results are sorted by source
    priority so that user-level hooks run before project-level ones.
    """

    SOURCE_PRIORITY = ["user", "project", "local", "plugin", "builtin", "session"]

    def __init__(
        self,
        project_dir: str,
        user_dir: str | None = None,
    ) -> None:
        self._project_dir = project_dir
        self._user_dir = user_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(
        self,
        event: HookEvent,
        session_hooks: SessionHookManager | None = None,
    ) -> list[HookMatcher]:
        """Load all matchers for *event*, sorted by source priority."""
        all_matchers: list[HookMatcher] = []

        # User-level settings
        if self._user_dir:
            all_matchers.extend(
                self._load_from_dir(self._user_dir, event, source="user"),
            )

        # Project-level settings
        all_matchers.extend(
            self._load_from_dir(self._project_dir, event, source="project"),
        )

        # Session hooks (added at runtime)
        if session_hooks is not None:
            all_matchers.extend(session_hooks.get_matchers(event))

        # Sort by source priority
        all_matchers.sort(key=lambda m: self._priority(m.source))

        return all_matchers

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_from_dir(
        self,
        directory: str,
        event: HookEvent,
        source: str,
    ) -> list[HookMatcher]:
        """Load hooks from ``<directory>/.chimera/settings.json``."""
        settings_path = os.path.join(directory, ".chimera", "settings.json")
        if not os.path.isfile(settings_path):
            return []

        try:
            with open(settings_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

        hooks_config = data.get("hooks", {})
        event_key = event.value  # e.g. "PreToolUse"
        event_entries = hooks_config.get(event_key, [])

        matchers: list[HookMatcher] = []
        for entry in event_entries:
            parsed = self._parse_hook_config(entry, source)
            if parsed is not None:
                matchers.append(parsed)

        return matchers

    @staticmethod
    def _parse_hook_config(config: dict[str, Any], source: str) -> HookMatcher | None:
        """Parse a single hook config dict into a HookMatcher."""
        hook_type = config.get("type", "command")
        matcher_pattern = config.get("matcher")

        if hook_type == "command":
            command = config.get("command", "")
            if not command:
                return None
            timeout = config.get("timeout", 60)
            hook = CommandHook(command=command, timeout=timeout)
            return HookMatcher(
                hooks=[hook],
                matcher=matcher_pattern,
                source=source,
            )
        elif hook_type == "prompt":
            prompt = config.get("prompt", "")
            if not prompt:
                return None
            timeout = config.get("timeout", 30)
            hook = PromptHook(prompt=prompt, timeout=timeout)
            return HookMatcher(
                hooks=[hook],
                matcher=matcher_pattern,
                source=source,
            )
        else:
            return None

    @classmethod
    def _priority(cls, source: str) -> int:
        """Return sort key for a source string. Lower = higher priority."""
        try:
            return cls.SOURCE_PRIORITY.index(source)
        except ValueError:
            return len(cls.SOURCE_PRIORITY)
