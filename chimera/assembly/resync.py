"""Hot-swap seam: re-discover plugins / skills / agents and rebind them live.

``/resync`` — available in the REPL and both TUI surfaces — re-reads the
resource catalogs on disk (plugin source, ``SKILL.md`` trees, agent
definition files) and rebinds them into the *running* session, no restart
required: edit a plugin or a skill, ``/resync``, and the next turn uses the
new behavior. The name says both halves — re-discover, then re-synchronize
the live session with what disk now says.

Two entry points, one report:

* :func:`resync_agent` — the assembled-stack seam. Targets a
  :class:`~chimera.assembly.coding_agent.CodingAgent` (what
  :class:`~chimera.assembly.driver.AgentDriver`, the TUI lanes, and the
  embed surface :class:`~chimera.embed.AgentSession` all wrap).
* :func:`resync_session` — the classic-REPL seam. Targets the duck-typed
  ``Session`` object ``chimera code --legacy-react`` (and the codename
  REPLs) drive, doing the strongest rebind that stack's shape allows.

Both return a :class:`ResyncReport` whose :meth:`ResyncReport.lines` render
identically in every frontend, so the transcript always says exactly what
changed, what failed, and — honestly — what a hot-swap can and cannot reach
mid-session.

Guarantees (also documented in ``docs/plugins.md``):

* **Busy refusal.** A resync never races a running turn: when the target is
  mid-turn the call returns a refused report and performs **no** rebinding.
* **Per-plugin isolation.** Each loaded plugin hot-swaps independently; one
  plugin failing to swap never blocks the others.
* **No half-applied plugin.** A plugin's visible registrations swap as one
  complete snapshot (the manager installs a registry only after a fully
  successful activation), **including its interceptor chains**: activation
  runs in an ownership scope, so an ``activate()`` that raises after
  registering part of its chains is rolled back by owner and the shipped
  interceptor registry is left exactly as it was — a failed swap can never
  orphan a chain. On a failed swap the previous in-memory registration is
  restored best-effort by re-activating the old instance; if that too
  fails the plugin ends **cleanly unloaded** and is reported as such.
  What is *not* guaranteed: automatic rollback of side effects a plugin
  performs outside the plugin registries (spawned processes, module-level
  state elsewhere).
* **Prompt honesty.** In the assembled stack the system prompt is
  reassembled every turn, so a refreshed skill catalog reaches the *next
  turn of the current conversation*. In the classic REPL the prompt was
  baked at startup; resync rebuilds it in place when the session recorded
  its base prompt, and says so either way.
* **Interceptor exactness.** Chains plugins register on the shipped
  :class:`~chimera.plugins.registry.PluginExtensionRegistry` are merged
  into every assembled turn by the host itself (plugin chains first, host
  chains last), so after a resync the per-turn merge carries exactly the
  reloaded plugins' chains. Resync accounts for them **per seam** in the
  report and never binds them a second time — a second binding would run
  every chain twice. Interceptor surfaces a *third-party* registry
  exposes bind through the generic fold under the same one ordering rule:
  plugin chains first, the host's own chain last, host final say.

Stdlib-only (zero-dependency core); every discovery step is best-effort and
reported rather than raised.
"""
from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping

    from chimera.skills.discovery import Skill

__all__ = [
    "BUSY_MESSAGE",
    "KindDelta",
    "ResyncReport",
    "resync_agent",
    "resync_plugins",
    "resync_session",
    "skill_state",
]

#: The one busy-refusal message, shared by every surface so tests and users
#: see the same sentence in the REPL and the TUI.
BUSY_MESSAGE = "a turn is running — cancel or wait for it, then /resync"

#: The four interceptor seams — the same closed set as
#: :class:`chimera.core.interception.Interceptors` and
#: :data:`chimera.plugins.registry.INTERCEPTOR_SEAMS`, drift-pinned by
#: ``tests/plugins/test_registry_interceptors.py`` so a seam added to core
#: cannot escape resync's per-seam accounting. Doubles as the bound on the
#: generic aggregation: whatever shape a third-party registry exposes, only
#: these chains are ever bound.
_INTERCEPTOR_SEAMS = ("provider_request", "tool_call", "tool_result", "context")


# ---------------------------------------------------------------------------
# Report model
# ---------------------------------------------------------------------------

