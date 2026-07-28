"""The experiment toolkit's API (M4 of ``docs/specs/storage-and-experiments.md``).

What is actually being defended here is that the five hand-rolled ProgramBench
drivers had a reason to exist and now do not: a run directory, a provenance
manifest, a flushed ledger, resume-by-key, and a receipt-shaped result are all
one import away. Each test below pins one of the patterns those scripts
rewrote — usually a little worse each time.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from chimera.config.paths import store_path
from chimera.experiments import (
    ExperimentError,
    NoSuchRun,
    Run,
    list_runs,
    load_run,
    resume,
    runs_root,
    start,
)
from chimera.experiments.run import git_provenance


# -- where runs land ---------------------------------------------------------


def test_a_run_lands_under_the_registrys_experiment_runs_store() -> None:
    """The store comes from the registry, never from a hand-built path."""
    run = start("pb-sweep", config={"model": "glm-5.2"})
    assert run.dir.parent.parent == store_path("experiment-runs")
    assert run.dir.parent.name == "pb-sweep"
    assert run.dir.name == run.stamp
    assert run.dir.is_dir()


def test_runs_root_follows_the_configured_storage_root(experiment_home: Path) -> None:
    """Relocating the root relocates experiment runs with everything else."""
    assert runs_root() == experiment_home / "experiment-runs"


def test_the_stamp_is_a_sortable_utc_timestamp() -> None:
    """Lexical order equals chronological order, on every machine."""
    run = start("stamped")
    assert len(run.stamp) == len("2026-07-27T14-03-11")
    assert run.stamp[4] == "-" and run.stamp[10] == "T" and run.stamp[13] == "-"


def test_two_runs_in_the_same_second_get_distinct_directories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sweep script restarted twice in one second must not share a ledger."""
    from chimera.experiments import run as run_mod

    frozen = run_mod._utc_now()
    monkeypatch.setattr(run_mod, "_utc_now", lambda: frozen)
    first = start("collide")
    second = start("collide")
    assert first.dir != second.dir
    assert second.stamp.startswith(first.stamp)


# -- the manifest: which code produced this number ---------------------------


