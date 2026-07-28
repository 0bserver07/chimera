"""Storage inspection and the one retention engine (spec M2).

:mod:`chimera.config.paths` declares *where* every store lives. This module
answers the two questions that declaration makes answerable:

* **What is actually on disk?** :func:`report_stores` measures every declared
  store — size, entry count, newest/oldest age, configured retention — and
  :func:`find_orphans` reports every directory that the registry does *not*
  claim. ``chimera doctor`` renders both.
* **What may be reclaimed?** :func:`plan_gc` turns ``[storage.<name>]``
  retention into a list of :class:`PruneCandidate` rows, each carrying the rule
  that selected it. ``chimera gc`` prints them; only ``--apply`` calls
  :func:`apply_prune`.

Three properties are structural rather than conventional, because the incident
that motivated the spec was a 2.0 GB tree nobody was in a position to notice:

1. **The orphan scan covers project-root ``.chimera*`` siblings.** As first
   specified it walked ``chimera_home()`` and ``<proj>/.chimera`` only — and
   would have walked straight past ``<workdir>/.chimera_checkpoints``, which
   sits *beside* the project state dir, not inside it. The scan that exists to
   catch that tree was blind to exactly it.
2. **:func:`apply_prune` cannot name an unregistered path.** Every candidate is
   revalidated at delete time against the registry: the store must be declared
   and ``prunable``, and the path must be a direct child of the declared root.
   Validation runs over the whole batch *before* the first deletion, so a bad
   candidate aborts the run rather than half-completing it. There is no code
   path from a directory the registry does not name to a deletion.
3. **Retention is opt-in and there is one implementation of it.**
   :func:`select_for_prune` is the only place a "which entries go" decision is
   made; ``chimera/tui/cohort.py`` calls it rather than keeping its own copy.
   A store with no ``[storage.<name>]`` table yields no candidates, ever.

Deletion is never automatic: ``gc`` is dry-run by default and
``--archive <dir>`` relocates instead of removing, matching the owner's
standing rule (archive/relocate, never delete by default).

Stdlib only, per the zero-dependency-core rule.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from chimera.config.paths import (
    STATE_DIRNAME,
    Store,
    StoreRetention,
    all_stores,
    chimera_home,
    get_store,
    project_state_dir,
    store_path,
    store_retention,
)

__all__ = [
    "GcPlan",
    "GcSkip",
    "SKIP_EMPTY",
    "SKIP_NESTED_PREFIX",
    "SKIP_NOT_PRUNABLE",
    "SKIP_NO_RETENTION",
    "SKIP_REASONS",
    "Orphan",
    "PruneCandidate",
    "StoreEntry",
    "StoreReport",
    "apply_prune",
    "collect_entries",
    "find_orphans",
    "format_age",
    "format_size",
    "gc_skips",
    "plan_gc",
    "report_stores",
    "select_for_prune",
    "tree_size",
]


# ---------------------------------------------------------------------------
# Formatting helpers (shared by doctor and gc so their columns agree)
# ---------------------------------------------------------------------------

_UNITS: tuple[str, ...] = ("B", "kB", "MB", "GB", "TB", "PB")


def format_size(num_bytes: int) -> str:
    """Render a byte count in decimal (1000-based) units.

    Decimal rather than binary because that is the unit disk-usage
    conversations actually happen in ("the 2 GB checkpoint"); a report that
    silently says GiB invites the reader to compare it against a number that
    means something else.

    Args:
        num_bytes: The size in bytes.

    Returns:
        A short human string, e.g. ``"347.2 MB"``.
    """
    value = float(num_bytes)
    for unit in _UNITS:
        if abs(value) < 1000.0 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1000.0
    return f"{value:.1f} {_UNITS[-1]}"  # pragma: no cover - loop always returns


def format_age(days: float | None) -> str:
    """Render an age in days, or ``"-"`` when unknown.

    Args:
        days: Age in days, or ``None``.

    Returns:
        e.g. ``"12.4d"``, ``"3.1h"``, or ``"-"``.
    """
    if days is None:
        return "-"
    if days < 1.0:
        return f"{days * 24.0:.1f}h"
    return f"{days:.1f}d"


def _now(now: datetime | None) -> datetime:
    """Resolve the reference clock (injected in tests, UTC otherwise)."""
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


def tree_size(path: Path) -> tuple[int, int]:
    """Measure a file or directory tree without following symlinks.

    Symlinks are counted at their own (tiny) size and never traversed, so a
    link into a large tree cannot inflate a report or make the walk unbounded.
    Unreadable subtrees are skipped rather than raising — a diagnostic must
    still produce a report on a partially-permissioned directory.

    Args:
        path: File or directory to measure.

    Returns:
        ``(total_bytes, file_count)``. ``(0, 0)`` when *path* does not exist.
    """
    try:
        stat = path.lstat()
    except OSError:
        return (0, 0)
    if not path.is_dir() or path.is_symlink():
        return (int(stat.st_size), 1)

    total = 0
    files = 0
    stack: list[Path] = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return (total, files)


# ---------------------------------------------------------------------------
# Inventory: what is on disk under each declared store
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoreReport:
    """One declared store, measured.

    Attributes:
        store: The registry row.
        path: Where it resolves right now (env override included).
        exists: Whether that path is present on disk.
        is_file: True when the store is a single file (``history``).
        size_bytes: Total bytes under *path*.
        file_count: Files under *path*, recursively.
        entries: Immediate children — the unit retention counts. ``None``
            when the store is absent. Dot-entries are included here (this is
            an inventory); :func:`collect_entries` excludes them, because that
            is a *reclaim* list and temp/metadata files are not run output.
        newest_age_days: Age of the most recently modified immediate child.
        oldest_age_days: Age of the least recently modified immediate child.
        retention: The resolved policy; inactive means "keep forever".
        error: A read error, when the path exists but could not be listed.
    """

    store: Store
    path: Path
    exists: bool
    is_file: bool
    size_bytes: int
    file_count: int
    entries: int | None
    newest_age_days: float | None
    oldest_age_days: float | None
    retention: StoreRetention
    error: str = ""

    @property
    def retention_label(self) -> str:
        """``retain``/``max-age-days`` as one short cell, or ``"keep forever"``."""
        if not self.store.prunable:
            return "never prunable"
        bits: list[str] = []
        if self.retention.retain is not None:
            bits.append(f"retain={self.retention.retain}")
        if self.retention.max_age_days is not None:
            bits.append(f"max-age-days={self.retention.max_age_days:g}")
        return " ".join(bits) if bits else "keep forever"

    def to_dict(self) -> dict[str, object]:
        """Render as a JSON-safe dict for ``doctor --json``."""
        return {
            "store": self.store.name,
            "scope": self.store.scope,
            "label": self.store.label,
            "path": str(self.path),
            "writer": self.store.writer,
            "exists": self.exists,
            "is_file": self.is_file,
            "size_bytes": self.size_bytes,
            "size_human": format_size(self.size_bytes),
            "file_count": self.file_count,
            "entries": self.entries,
            "newest_age_days": self.newest_age_days,
            "oldest_age_days": self.oldest_age_days,
            "prunable": self.store.prunable,
            "retention": {
                "retain": self.retention.retain,
                "max_age_days": self.retention.max_age_days,
                "active": self.retention.active,
                "label": self.retention_label,
            },
            "note": self.store.note,
            "error": self.error,
        }


def inspect_store(
    store: Store,
    *,
    project: str | os.PathLike[str] | None = None,
    now: datetime | None = None,
) -> StoreReport:
    """Measure one declared store.

    Args:
        store: The registry row to measure.
        project: Project root for project-scope stores (default: cwd).
        now: Clock override for age computation.

    Returns:
        The populated :class:`StoreReport`. Never raises for a missing or
        unreadable path — a diagnostic that dies on one bad directory reports
        nothing about the other thirty-five.
    """
    path = store_path(store.name, project)
    ref = _now(now)
    try:
        retention = store_retention(store.name, project)
    except Exception:  # noqa: BLE001 — config discovery is best-effort.
        retention = StoreRetention()

    if not path.exists():
        return StoreReport(
            store=store,
            path=path,
            exists=False,
            is_file=False,
            size_bytes=0,
            file_count=0,
            entries=None,
            newest_age_days=None,
            oldest_age_days=None,
            retention=retention,
        )

    size_bytes, file_count = tree_size(path)
    if path.is_file():
        age = _mtime_age_days(path, ref)
        return StoreReport(
            store=store,
            path=path,
            exists=True,
            is_file=True,
            size_bytes=size_bytes,
            file_count=file_count,
            entries=1,
            newest_age_days=age,
            oldest_age_days=age,
            retention=retention,
        )

    ages: list[float] = []
    error = ""
    try:
        children = list(path.iterdir())
    except OSError as exc:
        children = []
        error = str(exc)
    for child in children:
        ages.append(_mtime_age_days(child, ref))
    return StoreReport(
        store=store,
        path=path,
        exists=True,
        is_file=False,
        size_bytes=size_bytes,
        file_count=file_count,
        entries=len(children),
        newest_age_days=min(ages) if ages else None,
        oldest_age_days=max(ages) if ages else None,
        retention=retention,
        error=error,
    )


def report_stores(
    *,
    project: str | os.PathLike[str] | None = None,
    now: datetime | None = None,
) -> list[StoreReport]:
    """Measure every declared store, in registry order (user scope first).

    Args:
        project: Project root for project-scope stores (default: cwd).
        now: Clock override for age computation.

    Returns:
        One :class:`StoreReport` per registry row — including absent stores,
        because "declared and empty" and "not declared" are different facts and
        the report must be able to say which.
    """
    root = Path(project) if project is not None else Path.cwd()
    return [inspect_store(s, project=root, now=now) for s in all_stores()]


def _mtime_age_days(path: Path, ref: datetime) -> float:
    """Age of *path* in days by mtime; ``0.0`` when it cannot be stat'd."""
    try:
        mtime = path.lstat().st_mtime
    except OSError:
        return 0.0
    return max(0.0, (ref.timestamp() - mtime) / 86400.0)