@dataclass
class KindDelta:
    """What changed for one resource kind during a resync.

    Args:
        kind: Resource kind label (``"plugins"``, ``"skills"``, ``"agents"``,
            ``"plugin tools"``, ``"interceptors"``). For ``interceptors``
            the entries are per-seam: ``"<seam>:<callable name>"`` for
            chains on the shipped plugin registry, plus the generic
            surfaces' aggregate count (``"N bound"``) under refreshed.
        added: Names newly bound by this resync.
        removed: Names that disappeared (file deleted / plugin unloaded).
        refreshed: Names re-bound with changed content (edited skill, hot-
            swapped plugin, replaced tool, a reloaded plugin's fresh
            interceptor on the same seam).
        failed: ``(name, reason)`` pairs for entries that errored; the reason
            states what the failure left behind.
    """

    kind: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """Whether this kind saw any change or failure."""
        return bool(self.added or self.removed or self.refreshed or self.failed)

    def summary(self) -> str:
        """One-phrase summary (``"2 added, 1 refreshed"`` / ``"unchanged"``)."""
        parts: list[str] = []
        if self.added:
            parts.append(f"{len(self.added)} added")
        if self.removed:
            parts.append(f"{len(self.removed)} removed")
        if self.refreshed:
            parts.append(f"{len(self.refreshed)} refreshed")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return ", ".join(parts) if parts else "unchanged"


@dataclass
class ResyncReport:
    """Everything one ``/resync`` did (or refused to do), frontend-agnostic.

    Args:
        refused: ``True`` when the resync was refused outright (busy target);
            no rebinding of any kind happened.
        reason: The refusal reason (empty when not refused).
        deltas: Per-kind change records, in report order.
        notes: Honest one-liners appended to the transcript (prompt-rebind
            semantics, unbound surfaces, discovery errors).
    """

    refused: bool = False
    reason: str = ""
    deltas: list[KindDelta] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def delta(self, kind: str) -> KindDelta | None:
        """Return the delta for *kind*, or ``None`` when absent."""
        for d in self.deltas:
            if d.kind == kind:
                return d
        return None

    @property
    def ok(self) -> bool:
        """Whether the resync ran and nothing failed."""
        return not self.refused and not any(d.failed for d in self.deltas)

    def lines(self) -> list[str]:
        """Render the report as transcript lines (shared by REPL and TUI).

        Returns:
            A headline (``resync: <kind summaries>``), one ``!`` line per
            failure, then one ``·`` line per note. A refused resync renders
            as a single refusal line.
        """
        if self.refused:
            return [f"resync refused: {self.reason}"]
        if self.deltas:
            head = "resync: " + " · ".join(
                f"{d.kind} {d.summary()}" for d in self.deltas
            )
        else:
            head = "resync: nothing to rebind"
        out = [head]
        for d in self.deltas:
            for name, why in d.failed:
                out.append(f"  ! {d.kind} {name}: {why}")
        for note in self.notes:
            out.append(f"  · {note}")
        return out


# ---------------------------------------------------------------------------
# Snapshot / diff helpers
# ---------------------------------------------------------------------------

def _digest(*parts: object) -> str:
    """Short stable content hash over *parts* (for change detection)."""
    joined = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8", errors="replace")).hexdigest()[:16]


def skill_state(skills: list[Skill]) -> dict[str, str]:
    """Snapshot a discovered skill list as ``{name: content-hash}``.

    Args:
        skills: Skills from :func:`chimera.skills.discovery.discover_all_skills`.

    Returns:
        Mapping usable with :func:`_diff_named` to tell added / removed /
        refreshed skills apart across resyncs.
    """
    return {
        s.name: _digest(s.description, s.content, s.source) for s in skills
    }


def _agent_def_state(workdir: Path) -> dict[str, str]:
    """Snapshot the file-based agent-definition catalog under *workdir*."""
    from chimera.agents.loader import AgentLoader

    loader = AgentLoader(project_root=str(workdir))
    return {
        d.name: _digest(d.description, d.system_prompt, d.model, d.tools, d.loop)
        for d in loader.load_all().values()
    }


def _diff_named(
    old: Mapping[str, str] | None,
    new: Mapping[str, str],
) -> tuple[list[str], list[str], list[str]]:
    """Diff two name→hash snapshots into (added, removed, refreshed)."""
    prev: Mapping[str, str] = old or {}
    added = sorted(n for n in new if n not in prev)
    removed = sorted(n for n in prev if n not in new)
    refreshed = sorted(n for n in new if n in prev and prev[n] != new[n])
    return added, removed, refreshed


