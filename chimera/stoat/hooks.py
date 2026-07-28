"""Stoat hooks engine — load and dispatch user-config hooks.

Stoat exposes a per-CLI hook surface that mirrors mink's settings-driven
hooks but lives in a stoat-scoped JSON file (``~/.chimera/stoat/hooks.json``)
so users can declare PreToolUse / PostToolUse / SessionStart / etc.
hooks for stoat sessions without touching project-level
``.chimera/settings.json``.

The on-disk schema is the same dict-of-lists layout used by every
Chimera CLI:

.. code-block:: json

    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "bash",
            "hooks": [{"type": "command", "command": "echo PRE"}]
          }
        ],
        "SessionStart": [
          {"type": "command", "command": "echo session-started"}
        ]
      }
    }

Both the *flat* (``{"type": "command", ...}``) and *nested*
(``{"matcher": ..., "hooks": [...]}``) shapes are accepted, matching
mink's ``_build_hook_emitter`` so users can copy/paste hook entries
between CLIs.

Trademark hygiene: the file path is a filesystem fact (we own the
``~/.chimera/stoat/`` namespace); no upstream brand is named in source.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.hooks.emitter import HookEmitter
from chimera.hooks.events import HookEvent
from chimera.hooks.executor import HookExecutor
from chimera.config.paths import store_path
from chimera.hooks.hook_types import (
    CommandHook,
    HookMatcher,
    PromptHook,
)

__all__ = [
    "HOOKS_FILENAME",
    "default_hooks_path",
    "load_hooks_config",
    "build_hook_emitter",
    "build_emitter_from_path",
    "fire_session_start",
    "fire_session_end",
    "fire_user_prompt_submit",
]


HOOKS_FILENAME = "hooks.json"
"""On-disk filename used inside the stoat config dir."""

_DEFAULT_COMMAND_TIMEOUT = 60
_DEFAULT_PROMPT_TIMEOUT = 30


def default_hooks_path() -> Path:
    """Return ``~/.chimera/stoat/hooks.json`` honoring ``$CHIMERA_HOME``.

    ``$CHIMERA_HOME`` overrides the ``~/.chimera`` root so test fixtures
    and CI sandboxes can relocate the config dir without monkey-patching
    :func:`pathlib.Path.home`.

    Returns:
        Absolute path to the stoat-scoped hooks JSON file. The file does
        not need to exist; callers must handle ``FileNotFoundError``.
    """
    return store_path("stoat") / HOOKS_FILENAME


def load_hooks_config(
    path: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Read ``hooks.json`` and return its ``hooks`` sub-block.

    The file may be missing, malformed, or have an unexpected top-level
    shape; in any of those cases we return an empty dict so callers can
    treat "no hooks" as the default behavior without try/except churn.

    Args:
        path: Override the hooks file location (mainly for tests).
            Defaults to :func:`default_hooks_path`.

    Returns:
        Mapping from event name (e.g. ``"PreToolUse"``) to a list of
        spec dicts. Empty when the file is missing, can't be parsed,
        or doesn't expose a ``hooks`` block.
    """
    target = path or default_hooks_path()
    if not target.exists() or not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    # Two accepted shapes:
    #   {"hooks": {"PreToolUse": [...], ...}}
    #   {"PreToolUse": [...], ...}    (legacy / convenience)
    block: Any = raw.get("hooks", raw)
    if not isinstance(block, dict):
        return {}
    cleaned: dict[str, list[dict[str, Any]]] = {}
    for event, specs in block.items():
        if not isinstance(event, str):
            continue
        if not isinstance(specs, list):
            continue
        cleaned[event] = [s for s in specs if isinstance(s, dict)]
    return cleaned


def _build_hooks_for_spec(spec: dict[str, Any]) -> list[Any]:
    """Translate one user-config spec dict into a list of Chimera hook objects.

    Accepts the *flat* shape (one command/prompt directly on the spec)
    and the *nested* shape (a ``hooks`` array of inner specs). Returns
    an empty list when neither shape produces a valid hook so caller
    code can ``if built:`` cheaply.
    """
    inner_specs: list[dict[str, Any]]
    if isinstance(spec.get("hooks"), list):
        inner_specs = [s for s in spec["hooks"] if isinstance(s, dict)]
    else:
        inner_specs = [spec]

    built: list[Any] = []
    for inner in inner_specs:
        hook_type = str(inner.get("type") or "command").lower()
        if hook_type == "command":
            cmd = inner.get("command")
            if not cmd:
                continue
            built.append(
                CommandHook(
                    command=str(cmd),
                    timeout=int(
                        inner.get("timeout", _DEFAULT_COMMAND_TIMEOUT) or 0
                    ),
                ),
            )
        elif hook_type == "prompt":
            pmpt = inner.get("prompt")
            if not pmpt:
                continue
            built.append(
                PromptHook(
                    prompt=str(pmpt),
                    timeout=int(
                        inner.get("timeout", _DEFAULT_PROMPT_TIMEOUT) or 0
                    ),
                ),
            )
        # WHY: function hooks need a Python callable so they can't be
        # declared in JSON. Skip silently rather than raise.
    return built