# ---------------------------------------------------------------------------
# Orphans: directories on disk that the registry does not claim
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Orphan:
    """A directory Chimera's registry does not claim.

    Attributes:
        path: The unclaimed directory.
        scope: ``"user"`` (under the storage root), ``"project"`` (under
            ``<proj>/.chimera``), or ``"project-root"`` (a ``.chimera*``
            sibling *beside* the project state dir).
        size_bytes: Total bytes underneath it.
        file_count: Files underneath it, recursively.
        reason: Why it is being reported, in one line.
    """

    path: Path
    scope: str
    size_bytes: int
    file_count: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        """Render as a JSON-safe dict."""
        return {
            "path": str(self.path),
            "scope": self.scope,
            "size_bytes": self.size_bytes,
            "size_human": format_size(self.size_bytes),
            "file_count": self.file_count,
            "reason": self.reason,
        }


def _claimed_names(scope: str, project: Path) -> set[str]:
    """First-level directory names the registry claims inside *scope*'s root.

    Both the declared ``rel`` and the currently-env-resolved location are
    claimed. A store relocated by ``$CHIMERA_DATASETS_DIR`` must not make its
    declared slot look unclaimed — the registry still names it.
    """
    root = chimera_home() if scope == "user" else project_state_dir(project)
    claimed: set[str] = set()
    for store in all_stores():
        if store.scope != scope or not store.rel:
            continue
        claimed.add(Path(store.rel).parts[0])
        resolved = store_path(store.name, project)
        try:
            rel = resolved.relative_to(root)
        except ValueError:
            continue
        if rel.parts:
            claimed.add(rel.parts[0])
    return claimed