def _run_blocking(coro: Coroutine[Any, Any, Any], timeout: float = 15.0) -> Any:
    """Run *coro* to completion from any calling context.

    Uses :func:`asyncio.run` directly when no event loop is running; inside a
    running loop (the TUI's command handler) the coroutine runs on a private
    loop in a short-lived worker thread — the work is local file I/O, so
    blocking briefly is the deterministic choice over a fire-and-forget task
    whose outcome the report could not include.

    Args:
        coro: The coroutine to drive.
        timeout: Worker-thread join timeout in seconds.

    Returns:
        The coroutine's result.

    Raises:
        TimeoutError: If the worker thread does not finish in *timeout*.
        BaseException: Whatever the coroutine itself raised.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: list[Any] = []
    err: list[BaseException] = []

    def _worker() -> None:
        try:
            box.append(asyncio.run(coro))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller
            err.append(exc)

    t = threading.Thread(target=_worker, name="chimera-resync", daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        raise TimeoutError(f"resync worker did not finish within {timeout}s")
    if err:
        raise err[0]
    return box[0] if box else None


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------

def resync_plugins(manager: Any) -> KindDelta:
    """Hot-swap every loaded plugin on *manager*, isolating failures.

    Each plugin is reloaded through
    :meth:`~chimera.plugins.manager.PluginManager.reload` (module re-import →
    fresh instance → fresh registration). A plugin that errors is reported
    under :attr:`KindDelta.failed` and skipped; the others proceed.

    Failure handling — exactly what is guaranteed: the manager only installs
    a plugin's component registry after a fully successful activation, and
    activation runs in an interceptor ownership scope — an ``activate()``
    that raises after registering part of its chains is rolled back by
    owner, so a plugin is never left half-applied in *either* registry and
    a failed swap leaves the interceptor registry exactly as it was. On a
    failed swap this function re-activates the *previous* instance
    best-effort; when that restore also fails the plugin ends cleanly
    unloaded (recorded in :attr:`KindDelta.removed` as well, with its
    chains withdrawn by owner). No rollback of side effects a plugin
    performed outside the plugin registries is attempted.

    Args:
        manager: A :class:`~chimera.plugins.manager.PluginManager` (or
            ``None`` — returns an empty delta).

    Returns:
        The ``plugins`` :class:`KindDelta`.
    """
    delta = KindDelta(kind="plugins")
    if manager is None:
        return delta
    loaded = dict(getattr(manager, "plugins", {}) or {})
    for name in sorted(loaded):
        old = loaded[name]
        try:
            manager.reload(name)
        except Exception as exc:  # noqa: BLE001 - per-plugin isolation
            why = str(exc) or type(exc).__name__
            try:
                manager.load_plugin(old)
            except Exception:  # noqa: BLE001 - restore is best-effort
                delta.failed.append((name, f"{why} — plugin unloaded"))
                delta.removed.append(name)
            else:
                delta.failed.append(
                    (name, f"{why} — previous registration restored")
                )
        else:
            delta.refreshed.append(name)
    return delta


def _plugin_registries(manager: Any) -> list[Any]:
    """Best-effort view of the manager's per-plugin component registries."""
    if manager is None:
        return []
    registries = getattr(manager, "_registries", None)
    if isinstance(registries, dict):
        return list(registries.values())
    return []


def _callable_name(fn: Any) -> str:
    """Best-effort display name for an interceptor callable."""
    return (
        getattr(fn, "__qualname__", None)
        or getattr(fn, "__name__", None)
        or type(fn).__name__
    )


def _registry_interceptor_chains() -> dict[str, list[Any]]:
    """Snapshot the shipped plugin registry's per-seam interceptor chains.

    The typed half of the interceptor accounting: reads
    :meth:`~chimera.plugins.registry.PluginExtensionRegistry.get_all_interceptors`
    — the seam-validated surface plugins (and the bundled policy packs)
    register on — and returns its four chains keyed by seam name. The
    snapshot holds the callables themselves, not hashes: keeping the
    previous generation alive across a resync means the identity diff in
    :func:`_diff_interceptor_chains` can never be confused by object-id
    reuse after a reload garbage-collects the old instances.

    Returns:
        Mapping of seam name → interceptor callables, in chain order.
    """
    from chimera.plugins.registry import PluginExtensionRegistry

    bundle = PluginExtensionRegistry.get_all_interceptors()
    return {seam: list(getattr(bundle, seam)) for seam in _INTERCEPTOR_SEAMS}


