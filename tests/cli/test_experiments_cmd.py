"""``chimera experiments list`` / ``show``.

Two read-only verbs. The one behaviour worth naming: a run whose manifest says
``running`` but whose writer is gone renders as **interrupted**, because that
is the state ``resume()`` acts on and the state a person needs to notice.
Pruning is deliberately absent — retention belongs to ``chimera gc``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.cli.main import main
from chimera.experiments import start


@pytest.fixture(autouse=True)
def experiment_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the storage root at ``tmp_path`` (see tests/experiments/conftest.py)."""
    home = tmp_path / "home"
    root = tmp_path / "chimera-home"
    workdir = tmp_path / "cwd"
    for path in (home, root, workdir):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CHIMERA_HOME", str(root))
    monkeypatch.chdir(workdir)
    return root


def _orphan(run) -> None:  # type: ignore[no-untyped-def]
    """Make a run look interrupted: close it and orphan the recorded PID."""
    run.close()
    manifest = run.manifest()
    manifest["pid"] = -1
    (run.dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_list_with_no_runs_says_so_and_succeeds(capsys: pytest.CaptureFixture) -> None:
    assert main(["experiments", "list"]) == 0
    assert "No experiment runs recorded" in capsys.readouterr().out


def test_list_shows_each_run_with_its_status_and_score(
    capsys: pytest.CaptureFixture,
) -> None:
    done = start("pb-sweep", config={"model": "glm-5.2"}, stamp="2026-01-01T00-00-00")
    done.finish({"passed": 8, "total": 10, "cost_usd": 1.25})
    _orphan(start("pb-sweep", stamp="2026-02-01T00-00-00"))

    assert main(["experiments", "list"]) == 0
    out = capsys.readouterr().out
    assert "pb-sweep/2026-01-01T00-00-00" in out
    assert "completed" in out
    assert "8/10 (80.0%)" in out
    assert "$1.2500" in out
    assert "interrupted" in out


def test_list_filters_to_one_experiment(capsys: pytest.CaptureFixture) -> None:
    start("alpha", stamp="2026-01-01T00-00-00")
    start("beta", stamp="2026-01-01T00-00-00")
    assert main(["experiments", "list", "beta"]) == 0
    out = capsys.readouterr().out
    assert "beta/" in out
    assert "alpha/" not in out


def test_list_json_is_machine_readable(capsys: pytest.CaptureFixture) -> None:
    run = start("pb-sweep", config={"model": "glm-5.2"}, stamp="2026-01-01T00-00-00")
    run.finish({"passed": 1, "total": 2, "cost_usd": 0.5})

    assert main(["experiments", "list", "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert row["name"] == "pb-sweep"
    assert row["stamp"] == "2026-01-01T00-00-00"
    assert row["status"] == "completed"
    assert row["config"] == {"model": "glm-5.2"}
    assert row["cells"][0]["pass_rate"] == 0.5
    assert row["size_bytes"] > 0


def test_show_reports_the_provenance_and_the_receipt(
    capsys: pytest.CaptureFixture,
) -> None:
    run = start("pb-sweep", config={"model": "glm-5.2"}, stamp="2026-01-01T00-00-00")
    run.jsonl("progress.jsonl", {"task": "t0"})
    run.finish({"passed": 1, "total": 1, "cost_usd": 0.25, "benchmark": "mbpp"})

    assert main(["experiments", "show", "pb-sweep"]) == 0
    out = capsys.readouterr().out
    assert "pb-sweep/2026-01-01T00-00-00" in out
    assert "status     completed" in out
    assert "git " in out
    assert "progress.jsonl" in out
    assert "pb-sweep x mbpp: 1/1 (100.0%)  $0.2500  completed" in out
    assert "data/ is curated" in out


def test_show_of_an_unfinished_run_points_at_resume(
    capsys: pytest.CaptureFixture,
) -> None:
    _orphan(start("pb-sweep", stamp="2026-01-01T00-00-00"))
    assert main(["experiments", "show", "pb-sweep"]) == 0
    out = capsys.readouterr().out
    assert "status     interrupted" in out
    assert "never called finish()" in out
    assert "resume()" in out


def test_show_truncates_a_run_with_thousands_of_artifacts(
    capsys: pytest.CaptureFixture,
) -> None:
    """The command orients you; it is not an inventory. ``--json`` has them all."""
    from chimera.cli.experiments_cmd import MAX_LISTED_FILES

    artifacts = MAX_LISTED_FILES + 5
    run = start("noisy", stamp="2026-01-01T00-00-00")
    for i in range(artifacts):
        run.write_text(f"ws/task-{i:03d}/out.txt", "x")
    run.finish({"passed": 0, "total": 0})

    assert main(["experiments", "show", "noisy"]) == 0
    out = capsys.readouterr().out
    total = artifacts + 2  # + manifest.json + result.json
    assert f"… and {total - MAX_LISTED_FILES} more" in out
    listed = [line for line in out.splitlines() if line.startswith("    ws/")]
    assert len(listed) == MAX_LISTED_FILES - 2  # manifest/result sort first


def test_show_accepts_an_explicit_stamp(capsys: pytest.CaptureFixture) -> None:
    start("pb-sweep", stamp="2026-01-01T00-00-00")
    start("pb-sweep", stamp="2026-02-01T00-00-00")
    assert main(["experiments", "show", "pb-sweep/2026-01-01T00-00-00"]) == 0
    assert "pb-sweep/2026-01-01T00-00-00" in capsys.readouterr().out


def test_show_json_carries_the_whole_manifest_and_result(
    capsys: pytest.CaptureFixture,
) -> None:
    run = start("pb-sweep", config={"limit": 3}, stamp="2026-01-01T00-00-00")
    run.finish({"passed": 3, "total": 3})

    assert main(["experiments", "show", "pb-sweep", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest"]["config"] == {"limit": 3}
    assert set(payload["manifest"]["git"]) == {"sha", "branch", "dirty"}
    assert payload["result"]["cells"][0]["passed"] == 3


def test_show_of_an_unknown_run_exits_two(capsys: pytest.CaptureFixture) -> None:
    assert main(["experiments", "show", "never-run"]) == 2
    assert "no runs recorded" in capsys.readouterr().err


def test_show_of_a_traversing_reference_exits_two(
    capsys: pytest.CaptureFixture,
) -> None:
    """The CLI is not a way around the containment rules."""
    assert main(["experiments", "show", "../../etc"]) == 2
    assert "may not be '..'" in capsys.readouterr().err
    assert main(["experiments", "show", "/etc/passwd"]) == 2


def test_experiments_without_a_verb_exits_nonzero(
    capsys: pytest.CaptureFixture,
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["experiments"])
    assert excinfo.value.code != 0
