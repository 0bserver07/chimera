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


def test_the_three_tools_share_one_set():
    """list_files, repo_map and definition_lookup resolve to the same object.

    Identity, not equality: two equal-but-separate frozensets are exactly the
    state this module was written to end, and they would drift apart again the
    first time someone edited one of them.
    """
    from chimera.tools.definition_lookup import _IGNORE_DIRS as definition_dirs
    from chimera.tools.list_files import _IGNORED_DIRS as list_files_dirs
    from chimera.tools.repo_map import IGNORE_DIRS as repo_map_dirs

    assert list_files_dirs is NOT_SOURCE_DIRS
    assert repo_map_dirs is NOT_SOURCE_DIRS
    assert definition_dirs is NOT_SOURCE_DIRS


def test_the_checkpoint_writer_consumes_it_too():
    """The consumer that had no list at all is now the fourth caller."""
    from chimera.env.local import CHECKPOINT_EXCLUDED_DIRS

    assert NOT_SOURCE_DIRS <= CHECKPOINT_EXCLUDED_DIRS


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
