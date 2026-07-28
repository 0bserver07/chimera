"""The one "not source" list (M3 of the storage spec).

Three tools each carried a hand-copy of the same directory set and had already
drifted to two different answers — 26 entries against 15 — while the checkpoint
writer, the one component whose copy would have mattered most, consumed none of
them and wrote 2.0 GB of ``.venv`` and ``node_modules`` into a checkpoint.

The property these tests defend is not the contents of the set. It is that the
consumers cannot answer the question differently from each other again.
"""
from __future__ import annotations

from chimera.config.ignore import (
    DELIBERATELY_ABSENT,
    NOT_SOURCE_DIRS,
    is_not_source,
    prune_dirnames,
)


def test_every_module_level_copy_resolves_to_the_one_set():
    """Identity, not equality, for every named copy the sweep found.

    Two equal-but-separate frozensets are exactly the state this module was
    written to end: they would drift apart again the first time someone edited
    one of them. ``is`` is therefore the assertion, and a new copy appearing
    anywhere is a new name that has to be added here to pass.

    The audit named three copies. A grep for ``node_modules`` across
    ``chimera/`` found **seven** module-level sets plus five inline tuples; this
    is all of the former except the one documented exception below.
    """
    from chimera.badger import slash  # noqa: F401 — imported for the sweep below
    from chimera.context.repo_map import _SKIP_DIRS as context_repo_map_dirs
    from chimera.mcp_servers.rag_server import _IGNORE_DIRS as rag_dirs
    from chimera.tools.definition_lookup import _IGNORE_DIRS as definition_dirs
    from chimera.tools.list_files import _IGNORED_DIRS as list_files_dirs
    from chimera.tools.repo_map import IGNORE_DIRS as repo_map_dirs
    from chimera.tui.workspace import _SKIP_DIRS as workspace_dirs

    for name, value in (
        ("tools/list_files", list_files_dirs),
        ("tools/repo_map", repo_map_dirs),
        ("tools/definition_lookup", definition_dirs),
        ("mcp_servers/rag_server", rag_dirs),
        ("context/repo_map", context_repo_map_dirs),
        ("tui/workspace", workspace_dirs),
    ):
        assert value is NOT_SOURCE_DIRS, name


def test_the_checkpoint_writer_consumes_it_too():
    """The consumer that had no list at all — and whose absence cost 2.0 GB."""
    from chimera.env.local import CHECKPOINT_EXCLUDED_DIRS

    assert NOT_SOURCE_DIRS <= CHECKPOINT_EXCLUDED_DIRS


def test_the_standalone_hook_copy_can_age_but_never_contradict():
    """``chimera/hooks/validate_path.py`` is the one deliberate copy.

    It is invoked as a bare script path by ``chimera/hooks/hooks.json``, which
    puts ``chimera/hooks/`` on ``sys.path[0]`` instead of the repo root, so a
    ``chimera.*`` import is not guaranteed to resolve — and a hook that fails to
    import blocks every Write and Edit. Drift is bounded rather than prevented:
    the copy may lack newer entries, but it may never name a directory the
    shared set does not, which is the assertion that catches a contradiction.
    """
    from chimera.hooks.validate_path import _IGNORE_DIRS as hook_dirs

    assert hook_dirs is not NOT_SOURCE_DIRS  # the exception is real, not stale
    assert set(hook_dirs) <= set(NOT_SOURCE_DIRS)