def find_orphans(
    *,
    project: str | os.PathLike[str] | None = None,
) -> list[Orphan]:
    """Report every directory on disk that no registry row claims.

    Three locations are scanned:

    1. ``chimera_home()`` — user-scope stores.
    2. ``<project>/.chimera`` — project-scope stores.
    3. ``<project>/.chimera*`` **siblings** — the location the spec originally
       omitted. ``<workdir>/.chimera_checkpoints`` lives here, not inside the
       state dir, so a scan of (1) and (2) alone would miss the exact 2.0 GB
       tree this whole subsystem exists to surface.

    Only directories are reported. Loose files at a scope root are legitimate
    (``config.toml``, ``settings.json``, ``todo.json``, ``rules.md``), and
    flagging them would train the reader to ignore the section. When the two
    scope roots resolve to the same directory — running from ``$HOME``, or with
    ``$CHIMERA_HOME`` pointed inside the project — it is scanned once against
    the union of both vocabularies, so neither scope's stores are reported as
    the other's orphans.

    Args:
        project: Project root (default: cwd).

    Returns:
        Unclaimed directories, largest first. Empty when everything on disk is
        declared — which is the expected steady state.
    """
    root_project = Path(project) if project is not None else Path.cwd()
    found: list[Orphan] = []

    user_root = chimera_home()
    project_root = project_state_dir(root_project)
    user_claimed = _claimed_names("user", root_project)
    project_claimed = _claimed_names("project", root_project)

    if user_root == project_root:
        # Running from ``$HOME`` (or with ``CHIMERA_HOME`` pointed at the
        # project) makes one directory serve both scopes. Scanning it twice
        # with two half-vocabularies would report every user store as a
        # project orphan and vice versa, so a name claimed by *either* scope
        # counts, and the directory is walked once.
        scans = [("user", user_root, user_claimed | project_claimed)]
    else:
        scans = [
            ("user", user_root, user_claimed),
            ("project", project_root, project_claimed),
        ]

    for scope, root, claimed in scans:
        for entry in _child_dirs(root):
            if entry.name in claimed:
                continue
            size, files = tree_size(entry)
            found.append(
                Orphan(
                    path=entry,
                    scope=scope,
                    size_bytes=size,
                    file_count=files,
                    reason=f"no registry row claims {entry.name} under {root}",
                )
            )

    for entry in _child_dirs(root_project):
        if not entry.name.startswith(STATE_DIRNAME) or entry == project_root:
            continue
        size, files = tree_size(entry)
        found.append(
            Orphan(
                path=entry,
                scope="project-root",
                size_bytes=size,
                file_count=files,
                reason=(
                    f"{entry.name} sits beside {STATE_DIRNAME}, not inside it — "
                    "no registry row names this path"
                ),
            )
        )

    found.sort(key=lambda o: (-o.size_bytes, str(o.path)))
    return found


