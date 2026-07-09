"""SWE-bench per-instance image on Modal — offline unit tests.

Covers the three cooperating pieces added to
:mod:`chimera.eval.benchmarks.swe_bench`:

* :func:`swe_instance_image` — task dict -> image identifier (the official
  ``swebench/sweb.eval.<arch>.<id>:<tag>`` convention with ``__`` -> ``_1776_``,
  plus explicit-field overrides).
* :func:`swe_modal_env_factory` — task -> configured (unstarted)
  ``ModalSandboxEnvironment`` on that image.
* :class:`SweModalEnvFactory` — the zero-argument Harness bridge that walks the
  task list in lockstep and cycles across agent rows.

Modal is stubbed exactly as ``tests/env/test_modal_gpu.py`` does — a fake
``modal`` module captures what would reach ``modal.Sandbox.create`` — so no
cloud credentials or the ``modal`` package are needed.
"""
from __future__ import annotations

import types

import pytest

import chimera.env.modal_sandbox as ms
from chimera.eval.benchmarks.swe_bench import (
    DEFAULT_SWE_WORKDIR,
    SWEBench,
    SWEBenchInstance,
    SweModalEnvFactory,
    swe_instance_image,
    swe_modal_env_factory,
)


# --------------------------------------------------------------------------- #
# Modal stub (mirrors tests/env/test_modal_gpu.py)
# --------------------------------------------------------------------------- #


class _FakeSandbox:
    created: dict = {}

    @classmethod
    def create(cls, **kwargs):
        cls.created = dict(kwargs)
        return types.SimpleNamespace(terminate=lambda: None)


class _FakeApp:
    def __init__(self, name: str) -> None:
        self.name = name


def _fake_modal() -> types.SimpleNamespace:
    # No ``Image`` attr → _build_image returns the plain image string, so the
    # created kwargs carry the exact resolved image identifier.
    return types.SimpleNamespace(App=_FakeApp, Sandbox=_FakeSandbox)


# --------------------------------------------------------------------------- #
# swe_instance_image — resolution logic
# --------------------------------------------------------------------------- #


class TestSweInstanceImage:
    def test_official_convention(self) -> None:
        img = swe_instance_image({"instance_id": "django__django-12325"})
        assert img == (
            "swebench/sweb.eval.x86_64.django_1776_django-12325:latest"
        )

    def test_dunder_rewritten_to_1776(self) -> None:
        # The ``__`` run in the instance id must become ``_1776_``.
        img = swe_instance_image({"instance_id": "sympy__sympy-20438"})
        assert "__" not in img
        assert "sympy_1776_sympy-20438" in img

    def test_lowercased(self) -> None:
        img = swe_instance_image({"instance_id": "Astropy__Astropy-13236"})
        assert img == (
            "swebench/sweb.eval.x86_64.astropy_1776_astropy-13236:latest"
        )

    def test_id_field_fallback(self) -> None:
        # ``SWEBench.to_task`` sets ``id``; resolver reads it when
        # ``instance_id`` is absent.
        img = swe_instance_image({"id": "pytest-dev__pytest-5495"})
        assert img == (
            "swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-5495:latest"
        )

    def test_explicit_docker_image_wins(self) -> None:
        img = swe_instance_image(
            {"instance_id": "django__django-1", "docker_image": "my/custom:tag"}
        )
        assert img == "my/custom:tag"

    def test_explicit_image_field_wins(self) -> None:
        img = swe_instance_image(
            {"instance_id": "django__django-1", "image": "pinned/img:v2"}
        )
        assert img == "pinned/img:v2"

    def test_empty_explicit_falls_back_to_convention(self) -> None:
        # An empty docker_image (common when a dataset column is blank) must not
        # shadow the computed convention.
        img = swe_instance_image(
            {"instance_id": "flask__flask-5014", "docker_image": ""}
        )
        assert img == "swebench/sweb.eval.x86_64.flask_1776_flask-5014:latest"

    def test_missing_id_raises(self) -> None:
        with pytest.raises(ValueError, match="instance_id"):
            swe_instance_image({"repo": "a/b"})

    def test_empty_namespace_yields_local_key(self) -> None:
        # Local (un-namespaced) key: no ``_1776_`` rewrite, raw ``__`` kept.
        img = swe_instance_image(
            {"instance_id": "django__django-12325"}, namespace=""
        )
        assert img == "sweb.eval.x86_64.django__django-12325:latest"

    def test_arch_and_tag_override(self) -> None:
        img = swe_instance_image(
            {"instance_id": "django__django-1"}, arch="arm64", tag="v1"
        )
        assert img == "swebench/sweb.eval.arm64.django_1776_django-1:v1"

    def test_custom_namespace(self) -> None:
        img = swe_instance_image(
            {"instance_id": "django__django-1"}, namespace="myorg"
        )
        assert img == "myorg/sweb.eval.x86_64.django_1776_django-1:latest"