def _diff_interceptor_chains(
    old: Mapping[str, list[Any]] | None,
    new: Mapping[str, list[Any]],
) -> KindDelta:
    """Diff two per-seam chain snapshots into the ``interceptors`` delta.

    Entries are ``"<seam>:<callable name>"``, so the report counts
    interceptors *per seam*. Identity decides sameness: a callable present
    in both snapshots is unchanged (not listed); a same-named callable with
    a new identity on the same seam — a reloaded plugin's fresh instance —
    is *refreshed*; the remainder are added / removed.

    Args:
        old: Previous snapshot (``None`` on the first resync; everything in
            *new* then reports as added, mirroring the skills convention).
        new: Current snapshot from :func:`_registry_interceptor_chains`.

    Returns:
        The typed entries of the ``interceptors`` :class:`KindDelta` (the
        generic fallback appends its own accounting afterwards).
    """
    delta = KindDelta(kind="interceptors")
    prev: Mapping[str, list[Any]] = old or {}
    for seam in _INTERCEPTOR_SEAMS:
        remaining_old = list(prev.get(seam, []))
        fresh: list[Any] = []
        for fn in new.get(seam, []):
            for i, old_fn in enumerate(remaining_old):
                if old_fn is fn:
                    del remaining_old[i]
                    break
            else:
                fresh.append(fn)
        old_names = [_callable_name(fn) for fn in remaining_old]
        for fn in fresh:
            name = _callable_name(fn)
            if name in old_names:
                old_names.remove(name)
                delta.refreshed.append(f"{seam}:{name}")
            else:
                delta.added.append(f"{seam}:{name}")
        delta.removed.extend(f"{seam}:{name}" for name in old_names)
    delta.added.sort()
    delta.removed.sort()
    delta.refreshed.sort()
    return delta


def _interceptor_census(chains: Mapping[str, list[Any]]) -> str:
    """Per-seam census of non-empty chains (``"tool_call 1 · context 1"``)."""
    return " · ".join(
        f"{seam} {len(chains[seam])}"
        for seam in _INTERCEPTOR_SEAMS
        if chains.get(seam)
    )


def _collect_plugin_interceptors(
    manager: Any,
    *,
    exclude: Mapping[str, list[Any]] | None = None,
) -> tuple[Any | None, int, int]:
    """Aggregate whatever interceptor surface the plugin registries expose.

    Deliberately generic — it binds any shape it can do so soundly and
    counts (without binding) shapes it cannot, so a registry that grows a
    richer interceptor surface composes with this seam instead of fighting
    it. The shipped :class:`~chimera.plugins.registry.PluginExtensionRegistry`
    never routes through here — its chains take the typed path
    (:func:`_registry_interceptor_chains`) and ride the host's per-turn
    merge. Recognized shapes, per registry ``interceptors`` attribute (also
    ``manager.get_all_interceptors()`` when present):

    * an ``Interceptors``-shaped object (has the four seam-chain attributes),
    * a mapping of seam name → iterable of callables,
    * an iterable of either of the above.

    Args:
        manager: The plugin manager to read.
        exclude: Per-seam chains the target's per-turn merge already
            carries (the shipped registry's snapshot). A callable found on
            a generic surface that matches an excluded one — by the same
            equality :meth:`~chimera.plugins.registry.PluginExtensionRegistry.unregister_interceptor`
            uses, so a bound method re-derived from the same instance
            matches — is skipped silently: it is already live, and binding
            it here too would run it twice per event.

    Returns:
        ``(merged, bound, opaque)`` — a fresh merged ``Interceptors`` (or
        ``None`` when nothing bound), the number of interceptor callables
        bound, and the number of contributions skipped as unrecognizable.
    """
    if manager is None:
        return None, 0, 0
    from chimera.core.interception import Interceptors

    merged = Interceptors()
    bound = 0
    opaque = 0
    excluded = [fn for chain in (exclude or {}).values() for fn in chain]

    def _is_excluded(fn: Any) -> bool:
        for known in excluded:
            try:
                if fn is known or fn == known:
                    return True
            except Exception:  # noqa: BLE001 - a foreign __eq__ must not break resync
                continue
        return False

    def _fold(raw: Any) -> None:
        nonlocal bound, opaque
        if raw is None:
            return
        if all(hasattr(raw, seam) for seam in _INTERCEPTOR_SEAMS):
            for seam in _INTERCEPTOR_SEAMS:
                chain = getattr(raw, seam, None) or []
                for fn in chain:
                    if callable(fn):
                        if _is_excluded(fn):
                            continue
                        getattr(merged, seam).append(fn)
                        bound += 1
                    else:
                        opaque += 1
            return
        if isinstance(raw, dict):
            for seam, chain in raw.items():
                if seam not in _INTERCEPTOR_SEAMS:
                    opaque += len(list(chain)) if hasattr(chain, "__iter__") else 1
                    continue
                for fn in chain or []:
                    if callable(fn):
                        if _is_excluded(fn):
                            continue
                        getattr(merged, seam).append(fn)
                        bound += 1
                    else:
                        opaque += 1
            return
        if hasattr(raw, "__iter__") and not callable(raw):
            for item in raw:
                _fold(item)
            return
        opaque += 1

    getter = getattr(manager, "get_all_interceptors", None)
    if callable(getter):
        try:
            _fold(getter())
        except Exception:  # noqa: BLE001 - a foreign surface must not break resync
            opaque += 1
    for registry in _plugin_registries(manager):
        _fold(getattr(registry, "interceptors", None))

    return (merged if bound else None), bound, opaque