def _child_dirs(root: Path) -> list[Path]:
    """Immediate subdirectories of *root* (symlinks excluded), or ``[]``."""
    try:
        with os.scandir(root) as it:
            return sorted(
                Path(e.path) for e in it if e.is_dir(follow_symlinks=False)
            )
    except OSError:
        return []


# ---------------------------------------------------------------------------
# The retention engine — the one implementation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoreEntry:
    """One reclaimable unit inside a store (a session file, a cohort dir).

    Attributes:
        id: The entry's name, as the report and the caller's ``exclude`` set
            spell it.
        path: Absolute path to the entry.
        age_days: Age in days, by whatever clock the collector was given.
    """

    id: str
    path: Path
    age_days: float


@dataclass(frozen=True)
class PruneCandidate:
    """One entry retention selected, with the rule that selected it.

    Attributes:
        store: Registry key of the owning store. Revalidated against the
            registry in :func:`apply_prune`; an undeclared name is fatal.
        root: The store root the entry must be a direct child of.
        entry: The entry itself.
        rule: Human-readable reason, e.g. ``"retain=20 (position 21)"``.
        size_bytes: Bytes the entry occupies, for the dry-run total.
    """

    store: str
    root: Path
    entry: StoreEntry
    rule: str
    size_bytes: int = 0

    def to_dict(self) -> dict[str, object]:
        """Render as a JSON-safe dict."""
        return {
            "store": self.store,
            "root": str(self.root),
            "id": self.entry.id,
            "path": str(self.entry.path),
            "age_days": self.entry.age_days,
            "rule": self.rule,
            "size_bytes": self.size_bytes,
            "size_human": format_size(self.size_bytes),
        }


