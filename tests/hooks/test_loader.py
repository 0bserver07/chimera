"""Tests for chimera.hooks.loader — HookLoader."""
from __future__ import annotations

import json
import os
import tempfile

from chimera.hooks.events import HookEvent
from chimera.hooks.loader import HookLoader
from chimera.hooks.session_hooks import SessionHookManager
from chimera.hooks.hook_types import HookOutput


# ---------------------------------------------------------------------------
# Load from settings file
# ---------------------------------------------------------------------------


def test_load_from_project_settings():
    with tempfile.TemporaryDirectory() as tmpdir:
        chimera_dir = os.path.join(tmpdir, ".chimera")
        os.makedirs(chimera_dir)

        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "type": "command",
                        "command": "echo check",
                        "matcher": "bash",
                    }
                ],
            },
        }
        settings_path = os.path.join(chimera_dir, "settings.json")
        with open(settings_path, "w") as f:
            json.dump(settings, f)

        loader = HookLoader(project_dir=tmpdir)
        matchers = loader.load_all(HookEvent.PRE_TOOL_USE)

        assert len(matchers) == 1
        assert matchers[0].matcher == "bash"
        assert matchers[0].hooks[0].command == "echo check"
        assert matchers[0].source == "project"


def test_load_no_settings_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        loader = HookLoader(project_dir=tmpdir)
        matchers = loader.load_all(HookEvent.PRE_TOOL_USE)
        assert matchers == []


def test_load_with_session_hooks():
    with tempfile.TemporaryDirectory() as tmpdir:
        session_mgr = SessionHookManager()
        session_mgr.add_command_hook(HookEvent.PRE_TOOL_USE, "session-check")

        loader = HookLoader(project_dir=tmpdir)
        matchers = loader.load_all(HookEvent.PRE_TOOL_USE, session_hooks=session_mgr)

        assert len(matchers) == 1
        assert matchers[0].source == "session"


def test_load_combines_project_and_session():
    with tempfile.TemporaryDirectory() as tmpdir:
        chimera_dir = os.path.join(tmpdir, ".chimera")
        os.makedirs(chimera_dir)

        settings = {
            "hooks": {
                "PreToolUse": [
                    {"type": "command", "command": "project-check", "matcher": "bash"},
                ],
            },
        }
        with open(os.path.join(chimera_dir, "settings.json"), "w") as f:
            json.dump(settings, f)

        session_mgr = SessionHookManager()
        session_mgr.add_command_hook(HookEvent.PRE_TOOL_USE, "session-check")

        loader = HookLoader(project_dir=tmpdir)
        matchers = loader.load_all(HookEvent.PRE_TOOL_USE, session_hooks=session_mgr)

        assert len(matchers) == 2
        sources = [m.source for m in matchers]
        assert "project" in sources
        assert "session" in sources


def test_load_user_settings():
    with tempfile.TemporaryDirectory() as proj_dir:
        with tempfile.TemporaryDirectory() as user_dir:
            chimera_dir = os.path.join(user_dir, ".chimera")
            os.makedirs(chimera_dir)

            settings = {
                "hooks": {
                    "PreToolUse": [
                        {"type": "command", "command": "user-check"},
                    ],
                },
            }
            with open(os.path.join(chimera_dir, "settings.json"), "w") as f:
                json.dump(settings, f)

            loader = HookLoader(project_dir=proj_dir, user_dir=user_dir)
            matchers = loader.load_all(HookEvent.PRE_TOOL_USE)

            assert len(matchers) == 1
            assert matchers[0].source == "user"


def test_source_priority_order():
    """User-sourced matchers appear before project-sourced ones."""
    with tempfile.TemporaryDirectory() as proj_dir:
        with tempfile.TemporaryDirectory() as user_dir:
            # Project settings
            proj_chimera = os.path.join(proj_dir, ".chimera")
            os.makedirs(proj_chimera)
            with open(os.path.join(proj_chimera, "settings.json"), "w") as f:
                json.dump({"hooks": {"PreToolUse": [{"type": "command", "command": "proj"}]}}, f)

            # User settings
            user_chimera = os.path.join(user_dir, ".chimera")
            os.makedirs(user_chimera)
            with open(os.path.join(user_chimera, "settings.json"), "w") as f:
                json.dump({"hooks": {"PreToolUse": [{"type": "command", "command": "user"}]}}, f)

            session_mgr = SessionHookManager()
            session_mgr.add_command_hook(HookEvent.PRE_TOOL_USE, "session")

            loader = HookLoader(project_dir=proj_dir, user_dir=user_dir)
            matchers = loader.load_all(HookEvent.PRE_TOOL_USE, session_hooks=session_mgr)

            sources = [m.source for m in matchers]
            # user before project before session
            assert sources.index("user") < sources.index("project")
            assert sources.index("project") < sources.index("session")


def test_load_unmatched_event():
    """Loading for an event with no hooks in settings returns empty."""
    with tempfile.TemporaryDirectory() as tmpdir:
        chimera_dir = os.path.join(tmpdir, ".chimera")
        os.makedirs(chimera_dir)

        settings = {
            "hooks": {
                "PostToolUse": [
                    {"type": "command", "command": "post-check"},
                ],
            },
        }
        with open(os.path.join(chimera_dir, "settings.json"), "w") as f:
            json.dump(settings, f)

        loader = HookLoader(project_dir=tmpdir)
        matchers = loader.load_all(HookEvent.PRE_TOOL_USE)
        assert matchers == []