def _bind_interceptors(agent: Any, plugin_chain: Any | None) -> None:
    """Rebind the agent's interceptors as generic-surface chains ⊕ base.

    The one ordering rule everywhere (the merge contract in
    ``docs/guides/interception.md``): plugin chains first, host chains
    last, host final say — so the constructor-supplied chain (e.g. a
    team-policy gate) is stashed on first resync and always folded
    *after* plugin chains: it sees the plugin-effective value, its
    replacements land last, and a block from either side stays terminal.
    Clearing all plugin interceptors restores exactly the base. A target
    without an ``_interceptors`` seam is left untouched. Only chains from
    *generic* third-party surfaces route through here — the shipped
    registry's chains ride the host's own per-turn merge (which applies
    the same rule) and never touch the base.
    """
    if not hasattr(agent, "_interceptors"):
        return
    if not hasattr(agent, "_resync_base_interceptors"):
        agent._resync_base_interceptors = getattr(agent, "_interceptors", None)
    base = agent._resync_base_interceptors
    if plugin_chain is None:
        agent._interceptors = base
        return
    from chimera.core.interception import Interceptors

    merged = Interceptors()
    for source in (plugin_chain, base):
        if source is None:
            continue
        for seam in _INTERCEPTOR_SEAMS:
            getattr(merged, seam).extend(getattr(source, seam, None) or [])
    agent._interceptors = merged


def _bind_plugin_tools(agent: Any, manager: Any) -> KindDelta:
    """Sync plugin-contributed tools into the agent's live tool list.

    The tool list is mutated **in place** so every holder of the same list
    (the sub-agent spawner, the provider request built next turn) sees the
    update. Previously bound plugin tools are removed first, then the
    current aggregate is appended — a plugin edit therefore *replaces* its
    tools, a failed/unloaded plugin's tools drop out. A plugin tool whose
    name collides with a non-plugin tool is refused (reported under
    ``failed``) so a plugin can never shadow a built-in.

    Args:
        agent: The assembled agent (``tools`` list attribute).
        manager: The plugin manager whose ``tools`` aggregate to bind.

    Returns:
        The ``plugin tools`` :class:`KindDelta`.
    """
    delta = KindDelta(kind="plugin tools")
    tools = getattr(agent, "tools", None)
    if tools is None or manager is None:
        return delta
    previous: dict[str, int] = dict(getattr(agent, "_resync_plugin_tools", {}) or {})

    current: dict[str, Any] = {}
    for tool in list(getattr(manager, "tools", []) or []):
        name = str(getattr(tool, "name", "") or "")
        if name:
            current[name] = tool

    base_names = {
        str(getattr(t, "name", "") or "")
        for t in tools
        if str(getattr(t, "name", "") or "") not in previous
    }
    accepted: dict[str, Any] = {}
    for name, tool in current.items():
        if name in base_names:
            delta.failed.append(
                (name, "name collides with a non-plugin tool — not bound")
            )
            continue
        accepted[name] = tool

    delta.added = sorted(n for n in accepted if n not in previous)
    delta.removed = sorted(n for n in previous if n not in current)
    delta.refreshed = sorted(
        n for n in accepted if n in previous and previous[n] != id(accepted[n])
    )

    tools[:] = [
        t for t in tools if str(getattr(t, "name", "") or "") not in previous
    ] + list(accepted.values())
    agent._resync_plugin_tools = {n: id(t) for n, t in accepted.items()}
    return delta


def _plugin_surface_notes(manager: Any, notes: list[str]) -> None:
    """Append honest notes about plugin surfaces resync counts but cannot bind."""
    if manager is None:
        return
    try:
        commands = list(manager.get_all_commands())
    except Exception:  # noqa: BLE001 - aggregation is best-effort
        commands = []
    if commands:
        notes.append(
            f"plugin-registered commands: {len(commands)} "
            "(frontends refresh their catalogs — the REPL re-installs them, "
            "the TUI recomposes built-ins + plugin commands)"
        )
    try:
        hooks = manager.get_all_hooks()
        hook_count = sum(len(v) for v in hooks.values()) if isinstance(hooks, dict) else 0
    except Exception:  # noqa: BLE001
        hook_count = 0
    if hook_count:
        notes.append(
            f"plugin-registered hooks: {hook_count} "
            "(re-registered; bound by surfaces that read the plugin registries)"
        )
    try:
        skills = list(manager.get_all_skills())
    except Exception:  # noqa: BLE001
        skills = []
    if skills:
        notes.append(f"plugin-registered skills: {len(skills)} (advertised via the plugin registries)")


