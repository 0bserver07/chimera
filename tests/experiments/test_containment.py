"""A toolkit run is structurally unable to write outside its store.

This is the property that makes the rest of the storage subsystem tractable.
Because every byte a run writes is under
``<store>/experiment-runs/<name>/<stamp>/``:

* ``chimera gc`` can reclaim experiment output by naming one registry store,
  with no second retention mechanism and no way to reach an undeclared path;
* the repo root stays clean by construction rather than by a driver author
  remembering — which is what failed and produced 336 MB of ``pb-runs/``.

So the guarantee is tested as a guarantee: hostile names and hostile paths, and
then a sweep proving nothing was created anywhere else.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from chimera.experiments import OutsideRun, runs_root, start


# -- hostile run names -------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "../../etc",
        "a/b",
        "/absolute",
        "..",
        ".",
        ".hidden",
        "",
        "with space",
        "semi;colon",
        "null\x00byte",
        "~",
    ],
)
def test_a_run_name_that_could_escape_is_refused(name: str) -> None:
    """Refused, not sanitised: a silently rewritten name cannot be found again."""
    with pytest.raises(ValueError):
        start(name)


@pytest.mark.parametrize("stamp", ["../elsewhere", "a/b", "..", ".hidden", ""])
def test_a_stamp_that_could_escape_is_refused(stamp: str) -> None:
    with pytest.raises(ValueError):
        start("ok-name", stamp=stamp)


def test_a_refused_name_creates_nothing_at_all(experiment_home: Path) -> None:
    """Validation happens before any mkdir, so a bad name leaves no residue."""
    with pytest.raises(ValueError):
        start("../escape")
    assert not (experiment_home.parent / "escape").exists()
    assert list(experiment_home.rglob("escape")) == []


# -- hostile relative paths --------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    ["../outside.txt", "../../outside.txt", "ws/../../outside.txt", "a/b/../../../x"],
)
def test_dot_dot_traversal_is_refused(rel: str) -> None:
    run = start("contained")
    with pytest.raises(OutsideRun, match="outside the run directory"):
        run.path(rel)


@pytest.mark.parametrize("rel", ["/etc/passwd", "/tmp/x.txt"])
def test_an_absolute_path_is_refused(rel: str) -> None:
    run = start("contained")
    with pytest.raises(OutsideRun, match="absolute"):
        run.path(rel)


def test_an_empty_path_is_refused() -> None:
    run = start("contained")
    with pytest.raises(OutsideRun):
        run.path("   ")


def test_a_symlink_planted_inside_the_run_is_not_an_exit(tmp_path: Path) -> None:
    """Lexical checks miss this one; the guard compares real paths."""
    run = start("contained")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, run.dir / "escape-hatch")

    with pytest.raises(OutsideRun, match="outside the run directory"):
        run.path("escape-hatch/loot.txt")
    with pytest.raises(OutsideRun):
        run.jsonl("escape-hatch/loot.jsonl", {"task": "a"})
    assert list(outside.iterdir()) == []


def test_every_write_method_enforces_containment() -> None:
    """Not just ``path`` — the whole surface funnels through the same check."""
    run = start("contained")
    for call in (
        lambda: run.jsonl("../evil.jsonl", {"task": "a"}),
        lambda: run.write_text("../evil.txt", "x"),
        lambda: run.write_json("../evil.json", {}),
        lambda: run.subdir("../evil-dir"),
        lambda: run.rows("../evil.jsonl"),
        lambda: run.seen("../evil.jsonl"),
    ):
        with pytest.raises(OutsideRun):
            call()


def test_after_a_hostile_workout_nothing_exists_outside_the_store(
    experiment_home: Path, tmp_path: Path
) -> None:
    """The end-to-end statement of the guarantee.

    Everything the toolkit created lives under one registry store, and the
    directories a hostile caller aimed at are untouched.
    """
    before = {p for p in tmp_path.rglob("*")}
    run = start("workout", config={"model": "glm-5.2"})
    run.jsonl("progress.jsonl", {"task": "t0"})
    run.write_text("notes.md", "hello")
    run.subdir("ws/task-1")
    for hostile in ("../a", "/tmp/b", "ws/../../c", "..", "/"):
        with pytest.raises(OutsideRun):
            run.path(hostile)
    run.finish({"passed": 1, "total": 1})

    created = {p for p in tmp_path.rglob("*")} - before
    store = runs_root()
    stray = [p for p in created if store not in p.parents and p != store]
    assert stray == [], f"toolkit wrote outside its store: {stray}"
    assert store == experiment_home / "experiment-runs"
