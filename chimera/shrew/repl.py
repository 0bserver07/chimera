"""``chimera shrew`` interactive REPL — small-model delegation layer.

The shrew REPL forwards to :func:`chimera.cli.code.run_code` (the shared
interactive coding REPL) with two extras over weasel's REPL:

* It late-binds to optional shrew **skills** (S2) and **extensions**
  (S3). When those modules are available the namespace adaptor injects
  their hooks into the ``run_code`` namespace via ``_post_session_init``;
  when they aren't, the REPL still works (weasel-equivalent behaviour).
* It honors shrew's smaller ``--max-steps`` default (``30``) and the
  restricted ``--allowed-tools`` posture so the REPL feels like the
  small-model coding agent rather than a generic harness.

No sub-agents, no plan mode, no extra slash palette beyond what the
shared REPL already ships — minimalism is still the feature, just with
small-model-tuned defaults.

Trademark hygiene: never names the upstream brand. The skills /
extensions modules are namespaced under :mod:`chimera.shrew` and load
content authored under :mod:`chimera.shrew.skills`; user-scope
``~/.shrew/skills/`` is a filesystem path mention, not a brand claim.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
from typing import Any

# WHY: stdlib only at import time. The shared REPL pulls in providers
# lazily, and the skills/extensions discovery routines are themselves
# late-bound below so a missing S2/S3 module never breaks the REPL.

_DEFAULT_MAX_STEPS = 30
"""Default ``max_steps`` when the namespace doesn't carry one. Mirrors
the value pinned in :mod:`chimera.shrew.cli` so the REPL fallback path
honors shrew's small-model posture."""


# ---------------------------------------------------------------------------
# Skill + extension mounting (late-bound to S2 + S3)
# ---------------------------------------------------------------------------


def _mount_skills(workdir: str) -> list[Any]:
    """Discover shrew skills if :mod:`chimera.shrew.skills` is available.

    The S2 agent owns ``chimera/shrew/skills/`` (knowledge / protocols /
    tools markdowns) and exposes :func:`discover_shrew_skills` (the real
    public name) and ``discover_skills`` (a back-compat alias accepted
    here so test doubles can stub either). When the module is missing
    or exposes neither callable, the REPL falls back to the no-skills
    path silently.

    The function is best-effort: any import or discovery failure
    downgrades to a one-line stderr warning (so a malformed skill never
    breaks the REPL boot) and an empty list.

    Args:
        workdir: Project root passed through to the discovery routine
            for project-scope skill paths (consumed by both real and
            stubbed callables).

    Returns:
        A list of skill records (opaque to this module). Empty when no
        skills are available.
    """
    # WHY: use ``importlib.import_module`` rather than ``from chimera.shrew
    # import skills`` so test fixtures that pin ``sys.modules`` entries
    # (e.g. ``monkeypatch.setitem(sys.modules, "chimera.shrew.skills", ...)``)
    # are honored. The ``from`` form falls back to the parent package's
    # attribute, which survives once the real subpackage has been imported.
    sentinel = object()
    cached = sys.modules.get("chimera.shrew.skills", sentinel)
    if cached is None:
        # Test fixtures use ``None`` to simulate "module unavailable".
        return []
    _shrew_skills: Any
    if cached is sentinel:
        try:
            _shrew_skills = importlib.import_module("chimera.shrew.skills")
        except ImportError:
            return []
    else:
        _shrew_skills = cached
    if _shrew_skills is None:
        return []
    # Prefer the real S2 export name; accept the simpler ``discover_skills``
    # alias for test doubles and future REPL-side overrides.
    discover = getattr(_shrew_skills, "discover_shrew_skills", None) or getattr(
        _shrew_skills, "discover_skills", None,
    )
    if not callable(discover):
        return []
    records: Any = []
    try:
        # WHY: ``discover_shrew_skills`` takes ``extra_search_paths`` while
        # the lighter ``discover_skills`` stub used in tests takes a
        # ``workdir`` keyword. Try the real signature first, fall back to
        # the stub shape so both flavors work.
        try:
            records = discover(extra_search_paths=[workdir]) or []
        except TypeError:
            records = discover(workdir=workdir) or []
    except Exception as exc:  # noqa: BLE001 — never crash the REPL
        sys.stderr.write(
            f"[shrew] skills discovery failed; continuing without skills: {exc}\n"
        )
        sys.stderr.flush()
        return []
    return list(records)


def _mount_extensions(workdir: str) -> list[Any]:
    """Discover shrew small-model extensions if :mod:`chimera.shrew.extensions` is available.

    S3 owns ``chimera/shrew/extensions/`` (``moe_offload``,
    ``scaffold_fit``, ``tool_filter``) and exposes function-style
    helpers (``compute_optimal_context_window``, ``wrap_for_small_model``,
    ``filter_tools_for_model``) rather than a single ``load_extensions``
    factory. The REPL prefers a ``load_extensions`` callable when one is
    present (for test doubles and future plugin-style entry points) and
    otherwise falls back to a no-op handles list — the live extensions
    are applied directly inside the print/serve paths via the
    ``chimera.shrew.extensions`` exports rather than through the hook.

    Args:
        workdir: Project root passed through to the loader.

    Returns:
        A list of extension handles. Empty when no extensions are
        available or when the module ships only function-style helpers.
    """
    # WHY: identical reasoning to ``_mount_skills`` — go through
    # ``sys.modules`` + ``importlib.import_module`` so test fixtures that
    # pin the entry can intercept the lookup.
    sentinel = object()
    cached = sys.modules.get("chimera.shrew.extensions", sentinel)
    if cached is None:
        return []
    _shrew_ext: Any
    if cached is sentinel:
        try:
            _shrew_ext = importlib.import_module("chimera.shrew.extensions")
        except ImportError:
            return []
    else:
        _shrew_ext = cached
    if _shrew_ext is None:
        return []
    loader = getattr(_shrew_ext, "load_extensions", None)
    if not callable(loader):
        # The real S3 module is function-style: nothing to load through
        # this hook, the per-call helpers are invoked elsewhere.
        return []
    handles: Any = []
    try:
        handles = loader(workdir=workdir) or []
    except Exception as exc:  # noqa: BLE001 — never crash the REPL
        sys.stderr.write(
            f"[shrew] extensions load failed; continuing without extensions: {exc}\n"
        )
        sys.stderr.flush()
        return []
    return list(handles)