def _discover_unloaded_note(manager: Any, notes: list[str]) -> None:
    """Note entry-point plugins that exist but are not loaded (never auto-load)."""
    if manager is None:
        return
    try:
        available = set(manager.discover())
    except Exception:  # noqa: BLE001 - entry-point scan is best-effort
        return
    unloaded = sorted(available - set(getattr(manager, "plugins", {}) or {}))
    if unloaded:
        notes.append(
            "plugin entry points present but not loaded: "
            + ", ".join(unloaded)
            + " (loading stays explicit — /plugin enable <name>)"
        )


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

def _discover_skill_catalog(workdir: Path) -> tuple[list[Any], dict[str, str]]:
    """Discover the nested-``SKILL.md`` catalog and its snapshot for *workdir*."""
    from chimera.skills.discovery import discover_all_skills

    skills = discover_all_skills(str(workdir))
    return skills, skill_state(skills)


def _refresh_skill_commands(registry: Any, workdir: Path) -> tuple[int, str | None]:
    """Rebind the flat ``*.md`` skill commands into the live command registry.

    Loads a throwaway registry from disk, drops the live registry's stale
    ``source="skill"`` entries, and re-registers only the fresh skill
    commands — builtin commands are deliberately not (re)installed here so a
    resync never changes which non-skill commands exist.

    Args:
        registry: The live :class:`~chimera.commands.registry.CommandRegistry`.
        workdir: Project root for the project-scope skill store.

    Returns:
        ``(count, error)`` — invocable skill commands after the refresh, and
        an error string when the refresh failed (registry left as it was
        except for already-removed stale entries being restored is *not*
        attempted; on failure nothing was removed because removal happens
        after a successful load).
    """
    from chimera.commands.registry import CommandRegistry

    staging = CommandRegistry()
    try:
        _run_blocking(staging.load_all(Path(workdir)))
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return 0, str(exc) or type(exc).__name__
    fresh = [
        cmd
        for cmd in staging.list_commands(include_hidden=True)
        if getattr(cmd, "source", "") == "skill"
    ]
    registry.unregister_source("skill")
    for cmd in fresh:
        registry.register(cmd)
    return len(fresh), None


# ---------------------------------------------------------------------------
# Entry point: assembled stack
# ---------------------------------------------------------------------------