def _default_select(path: Path) -> bool:
    """Default reclaim unit: any immediate child that is not dot-prefixed.

    Dot-entries are skipped because atomic writers stage temp files as
    ``.<name>.tmp`` in the same directory; a reclaim pass must not race one.
    """
    return not path.name.startswith(".")


def collect_entries(
    root: Path,
    *,
    select: Callable[[Path], bool] | None = None,
    age_of: Callable[[Path, datetime], float] | None = None,
    order: str = "age",
    now: datetime | None = None,
) -> list[StoreEntry]:
    """List a store's reclaimable entries, **newest first**.

    Newest-first is the contract :func:`select_for_prune` depends on: it keeps
    the first ``retain`` positions unconditionally, so position 0 must be the
    entry a user would least expect to lose.

    Args:
        root: The store root to scan (immediate children only).
        select: Predicate deciding what counts as an entry. Default:
            :func:`_default_select` (non-dot children).
        age_of: ``(path, ref) -> days``. Default: mtime.
        order: ``"age"`` sorts by measured age (ties broken by descending id);
            ``"id"`` sorts by descending name, which is chronological for
            timestamp-prefixed ids and is what cohorts have always used.
        now: Clock override.

    Returns:
        The entries, newest first. Empty when *root* is not a directory.
    """
    if not root.is_dir():
        return []
    ref = _now(now)
    keep = select or _default_select
    age = age_of or _mtime_age_days

    entries: list[StoreEntry] = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []
    for child in children:
        try:
            if not keep(child):
                continue
        except OSError:
            continue
        entries.append(StoreEntry(id=child.name, path=child, age_days=age(child, ref)))

    if order == "id":
        entries.sort(key=lambda e: e.id, reverse=True)
    else:
        entries.sort(key=lambda e: (e.age_days, _reverse_str(e.id)))
    return entries


def _reverse_str(value: str) -> tuple[int, ...]:
    """Sort key that orders strings descending inside an ascending sort."""
    return tuple(-ord(ch) for ch in value)


def select_for_prune(
    entries: Sequence[StoreEntry],
    retention: StoreRetention | None,
    *,
    store: str,
    root: Path,
    exclude: Iterable[str] = (),
    measure: bool = True,
) -> list[PruneCandidate]:
    """Apply a retention policy to newest-first *entries*.

    The one place a "which entries go" decision is made. ``chimera gc`` and the
    cohort pruner both route through here, so there is a single set of rules to
    reason about and to test:

    * ``retain=N`` keeps the newest ``N`` positions as a hard floor; entries
      past that window are selected.
    * ``max-age-days=D`` selects anything older than ``D`` — but only outside
      the ``retain`` floor, so the two knobs compose as "keep N, and of the
      rest drop what is older than D".
    * Neither set selects nothing at all. Retention is opt-in.

    Ids in *exclude* are never selected, and still occupy their position in the
    ``retain`` window — the caller's live cohort is untouchable without
    silently promoting an older entry into the kept set.

    Args:
        entries: Newest-first entries (see :func:`collect_entries`).
        retention: The policy; ``None`` or inactive selects nothing.
        store: Registry key, recorded on each candidate and revalidated by
            :func:`apply_prune`.
        root: The store root; candidates must be direct children of it.
        exclude: Entry ids that must survive.
        measure: Compute each candidate's size (a tree walk). Pass ``False``
            when the caller only needs the ids.

    Returns:
        The selected candidates, newest-first within the store.
    """
    if retention is None or not retention.active:
        return []
    excluded = set(exclude)
    keep = retention.retain
    max_age = retention.max_age_days
    out: list[PruneCandidate] = []
    for position, entry in enumerate(entries):
        if entry.id in excluded:
            continue
        if keep is not None and position < keep:
            continue  # the retain floor: newest N are never selected
        if max_age is not None and entry.age_days > max_age:
            rule = f"max-age-days={max_age:g} (age {format_age(entry.age_days)})"
        elif max_age is None and keep is not None:
            rule = f"retain={keep} (position {position + 1})"
        else:
            continue
        size = tree_size(entry.path)[0] if measure else 0
        out.append(
            PruneCandidate(
                store=store, root=root, entry=entry, rule=rule, size_bytes=size
            )
        )
    return out