def _make_post_session_init(
    skills: list[Any], extensions: list[Any],
) -> Any | None:
    """Build a ``_post_session_init`` callable for ``run_code``.

    The shared REPL reads ``args._post_session_init`` and, when
    callable, invokes it with the live session. We use that hook to
    apply each loaded extension to the session (so S3 extensions can
    tweak ``loop_config`` / message queues / tool filters in-place) and
    to attach the skills list to the session for any future skills-aware
    slash commands.

    Args:
        skills: Discovered skill records (may be empty).
        extensions: Loaded extension handles (may be empty).

    Returns:
        A no-arg callable suitable for ``args._post_session_init``, or
        ``None`` when nothing needs to fire (so the shared REPL can take
        its no-hook fast path).
    """
    if not skills and not extensions:
        return None

    def _hook(session: Any) -> None:
        # WHY: best-effort. Any extension that raises is logged but
        # never crashes the REPL; the user gets a degraded session
        # rather than a hard failure on a startup hook.
        for ext in extensions:
            apply = getattr(ext, "apply", None)
            if not callable(apply):
                continue
            try:
                apply(session)
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(
                    f"[shrew] extension apply failed: {exc}\n"
                )
                sys.stderr.flush()
        # Stash skills on the session for skills-aware slash commands
        # (collab with S2). Defensive: only set the attribute when the
        # session-like object accepts attribute assignment.
        if skills:
            try:
                setattr(session, "shrew_skills", list(skills))
            except Exception:  # noqa: BLE001
                pass

    return _hook


# ---------------------------------------------------------------------------
# Namespace adaptor for ``chimera.cli.code.run_code``
# ---------------------------------------------------------------------------


def _build_run_code_namespace(args: argparse.Namespace) -> argparse.Namespace:
    """Translate a shrew CLI namespace into the shape ``run_code`` expects.

    :func:`chimera.cli.code.run_code` reads attributes via ``getattr`` so
    a fresh namespace populated with the right keys is enough. Shrew
    uses ``--cwd`` while the shared REPL reads ``workdir``; we adapt
    here so shrew's CLI surface stays small while still reusing the
    REPL implementation.

    The adaptor also late-binds skill + extension discovery so a
    follow-up agent can ship those modules without touching this code.

    Args:
        args: Parsed shrew CLI namespace.

    Returns:
        A new namespace ready to feed :func:`run_code`, with optional
        ``_post_session_init`` set when shrew skills / extensions are
        available.
    """
    workdir = getattr(args, "cwd", None) or os.getcwd()
    max_steps = int(getattr(args, "max_steps", _DEFAULT_MAX_STEPS) or _DEFAULT_MAX_STEPS)
    skills = _mount_skills(workdir)
    extensions = _mount_extensions(workdir)
    post_init = _make_post_session_init(skills, extensions)

    ns = argparse.Namespace(
        mode="interactive",
        model=getattr(args, "model", None),
        workdir=workdir,
        max_steps=max_steps,
        # WHY: explicitly None so ``run_code`` doesn't try to take the
        # ``--print``/``-p`` short-circuit path inside its own dispatcher.
        # The shrew CLI handles ``-p`` upstream of this call.
        print_mode=None,
        # WHY: shrew exposes no preset selector. ``run_code`` opt-in
        # branches on a truthy preset, so leaving it ``None`` keeps the
        # default code path active.
        preset=None,
        # WHY (G3, wave 10): bare ``chimera code`` defaults to the new
        # CodingAgent stack. Shrew tunes for small local models on top of
        # the legacy rich REPL (skills + extensions wired via
        # ``_post_session_init``). Pin legacy_react=True so the default
        # flip can't regress shrew's small-model harness.
        legacy_react=True,
    )
    if post_init is not None:
        # WHY: ``run_code`` reads ``_post_session_init`` via ``getattr``
        # with a ``None`` default. Setting it only when we have a real
        # hook keeps the shared REPL on its fast path otherwise.
        ns._post_session_init = post_init  # type: ignore[attr-defined]
    return ns


def run(args: argparse.Namespace) -> int:
    """Run the shrew interactive REPL.

    Delegates to :func:`chimera.cli.code.run_code` with a translated
    namespace. The shared REPL handles provider creation, MCP loading,
    slash commands, and event-sourced session persistence — shrew
    inherits all of that for free, then layers small-model skills and
    extensions on top via ``_post_session_init``.

    Args:
        args: Parsed shrew CLI namespace.

    Returns:
        Process exit code from the underlying REPL.
    """
    from chimera.cli.code import run_code

    adapted = _build_run_code_namespace(args)
    rc: Any = run_code(adapted)
    try:
        return int(rc)
    except (TypeError, ValueError):
        return 0


__all__ = ["run"]
