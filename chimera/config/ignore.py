"""The one definition of "this directory is not source".

Five modules used to carry a hand-copied answer to the same question — which
directories a tree walk should step over because they hold vendored packages,
build output, VCS internals, or editor state rather than code someone wrote.
Three of them were the ones the storage audit named
(``chimera/tools/list_files.py``, ``chimera/tools/repo_map.py``,
``chimera/tools/definition_lookup.py``); they had already drifted to two
different answers, 26 entries versus 15.

The drift was not cosmetic. The set that *nothing* consumed was the checkpoint
writer's, and its full-tree copy is how a 2.0 GB checkpoint containing
``.venv``, ``site/node_modules`` and a duplicated run-output tree came to sit
undetected for four months (spec: ``docs/specs/storage-and-experiments.md``).
One list is the fix: a walker that skips these cannot be taught to skip them
differently by a copy nobody remembered to update.

:data:`NOT_SOURCE_DIRS` is the union of what those lists said, so adopting it
never *narrows* a consumer's skipping — the only movement is that the two
15-entry copies gained the newer entries (``site-packages``, ``target``, and
the framework caches), all of which are non-source by the same reasoning that
put the rest in. Widening is stated rather than assumed because a consumer
that legitimately needs a different set should say so at its own call site
(see :data:`DELIBERATELY_ABSENT` for the one entry that is a decision, not an
oversight).

Membership is by **directory name**, matched against a single path segment.
Nothing here is a glob and nothing matches a file: a file literally named
``build`` is source, a directory named ``build`` is not, and every consumer is
responsible for only testing directory segments.

Stdlib only, per the zero-dependency-core rule.
"""
from __future__ import annotations

__all__ = [
    "DELIBERATELY_ABSENT",
    "NOT_SOURCE_DIRS",
    "is_not_source",
    "prune_dirnames",
]

#: Names deliberately kept **out** of :data:`NOT_SOURCE_DIRS`, with the reason
#: recorded at the point of the decision. This one reads as an obvious addition
#: and is not: adding it would hide real source. Pinned by a test so the
#: reasoning survives the next reviewer who notices the "gap".
DELIBERATELY_ABSENT: dict[str, str] = {
    "env": (
        "Bare `env` collides with real source directories — `chimera/env` is a "
        "package in this very repo. Only the dot-prefixed and virtualenv "
        "spellings (.venv, venv, .virtualenv) are safe to skip. Carried over "
        "verbatim from chimera/tools/list_files.py, which discovered it."
    ),
}

#: Directories a source walk steps over. Union of the three tool copies this
#: module replaced; grouped by why each entry is here.
NOT_SOURCE_DIRS: frozenset[str] = frozenset({
    # Version control internals.
    ".git", ".hg", ".svn",
    # Python virtual environments and installed packages.
    ".venv", "venv", ".virtualenv", "site-packages", ".eggs",
    # JavaScript dependencies.
    "node_modules",
    # Tool caches and compiled artifacts.
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
    ".cache",
    # Editor and IDE state.
    ".idea", ".vscode",
    # Agent state, including the pre-M3 checkpoint location that is still on
    # disk wherever a checkpoint was ever taken.
    ".chimera", ".chimera_checkpoints", ".antigravitycli",
    # Framework build output. `.astro` came from chimera/context/repo_map.py,
    # which was the only copy that had it — and this repo's own docs site is
    # Astro, so it was the copy that mattered here.
    ".next", ".nuxt", ".svelte-kit", ".gradle", ".astro",
    # Generic build output.
    "dist", "build", "target",
})


def is_not_source(name: str) -> bool:
    """Return whether one path *segment* names a non-source directory.

    Args:
        name: A single path segment (never a path). Callers split their own
            paths so that only directory segments are tested — a *file* named
            ``build`` is source and must not be dropped.

    Returns:
        ``True`` if a source walk should step over a directory with this name.
    """
    return name in NOT_SOURCE_DIRS


def prune_dirnames(dirnames: list[str], *, skip_hidden: bool = False) -> list[str]:
    """Filter an :func:`os.walk` ``dirnames`` list in place, and return it.

    Written for the ``dirs[:] = ...`` idiom that prunes an ``os.walk`` before it
    descends, which is the only way to avoid *entering* a ``node_modules`` at
    all rather than walking it and discarding the results.

    Args:
        dirnames: The mutable list ``os.walk`` yields. Modified in place, as
            ``os.walk`` requires — rebinding the name has no effect on the walk.
        skip_hidden: Also drop every dot-prefixed directory. Consumers that
            already did this keep doing it; it is a separate policy from
            :data:`NOT_SOURCE_DIRS` (which names specific directories, some of
            them not hidden) and is therefore opt-in rather than folded in.

    Returns:
        The same list object, for convenience.
    """
    kept = [
        d for d in dirnames
        if not is_not_source(d) and not (skip_hidden and d.startswith("."))
    ]
    dirnames[:] = kept
    return dirnames