def _validate(candidate: PruneCandidate) -> None:
    """Re-derive a candidate's right to exist from the registry.

    Raises:
        UnknownStore: If the store name is not declared. This is the guarantee
            the spec asks for: an arbitrary directory cannot be laundered into
            a deletion by wrapping it in a candidate.
        ValueError: If the store is not prunable, or the path is not a direct
            child of the declared root.
    """
    store = get_store(candidate.store)  # raises UnknownStore
    if not store.prunable:
        raise ValueError(
            f"store {store.name!r} is declared prunable=False; "
            "no retention config can make it reclaimable"
        )
    path = candidate.entry.path
    if path == candidate.root or path.parent != candidate.root:
        raise ValueError(
            f"{path} is not a direct child of the {store.name!r} root "
            f"{candidate.root} — refusing to touch it"
        )


def apply_prune(
    candidates: Sequence[PruneCandidate],
    *,
    archive_to: Path | None = None,
) -> list[PruneCandidate]:
    """Act on *candidates* — the only destructive function in this module.

    Every candidate is validated against the registry **before the first
    deletion**, so a malformed batch aborts intact rather than half-applied.
    Individual filesystem failures afterwards are best-effort: a directory that
    vanished or is held by a concurrent reader is skipped, never fatal.

    Args:
        candidates: What to reclaim.
        archive_to: Relocate into this directory instead of deleting. The
            owner's standing rule prefers this; ``chimera gc --archive`` is how
            it is reached.

    Returns:
        The candidates actually acted on.

    Raises:
        UnknownStore: If any candidate names a store the registry does not
            declare. Nothing is deleted.
        ValueError: If any candidate is not a direct child of its declared
            store root, or its store is not prunable. Nothing is deleted.
    """
    for candidate in candidates:
        _validate(candidate)

    if archive_to is not None:
        archive_to.mkdir(parents=True, exist_ok=True)

    done: list[PruneCandidate] = []
    for candidate in candidates:
        path = candidate.entry.path
        try:
            if archive_to is not None:
                target = archive_to / candidate.store
                target.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(target / candidate.entry.id))
            elif path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError:
            continue  # best-effort: a racer or a vanished path is not fatal
        done.append(candidate)
    return done


# ---------------------------------------------------------------------------
# The whole-registry plan that ``chimera gc`` prints
# ---------------------------------------------------------------------------


#: Why ``plan_gc`` passed a store over. Constants rather than inline literals
#: so the reporter can group by reason without re-spelling the sentence.
SKIP_NOT_PRUNABLE = "never prunable (structural)"
SKIP_NO_RETENTION = "no retention configured"
SKIP_EMPTY = "nothing on disk"
SKIP_NESTED_PREFIX = "root contains the "

#: The grouped skip reasons, in report order.
SKIP_REASONS: tuple[str, ...] = (
    SKIP_NO_RETENTION,
    SKIP_NOT_PRUNABLE,
    SKIP_EMPTY,
)


@dataclass(frozen=True)
class GcSkip:
    """A store gc deliberately did not consider, and why.

    Made explicit so the dry-run report can say "cohorts: no retention
    configured" rather than silently omitting it — silence is what let the
    original problem grow.
    """

    store: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        """Render as a JSON-safe dict."""
        return {"store": self.store, "reason": self.reason}


def _resolve_all(project: Path) -> dict[str, Path]:
    """Resolve every store once.

    ``store_path`` re-reads the config chain on each call, so resolving all 36
    stores per store (to find nesting) would be ~1300 config reads for one
    ``gc`` invocation. One pass, reused.
    """
    return {s.name: store_path(s.name, project) for s in all_stores()}


