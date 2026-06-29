"""Tests for the ProgramBench adapter.

The mocked tests verify orchestration logic — image-name derivation,
task loading from the upstream ``tasks_dir`` shape, eval.json parsing,
skip pattern when docker is unavailable, and the subprocess command
shape passed to ``programbench eval``.

A live integration test is gated behind ``CHIMERA_PROGRAMBENCH_LIVE=1``
so CI on macOS/arm64 stays green.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chimera.eval.benchmarks.programbench import (
    BenchmarkSkipped,
    ProgramBench,
    ProgramBenchGradingError,
    ProgramBenchInstance,
    check_runtime_or_skip,
    docker_available,
    is_linux_amd64,
    parse_eval_json,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_tasks_dir(tmp_path):
    """Build a tasks/ directory mirroring the upstream layout."""
    root = tmp_path / "tasks"
    root.mkdir()

    items = [
        (
            "abishekvashok__cmatrix.5c082c6",
            {
                "repository": "abishekvashok/cmatrix",
                "commit": "5c082c64a1296859a11bee60c8c086655953a416",
                "language": "c",
                "difficulty": "easy",
                "eval_clean_hashes": ["776f1c415ab5cb4e73"],
            },
        ),
        (
            "agourlay__zip-password-finder.704700d",
            {
                "repository": "agourlay/zip-password-finder",
                "commit": "704700deadbeefdeadbeefdeadbeefdeadbeefde",
                "language": "rust",
                "difficulty": "medium",
            },
        ),
    ]
    for instance_id, meta in items:
        d = root / instance_id
        d.mkdir()
        (d / "task.yaml").write_text(
            "\n".join(f"{k}: {json.dumps(v)}" for k, v in meta.items()) + "\n"
        )
    return str(root)


@pytest.fixture
def sample_eval_json(tmp_path):
    """Create a minimal valid eval.json for parse_eval_json tests."""
    payload = {
        "test_results": [
            {"name": "tests.test_foo.test_a", "branch": "abc", "status": "passed"},
            {"name": "tests.test_foo.test_b", "branch": "abc", "status": "passed"},
            {"name": "tests.test_foo.test_c", "branch": "abc", "status": "failure"},
        ],
        "test_branches": ["abc", "def"],
        "error_code": None,
        "warnings": ["minor warn"],
    }
    p = tmp_path / "x.eval.json"
    p.write_text(json.dumps(payload))
    return p


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


class TestLoadingFromTasksDir:
    def test_loads_two_instances(self, fake_tasks_dir):
        bench = ProgramBench(tasks_dir=fake_tasks_dir)
        assert len(bench.instances) == 2
        ids = {i.instance_id for i in bench.instances}
        assert ids == {
            "abishekvashok__cmatrix.5c082c6",
            "agourlay__zip-password-finder.704700d",
        }

    def test_language_filter(self, fake_tasks_dir):
        bench = ProgramBench(tasks_dir=fake_tasks_dir, language="rust")
        assert {i.instance_id for i in bench.instances} == {
            "agourlay__zip-password-finder.704700d"
        }

    def test_difficulty_filter(self, fake_tasks_dir):
        bench = ProgramBench(tasks_dir=fake_tasks_dir, difficulty="easy")
        assert {i.instance_id for i in bench.instances} == {
            "abishekvashok__cmatrix.5c082c6"
        }

    def test_limit(self, fake_tasks_dir):
        bench = ProgramBench(tasks_dir=fake_tasks_dir, limit=1)
        assert len(bench.instances) == 1

    def test_breakdowns(self, fake_tasks_dir):
        bench = ProgramBench(tasks_dir=fake_tasks_dir)
        assert bench.language_breakdown() == {"c": 1, "rust": 1}
        assert bench.difficulty_breakdown() == {"easy": 1, "medium": 1}

    def test_missing_tasks_dir_raises(self):
        with pytest.raises(FileNotFoundError):
            ProgramBench(tasks_dir="/nonexistent/tasks")


class TestLoadingFromJSON:
    @pytest.fixture
    def dataset(self, tmp_path):
        items = [
            {
                "instance_id": "owner__repo.deadbee",
                "repo": "owner/repo",
                "commit": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                "language": "go",
                "difficulty": "hard",
            }
        ]
        path = tmp_path / "pb.json"
        path.write_text(json.dumps(items))
        return str(path)

    def test_loads_from_json(self, dataset):
        bench = ProgramBench(dataset_path=dataset)
        assert len(bench.instances) == 1
        assert bench.instances[0].language == "go"

    def test_rejects_both_dataset_and_tasks_dir(self, dataset, fake_tasks_dir):
        with pytest.raises(ValueError):
            ProgramBench(dataset_path=dataset, tasks_dir=fake_tasks_dir)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


class TestShape:
    def test_default_name(self):
        assert ProgramBench().name() == "programbench"

    def test_filtered_name(self):
        bench = ProgramBench(language="rust", difficulty="medium")
        assert bench.name() == "programbench-rust-medium"

    def test_cleanroom_image_naming(self):
        inst = ProgramBenchInstance(
            instance_id="abishekvashok__cmatrix.5c082c6",
            repo="abishekvashok/cmatrix",
            commit="5c082c64",
        )
        assert (
            inst.cleanroom_image()
            == "programbench/abishekvashok_1776_cmatrix.5c082c6:task_cleanroom"
        )

    def test_cleanroom_image_custom_tag(self):
        inst = ProgramBenchInstance(
            instance_id="o__r.abc1234",
            repo="o/r",
            commit="abc1234",
        )
        assert inst.cleanroom_image(tag="task") == "programbench/o_1776_r.abc1234:task"

    def test_short_sha_from_id(self):
        inst = ProgramBenchInstance(
            instance_id="o__r.abc1234",
            repo="o/r",
            commit="abc1234abc1234",
        )
        assert inst.short_sha() == "abc1234"

    def test_to_task_payload(self):
        inst = ProgramBenchInstance(
            instance_id="o__r.abc1234",
            repo="o/r",
            commit="abc1234",
            language="c",
            difficulty="easy",
        )
        task = inst.to_task()
        assert task["id"] == "o__r.abc1234"
        assert task["instance_id"] == "o__r.abc1234"
        assert task["language"] == "c"
        assert task["cleanroom_image"].endswith(":task_cleanroom")
        assert "Rebuild the program" in task["prompt"]

    def test_add_instance(self):
        bench = ProgramBench()
        bench.add_instance(
            ProgramBenchInstance(
                instance_id="o__r.abc1234",
                repo="o/r",
                commit="abc",
            )
        )
        assert len(bench.tasks()) == 1


# ---------------------------------------------------------------------------
# Skip pattern (docker / linux-amd64)
# ---------------------------------------------------------------------------


class TestRuntimeChecks:
    def test_docker_available_when_cli_missing(self):
        with patch(
            "chimera.eval.benchmarks.programbench.shutil.which",
            return_value=None,
        ):
            assert docker_available() is False

    def test_docker_available_when_version_succeeds(self):
        with (
            patch(
                "chimera.eval.benchmarks.programbench.shutil.which",
                return_value="/usr/bin/docker",
            ),
            patch(
                "chimera.eval.benchmarks.programbench.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="24.0.0", stderr=""
                ),
            ),
        ):
            assert docker_available() is True

    def test_docker_available_when_version_fails(self):
        with (
            patch(
                "chimera.eval.benchmarks.programbench.shutil.which",
                return_value="/usr/bin/docker",
            ),
            patch(
                "chimera.eval.benchmarks.programbench.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="boom"
                ),
            ),
        ):
            assert docker_available() is False

    def test_check_runtime_skips_when_no_docker(self):
        with (
            patch(
                "chimera.eval.benchmarks.programbench.docker_available",
                return_value=False,
            ),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("CHIMERA_PROGRAMBENCH_LIVE", None)
            with pytest.raises(BenchmarkSkipped, match="Docker"):
                check_runtime_or_skip()

    def test_check_runtime_skips_when_not_amd64(self):
        with (
            patch(
                "chimera.eval.benchmarks.programbench.docker_available",
                return_value=True,
            ),
            patch(
                "chimera.eval.benchmarks.programbench.is_linux_amd64",
                return_value=False,
            ),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("CHIMERA_PROGRAMBENCH_LIVE", None)
            with pytest.raises(BenchmarkSkipped, match="linux/amd64"):
                check_runtime_or_skip()

    def test_check_runtime_force_via_env(self):
        with (
            patch(
                "chimera.eval.benchmarks.programbench.docker_available",
                return_value=False,
            ),
            patch.dict(os.environ, {"CHIMERA_PROGRAMBENCH_LIVE": "1"}),
        ):
            # Should not raise
            check_runtime_or_skip()

    def test_is_linux_amd64_on_macos(self):
        with patch(
            "chimera.eval.benchmarks.programbench.platform.system",
            return_value="Darwin",
        ):
            assert is_linux_amd64() is False


# ---------------------------------------------------------------------------
# parse_eval_json
# ---------------------------------------------------------------------------


class TestParseEvalJson:
    def test_summary_counts(self, sample_eval_json):
        s = parse_eval_json(sample_eval_json)
        assert s["passed"] == 2
        assert s["total"] == 3
        assert s["branches"] == 2
        assert s["error_code"] is None
        assert s["warnings"] == ["minor warn"]

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_eval_json(tmp_path / "nope.json")

    def test_empty_results(self, tmp_path):
        p = tmp_path / "empty.eval.json"
        p.write_text(json.dumps({"test_results": []}))
        assert parse_eval_json(p)["total"] == 0


# ---------------------------------------------------------------------------
# Evaluate orchestration (mocked subprocess)
# ---------------------------------------------------------------------------


class TestEvaluateOrchestration:
    def test_evaluate_calls_cli_and_parses_eval_json(self, tmp_path):
        # Stage a fake submission tarball
        submission = tmp_path / "submission.tar.gz"
        submission.write_bytes(b"\x1f\x8b")  # magic gzip header — content irrelevant

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        bench = ProgramBench(run_dir=str(run_dir))
        bench.add_instance(
            ProgramBenchInstance(
                instance_id="o__r.abc1234",
                repo="o/r",
                commit="abc1234",
                language="c",
            )
        )
        task = bench.tasks()[0]

        # Capture the cmd and write the eval.json the way the real CLI would.
        captured: dict[str, list[str]] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            inst_dir = run_dir / "o__r.abc1234"
            (inst_dir / "o__r.abc1234.eval.json").write_text(
                json.dumps(
                    {
                        "test_results": [
                            {"name": "t1", "branch": "b", "status": "passed"},
                            {"name": "t2", "branch": "b", "status": "passed"},
                        ],
                        "test_branches": ["b"],
                        "error_code": None,
                        "warnings": [],
                    }
                )
            )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        with (
            patch(
                "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
                return_value=None,
            ),
            patch(
                "chimera.eval.benchmarks.programbench.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            passed = bench.evaluate(task, str(submission))

        assert passed is True
        assert (run_dir / "o__r.abc1234" / "submission.tar.gz").exists()
        # The CLI command must include the run_dir, an instance filter, and
        # the image-tag argument.
        cmd = captured["cmd"]
        assert "programbench" in cmd[0] or cmd[0] == "programbench"
        assert "eval" in cmd
        assert str(run_dir) in cmd
        assert "--filter" in cmd
        assert "^o__r.abc1234$" in cmd
        assert "--image-tag" in cmd
        # default tag is task_cleanroom -> stripped to 'task' for CLI
        idx = cmd.index("--image-tag")
        assert cmd[idx + 1] == "task"

    def test_evaluate_returns_false_on_partial_failures(self, tmp_path):
        submission = tmp_path / "submission.tar.gz"
        submission.write_bytes(b"\x1f\x8b")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        bench = ProgramBench(run_dir=str(run_dir))
        bench.add_instance(
            ProgramBenchInstance(
                instance_id="o__r.abc1234",
                repo="o/r",
                commit="abc1234",
            )
        )

        def fake_run(cmd, **kwargs):
            inst_dir = run_dir / "o__r.abc1234"
            (inst_dir / "o__r.abc1234.eval.json").write_text(
                json.dumps(
                    {
                        "test_results": [
                            {"name": "t1", "branch": "b", "status": "passed"},
                            {"name": "t2", "branch": "b", "status": "failure"},
                        ],
                        "test_branches": ["b"],
                    }
                )
            )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
                return_value=None,
            ),
            patch(
                "chimera.eval.benchmarks.programbench.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            assert bench.evaluate(bench.tasks()[0], str(submission)) is False

    def test_evaluate_raises_when_cli_errors_without_eval_json(self, tmp_path):
        # A grader crash that writes no eval.json is an infrastructure failure
        # (e.g. Docker down), NOT a legitimate 0-score — it must be raised so a
        # sweep cannot silently report all-zeros.
        submission = tmp_path / "submission.tar.gz"
        submission.write_bytes(b"\x1f\x8b")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        bench = ProgramBench(run_dir=str(run_dir))
        bench.add_instance(
            ProgramBenchInstance(
                instance_id="o__r.abc1234", repo="o/r", commit="abc"
            )
        )

        def fake_run(cmd, **kwargs):
            raise subprocess.CalledProcessError(
                returncode=2, cmd=cmd, stderr="Cannot connect to the Docker daemon"
            )

        with (
            patch(
                "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
                return_value=None,
            ),
            patch(
                "chimera.eval.benchmarks.programbench.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            with pytest.raises(ProgramBenchGradingError, match="Docker daemon"):
                bench.evaluate(bench.tasks()[0], str(submission))

    def test_evaluate_uses_eval_json_even_when_cli_exits_nonzero(self, tmp_path):
        # If the grader DID write an eval.json, a non-zero CLI exit is just the
        # task failing — parse the result, do not raise.
        submission = tmp_path / "submission.tar.gz"
        submission.write_bytes(b"\x1f\x8b")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        bench = ProgramBench(run_dir=str(run_dir))
        bench.add_instance(
            ProgramBenchInstance(
                instance_id="o__r.abc1234", repo="o/r", commit="abc"
            )
        )

        def fake_run(cmd, **kwargs):
            inst_dir = run_dir / "o__r.abc1234"
            (inst_dir / "o__r.abc1234.eval.json").write_text(
                json.dumps(
                    {
                        "test_results": [
                            {"name": "t1", "branch": "b", "status": "failure"},
                        ],
                        "test_branches": ["b"],
                    }
                )
            )
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd)

        with (
            patch(
                "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
                return_value=None,
            ),
            patch(
                "chimera.eval.benchmarks.programbench.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            assert bench.evaluate(bench.tasks()[0], str(submission)) is False

    def test_evaluate_skips_when_cli_missing(self, tmp_path):
        submission = tmp_path / "submission.tar.gz"
        submission.write_bytes(b"\x1f\x8b")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        bench = ProgramBench(run_dir=str(run_dir))
        bench.add_instance(
            ProgramBenchInstance(
                instance_id="o__r.abc1234", repo="o/r", commit="abc"
            )
        )

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError(cmd[0])

        with (
            patch(
                "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
                return_value=None,
            ),
            patch(
                "chimera.eval.benchmarks.programbench.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            with pytest.raises(BenchmarkSkipped, match="programbench CLI"):
                bench.evaluate(bench.tasks()[0], str(submission))

    def test_evaluate_requires_run_dir(self, tmp_path):
        submission = tmp_path / "submission.tar.gz"
        submission.write_bytes(b"\x1f\x8b")
        bench = ProgramBench()
        bench.add_instance(
            ProgramBenchInstance(
                instance_id="o__r.abc1234", repo="o/r", commit="abc"
            )
        )
        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            with pytest.raises(BenchmarkSkipped, match="run_dir"):
                bench.evaluate(bench.tasks()[0], str(submission))

    def test_evaluate_skips_when_runtime_check_fails(self, tmp_path):
        submission = tmp_path / "submission.tar.gz"
        submission.write_bytes(b"\x1f\x8b")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        bench = ProgramBench(run_dir=str(run_dir))
        bench.add_instance(
            ProgramBenchInstance(
                instance_id="o__r.abc1234", repo="o/r", commit="abc"
            )
        )
        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            side_effect=BenchmarkSkipped("no docker"),
        ):
            with pytest.raises(BenchmarkSkipped):
                bench.evaluate(bench.tasks()[0], str(submission))


# ---------------------------------------------------------------------------
# Live integration — gated
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("CHIMERA_PROGRAMBENCH_LIVE") != "1",
    reason="Live ProgramBench run requires CHIMERA_PROGRAMBENCH_LIVE=1 + docker on linux/amd64",
)
class TestLiveIntegration:
    def test_smoke_invokes_real_cli(self, tmp_path):
        # This is a smoke test only — it runs against the real CLI with
        # an empty submission, so the failure path is the expected one.
        # We don't assert any score; we just check that the orchestration
        # round-trips without raising.
        if shutil.which("programbench") is None:
            pytest.skip("programbench CLI not installed")
        submission = tmp_path / "submission.tar.gz"
        # Empty gzip — CLI should produce an eval.json with errors.
        submission.write_bytes(
            b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        )
        run_dir = tmp_path / "live"
        run_dir.mkdir()
        bench = ProgramBench(run_dir=str(run_dir))
        bench.add_instance(
            ProgramBenchInstance(
                instance_id="abishekvashok__cmatrix.5c082c6",
                repo="abishekvashok/cmatrix",
                commit="5c082c64a1296859a11bee60c8c086655953a416",
                language="c",
                difficulty="easy",
            )
        )
        task = bench.tasks()[0]
        # The CLI may still error; we only require that we get a bool back
        # rather than an unhandled exception.
        result = bench.evaluate(task, str(submission))
        assert isinstance(result, bool)


# Defensive import-time check: the package must re-export ProgramBench.
def test_importable_from_package():
    from chimera.eval.benchmarks import (
        BenchmarkSkipped as PkgSkipped,
        ProgramBench as PkgPB,
        ProgramBenchInstance as PkgPBI,
    )

    assert PkgPB is ProgramBench
    assert PkgPBI is ProgramBenchInstance
    assert PkgSkipped is BenchmarkSkipped


def _ensure_path_used() -> Path:
    """Silence unused-import warnings for ``Path`` in this module."""
    return Path(".")
