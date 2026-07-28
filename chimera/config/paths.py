"""The path registry: one declared truth for every on-disk store Chimera owns.

Before this module every writer hand-built its own ``Path.home() / ".chimera"
/ …``. Ninety-odd such constructions across sixty files meant no component
could answer three questions the owner actually asks: *where does Chimera keep
my data*, *what is safe to reclaim*, and *what on disk is nobody's*. A 2.0 GB
orphaned checkpoint tree sat undetected for four months because nothing was in
a position to notice it (spec: ``docs/specs/storage-and-experiments.md``).

The fix is a declared registry. Every store is a :class:`Store` row here, and
callers reach it through :func:`store_path` / :func:`chimera_home` /
:func:`project_state_dir` rather than composing paths themselves. A directory
that is not declared here does not belong to Chimera — which is precisely what
lets ``chimera doctor`` (M2) call it an orphan, and what makes it structurally
impossible for ``chimera gc`` to delete something the registry never named.

Root resolution, highest precedence first:

1. ``$CHIMERA_HOME`` — an explicit root (embedders, CI sandboxes, tests).
2. ``[storage] root`` in the one config chain (XDG < user < project), read via
   :func:`chimera.config.user_config.load_storage_config`.
3. ``~/.chimera`` — the historical default.

With neither the environment variable nor a config file present, every
accessor resolves byte-identically to where the data already lives; adopting
this module is a no-op for an unconfigured machine.

**Config discovery is deliberately *not* relocated by the root.** The chain
that supplies ``[storage] root`` is anchored on :func:`user_scope_dir`
(``<home>/.chimera``), because a root read from a file inside the root it
declares would be circular. The one consequence worth stating plainly: with
``[storage] root`` set, ``config.toml`` still lives at ``~/.chimera`` while the
stores live under the configured root.

Retention (``[storage.<name>] retain / max-age-days``) is read here and
exposed as :class:`StoreRetention`; nothing in this module prunes anything.
``chimera gc`` (M2) is the only consumer that acts on it, and it is dry-run
first. Stores flagged ``prunable=False`` return an inactive policy no matter
what a config file says — datasets and synthesised model artifacts cannot be
made reclaimable by a typo.

Stdlib only, per the zero-dependency-core rule.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "Store",
    "StoreRetention",
    "UnknownStore",
    "all_stores",
    "chimera_home",
    "get_store",
    "project_state_dir",
    "store_path",
    "store_retention",
    "user_scope_dir",
]

#: Environment override for the storage root. Wins over ``[storage] root``.
HOME_ENV = "CHIMERA_HOME"

#: The directory name Chimera uses in both scopes (``~/.chimera``,
#: ``<project>/.chimera``).
STATE_DIRNAME = ".chimera"

#: Valid :attr:`Store.scope` values.
SCOPES: tuple[str, ...] = ("user", "project")


class UnknownStore(KeyError):
    """Raised when a store name is not declared in the registry."""


@dataclass(frozen=True)
class Store:
    """One declared on-disk store.

    Attributes:
        name: Registry key, unique across both scopes. Project-scope stores
            carry a ``project-`` prefix so a bare name is never ambiguous
            (the spec writes these as ``project:state``; the registry key is
            ``project-state``).
        scope: ``"user"`` (under :func:`chimera_home`) or ``"project"``
            (under ``<project>/.chimera``).
        rel: Path relative to the scope root. Empty means the scope root
            itself.
        writer: The module that owns writes here, for ``doctor``'s report.
        prunable: Whether ``[storage.<name>]`` retention may apply at all.
            ``False`` is a structural guarantee, not a default: no retention
            config can make the store reclaimable.
        note: Anything a reader needs that the columns cannot say — including,
            deliberately, "no writer found" where that is the truth.
        env: Optional environment variable that relocates *this store alone*,
            checked before the scope root. Kept for backwards compatibility
            with overrides that shipped before the registry existed.
    """

    name: str
    scope: str
    rel: str
    writer: str
    prunable: bool
    note: str = ""
    env: str | None = None

    @property
    def label(self) -> str:
        """The ``scope:name`` label the spec and ``doctor`` use."""
        return f"{self.scope}:{self.name}"


# The registry. Rows are data: adding a store is a row, not a code path.
#
# Sources: the migration table in docs/specs/storage-and-experiments.md, plus
# the M1 writer sweep (2026-07-27) which found nineteen further directories the
# code can create that the table had not enumerated. They are declared here so
# doctor's orphan detection stays truthful — an undeclared directory is
# reported as unclaimed, and silently omitting a real writer would manufacture
# a false orphan.
_STORES: tuple[Store, ...] = (
    # -- user scope: the spec's migration table -----------------------------
    Store(
        name="datasets",
        scope="user",
        rel="datasets",
        writer="chimera/eval/datasets.py",
        prunable=False,
        note=(
            "Never prunable: deliberately staged benchmark inputs. Re-fetching "
            "is expensive and, for pinned revisions, not always possible."
        ),
        env="CHIMERA_DATASETS_DIR",
    ),
    Store(
        name="cohorts",
        scope="user",
        rel="cohorts",
        writer="chimera/tui/cohort.py",
        prunable=True,
        note=(
            "Retention predates the registry: [tui.cohorts] is still read as a "
            "legacy alias when [storage.cohorts] is absent."
        ),
    ),
    Store(
        name="sessions",
        scope="user",
        rel="sessions",
        writer="chimera/cli/code.py",
        prunable=True,
        note="One JSONL per workdir hash; also read by the codename CLIs.",
    ),
    Store(
        name="eventlog",
        scope="user",
        rel="eventlog",
        writer="chimera/sessions/eventlog/",
        prunable=True,
        note="Append-only event-sourced session logs, shared by every CLI.",
    ),
    Store(
        name="history",
        scope="user",
        rel="history",
        writer="chimera/cli/code.py",
        prunable=False,
        note=(
            "M1 sweep: writer is the REPL's readline setup. Contrary to the "
            "spec's table this is a single file, not a directory, so "
            "entry-count retention cannot apply; readline caps it at 1000 "
            "lines itself."
        ),
    ),
    Store(
        name="projects",
        scope="user",
        rel="projects",
        writer="chimera/tools/todo.py",
        prunable=True,
        note=(
            "M1 sweep: writer is the todo tool's user-scope fallback — one "
            "<sha256(cwd)[:16]>/todo.json per project. Entries for deleted "
            "projects are never reclaimed by their writer."
        ),
    ),
    Store(
        name="function_synthesis",
        scope="user",
        rel="function_synthesis",
        writer="chimera/function_synthesis/",
        prunable=False,
        note=(
            "Never prunable: base models, compiled .chi bundles, the ONNX "
            "cache, and credentials.json. $CHIMERA_FS_HOME relocates it."
        ),
        env="CHIMERA_FS_HOME",
    ),
    Store(
        name="tasks",
        scope="user",
        rel="tasks",
        writer="chimera/tools/task_tool.py",
        prunable=True,
        note="M1 sweep: writer is the task tool's background-agent output dir.",
    ),
    Store(
        name="experiment-runs",
        scope="user",
        rel="experiment-runs",
        writer="scripts/experiments/",
        prunable=True,
        note=(
            "$CHIMERA_PB_RUNS overrides the pb-runs *subtree* only, not this "
            "root — it predates the registry and the drivers still read it. "
            "The M4 toolkit becomes the writer."
        ),
    ),
    # -- user scope: found by the M1 writer sweep, absent from the spec -----
    Store(
        name="exports",
        scope="user",
        rel="exports",
        writer="chimera/sessions/share.py",
        prunable=True,
        note="Exported transcripts (also written by badger's /export).",
    ),
    Store(
        name="shares",
        scope="user",
        rel="shares",
        writer="chimera/otter/share_cmd.py",
        prunable=True,
        note="Share bundles; the otter/shrew/stoat/weasel CLIs all read it.",
    ),
    Store(
        name="snapshots",
        scope="user",
        rel="snapshots",
        writer="chimera/otter/snapshot.py",
        prunable=True,
    ),
    Store(
        name="worktrees",
        scope="user",
        rel="worktrees",
        writer="chimera/otter/worktree.py",
        prunable=True,
        note=(
            "Git worktrees: reclaiming one needs `git worktree remove`, not a "
            "tree delete, or the parent repo keeps stale admin entries."
        ),
    ),
    Store(
        name="teams",
        scope="user",
        rel="teams",
        writer="chimera/cli/agent_teams.py",
        prunable=True,
        note="$CHIMERA_TEAMS_HOME overrides the teams root for a single run.",
    ),
    Store(
        name="plans",
        scope="user",
        rel="plans",
        writer="chimera/stoat/plan_mode.py",
        prunable=True,
    ),
    Store(
        name="cache",
        scope="user",
        rel="cache",
        writer="chimera/skills/discovery.py",
        prunable=True,
        note="Derived data only — safe to lose, rebuilt on demand.",
    ),
    Store(
        name="cron",
        scope="user",
        rel="cron",
        writer="chimera/tools/cron_tools.py",
        prunable=False,
        note=(
            "Scheduled jobs: pruning would silently unschedule live work. "
            "$CHIMERA_CRON_DIR overrides the directory for a single run."
        ),
    ),
    Store(
        name="learning",
        scope="user",
        rel="learning",
        writer="chimera/learning/store.py",
        prunable=False,
        note="SQLite learning store; retention belongs to the DB, not the tree.",
    ),
    Store(
        name="tokens",
        scope="user",
        rel="tokens",
        writer="chimera/mcp/oauth.py",
        prunable=False,
        note="OAuth credentials — never lifecycle-managed.",
    ),
    Store(
        name="run",
        scope="user",
        rel="run",
        writer="chimera/otter/server_pidfile.py",
        prunable=False,
        note="Live pidfiles; the server owns staleness detection.",
    ),
    Store(
        name="agents",
        scope="user",
        rel="agents",
        writer="chimera/agents/team_roles.py",
        prunable=False,
        note="User-authored agent definitions — input, not output.",
    ),
    Store(
        name="skills",
        scope="user",
        rel="skills",
        writer="chimera/skills/discovery.py",
        prunable=False,
        note="User-authored skills — input, not output.",
    ),
    Store(
        name="completion",
        scope="user",
        rel="completion",
        writer="chimera/cli/completion.py",
        prunable=False,
        note=(
            "Installed shell-completion scripts sourced at login (dir 0o700). "
            "Anchored on the real home rather than the storage root, because "
            "the absolute path is baked into ~/.bashrc / ~/.zshrc at install "
            "time; relocating the root would not move an already-wired script."
        ),
    ),
    Store(
        name="profiles",
        scope="user",
        rel="profiles",
        writer="chimera/ferret/cli.py",
        prunable=False,
        note="Authored TOML profile overlays for --profile — input, not output.",
    ),
    Store(
        name="badger",
        scope="user",
        rel="badger",
        writer="chimera/badger/slash.py",
        prunable=False,
        note="CLI-scoped state (memory.md).",
    ),
    Store(
        name="ferret",
        scope="user",
        rel="ferret",
        writer="chimera/ferret/subcommands/mcp_manage.py",
        prunable=False,
        note="CLI-scoped config (mcp_servers.json).",
    ),
    Store(
        name="shrew",
        scope="user",
        rel="shrew",
        writer="chimera/shrew/model_profiles.py",
        prunable=False,
        note="CLI-scoped config (settings.json).",
    ),
    Store(
        name="stoat",
        scope="user",
        rel="stoat",
        writer="chimera/stoat/hooks.py",
        prunable=False,
        note="CLI-scoped config (hooks.json).",
    ),
    # -- project scope: <project>/.chimera ----------------------------------
    Store(
        name="project-state",
        scope="project",
        rel="",
        writer="chimera/commands/builtins.py",
        prunable=True,
        note=(
            "The project state dir itself. Holds settings.json, todo.json, "
            "rules.md, instructions.md and the sub-stores below."
        ),
    ),
    Store(
        name="project-sessions",
        scope="project",
        rel="sessions",
        writer="chimera/assembly/coding_agent.py",
        prunable=True,
        note=(
            "Per-project transcripts, written when AssemblyConfig.transcripts "
            "is on. Distinct from the user-scope `sessions` store, which "
            "chimera/cli/code.py writes — the spec's table attributes both to "
            "chimera/cli/code.py; the M1 sweep found the project-scope writer "
            "is the assembled CodingAgent's TranscriptStorage."
        ),
    ),
    Store(
        name="project-agents",
        scope="project",
        rel="agents",
        writer="chimera/agents/team_roles.py",
        prunable=False,
        note="Project-authored agent/role definitions — input, not output.",
    ),
    Store(
        name="project-checkpoints",
        scope="project",
        rel="checkpoints",
        writer="chimera/checkpoints.py",
        prunable=True,
        note=(
            "No writer as of 2026-07-27: CheckpointManager still takes an "
            "explicit directory and the orphaned .chimera_checkpoints/ tree "
            "was archived out. M3 points the writer here with retain-N."
        ),
    ),
    Store(
        name="project-snapshots",
        scope="project",
        rel="snapshots",
        writer="chimera/commands/builtins.py",
        prunable=True,
    ),
    Store(
        name="project-memory",
        scope="project",
        rel="memory",
        writer="chimera/core/memory.py",
        prunable=False,
        note="MEMORY.md — authored notes, not run output.",
    ),
    Store(
        name="project-prompts",
        scope="project",
        rel="prompts",
        writer="chimera/core/prompt_template.py",
        prunable=False,
        note="Authored prompt templates — read-only to Chimera.",
    ),
    Store(
        name="project-skills",
        scope="project",
        rel="skills",
        writer="chimera/skills/discovery.py",
        prunable=False,
        note="Authored skills — read-only to Chimera.",
    ),
)

_BY_NAME: dict[str, Store] = {store.name: store for store in _STORES}

#: Retention tables that predate ``[storage.<name>]``, read when the new
#: spelling is absent. Maps a store name to its ``(section, table)`` path in
#: the merged config.
_LEGACY_RETENTION: dict[str, tuple[str, str]] = {"cohorts": ("tui", "cohorts")}


def all_stores() -> tuple[Store, ...]:
    """Return every declared store, user scope first.

    Returns:
        The registry, in declaration order. The tuple is the registry itself —
        rows are frozen dataclasses, so callers cannot mutate the truth.
    """
    return _STORES


def get_store(name: str) -> Store:
    """Look up one store by registry key.

    Args:
        name: Registry key (see :attr:`Store.name`).

    Returns:
        The declared :class:`Store`.

    Raises:
        UnknownStore: If no store is declared under that name. Unknown names
            fail loudly on purpose: a typo must never silently resolve to a
            path that a later ``gc`` could act on.
    """
    try:
        return _BY_NAME[name]
    except KeyError:
        known = ", ".join(sorted(_BY_NAME))
        raise UnknownStore(f"unknown store {name!r}; declared stores: {known}") from None


def user_scope_dir(home: str | os.PathLike[str] | None = None) -> Path:
    """Return ``<home>/.chimera`` — the fixed anchor for config discovery.

    This is *not* :func:`chimera_home`. It ignores ``$CHIMERA_HOME`` and
    ``[storage] root`` because it is where those settings are read *from*;
    resolving it through them would be circular. Use :func:`chimera_home` for
    everything else.

    Args:
        home: Home-directory override (tests). Defaults to :meth:`Path.home`,
            resolved at call time so a monkeypatched home is honored.

    Returns:
        The user-scope Chimera directory.
    """
    base = Path(home) if home is not None else Path.home()
    return base / STATE_DIRNAME


def _root_from_config() -> Path | None:
    """Return ``[storage] root`` from the config chain, or ``None``.

    Every failure mode — no config, a broken config, an unreadable cwd — maps
    to ``None`` so that resolving a path can never be what takes a process
    down.
    """
    try:
        from chimera.config.user_config import load_storage_config

        raw = load_storage_config().get("root")
    except Exception:  # noqa: BLE001 — config discovery is best-effort.
        return None
    if isinstance(raw, str) and raw.strip():
        return Path(raw.strip()).expanduser()
    return None


def chimera_home() -> Path:
    """Return the user-scope storage root.

    Precedence: ``$CHIMERA_HOME`` → ``[storage] root`` in the config chain
    (XDG < user < project) → ``~/.chimera``. Resolution happens on every call,
    never at import, so tests that patch the home directory or set the
    environment variable are honored by code that was imported earlier.

    Returns:
        The storage root. It is not created and need not exist.
    """
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override).expanduser()
    configured = _root_from_config()
    if configured is not None:
        return configured
    return user_scope_dir()


def project_state_dir(project: str | os.PathLike[str]) -> Path:
    """Return ``<project>/.chimera`` — the project-scope state root.

    Project state stays with the project by definition, so this is unaffected
    by ``$CHIMERA_HOME`` and ``[storage] root``.

    Args:
        project: The project root.

    Returns:
        The project's Chimera state directory (not created).
    """
    return Path(project) / STATE_DIRNAME


def store_path(name: str, project: str | os.PathLike[str] | None = None) -> Path:
    """Resolve one store to an absolute path.

    Args:
        name: Registry key (see :func:`all_stores`).
        project: Project root for project-scope stores. Defaults to the
            current working directory. Ignored by user-scope stores.

    Returns:
        The store's directory (or file, for the ``history`` store). Nothing is
        created — callers ``mkdir`` when they are about to write.

    Raises:
        UnknownStore: If the name is not declared.
    """
    store = get_store(name)
    if store.env:
        override = os.environ.get(store.env)
        if override:
            return Path(override).expanduser()
    if store.scope == "project":
        root = project_state_dir(project if project is not None else Path.cwd())
    else:
        root = chimera_home()
    return root / store.rel if store.rel else root


@dataclass(frozen=True)
class StoreRetention:
    """A store's resolved retention policy.

    Both knobs are optional and **off by default**: no configuration means
    nothing is ever reclaimed, matching the cohort precedent and the spec's
    data-preserving rule (nobody loses work they did not ask to discard).

    Attributes:
        retain: Keep at most this many of the newest entries; ``None`` keeps
            any number.
        max_age_days: Drop entries older than this; ``None`` imposes no age
            limit.
    """

    retain: int | None = None
    max_age_days: float | None = None

    @property
    def active(self) -> bool:
        """Whether any knob is set (an inactive policy reclaims nothing)."""
        return self.retain is not None or self.max_age_days is not None


def _positive_int(value: Any) -> int | None:
    """Coerce a config value to a positive int, else ``None`` (knob disabled)."""
    if isinstance(value, bool):  # bool is an int subclass — reject it explicitly
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _positive_float(value: Any) -> float | None:
    """Coerce a config value to a positive float, else ``None`` (knob disabled)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _retention_from_table(table: Any) -> StoreRetention:
    """Parse one ``retain`` / ``max-age-days`` table (dash or underscore keys).

    Unset, non-positive, or malformed values disable that knob — the safe
    default is "reclaim nothing" unless a valid positive limit is given.
    """
    if not isinstance(table, dict):
        return StoreRetention()
    return StoreRetention(
        retain=_positive_int(table.get("retain")),
        max_age_days=_positive_float(
            table.get("max-age-days", table.get("max_age_days"))
        ),
    )


