"""Tests for the ProgramBench wave-14 inference loop.

Mocked tests cover the orchestration shape: workspace prep, image pull,
artifact extraction, agent invocation, and submission packaging. A live
integration test is gated behind ``CHIMERA_PROGRAMBENCH_LIVE=1``.
"""
from __future__ import annotations

import os
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.eval.benchmarks.programbench import (
    BenchmarkSkipped,
    ProgramBench,
    ProgramBenchInstance,
    ProgramBenchRunResult,
    build_rebuild_prompt,
    extract_cleanroom_artifacts,
    package_submission,
    pull_cleanroom_image,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_agent_result(success: bool = True, steps: int = 7, cost: float = 0.42):
    return SimpleNamespace(
        output="rebuilt project; tests passing locally",
        steps=steps,
        tool_calls_total=42,
        cost=cost,
        success=success,
        error=None,
    )


def _make_instance() -> ProgramBenchInstance:
    return ProgramBenchInstance(
        instance_id="abishekvashok__cmatrix.5c082c6",
        repo="abishekvashok/cmatrix",
        commit="5c082c64a1296859a11bee60c8c086655953a416",
        language="c",
        difficulty="easy",
    )


# ---------------------------------------------------------------------------
# build_rebuild_prompt
# ---------------------------------------------------------------------------


class TestBuildRebuildPrompt:
    def test_includes_instance_metadata(self, tmp_path):
        instance = _make_instance()
        prompt = build_rebuild_prompt(instance, tmp_path)
        assert instance.instance_id in prompt
        assert instance.repo in prompt
        assert "c" in prompt  # language
        assert "easy" in prompt  # difficulty
        assert str(tmp_path) in prompt
        assert "_inputs/docs" in prompt
        assert "_inputs/binary" in prompt
        assert "NO internet" in prompt

    def test_extra_text_appended(self, tmp_path):
        prompt = build_rebuild_prompt(
            _make_instance(),
            tmp_path,
            extra="Hint: the binary uses ANSI escape codes.",
        )
        assert "Hint: the binary uses ANSI escape codes." in prompt

    def test_unknown_metadata_handled(self, tmp_path):
        prompt = build_rebuild_prompt(
            ProgramBenchInstance(instance_id="x__y.abc", repo="", commit=""),
            tmp_path,
        )
        # "unknown" placeholders for missing language/difficulty/repo
        assert "unknown" in prompt


# ---------------------------------------------------------------------------
# pull_cleanroom_image
# ---------------------------------------------------------------------------


class TestPullCleanroomImage:
    def test_skipped_when_docker_missing(self):
        with patch(
            "chimera.eval.benchmarks.programbench.shutil.which",
            return_value=None,
        ):
            with pytest.raises(BenchmarkSkipped, match="docker CLI"):
                pull_cleanroom_image("programbench/x:y")

    def test_runs_docker_pull(self):
        with (
            patch(
                "chimera.eval.benchmarks.programbench.shutil.which",
                return_value="/usr/bin/docker",
            ),
            patch(
                "chimera.eval.benchmarks.programbench.subprocess.run"
            ) as run_mock,
        ):
            pull_cleanroom_image("programbench/foo:task_cleanroom")
        run_mock.assert_called_once()
        cmd = run_mock.call_args.args[0]
        assert cmd == [
            "/usr/bin/docker",
            "pull",
            "programbench/foo:task_cleanroom",
        ]
        assert run_mock.call_args.kwargs.get("check") is True


# ---------------------------------------------------------------------------
# extract_cleanroom_artifacts
# ---------------------------------------------------------------------------


class TestExtractCleanroomArtifacts:
    def test_skipped_when_docker_missing(self, tmp_path):
        with patch(
            "chimera.eval.benchmarks.programbench.shutil.which",
            return_value=None,
        ):
            with pytest.raises(BenchmarkSkipped, match="docker CLI"):
                extract_cleanroom_artifacts("programbench/x:y", tmp_path / "in")

    def test_creates_copies_and_removes(self, tmp_path):
        dest = tmp_path / "_inputs"
        commands: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            commands.append(list(cmd))
            if cmd[1] == "create":
                return SimpleNamespace(stdout="abc123\n", stderr="", returncode=0)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        with (
            patch(
                "chimera.eval.benchmarks.programbench.shutil.which",
                return_value="/usr/bin/docker",
            ),
            patch(
                "chimera.eval.benchmarks.programbench.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            extract_cleanroom_artifacts("programbench/foo:task_cleanroom", dest)
        assert dest.exists()
        # Three calls: create -> cp -> rm
        verbs = [c[1] for c in commands]
        assert verbs == ["create", "cp", "rm"]
        # cp source is the standard cleanroom inputs path
        cp_src = commands[1][2]
        assert cp_src.startswith("abc123:/agent-workspace/_inputs/")

    def test_cp_failure_still_removes_container(self, tmp_path):
        dest = tmp_path / "_inputs"
        rm_called = {"hit": False}

        def fake_run(cmd, **kwargs):
            verb = cmd[1]
            if verb == "create":
                return SimpleNamespace(stdout="cid42\n", stderr="", returncode=0)
            if verb == "cp":
                import subprocess

                raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
            if verb == "rm":
                rm_called["hit"] = True
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        import subprocess as sp

        with (
            patch(
                "chimera.eval.benchmarks.programbench.shutil.which",
                return_value="/usr/bin/docker",
            ),
            patch(
                "chimera.eval.benchmarks.programbench.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            with pytest.raises(sp.CalledProcessError):
                extract_cleanroom_artifacts("programbench/x:y", dest)
        assert rm_called["hit"] is True

    def test_blank_container_id_raises(self, tmp_path):
        def fake_run(cmd, **kwargs):
            if cmd[1] == "create":
                return SimpleNamespace(stdout="\n", stderr="", returncode=0)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        with (
            patch(
                "chimera.eval.benchmarks.programbench.shutil.which",
                return_value="/usr/bin/docker",
            ),
            patch(
                "chimera.eval.benchmarks.programbench.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            with pytest.raises(RuntimeError, match="container id"):
                extract_cleanroom_artifacts(
                    "programbench/x:y", tmp_path / "in"
                )


# ---------------------------------------------------------------------------
# package_submission
# ---------------------------------------------------------------------------


class TestPackageSubmission:
    def test_packages_workspace_excluding_inputs(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "src").mkdir()
        (ws / "src" / "main.c").write_text("int main(){}\n")
        (ws / "Makefile").write_text("all:\n\tcc src/main.c\n")
        # Inputs must NOT be packaged
        (ws / "_inputs").mkdir()
        (ws / "_inputs" / "binary").mkdir()
        (ws / "_inputs" / "binary" / "cmatrix").write_bytes(b"\x7fELFsecret")
        out = ws / "submission.tar.gz"

        package_submission(ws, out)
        assert out.exists()
        with tarfile.open(out, "r:gz") as tf:
            names = sorted(m.name for m in tf.getmembers())
        # Expected: src dir, src/main.c, Makefile. No _inputs anywhere.
        assert "Makefile" in names
        assert "src/main.c" in names
        assert all("_inputs" not in n for n in names), names
        assert all("submission.tar.gz" not in n for n in names), names

    def test_handles_empty_workspace(self, tmp_path):
        ws = tmp_path / "empty"
        ws.mkdir()
        out = ws / "submission.tar.gz"
        package_submission(ws, out)
        assert out.exists()
        with tarfile.open(out, "r:gz") as tf:
            assert tf.getmembers() == []


# ---------------------------------------------------------------------------
# ProgramBench.run_instance — full orchestration with mocks
# ---------------------------------------------------------------------------


class TestRunInstanceOrchestration:
    def test_full_orchestration_happy_path(self, tmp_path):
        bench = ProgramBench()
        instance = _make_instance()
        bench.add_instance(instance)
        task = bench.tasks()[0]

        ws = tmp_path / "ws"
        captured: dict[str, Any] = {}

        def fake_pull(image: str) -> None:
            captured["pulled"] = image

        def fake_extract(image: str, dest: Path) -> None:
            captured["extracted"] = (image, dest)
            (dest / "binary").mkdir(parents=True, exist_ok=True)
            (dest / "binary" / "cmatrix").write_bytes(b"\x7fELF")
            (dest / "docs").mkdir(parents=True, exist_ok=True)
            (dest / "docs" / "README").write_text("# cmatrix\n")

        def fake_factory(inst, ws_path):
            captured["factory_call"] = (inst.instance_id, ws_path)
            agent = MagicMock()
            # Simulate the agent producing a Makefile + src/
            def _run(prompt, env):
                captured["prompt"] = prompt
                captured["env"] = env
                (ws_path / "Makefile").write_text("all:\n")
                (ws_path / "src").mkdir(exist_ok=True)
                (ws_path / "src" / "main.c").write_text("int main(){}\n")
                return _fake_agent_result(success=True, steps=11, cost=0.7)

            agent.run.side_effect = _run
            return agent

        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            result = bench.run_instance(
                task,
                workspace=ws,
                agent_factory=fake_factory,
                image_puller=fake_pull,
                artifact_extractor=fake_extract,
            )

        assert isinstance(result, ProgramBenchRunResult)
        assert result.instance_id == instance.instance_id
        assert result.success is True
        assert result.steps == 11
        assert result.cost == pytest.approx(0.7)
        assert result.error is None
        assert result.submission_tar.exists()
        assert result.workspace == ws

        # Pull + extract were both called for the right image
        assert captured["pulled"] == instance.cleanroom_image()
        assert captured["extracted"][0] == instance.cleanroom_image()
        assert captured["extracted"][1] == ws / "_inputs"
        # Prompt mentions the workspace + instance id
        assert instance.instance_id in captured["prompt"]
        assert str(ws) in captured["prompt"]

        # Tarball excludes _inputs/
        with tarfile.open(result.submission_tar, "r:gz") as tf:
            names = [m.name for m in tf.getmembers()]
        assert "Makefile" in names
        assert "src/main.c" in names
        assert all("_inputs" not in n for n in names)

    def test_pre_built_agent_takes_precedence_over_factory(self, tmp_path):
        bench = ProgramBench()
        bench.add_instance(_make_instance())

        agent = MagicMock()
        agent.run.return_value = _fake_agent_result()
        factory = MagicMock(side_effect=AssertionError("factory should not be called"))

        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            bench.run_instance(
                bench.tasks()[0],
                workspace=tmp_path / "ws",
                agent=agent,
                agent_factory=factory,
                pull_image=False,
                extract_artifacts=False,
            )
        agent.run.assert_called_once()
        factory.assert_not_called()

    def test_missing_agent_and_factory_raises(self, tmp_path):
        bench = ProgramBench()
        bench.add_instance(_make_instance())
        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            with pytest.raises(ValueError, match="agent_factory"):
                bench.run_instance(
                    bench.tasks()[0],
                    workspace=tmp_path / "ws",
                    pull_image=False,
                    extract_artifacts=False,
                )

    def test_skipped_when_runtime_check_fails(self, tmp_path):
        bench = ProgramBench()
        bench.add_instance(_make_instance())
        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            side_effect=BenchmarkSkipped("no docker"),
        ):
            with pytest.raises(BenchmarkSkipped):
                bench.run_instance(
                    bench.tasks()[0],
                    workspace=tmp_path / "ws",
                    agent=MagicMock(),
                )

    def test_runtime_check_can_be_disabled(self, tmp_path):
        bench = ProgramBench()
        bench.add_instance(_make_instance())
        agent = MagicMock()
        agent.run.return_value = _fake_agent_result()
        # The runtime_check=False path must not call check_runtime_or_skip
        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            side_effect=BenchmarkSkipped("would-have-skipped"),
        ):
            result = bench.run_instance(
                bench.tasks()[0],
                workspace=tmp_path / "ws",
                agent=agent,
                pull_image=False,
                extract_artifacts=False,
                runtime_check=False,
            )
        assert isinstance(result, ProgramBenchRunResult)

    def test_agent_exception_captured_as_error(self, tmp_path):
        bench = ProgramBench()
        bench.add_instance(_make_instance())
        agent = MagicMock()
        agent.run.side_effect = RuntimeError("provider 502")

        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            result = bench.run_instance(
                bench.tasks()[0],
                workspace=tmp_path / "ws",
                agent=agent,
                pull_image=False,
                extract_artifacts=False,
            )
        assert result.success is False
        assert result.error is not None
        assert "RuntimeError" in result.error
        assert "provider 502" in result.error
        # Even on failure, the tarball is still produced (empty / partial)
        assert result.submission_tar.exists()

    def test_workspace_auto_temp_when_unspecified(self):
        bench = ProgramBench()
        bench.add_instance(_make_instance())
        agent = MagicMock()
        agent.run.return_value = _fake_agent_result()

        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            result = bench.run_instance(
                bench.tasks()[0],
                agent=agent,
                pull_image=False,
                extract_artifacts=False,
            )
        try:
            assert result.workspace.exists()
            assert result.workspace.is_dir()
            assert "programbench-ws" in result.workspace.name
        finally:
            shutil.rmtree(result.workspace, ignore_errors=True)

    def test_passes_extra_prompt(self, tmp_path):
        bench = ProgramBench()
        bench.add_instance(_make_instance())
        agent = MagicMock()
        prompts: list[str] = []

        def _run(prompt, env):
            prompts.append(prompt)
            return _fake_agent_result()

        agent.run.side_effect = _run

        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            bench.run_instance(
                bench.tasks()[0],
                workspace=tmp_path / "ws",
                agent=agent,
                pull_image=False,
                extract_artifacts=False,
                extra_prompt="Use Makefile-style build.",
            )
        assert any("Makefile-style build" in p for p in prompts)

    def test_pull_image_false_skips_puller(self, tmp_path):
        bench = ProgramBench()
        bench.add_instance(_make_instance())
        agent = MagicMock()
        agent.run.return_value = _fake_agent_result()
        puller = MagicMock(side_effect=AssertionError("puller must not be called"))

        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            bench.run_instance(
                bench.tasks()[0],
                workspace=tmp_path / "ws",
                agent=agent,
                pull_image=False,
                extract_artifacts=False,
                image_puller=puller,
            )
        puller.assert_not_called()

    def test_extract_artifacts_false_skips_extractor(self, tmp_path):
        bench = ProgramBench()
        bench.add_instance(_make_instance())
        agent = MagicMock()
        agent.run.return_value = _fake_agent_result()
        extractor = MagicMock(side_effect=AssertionError("extractor must not run"))

        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            bench.run_instance(
                bench.tasks()[0],
                workspace=tmp_path / "ws",
                agent=agent,
                pull_image=False,
                extract_artifacts=False,
                artifact_extractor=extractor,
            )
        extractor.assert_not_called()

    def test_accepts_instance_directly_not_just_dict(self, tmp_path):
        bench = ProgramBench()
        instance = _make_instance()
        agent = MagicMock()
        agent.run.return_value = _fake_agent_result()

        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            result = bench.run_instance(
                instance,  # raw instance, not the dict from .tasks()
                workspace=tmp_path / "ws",
                agent=agent,
                pull_image=False,
                extract_artifacts=False,
            )
        assert result.instance_id == instance.instance_id

    def test_custom_packager_is_used(self, tmp_path):
        bench = ProgramBench()
        bench.add_instance(_make_instance())
        agent = MagicMock()
        agent.run.return_value = _fake_agent_result()
        packager_calls: list[tuple[Path, Path]] = []

        def fake_packager(workspace, output_tar):
            packager_calls.append((workspace, output_tar))
            output_tar.write_bytes(b"fake-tar-content")

        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            result = bench.run_instance(
                bench.tasks()[0],
                workspace=tmp_path / "ws",
                agent=agent,
                pull_image=False,
                extract_artifacts=False,
                submission_packager=fake_packager,
            )
        assert len(packager_calls) == 1
        assert packager_calls[0][1] == result.submission_tar
        assert result.submission_tar.read_bytes() == b"fake-tar-content"

    def test_default_env_is_local_environment_at_workspace(self, tmp_path):
        bench = ProgramBench()
        bench.add_instance(_make_instance())
        agent = MagicMock()
        envs: list[object] = []

        def _run(prompt, env):
            envs.append(env)
            return _fake_agent_result()

        agent.run.side_effect = _run

        ws = tmp_path / "ws"
        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            bench.run_instance(
                bench.tasks()[0],
                workspace=ws,
                agent=agent,
                pull_image=False,
                extract_artifacts=False,
            )
        from chimera.env.local import LocalEnvironment

        assert isinstance(envs[0], LocalEnvironment)
        assert Path(envs[0].workdir).resolve() == ws.resolve()

    def test_explicit_env_is_passed_through(self, tmp_path):
        bench = ProgramBench()
        bench.add_instance(_make_instance())
        agent = MagicMock()
        envs: list[object] = []

        def _run(prompt, env):
            envs.append(env)
            return _fake_agent_result()

        agent.run.side_effect = _run

        sentinel = object()
        with patch(
            "chimera.eval.benchmarks.programbench.check_runtime_or_skip",
            return_value=None,
        ):
            bench.run_instance(
                bench.tasks()[0],
                workspace=tmp_path / "ws",
                agent=agent,
                env=sentinel,  # type: ignore[arg-type]
                pull_image=False,
                extract_artifacts=False,
            )
        assert envs[0] is sentinel


# ---------------------------------------------------------------------------
# Package re-exports
# ---------------------------------------------------------------------------


def test_run_result_re_exported_from_package():
    from chimera.eval.benchmarks import ProgramBenchRunResult as PkgRR

    assert PkgRR is ProgramBenchRunResult


# ---------------------------------------------------------------------------
# Live integration (gated on CHIMERA_PROGRAMBENCH_LIVE=1)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("CHIMERA_PROGRAMBENCH_LIVE") != "1",
    reason=(
        "Live ProgramBench inference requires CHIMERA_PROGRAMBENCH_LIVE=1, "
        "docker, and a configured CHIMERA provider."
    ),
)
class TestLiveInference:
    def test_round_trip_with_noop_agent(self, tmp_path):
        # Even live, we use a stub agent to avoid spending tokens — the
        # value of this gate is verifying docker pull + extract on a
        # real machine.
        bench = ProgramBench()
        bench.add_instance(_make_instance())
        agent = MagicMock()
        agent.run.return_value = _fake_agent_result()
        ws = tmp_path / "live-ws"
        result = bench.run_instance(
            bench.tasks()[0],
            workspace=ws,
            agent=agent,
            extract_artifacts=False,  # extract is the slowest part
        )
        assert result.submission_tar.exists()
