"""Modal-on-benches: GPU sandbox knob + `bench-matrix --env modal` wiring.

Runs offline — a stub `modal` module captures what would be sent to
`modal.Sandbox.create`, so no cloud creds are needed. (The live path is
exercised separately by the creds-gated tests/env/test_modal_sandbox.py.)
"""

from __future__ import annotations

import types

import chimera.env.modal_sandbox as ms
from chimera.env.modal_sandbox import ModalSandboxEnvironment


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
    # No `Image` attr → _build_image returns the plain image string.
    return types.SimpleNamespace(App=_FakeApp, Sandbox=_FakeSandbox)


def test_gpu_forwarded_to_sandbox_create(monkeypatch) -> None:
    _FakeSandbox.created = {}
    monkeypatch.setattr(ms, "modal", _fake_modal())
    env = ModalSandboxEnvironment(gpu="H100", image="python:3.11-slim")
    env.setup()
    assert _FakeSandbox.created.get("gpu") == "H100"
    assert _FakeSandbox.created.get("image") == "python:3.11-slim"


def test_no_gpu_means_cpu_only(monkeypatch) -> None:
    _FakeSandbox.created = {}
    monkeypatch.setattr(ms, "modal", _fake_modal())
    env = ModalSandboxEnvironment()  # gpu defaults to None
    env.setup()
    assert "gpu" not in _FakeSandbox.created  # CPU-only sandbox


def test_gpu_multi_spec_forwarded(monkeypatch) -> None:
    _FakeSandbox.created = {}
    monkeypatch.setattr(ms, "modal", _fake_modal())
    ModalSandboxEnvironment(gpu="A100:2").setup()
    assert _FakeSandbox.created.get("gpu") == "A100:2"


def test_bench_matrix_parser_accepts_modal_flags() -> None:
    import argparse

    from chimera.cli.bench_matrix import add_bench_matrix_parser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    add_bench_matrix_parser(sub)
    args = parser.parse_args(
        ["bench-matrix", "--agents", "react", "--benchmarks", "human-eval",
         "--env", "modal", "--modal-gpu", "H100", "--modal-image", "python:3.12"]
    )
    assert args.env_kind == "modal"
    assert args.modal_gpu == "H100"
    assert args.modal_image == "python:3.12"


def test_env_modal_without_creds_exits_2(monkeypatch) -> None:
    """--env modal with no Modal auth fails loudly (rc 2), never silently local."""
    import argparse

    from chimera.cli.bench_matrix import run_bench_matrix

    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    args = argparse.Namespace(
        agents="react", benchmarks="human-eval", model="glm-5.2", limit=1,
        dataset=None, registry=None, max_tool_calls=5, max_llm_calls=5,
        max_wall_clock=None, max_cost=0.05, fmt="terminal", output=None,
        env_kind="modal", modal_gpu="H100", modal_image="python:3.11-slim",
    )
    assert run_bench_matrix(args) == 2