# --------------------------------------------------------------------------- #
# swe_modal_env_factory — task -> env
# --------------------------------------------------------------------------- #


class TestSweModalEnvFactory:
    def test_env_uses_instance_image(self) -> None:
        env = swe_modal_env_factory({"instance_id": "django__django-12325"})
        assert env.image == (
            "swebench/sweb.eval.x86_64.django_1776_django-12325:latest"
        )

    def test_default_workdir_is_testbed(self) -> None:
        env = swe_modal_env_factory({"instance_id": "x__y-1"})
        # /testbed is where the official images place the repo tree.
        assert DEFAULT_SWE_WORKDIR == "/testbed"
        assert env._workdir == "/testbed"

    def test_fixed_image_override(self) -> None:
        env = swe_modal_env_factory(
            {"instance_id": "django__django-1"}, image="python:3.11-slim"
        )
        assert env.image == "python:3.11-slim"

    def test_gpu_forwarded(self) -> None:
        env = swe_modal_env_factory({"instance_id": "x__y-1"}, gpu="A100")
        assert env._gpu == "A100"

    def test_setup_forwards_resolved_image_to_sandbox_create(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _FakeSandbox.created = {}
        monkeypatch.setattr(ms, "modal", _fake_modal())

        env = swe_modal_env_factory(
            {"instance_id": "django__django-12325"}, gpu="T4"
        )
        env.setup()

        assert _FakeSandbox.created.get("image") == (
            "swebench/sweb.eval.x86_64.django_1776_django-12325:latest"
        )
        assert _FakeSandbox.created.get("gpu") == "T4"
        assert _FakeSandbox.created.get("workdir") == "/testbed"


# --------------------------------------------------------------------------- #
# SweModalEnvFactory — the zero-arg Harness bridge
# --------------------------------------------------------------------------- #


class TestSweModalEnvFactoryBridge:
    def _tasks(self) -> list[dict[str, str]]:
        return [
            {"instance_id": "django__django-1"},
            {"instance_id": "flask__flask-2"},
            {"instance_id": "sympy__sympy-3"},
        ]

    def test_lockstep_ordering(self) -> None:
        # k-th zero-arg call -> env on the k-th task's per-instance image, in
        # the same order the Harness iterates the task list.
        factory = SweModalEnvFactory(self._tasks())
        images = [factory().image for _ in range(3)]
        assert images == [
            "swebench/sweb.eval.x86_64.django_1776_django-1:latest",
            "swebench/sweb.eval.x86_64.flask_1776_flask-2:latest",
            "swebench/sweb.eval.x86_64.sympy_1776_sympy-3:latest",
        ]

    def test_cycles_across_agent_rows(self) -> None:
        # A bench-matrix run makes one full pass per agent row; modulo indexing
        # lines the second row up with the first without a manual reset.
        factory = SweModalEnvFactory(self._tasks())
        first_row = [factory().image for _ in range(3)]
        second_row = [factory().image for _ in range(3)]
        assert first_row == second_row

    def test_images_property_matches_task_order(self) -> None:
        factory = SweModalEnvFactory(self._tasks())
        assert factory.images == [
            "swebench/sweb.eval.x86_64.django_1776_django-1:latest",
            "swebench/sweb.eval.x86_64.flask_1776_flask-2:latest",
            "swebench/sweb.eval.x86_64.sympy_1776_sympy-3:latest",
        ]

    def test_reset_rewinds_cursor(self) -> None:
        factory = SweModalEnvFactory(self._tasks())
        first = factory().image
        factory.reset()
        assert factory().image == first

    def test_empty_task_list_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            SweModalEnvFactory([])

    def test_bad_instance_id_fails_fast_at_construction(self) -> None:
        # Eager resolution: a task with no id/image raises here, not mid-run.
        with pytest.raises(ValueError, match="instance_id"):
            SweModalEnvFactory([{"repo": "a/b"}])

    def test_gpu_and_fixed_image_applied_to_every_env(self) -> None:
        factory = SweModalEnvFactory(
            self._tasks(), gpu="H100", image="python:3.11-slim"
        )
        for _ in range(3):
            env = factory()
            assert env._gpu == "H100"
            assert env.image == "python:3.11-slim"

    def test_setup_of_bridged_env_forwards_per_instance_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _FakeSandbox.created = {}
        monkeypatch.setattr(ms, "modal", _fake_modal())

        factory = SweModalEnvFactory(self._tasks())
        factory()  # skip the first task
        env = factory()  # second task -> flask
        env.setup()

        assert _FakeSandbox.created.get("image") == (
            "swebench/sweb.eval.x86_64.flask_1776_flask-2:latest"
        )


# --------------------------------------------------------------------------- #
# SWEBenchInstance / loader — explicit image round-trips
# --------------------------------------------------------------------------- #


class TestInstanceImageRoundTrip:
    def test_instance_carries_explicit_image(self) -> None:
        inst = SWEBenchInstance(
            instance_id="t1",
            repo="r/r",
            base_commit="c1",
            problem_statement="desc",
            image="my/pinned:img",
        )
        task = inst.to_task()
        assert task["docker_image"] == "my/pinned:img"
        assert task["instance_id"] == "t1"
        # And the resolver honors it over the computed convention.
        assert swe_instance_image(task) == "my/pinned:img"

    def test_instance_without_image_derives_convention(self) -> None:
        inst = SWEBenchInstance(
            instance_id="django__django-99",
            repo="django/django",
            base_commit="c1",
            problem_statement="desc",
        )
        task = inst.to_task()
        assert task["docker_image"] == ""
        assert swe_instance_image(task) == (
            "swebench/sweb.eval.x86_64.django_1776_django-99:latest"
        )

    def test_loader_reads_docker_image_field(self, tmp_path) -> None:
        import json

        data = [
            {
                "instance_id": "django__django-1",
                "repo": "django/django",
                "base_commit": "abc",
                "problem_statement": "fix it",
                "docker_image": "explicit/image:tag",
            }
        ]
        path = tmp_path / "swe.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        bench = SWEBench(dataset_path=str(path))
        task = bench.tasks()[0]
        assert task["docker_image"] == "explicit/image:tag"
        assert swe_instance_image(task) == "explicit/image:tag"

    def test_loader_reads_image_name_alias(self, tmp_path) -> None:
        import json

        data = [
            {
                "instance_id": "flask__flask-1",
                "problem_statement": "x",
                "image_name": "aliased/image:tag",
            }
        ]
        path = tmp_path / "swe.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        bench = SWEBench(dataset_path=str(path))
        assert bench.tasks()[0]["docker_image"] == "aliased/image:tag"


# --------------------------------------------------------------------------- #
# End-to-end: the real Harness loop drives the zero-arg factory, and each task
# lands in ITS per-instance image. This exercises the exact seam bench-matrix
# --env swe-modal relies on (Harness.run calls env_factory() with no task).
# --------------------------------------------------------------------------- #


class _RecordingAgent:
    """Fake agent recording the image of the env it is handed per task."""

    def __init__(self) -> None:
        self.seen_images: list[str] = []

    def run(self, prompt: str, env=None):
        from chimera.types import AgentResult

        del prompt  # part of the Harness-driven signature; unused here
        self.seen_images.append(env.image if env is not None else "")
        return AgentResult(
            output="done", steps=1, tool_calls_total=0, cost=0.0, success=True
        )


class TestHarnessIntegration:
    def test_each_task_runs_in_its_instance_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from chimera.eval.harness import Harness

        # Stub modal so Harness's per-task env.setup() uses the fake sandbox
        # path instead of trying to build a real registry image (deterministic
        # whether or not the modal package is installed).
        monkeypatch.setattr(ms, "modal", _fake_modal())

        bench = SWEBench()
        for iid in ("django__django-1", "flask__flask-2", "sympy__sympy-3"):
            bench.add_instance(
                SWEBenchInstance(
                    instance_id=iid,
                    repo="r/r",
                    base_commit="c",
                    problem_statement="fix it",
                )
            )

        agent = _RecordingAgent()
        # The exact wiring the CLI diff performs: build the bridge from the
        # benchmark's task list, hand it to the Harness as the env_factory.
        factory = SweModalEnvFactory(bench.tasks())
        Harness(bench, agent, env_factory=factory).run()

        assert agent.seen_images == [
            "swebench/sweb.eval.x86_64.django_1776_django-1:latest",
            "swebench/sweb.eval.x86_64.flask_1776_flask-2:latest",
            "swebench/sweb.eval.x86_64.sympy_1776_sympy-3:latest",
        ]