def store_retention(
    name: str,
    project_dir: str | os.PathLike[str] | None = None,
    *,
    home: str | os.PathLike[str] | None = None,
) -> StoreRetention:
    """Resolve a store's retention policy from the config chain.

    Reads ``[storage.<name>]`` across XDG < user < project. A store whose name
    contains a dash also answers to the underscore spelling. Stores declared
    ``prunable=False`` always return an inactive policy — that is the
    structural guarantee, not a default, so no config file can make datasets
    or synthesised model artifacts reclaimable.

    Legacy aliases are honored when the new spelling is absent: cohort
    retention still reads ``[tui.cohorts]``, so configs written before the
    registry keep working unchanged.

    Args:
        name: Registry key.
        project_dir: Project root for the project-scope config lookup
            (default: cwd).
        home: Home-directory override (tests).

    Returns:
        The resolved policy; inactive on any failure, since config discovery
        must never block a run.

    Raises:
        UnknownStore: If the name is not declared.
    """
    store = get_store(name)
    if not store.prunable:
        return StoreRetention()
    try:
        from chimera.config.user_config import load_section, load_storage_config

        storage = load_storage_config(project_dir, home=home)
        table = storage.get(name)
        if table is None and "-" in name:
            table = storage.get(name.replace("-", "_"))
        if table is None:
            legacy = _LEGACY_RETENTION.get(name)
            if legacy is not None:
                section, key = legacy
                table = load_section(section, project_dir, home=home).get(key)
    except Exception:  # noqa: BLE001 — config discovery is best-effort.
        return StoreRetention()
    return _retention_from_table(table)
