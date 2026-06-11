"""Docker-gated end-to-end test for Harbor task provisioning.

Runs only when the optional ``docker`` package is installed AND a Docker
daemon is reachable; otherwise skips. Uses a small stock image instead
of a real (multi-GB, per-task) Harbor image — the point is to prove the
provisioning + verifier plumbing end to end, not to grade a real task.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.eval.benchmarks.harbor import HarborBenchmark, docker_env_factory

docker = pytest.importorskip("docker", reason="optional docker package not installed")

TEST_IMAGE = "python:3.11-slim"


def _daemon_reachable() -> bool:
    try:
        docker.from_env().ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _daemon_reachable(), reason="no reachable Docker daemon"
)


def _write_patchless_task(root: Path, name: str) -> Path:
    task_dir = root / name
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "task.toml").write_text(
        f'schema_version = "1.1"\n[metadata]\ntask_id = "{name}"\n'
        '[environment]\ndocker_image = "example.test/never-pulled:latest"\n',
        encoding="utf-8",
    )
    (task_dir / "instruction.md").write_text("Make the verifier pass.\n", encoding="utf-8")
    (task_dir / "tests" / "test.sh").write_text(
        "#!/bin/bash\nset -uo pipefail\ntest -f marker.txt\n", encoding="utf-8"
    )
    return task_dir


def test_factory_requires_an_image() -> None:
    with pytest.raises(ValueError, match="no docker_image"):
        docker_env_factory({"docker_image": ""})


def test_verifier_flow_inside_a_real_container(tmp_path: Path) -> None:
    _write_patchless_task(tmp_path, "docker-smoke")
    bench = HarborBenchmark(dataset_path=str(tmp_path))
    task = bench.tasks()[0]

    env = docker_env_factory(task, image_override=TEST_IMAGE)
    env.setup()
    try:
        # The verifier script requires marker.txt; prove both outcomes.
        assert bench.evaluate(task, "", env) is False

        env.write_file("marker.txt", "agent was here\n")
        result = env.run_command("cat marker.txt")
        assert result.success

        assert bench.evaluate(task, "", env) is True
    finally:
        env.cleanup()
