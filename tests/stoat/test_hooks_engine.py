"""Tests for the stoat hooks engine (W14-3, item 1).

Covers:

* ``default_hooks_path`` — honours ``$CHIMERA_HOME``.
* ``load_hooks_config`` — parses both top-level ``hooks`` and bare
  event-keyed dicts; tolerates malformed JSON, missing files, and
  unexpected top-level shapes.
* ``build_hook_emitter`` — translates flat and nested specs into a
  configured :class:`HookEmitter`; returns ``None`` for empty input.
* ``build_emitter_from_path`` — one-shot helper.
* ``fire_session_start`` / ``fire_session_end`` /
  ``fire_user_prompt_submit`` — best-effort wrappers swallow exceptions
  and skip work when no emitter is wired.
* End-to-end: a PreToolUse command hook actually runs (writes a marker
  file) when fired through the emitter.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from chimera.hooks.events import HookEvent
from chimera.stoat import hooks as stoat_hooks


# ---------------------------------------------------------------------------
# default_hooks_path
# ---------------------------------------------------------------------------


def test_default_hooks_path_honours_chimera_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$CHIMERA_HOME`` overrides the ``~/.chimera`` root."""
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    p = stoat_hooks.default_hooks_path()
    assert p == tmp_path / "stoat" / "hooks.json"


def test_default_hooks_path_falls_back_to_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``$CHIMERA_HOME`` the default is ``~/.chimera/stoat/hooks.json``."""
    monkeypatch.delenv("CHIMERA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    p = stoat_hooks.default_hooks_path()
    assert p == tmp_path / ".chimera" / "stoat" / "hooks.json"


# ---------------------------------------------------------------------------
# load_hooks_config
# ---------------------------------------------------------------------------


def test_load_hooks_missing_file_returns_empty(tmp_path: Path) -> None:
    """Missing config file -> ``{}`` (no exception)."""
    target = tmp_path / "missing.json"
    assert stoat_hooks.load_hooks_config(target) == {}


def test_load_hooks_malformed_json_returns_empty(tmp_path: Path) -> None:
    """A file with broken JSON degrades to ``{}`` rather than raising."""
    target = tmp_path / "broken.json"
    target.write_text("{not-json")
    assert stoat_hooks.load_hooks_config(target) == {}


def test_load_hooks_top_level_hooks_block(tmp_path: Path) -> None:
    """Canonical shape ``{"hooks": {<event>: [...]}}`` parses fully."""
    target = tmp_path / "hooks.json"
    target.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"type": "command", "command": "echo PRE"}
                    ],
                    "SessionStart": [
                        {"type": "command", "command": "echo START"}
                    ],
                },
            },
        ),
    )
    cfg = stoat_hooks.load_hooks_config(target)
    assert "PreToolUse" in cfg
    assert "SessionStart" in cfg
    assert cfg["PreToolUse"][0]["command"] == "echo PRE"


def test_load_hooks_bare_event_keyed_shape(tmp_path: Path) -> None:
    """Convenience shape (no top-level ``hooks`` wrapper) is also accepted."""
    target = tmp_path / "hooks.json"
    target.write_text(
        json.dumps(
            {"PreToolUse": [{"type": "command", "command": "echo BARE"}]},
        ),
    )
    cfg = stoat_hooks.load_hooks_config(target)
    assert cfg["PreToolUse"][0]["command"] == "echo BARE"


def test_load_hooks_drops_non_dict_specs(tmp_path: Path) -> None:
    """Non-dict entries inside the event list are dropped silently."""
    target = tmp_path / "hooks.json"
    target.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        "not-a-dict",
                        42,
                        {"type": "command", "command": "echo OK"},
                    ],
                },
            },
        ),
    )
    cfg = stoat_hooks.load_hooks_config(target)
    assert cfg["PreToolUse"] == [{"type": "command", "command": "echo OK"}]


def test_load_hooks_non_dict_top_level(tmp_path: Path) -> None:
    """A list at the top level isn't a hooks config — return empty."""
    target = tmp_path / "hooks.json"
    target.write_text(json.dumps([1, 2, 3]))
    assert stoat_hooks.load_hooks_config(target) == {}


# ---------------------------------------------------------------------------
# build_hook_emitter
# ---------------------------------------------------------------------------


def test_build_hook_emitter_empty_returns_none() -> None:
    """An empty config produces ``None`` (LoopConfig stays unchanged)."""
    assert stoat_hooks.build_hook_emitter(None) is None
    assert stoat_hooks.build_hook_emitter({}) is None


def test_build_hook_emitter_flat_spec_active() -> None:
    """A flat ``{"type": "command", ...}`` spec becomes an active emitter."""
    emitter = stoat_hooks.build_hook_emitter(
        {"PreToolUse": [{"type": "command", "command": "true"}]},
    )
    assert emitter is not None
    assert emitter.active


def test_build_hook_emitter_nested_spec_active() -> None:
    """Nested ``{"matcher": ..., "hooks": [...]}`` shape works too."""
    emitter = stoat_hooks.build_hook_emitter(
        {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "true"}],
                },
            ],
        },
    )
    assert emitter is not None
    assert emitter.active


def test_build_hook_emitter_skips_specs_without_command() -> None:
    """Specs that don't carry a ``command`` are dropped; if all drop, emitter is None."""
    emitter = stoat_hooks.build_hook_emitter(
        {"PreToolUse": [{"type": "command"}, {"type": "command", "command": ""}]},
    )
    assert emitter is None