def test_the_manifest_records_the_provenance_the_playbook_asks_for() -> None:
    """Name, stamp, config, argv, cwd, git SHA + dirty flag, status=running."""
    run = start("provenance", config={"model": "glm-5.2", "limit": 10})
    manifest = run.manifest()

    assert manifest["name"] == "provenance"
    assert manifest["stamp"] == run.stamp
    assert manifest["status"] == "running"
    assert manifest["config"] == {"model": "glm-5.2", "limit": 10}
    assert isinstance(manifest["argv"], list)
    assert manifest["cwd"] == str(Path.cwd())
    assert set(manifest["git"]) == {"sha", "branch", "dirty"}
    assert manifest["started_at"].endswith("Z")
    assert isinstance(manifest["pid"], int)
    assert manifest["host"]
    assert manifest["chimera_version"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_provenance_reports_the_real_sha_and_dirty_flag(tmp_path: Path) -> None:
    """The dirty flag is the half that says 'not reproducible from the SHA'."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    base = ["git", "-c", "user.email=t@example.com", "-c", "user.name=T"]
    subprocess.run([*base, "init", "-q"], cwd=repo, check=True, env={**env, "PATH": "/usr/bin:/bin:/usr/local/bin"})
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run([*base, "add", "."], cwd=repo, check=True)
    subprocess.run([*base, "commit", "-qm", "first"], cwd=repo, check=True)

    clean = git_provenance(repo)
    assert clean["sha"] and len(clean["sha"]) == 40
    assert clean["dirty"] is False

    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    assert git_provenance(repo)["dirty"] is True


def test_git_provenance_outside_a_repository_is_null_not_an_error(
    tmp_path: Path,
) -> None:
    """Recording provenance must never be what stops an experiment starting."""
    assert git_provenance(tmp_path) == {"sha": None, "branch": None, "dirty": None}


# -- the ledger: jsonl / seen / rows -----------------------------------------


def test_jsonl_appends_one_record_per_call() -> None:
    run = start("ledger")
    run.jsonl("progress.jsonl", {"task": "a", "ok": True})
    run.jsonl("progress.jsonl", {"task": "b", "ok": False})
    lines = (run.dir / "progress.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["task"] for line in lines] == ["a", "b"]


def test_jsonl_flushes_so_a_reader_sees_rows_before_the_run_ends() -> None:
    """Without the flush a crashed run loses everything it 'wrote'."""
    run = start("flushed")
    run.jsonl("progress.jsonl", {"task": "a"})
    assert (run.dir / "progress.jsonl").read_text(encoding="utf-8").strip()


def test_jsonl_records_unserialisable_values_instead_of_raising() -> None:
    """A stray object must not destroy a long run's ledger."""
    run = start("stringly")
    run.jsonl("progress.jsonl", {"task": "a", "obj": object()})
    assert run.rows("progress.jsonl")[0]["obj"].startswith("<object object")


def test_seen_returns_the_keys_already_recorded() -> None:
    """The resume primitive every pb driver rewrote."""
    run = start("resumable")
    for task in ("t0", "t1", "t2"):
        run.jsonl("progress.jsonl", {"task": task, "ok": True})
    assert run.seen("progress.jsonl", key="task") == {"t0", "t1", "t2"}


def test_seen_on_a_missing_file_is_empty_not_an_error() -> None:
    """First run of a resumable loop reads a ledger that does not exist yet."""
    assert start("fresh").seen("progress.jsonl", key="task") == set()


def test_seen_skips_a_truncated_final_line() -> None:
    """A hard kill can cut a row mid-write; that must not poison resume."""
    run = start("truncated")
    run.jsonl("progress.jsonl", {"task": "t0"})
    run.close()
    with open(run.dir / "progress.jsonl", "a", encoding="utf-8") as handle:
        handle.write('{"task": "t1", "ok')  # killed mid-write
    assert run.seen("progress.jsonl", key="task") == {"t0"}
    assert len(run.rows("progress.jsonl")) == 1


def test_seen_ignores_rows_without_the_key() -> None:
    run = start("mixed")
    run.jsonl("progress.jsonl", {"task": "t0"})
    run.jsonl("progress.jsonl", {"event": "config"})
    assert run.seen("progress.jsonl", key="task") == {"t0"}


def test_the_toolkits_own_filenames_are_refused_as_ledgers() -> None:
    """Appending JSONL to manifest.json would destroy the run's own record."""
    run = start("reserved")
    with pytest.raises(ExperimentError, match="written by the toolkit"):
        run.jsonl("manifest.json", {"task": "a"})
    with pytest.raises(ExperimentError, match="written by the toolkit"):
        run.jsonl("result.json", {"task": "a"})


# -- free-form artifacts -----------------------------------------------------


def test_path_creates_parent_directories_so_writes_just_work() -> None:
    run = start("artifacts")
    target = run.path("ws/task-1/out.txt")
    target.write_text("hi", encoding="utf-8")
    assert (run.dir / "ws" / "task-1" / "out.txt").read_text(encoding="utf-8") == "hi"


def test_subdir_creates_and_returns_a_directory_in_the_run() -> None:
    run = start("workspaces")
    workspace = run.subdir("ws/task-1")
    assert workspace.is_dir()
    assert workspace == run.dir / "ws" / "task-1"


def test_write_text_and_write_json_land_in_the_run() -> None:
    run = start("writers")
    run.write_text("notes.md", "hello")
    run.write_json("params.json", {"k": 1})
    assert (run.dir / "notes.md").read_text(encoding="utf-8") == "hello"
    assert json.loads((run.dir / "params.json").read_text(encoding="utf-8")) == {"k": 1}


# -- finishing: the receipt --------------------------------------------------


def test_finish_writes_a_receipt_shaped_result_and_completes_the_run() -> None:
    """``result.json`` is copyable into ``data/`` as-is — cells and all."""
    run = start("receipt", config={"model": "glm-5.2"})
    run.finish({"passed": 8, "total": 10, "cost_usd": 1.25, "benchmark": "mbpp"})

    receipt = json.loads(run.result_path.read_text(encoding="utf-8"))
    assert receipt["run_id"] == f"receipt/{run.stamp}"
    assert receipt["model"] == "glm-5.2"
    assert receipt["git"] == run.manifest()["git"]
    assert receipt["config"] == {"model": "glm-5.2"}
    (cell,) = receipt["cells"]
    assert cell == {
        "passed": 8,
        "total": 10,
        "cost_usd": 1.25,
        "benchmark": "mbpp",
        "agent_id": "receipt",
        "pass_rate": 0.8,
        "status": "completed",
    }
    assert run.manifest()["status"] == "completed"
    assert run.manifest()["ended_at"].endswith("Z")


def test_finish_accepts_an_explicit_multi_cell_list() -> None:
    """A matrix driver reports one cell per (agent, benchmark) pair."""
    run = start("matrix")
    run.finish(
        {
            "cells": [
                {"agent_id": "react", "benchmark": "mbpp", "passed": 5, "total": 5},
                {"agent_id": "react", "benchmark": "humaneval", "passed": 0, "total": 2},
            ]
        }
    )
    cells = json.loads(run.result_path.read_text(encoding="utf-8"))["cells"]
    assert [c["pass_rate"] for c in cells] == [1.0, 0.0]


def test_finish_with_no_summary_still_completes_the_run() -> None:
    """Not every experiment produces a score; it still produces a receipt."""
    run = start("scoreless")
    run.finish()
    assert json.loads(run.result_path.read_text(encoding="utf-8"))["cells"] == []
    assert run.manifest()["status"] == "completed"


def test_cost_and_agent_and_bench_aliases_are_accepted() -> None:
    """The pb drivers wrote ``cost``/``model``; the receiver speaks both."""
    run = start("aliases")
    run.finish({"agent": "swe", "bench": "mbpp", "passed": 1, "total": 1, "cost": 0.5})
    (cell,) = json.loads(run.result_path.read_text(encoding="utf-8"))["cells"]
    assert cell["agent_id"] == "swe"
    assert cell["benchmark"] == "mbpp"
    assert cell["cost_usd"] == 0.5


@pytest.mark.parametrize(
    "summary, message",
    [
        ({"passed": 11, "total": 10}, "cannot pass more tasks"),
        ({"passed": 1, "total": 10, "pass_rate": 0.9}, "disagrees with"),
        (
            {"passed": 1, "total": 10, "status_counts": {"completed": 3}},
            "sums to 3 but total=10",
        ),
        ({"passed": 3, "total": 10, "status": "error"}, "errored run cannot"),
    ],
)
def test_finish_refuses_a_receipt_the_observatory_would_reject(
    summary: dict, message: str
) -> None:
    """The integrity invariants are enforced at write time, not months later.

    ``scripts/render_observatory.py`` raises on exactly these shapes when it
    renders ``data/``. Catching them here means a receipt copied into ``data/``
    cannot fail that gate — and a driver learns it is wrong while the run is
    still in front of the person who started it.
    """
    run = start("invalid")
    with pytest.raises(ValueError, match=message):
        run.finish(summary)
    assert not run.result_path.exists()
    assert run.manifest()["status"] == "running"


def test_a_run_that_never_finishes_stays_running() -> None:
    """The whole interrupted/completed distinction rests on this."""
    run = start("unfinished")
    run.jsonl("progress.jsonl", {"task": "a"})
    assert run.manifest()["status"] == "running"
    assert load_run("unfinished").result is None


def test_fail_records_the_reason_and_is_not_an_interruption() -> None:
    """``failed`` means the run decided; ``running`` means nobody got to."""
    run = start("gave-up")
    run.fail("provider returned 401")
    info = load_run("gave-up")
    assert info.status == "failed"
    assert info.manifest["error"] == "provider returned 401"
    assert info.interrupted is False


def test_a_context_manager_body_that_raises_records_a_failure() -> None:
    with pytest.raises(RuntimeError):
        with start("exploding") as run:
            run.jsonl("progress.jsonl", {"task": "a"})
            raise RuntimeError("boom")
    info = load_run("exploding")
    assert info.status == "failed"
    assert "RuntimeError: boom" in info.manifest["error"]
    assert len(run.rows("progress.jsonl")) == 1


def test_a_context_manager_body_that_finishes_stays_completed() -> None:
    with start("tidy") as run:
        run.finish({"passed": 1, "total": 1})
    assert load_run("tidy").status == "completed"


# -- resume ------------------------------------------------------------------


def _interrupt(run: Run) -> None:
    """Simulate the writer disappearing: close handles, orphan the PID.

    ``tests/experiments/test_crash_safety.py`` does this for real with dying
    child processes; here the point is the resume *logic*, so the state is
    forged rather than earned.
    """
    run.close()
    manifest = run.manifest()
    manifest["pid"] = -1  # no such process
    (run.dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize("pid", [0, -1, -12345])
def test_a_nonpositive_pid_is_never_handed_to_os_kill(pid: int) -> None:
    """POSIX reads 0 as 'my process group' and -1 as 'everything'.

    A corrupt manifest must not turn a liveness probe into a machine-wide
    signal, so those values are answered before ``os.kill`` is reached.
    """
    from chimera.experiments.run import _pid_alive

    assert _pid_alive(pid) is False


def test_resume_reattaches_to_the_newest_interrupted_run() -> None:
    first = start("sweep", config={"limit": 3})
    first.jsonl("progress.jsonl", {"task": "t0"})
    _interrupt(first)

    second = resume("sweep")
    assert second.dir == first.dir
    assert second.config == {"limit": 3}
    assert second.seen("progress.jsonl", key="task") == {"t0"}
    assert second.manifest()["resumed_at"].endswith("Z")
    assert second.manifest()["status"] == "running"

    second.jsonl("progress.jsonl", {"task": "t1"})
    second.finish({"passed": 2, "total": 2})
    assert load_run("sweep").status == "completed"
    assert len(second.rows("progress.jsonl")) == 2


def test_resume_refuses_to_append_to_a_finished_run() -> None:
    """Silently reopening a published result is how a receipt gets corrupted."""
    run = start("done")
    run.finish({"passed": 1, "total": 1})
    with pytest.raises(NoSuchRun, match="no interrupted run"):
        resume("done")


def test_resume_of_an_unknown_experiment_raises() -> None:
    with pytest.raises(NoSuchRun, match="no runs recorded"):
        resume("never-run")


def test_resume_accepts_an_explicit_stamp_regardless_of_status() -> None:
    run = start("pinned")
    run.finish({"passed": 1, "total": 1})
    reopened = resume("pinned", run.stamp)
    assert reopened.dir == run.dir
    assert reopened.manifest()["status"] == "running"


def test_resume_with_an_unknown_stamp_lists_what_exists() -> None:
    run = start("pinned")
    with pytest.raises(NoSuchRun, match=run.stamp):
        resume("pinned", "2000-01-01T00-00-00")


def test_start_with_resume_reattaches_or_begins_in_one_call() -> None:
    """The one-call form: the loop body is identical either way."""
    first = start("oneshot", config={"limit": 2}, resume=True)
    first.jsonl("progress.jsonl", {"task": "t0"})
    _interrupt(first)

    again = start("oneshot", config={"limit": 2}, resume=True)
    assert again.dir == first.dir
    assert again.seen("progress.jsonl", key="task") == {"t0"}


def test_start_with_resume_and_nothing_to_resume_begins_a_new_run() -> None:
    run = start("nothing-yet", resume=True)
    assert run.manifest()["status"] == "running"
    assert len(list_runs("nothing-yet")) == 1


# -- listing and lookup ------------------------------------------------------


def test_list_runs_is_empty_before_anything_has_run() -> None:
    """An absent store is 'no experiments yet', not an error."""
    assert list_runs() == []


def test_list_runs_returns_every_experiment_oldest_first() -> None:
    start("alpha", stamp="2026-01-01T00-00-00")
    start("alpha", stamp="2026-02-01T00-00-00")
    start("beta", stamp="2026-01-15T00-00-00")
    assert [(i.name, i.stamp) for i in list_runs()] == [
        ("alpha", "2026-01-01T00-00-00"),
        ("alpha", "2026-02-01T00-00-00"),
        ("beta", "2026-01-15T00-00-00"),
    ]
    assert [i.name for i in list_runs("beta")] == ["beta"]


def test_load_run_resolves_a_bare_name_to_the_newest_run() -> None:
    start("many", stamp="2026-01-01T00-00-00")
    start("many", stamp="2026-06-01T00-00-00")
    assert load_run("many").stamp == "2026-06-01T00-00-00"
    assert load_run("many/2026-01-01T00-00-00").stamp == "2026-01-01T00-00-00"


def test_load_run_of_an_unknown_stamp_raises() -> None:
    start("known", stamp="2026-01-01T00-00-00")
    with pytest.raises(NoSuchRun):
        load_run("known/2026-09-09T00-00-00")


def test_run_info_reports_size_and_reopens_a_writable_run() -> None:
    run = start("sized")
    run.write_text("blob.txt", "x" * 4096)
    info = load_run("sized")
    assert info.size_bytes() >= 4096
    reopened = info.open()
    reopened.jsonl("progress.jsonl", {"task": "t0"})
    assert reopened.seen("progress.jsonl", key="task") == {"t0"}


def test_a_corrupt_manifest_does_not_break_listing() -> None:
    """One damaged run must not hide every other run from ``doctor``."""
    run = start("damaged")
    (run.dir / "manifest.json").write_text("{not json", encoding="utf-8")
    (info,) = list_runs("damaged")
    assert info.manifest == {}
    assert info.status == "running"
    assert info.interrupted is True


# -- closed runs -------------------------------------------------------------


def test_writing_to_a_closed_run_raises_rather_than_silently_dropping() -> None:
    run = start("closed")
    run.close()
    with pytest.raises(ExperimentError, match="is closed"):
        run.jsonl("progress.jsonl", {"task": "a"})