def resync_agent(
    agent: Any,
    *,
    workdir: str | Path | None = None,
    plugin_manager: Any = None,
) -> ResyncReport:
    """Re-discover and rebind resources for a live assembled agent.

    The seam behind ``CodingAgent.resync_resources()`` /
    ``AgentDriver.resync_resources()`` — and therefore behind ``/resync`` in
    the TUI, the assembled REPL, and the embed surface.

    What it rebinds, per kind:

    * **skills** — re-walks the nested ``SKILL.md`` catalog
      (:func:`~chimera.skills.discovery.discover_all_skills`) and installs
      the refreshed catalog as the agent's skills prompt section; because
      the assembled prompt is reassembled every turn, the refreshed catalog
      reaches the next turn of the *current* conversation. The flat ``*.md``
      skill commands (the ``skill`` tool's registry) are re-read too.
    * **agents** — re-scans the file-based agent-definition catalog
      (:class:`~chimera.agents.loader.AgentLoader`) and reports the diff;
      definitions are re-read at each invocation, so the reported catalog is
      exactly what the next ``/subagent`` or spawner call sees.
    * **plugins** — hot-swaps every loaded plugin on the attached manager
      (:func:`resync_plugins`), then rebinds plugin-contributed tools into
      the live tool list.
    * **interceptors** — two paths, composed. Chains registered on the
      shipped :class:`~chimera.plugins.registry.PluginExtensionRegistry`
      already ride the host's per-turn merge (plugin chains first, host
      chains last), so after the reload above the next turn enforces
      exactly the reloaded chains; this seam diffs them **per seam**
      (``"<seam>:<name>"`` added / removed / refreshed) and reports a
      census note, never binding them a second time. Interceptor surfaces
      a *third-party* registry exposes still bind generically
      (:func:`_collect_plugin_interceptors`) — ahead of the
      constructor-supplied chain, the same plugin-first / host-last /
      host-final-say rule as the per-turn merge — with registry-carried
      duplicates excluded on hosts that merge per turn.

    Args:
        agent: The :class:`~chimera.assembly.coding_agent.CodingAgent` (or a
            structurally compatible target).
        workdir: Project root; defaults to the agent's ``_project_dir``.
        plugin_manager: Plugin manager override; defaults to the manager
            attached via ``agent.attach_plugin_manager``.

    Returns:
        The :class:`ResyncReport` (refused, with nothing touched, when the
        agent is mid-turn).
    """
    if getattr(agent, "_turn_active", False):
        return ResyncReport(refused=True, reason=BUSY_MESSAGE)

    report = ResyncReport()
    wd = Path(workdir or getattr(agent, "_project_dir", None) or ".")
    manager = (
        plugin_manager
        if plugin_manager is not None
        else getattr(agent, "_plugin_manager", None)
    )

    # -- plugins (first: their tools/interceptors bind below) ---------------
    plugins_delta = resync_plugins(manager)
    report.deltas.append(plugins_delta)
    if manager is None:
        report.notes.append(
            "no plugin manager attached — plugins were not scanned "
            "(attach one via agent.attach_plugin_manager)"
        )
    else:
        tools_delta = _bind_plugin_tools(agent, manager)
        if tools_delta.changed:
            report.deltas.append(tools_delta)

    # -- interceptors: typed path for the shipped registry ------------------
    # Chains plugins register on chimera.plugins.registry are merged into
    # every assembled turn by the host itself (plugin chains first, host
    # chains last), so after the reload above the next turn's merge already
    # carries exactly the reloaded chains. Resync's job for them is honest
    # per-seam accounting — never a second binding, which would run every
    # chain twice.
    registry_chains = _registry_interceptor_chains()
    inter_delta = _diff_interceptor_chains(
        getattr(agent, "_resync_interceptor_state", None), registry_chains,
    )
    agent._resync_interceptor_state = registry_chains
    host_merges_per_turn = hasattr(agent, "_effective_interceptors")

    if manager is not None:
        # Generic fallback: interceptor surfaces third-party registries
        # expose. On hosts that merge the shipped registry per turn,
        # registry-carried callables republished on a generic surface are
        # excluded — they are already live once.
        chain, bound, opaque = _collect_plugin_interceptors(
            manager,
            exclude=registry_chains if host_merges_per_turn else None,
        )
        _bind_interceptors(agent, chain)
        if bound:
            inter_delta.refreshed.append(f"{bound} bound")
        if opaque:
            inter_delta.failed.append(
                ("opaque", f"{opaque} contribution(s) in a shape this seam cannot bind")
            )
    if inter_delta.changed:
        report.deltas.append(inter_delta)
    census = _interceptor_census(registry_chains)
    if census:
        report.notes.append(
            f"plugin-registered interceptors: {census} — "
            + (
                "merged into every turn, ahead of host chains"
                if host_merges_per_turn
                else "counted only; this target does not expose the per-turn merge"
            )
        )
    if manager is not None:
        _plugin_surface_notes(manager, report.notes)
        _discover_unloaded_note(manager, report.notes)

    # -- skills -------------------------------------------------------------
    skills_delta = KindDelta(kind="skills")
    try:
        skills, snapshot = _discover_skill_catalog(wd)
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        skills_delta.failed.append(("discovery", str(exc) or type(exc).__name__))
    else:
        previous = getattr(agent, "_resync_skill_state", None)
        first = previous is None
        added, removed, refreshed = _diff_named(previous, snapshot)
        skills_delta.added, skills_delta.removed, skills_delta.refreshed = (
            added, removed, refreshed,
        )
        agent._resync_skill_state = snapshot
        from chimera.skills.discovery import format_skills_for_prompt

        section = format_skills_for_prompt(skills)
        if hasattr(agent, "_skills_prompt_section"):
            agent._skills_prompt_section = ("\n\n" + section) if section else ""
        if first and snapshot:
            report.notes.append(
                f"first resync: {len(snapshot)} skill(s) bound to the prompt catalog"
            )
        registry = getattr(agent, "_command_registry", None)
        if registry is not None and hasattr(registry, "unregister_source"):
            count, err = _refresh_skill_commands(registry, wd)
            if err is not None:
                # Auxiliary surface: its absence (e.g. optional yaml dep
                # missing) must not mark the whole resync failed — the
                # primary SKILL.md catalog above already refreshed.
                report.notes.append(f"skill-command refresh skipped: {err}")
            elif count:
                report.notes.append(
                    f"invocable skill commands (flat *.md): {count} — live in the skill tool"
                )
    report.deltas.append(skills_delta)

    # -- agents -------------------------------------------------------------
    agents_delta = KindDelta(kind="agents")
    try:
        agent_snapshot = _agent_def_state(wd)
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        agents_delta.failed.append(("discovery", str(exc) or type(exc).__name__))
    else:
        previous = getattr(agent, "_resync_agent_state", None)
        added, removed, refreshed = _diff_named(previous, agent_snapshot)
        agents_delta.added, agents_delta.removed, agents_delta.refreshed = (
            added, removed, refreshed,
        )
        agent._resync_agent_state = agent_snapshot
    report.deltas.append(agents_delta)

    # -- honesty ------------------------------------------------------------
    report.notes.append(
        "system prompt is reassembled every turn — the refreshed skill catalog "
        "reaches the next turn of this conversation"
    )
    report.notes.append(
        "agent definitions are re-read at each invocation; the catalog above is "
        "what the next use sees"
    )
    return report