def test_no_new_hand_rolled_copy_appears_in_the_package():
    """A static sweep, so the next copy fails here instead of drifting quietly.

    Any module that writes ``node_modules`` as a string literal *beside another*
    non-source name is spelling out a collection — which is answering the
    question this module owns. New hits must either consume
    :data:`NOT_SOURCE_DIRS` or be added to the allowlist with a reason.

    A lone ``plugin_dir / "node_modules"`` is a path, not a list, so a single
    quoted name never trips this.
    """
    from pathlib import Path

    package = Path(__import__("chimera").__file__).parent
    # Modules allowed to spell the set out, with why.
    allowed = {
        "config/ignore.py",         # the definition itself
        "hooks/validate_path.py",   # standalone-stdlib hook; see the test above
        "env/watcher.py",           # glob *patterns* for change events, not a
                                    # directory-name set, and a caller-supplied
                                    # default the `ignore=` argument overrides
    }
    others = {n for n in NOT_SOURCE_DIRS if n != "node_modules"}
    offenders = []
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(package).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        # Join continuations so a set literal split over two lines still reads
        # as one collection.
        flat = text.replace("\n", " ")
        for quote in ('"', "'"):
            token = f"{quote}node_modules{quote}"
            if token not in flat:
                continue
            for chunk in flat.split(token)[1:]:
                window = chunk[:200]
                if any(f"{quote}{n}{quote}" in window for n in others):
                    offenders.append(f"{rel}: {token} beside a sibling entry")
                    break
    assert offenders == [], (
        "hand-rolled non-source list(s) found; consume NOT_SOURCE_DIRS or "
        "extend the allowlist with a reason:\n" + "\n".join(sorted(set(offenders)))
    )


def test_no_consumer_lost_an_entry_it_used_to_have():
    """The union covers both historical sets, verbatim.

    Written longhand rather than derived, so deleting an entry from the module
    fails here instead of being agreed with. The first block is what
    ``list_files`` knew; the second is the extra pair the other two carried.
    """
    for name in (
        ".git", ".hg", ".svn",
        ".venv", "venv", ".virtualenv", "site-packages",
        "node_modules",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
        ".cache", ".idea", ".vscode",
        ".chimera", ".antigravitycli",
        ".next", ".nuxt", ".svelte-kit", ".gradle",
        "dist", "build", "target",
    ):
        assert name in NOT_SOURCE_DIRS, name
    for name in (".eggs", ".chimera_checkpoints"):
        assert name in NOT_SOURCE_DIRS, name


def test_bare_env_stays_out_because_it_collides_with_real_source():
    """The one decision in the set, and the reason it is not an oversight.

    ``chimera/env`` is a package in this repo. Adding bare ``env`` would hide it
    from every listing, map and definition lookup at once — which is precisely
    the failure mode a single shared list makes cheap to cause.
    """
    assert "env" not in NOT_SOURCE_DIRS
    assert "env" in DELIBERATELY_ABSENT
    assert "chimera/env" in DELIBERATELY_ABSENT["env"]
    # The virtualenv spellings that *are* safe remain covered.
    assert {".venv", "venv", ".virtualenv"} <= NOT_SOURCE_DIRS


def test_membership_is_by_segment_never_by_path():
    assert is_not_source("node_modules")
    assert not is_not_source("src/node_modules")
    assert not is_not_source("node_modules_helper")


def test_prune_dirnames_mutates_in_place_as_os_walk_requires():
    """``os.walk`` only honours a ``dirnames`` list modified in place."""
    dirs = ["src", "node_modules", ".venv", "tests"]
    returned = prune_dirnames(dirs)
    assert dirs == ["src", "tests"]
    assert returned is dirs


def test_prune_dirnames_hidden_skip_is_opt_in():
    """Dot-prefix skipping is a separate policy from the named set."""
    assert prune_dirnames([".github", "src"]) == [".github", "src"]
    assert prune_dirnames([".github", "src"], skip_hidden=True) == ["src"]


def test_a_file_named_build_is_not_a_build_directory(tmp_path):
    """The set names directories; the tools must never drop a same-named file."""
    from chimera.env.local import LocalEnvironment
    from chimera.tools.list_files import ListFilesTool

    env = LocalEnvironment(workdir=str(tmp_path), test_cmd="true")
    env.setup()
    env.write_file("src/build", "a build script, not a build directory")
    env.write_file("build/generated.py", "output nobody edits")

    result = ListFilesTool().execute({"path": "."}, env)
    assert "src/build" in result.output
    assert "generated.py" not in result.output