def build_hook_emitter(
    hooks_config: dict[str, list[dict[str, Any]]] | None,
) -> HookEmitter | None:
    """Translate a parsed hooks-config dict into a :class:`HookEmitter`.

    Mirrors :func:`chimera.mink.cli._build_hook_emitter` byte-for-byte
    for the parts we share so a settings.json hook entry that works in
    mink also works here. Differences are intentional and minimal:

    * Source is recorded as ``"stoat"`` (not ``"project"``) so audit
      logs can attribute hooks back to the stoat-scoped file.
    * ``HookMatcher.events`` is populated with the source event name so
      the executor only fires a matcher for the event it was declared
      under (mink's variant relies on a separate matchers table).

    Args:
        hooks_config: ``{event_name: [spec, ...]}`` dict from
            :func:`load_hooks_config`. ``None`` or empty returns
            ``None`` so :class:`LoopConfig.hook_emitter` stays unset.

    Returns:
        A configured :class:`HookEmitter` or ``None`` when no usable
        hook entries were found.
    """
    if not hooks_config:
        return None

    matchers: list[HookMatcher] = []
    for event_name, specs in hooks_config.items():
        if not isinstance(specs, list):
            continue
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            built = _build_hooks_for_spec(spec)
            if not built:
                continue
            matcher = spec.get("matcher")
            matchers.append(
                HookMatcher(
                    hooks=built,
                    matcher=str(matcher) if matcher else None,
                    source="stoat",
                    events=[event_name],
                ),
            )

    if not matchers:
        return None
    executor = HookExecutor()
    return HookEmitter(executor=executor, matchers=matchers)


def build_emitter_from_path(path: Path | None = None) -> HookEmitter | None:
    """One-shot helper: load + build in a single call.

    Args:
        path: Override the hooks file location. Defaults to
            :func:`default_hooks_path`.

    Returns:
        A configured :class:`HookEmitter` or ``None`` when no usable
        hooks were declared.
    """
    return build_hook_emitter(load_hooks_config(path))


# ---------------------------------------------------------------------------
# Lifecycle helpers — fire SessionStart / SessionEnd / UserPromptSubmit
# ---------------------------------------------------------------------------
#
# The REPL calls these helpers at the matching lifecycle moments. They
# never raise: a missing emitter, a hook subprocess that crashes, or a
# malformed user config must not bring down the REPL itself.


def fire_session_start(
    emitter: HookEmitter | None,
    *,
    session_id: str = "",
) -> None:
    """Fire :data:`HookEvent.SESSION_START`. Best-effort, swallows errors."""
    if emitter is None or not emitter.active:
        return
    try:
        emitter.emit_sync(HookEvent.SESSION_START, session_id=session_id)
    except Exception:  # noqa: BLE001 — REPL hooks must never crash the loop
        pass


def fire_session_end(
    emitter: HookEmitter | None,
    *,
    session_id: str = "",
) -> None:
    """Fire :data:`HookEvent.SESSION_END`. Best-effort, swallows errors."""
    if emitter is None or not emitter.active:
        return
    try:
        emitter.emit_sync(HookEvent.SESSION_END, session_id=session_id)
    except Exception:  # noqa: BLE001 — REPL hooks must never crash the loop
        pass


def fire_user_prompt_submit(
    emitter: HookEmitter | None,
    *,
    user_prompt: str,
    session_id: str = "",
) -> None:
    """Fire :data:`HookEvent.USER_PROMPT_SUBMIT` on each REPL line.

    Best-effort: any exception is suppressed so a busted hook script
    never blocks the user from typing the next prompt.
    """
    if emitter is None or not emitter.active:
        return
    try:
        emitter.emit_sync(
            HookEvent.USER_PROMPT_SUBMIT,
            session_id=session_id,
            user_prompt=user_prompt,
        )
    except Exception:  # noqa: BLE001 — REPL hooks must never crash the loop
        pass