def _parent_of_another_store(name: str, resolved: dict[str, Path]) -> str | None:
    """Return the name of a declared store nested inside *name*'s root, if any.

    ``project-state`` resolves to ``<proj>/.chimera`` itself, whose children
    include ``sessions/``, ``checkpoints/``, ``settings.json`` and
    ``todo.json``. Treating those as reclaimable units would let one retention
    line delete other stores and live config, so a store that contains another
    declared store is never child-pruned.
    """
    root = resolved[name]
    for other, other_path in resolved.items():
        if other == name or other_path == root:
            continue
        try:
            other_path.relative_to(root)
        except ValueError:
            continue
        return other
    return None


@dataclass
class GcPlan:
    """What ``chimera gc`` would do, and what it declined to consider."""

    candidates: list[PruneCandidate] = field(default_factory=list)
    skips: list[GcSkip] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        """Bytes the candidates occupy."""
        return sum(c.size_bytes for c in self.candidates)

    @property
    def stores(self) -> list[str]:
        """Store names with at least one candidate, in plan order."""
        seen: list[str] = []
        for candidate in self.candidates:
            if candidate.store not in seen:
                seen.append(candidate.store)
        return seen


def plan_gc(
    *,
    project: str | os.PathLike[str] | None = None,
    stores: Iterable[str] | None = None,
    now: datetime | None = None,
    measure: bool = True,
) -> GcPlan:
    """Build the reclaim plan for every store that opted into retention.

    A store is considered only when it is declared ``prunable`` **and** has an
    active ``[storage.<name>]`` policy. Everything else is recorded as a
    :class:`GcSkip` with the reason, so the dry-run output accounts for all 36
    rows rather than showing only the interesting ones.

    Args:
        project: Project root for project-scope stores (default: cwd).
        stores: Restrict to these registry keys (default: all).
        now: Clock override.
        measure: Compute candidate sizes.

    Returns:
        The :class:`GcPlan`. Building it never touches the filesystem
        destructively — :func:`apply_prune` is the only thing that does.

    Raises:
        UnknownStore: If *stores* names something the registry does not
            declare. A typo must fail loudly rather than filtering to nothing:
            ``--store sesions`` reporting "0 candidates" would read exactly
            like "retention is configured and there is nothing to reclaim".
    """
    root_project = Path(project) if project is not None else Path.cwd()
    wanted: set[str] | None = None
    if stores is not None:
        wanted = set(stores)
        for name in sorted(wanted):
            get_store(name)  # raises UnknownStore
    plan = GcPlan()
    resolved = _resolve_all(root_project)

    for store in all_stores():
        if wanted is not None and store.name not in wanted:
            continue
        if not store.prunable:
            plan.skips.append(GcSkip(store.name, SKIP_NOT_PRUNABLE))
            continue
        nested = _parent_of_another_store(store.name, resolved)
        if nested is not None:
            plan.skips.append(
                GcSkip(store.name, f"{SKIP_NESTED_PREFIX}{nested!r} store")
            )
            continue
        try:
            retention = store_retention(store.name, root_project)
        except Exception:  # noqa: BLE001 — config discovery is best-effort.
            retention = StoreRetention()
        if not retention.active:
            plan.skips.append(GcSkip(store.name, SKIP_NO_RETENTION))
            continue
        root = resolved[store.name]
        if not root.is_dir():
            plan.skips.append(GcSkip(store.name, SKIP_EMPTY))
            continue
        entries = collect_entries(root, now=now)
        plan.candidates.extend(
            select_for_prune(
                entries, retention, store=store.name, root=root, measure=measure
            )
        )
    return plan


def gc_skips(plan: GcPlan, reason: str) -> list[str]:
    """Store names skipped for *reason*, for grouped reporting.

    Args:
        plan: The plan to read.
        reason: Exact reason string to match.

    Returns:
        The matching store names, in plan order.
    """
    return [s.store for s in plan.skips if s.reason == reason]