# ---------------------------------------------------------------------------
# Entry point: classic REPL session
# ---------------------------------------------------------------------------

def resync_session(session: Any, env: Any = None) -> ResyncReport:
    """Re-discover and rebind resources for a classic-REPL ``Session``.

    The strongest rebind the classic stack's shape allows:

    * **plugins** — hot-swaps every plugin loaded through ``/plugin`` (the
      manager cached on ``session._plugin_manager``), then re-installs
      plugin-contributed slash commands into the live REPL dispatch registry.
    * **skills** — re-walks the ``SKILL.md`` catalog. When the REPL recorded
      its base prompt (``session._system_base``, stashed at startup by
      ``chimera code``) the live agent's system prompt is rebuilt in place —
      the refreshed catalog applies from the next turn of *this* session.
      Without the stash the prompt genuinely cannot be rebuilt mid-session,
      and the report says so instead of pretending.
    * **agents** — re-scans the definition catalog and reports the diff
      (``/subagent`` re-reads definitions per call, so the catalog is live
      by construction).

    Args:
        session: The live REPL session (duck-typed).
        env: The REPL environment (its ``workdir`` anchors discovery).

    Returns:
        The :class:`ResyncReport` (refused when the session exposes an
        active-turn flag and it is set).
    """
    if getattr(session, "_turn_active", False):
        return ResyncReport(refused=True, reason=BUSY_MESSAGE)

    import os

    report = ResyncReport()
    wd = Path(getattr(env, "workdir", None) or os.getcwd())

    # -- plugins ------------------------------------------------------------
    manager = getattr(session, "_plugin_manager", None)
    plugins_delta = resync_plugins(manager)
    report.deltas.append(plugins_delta)
    if manager is None:
        report.notes.append(
            "no plugins loaded — /plugin enable <name> loads one; "
            "/resync then hot-swaps it"
        )
    else:
        _plugin_surface_notes(manager, report.notes)
        _discover_unloaded_note(manager, report.notes)
        try:
            from chimera.plugins.ui import install_into_repl

            installed = install_into_repl()
            if installed:
                report.notes.append(
                    "plugin commands re-installed: "
                    + ", ".join(f"/{n}" for n in installed)
                )
        except Exception as exc:  # noqa: BLE001 - best-effort surface refresh
            report.notes.append(f"plugin command re-install failed: {exc}")

    # -- skills -------------------------------------------------------------
    skills_delta = KindDelta(kind="skills")
    section = ""
    snapshot: dict[str, str] | None = None
    try:
        skills, discovered = _discover_skill_catalog(wd)
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        skills_delta.failed.append(("discovery", str(exc) or type(exc).__name__))
    else:
        from chimera.skills.discovery import format_skills_for_prompt

        snapshot = discovered
        section = format_skills_for_prompt(skills)
        previous = getattr(session, "_skills_state", None)
        added, removed, refreshed = _diff_named(previous, discovered)
        skills_delta.added, skills_delta.removed, skills_delta.refreshed = (
            added, removed, refreshed,
        )
        session._skills_state = discovered
    report.deltas.append(skills_delta)

    base = getattr(session, "_system_base", None)
    agent = getattr(session, "_agent", None) or getattr(session, "agent", None)
    if snapshot is not None and isinstance(base, str) and agent is not None and hasattr(agent, "prompt"):
        try:
            from chimera.core.prompt import Prompt

            rebuilt = base + ("\n\n" + section if section else "")
            agent.prompt = Prompt.from_string(rebuilt)
            report.notes.append(
                "system prompt rebuilt in place — the refreshed skill catalog "
                "applies from the next turn of this session"
            )
        except Exception as exc:  # noqa: BLE001 - the swap is best-effort
            report.notes.append(f"system prompt rebuild failed: {exc}")
    else:
        report.notes.append(
            "system prompt was fixed at session start — refreshed skills apply "
            "to new sessions (this session did not record its base prompt)"
        )

    # -- agents -------------------------------------------------------------
    agents_delta = KindDelta(kind="agents")
    try:
        agent_snapshot = _agent_def_state(wd)
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        agents_delta.failed.append(("discovery", str(exc) or type(exc).__name__))
    else:
        previous = getattr(session, "_agent_defs_state", None)
        added, removed, refreshed = _diff_named(previous, agent_snapshot)
        agents_delta.added, agents_delta.removed, agents_delta.refreshed = (
            added, removed, refreshed,
        )
        session._agent_defs_state = agent_snapshot
    report.deltas.append(agents_delta)
    report.notes.append(
        "agent definitions are re-read at each invocation; the catalog above is "
        "what the next use sees"
    )
    return report