def test_build_hook_emitter_prompt_hook_supported() -> None:
    """A prompt hook spec is also accepted and yields an active emitter."""
    emitter = stoat_hooks.build_hook_emitter(
        {"PreToolUse": [{"type": "prompt", "prompt": "is this safe?"}]},
    )
    assert emitter is not None
    assert emitter.active


def test_build_hook_emitter_unknown_type_skipped() -> None:
    """Unknown hook types are dropped silently rather than raising."""
    emitter = stoat_hooks.build_hook_emitter(
        {"PreToolUse": [{"type": "wat", "command": "echo nope"}]},
    )
    assert emitter is None


def test_build_hook_emitter_records_stoat_source() -> None:
    """Matchers built here record ``source='stoat'`` so audits attribute correctly."""
    emitter = stoat_hooks.build_hook_emitter(
        {"PreToolUse": [{"type": "command", "command": "true"}]},
    )
    assert emitter is not None
    # Internal matcher list — accessed via the executor's perspective.
    matchers: list[Any] = list(getattr(emitter, "_matchers", []))
    assert matchers, "expected at least one matcher"
    assert all(m.source == "stoat" for m in matchers)


# ---------------------------------------------------------------------------
# build_emitter_from_path — one-shot path helper
# ---------------------------------------------------------------------------


def test_build_emitter_from_path_none_when_missing(tmp_path: Path) -> None:
    """Missing file -> ``None`` so callers can leave LoopConfig untouched."""
    assert stoat_hooks.build_emitter_from_path(tmp_path / "nope.json") is None


def test_build_emitter_from_path_active_when_present(tmp_path: Path) -> None:
    """A real file with a hook spec yields an active emitter."""
    target = tmp_path / "hooks.json"
    target.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [{"type": "command", "command": "true"}],
                },
            },
        ),
    )
    emitter = stoat_hooks.build_emitter_from_path(target)
    assert emitter is not None
    assert emitter.active


def test_build_emitter_from_path_uses_chimera_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No path arg -> reads from ``$CHIMERA_HOME/stoat/hooks.json``."""
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    target = tmp_path / "stoat" / "hooks.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {"hooks": {"PreToolUse": [{"type": "command", "command": "true"}]}},
        ),
    )
    emitter = stoat_hooks.build_emitter_from_path()
    assert emitter is not None
    assert emitter.active


# ---------------------------------------------------------------------------
# End-to-end: hook command actually executes
# ---------------------------------------------------------------------------


def test_command_hook_actually_runs(tmp_path: Path) -> None:
    """A PreToolUse command hook runs the registered subprocess.

    Writes a marker file via the hook command so we can assert the
    subprocess actually fired (an exit-code-only check leaves a window
    where the hook never ran).
    """
    marker = tmp_path / "fired.txt"
    cfg = {
        "PreToolUse": [
            {"type": "command", "command": f"echo fired > {marker}"},
        ],
    }
    emitter = stoat_hooks.build_hook_emitter(cfg)
    assert emitter is not None

    output = asyncio.run(
        emitter.emit(
            HookEvent.PRE_TOOL_USE,
            session_id="t",
            tool_name="bash",
            tool_input={"command": "echo hi"},
        ),
    )
    assert output.continue_execution is True
    assert marker.exists(), "PreToolUse hook command did not execute"
    assert "fired" in marker.read_text()


# ---------------------------------------------------------------------------
# Lifecycle helpers (best-effort)
# ---------------------------------------------------------------------------


def test_lifecycle_helpers_no_op_with_none() -> None:
    """``fire_*`` helpers return cleanly when no emitter is wired."""
    stoat_hooks.fire_session_start(None)
    stoat_hooks.fire_session_end(None)
    stoat_hooks.fire_user_prompt_submit(None, user_prompt="x")
    # No assertion — the contract is "doesn't raise".


def test_fire_session_start_invokes_emitter(
    tmp_path: Path,
) -> None:
    """``fire_session_start`` runs the SessionStart command hook."""
    marker = tmp_path / "session_start.txt"
    cfg = {
        "SessionStart": [
            {"type": "command", "command": f"echo started > {marker}"},
        ],
    }
    emitter = stoat_hooks.build_hook_emitter(cfg)
    assert emitter is not None
    stoat_hooks.fire_session_start(emitter, session_id="ssn-1")
    assert marker.exists(), "SessionStart hook command did not execute"


def test_fire_user_prompt_submit_invokes_emitter(tmp_path: Path) -> None:
    """``fire_user_prompt_submit`` runs the registered hook on each line."""
    marker = tmp_path / "prompt_submit.txt"
    cfg = {
        "UserPromptSubmit": [
            {"type": "command", "command": f"echo got > {marker}"},
        ],
    }
    emitter = stoat_hooks.build_hook_emitter(cfg)
    assert emitter is not None
    stoat_hooks.fire_user_prompt_submit(
        emitter, user_prompt="hi", session_id="ssn-2",
    )
    assert marker.exists()


def test_lifecycle_helpers_swallow_exceptions() -> None:
    """A blow-up inside the emitter doesn't propagate out of ``fire_*``."""

    class _BoomEmitter:
        active = True

        def emit_sync(self, *_a: Any, **_kw: Any) -> Any:
            raise RuntimeError("boom")

    fake = _BoomEmitter()
    # No assertion — these must just not raise.
    stoat_hooks.fire_session_start(fake)  # type: ignore[arg-type]
    stoat_hooks.fire_session_end(fake)  # type: ignore[arg-type]
    stoat_hooks.fire_user_prompt_submit(fake, user_prompt="x")  # type: ignore[arg-type]
