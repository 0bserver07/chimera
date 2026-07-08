"""chimera bench-modal — CLI wiring + command construction (no live Modal)."""

from __future__ import annotations

import argparse

from chimera.cli.bench_modal import (
    add_bench_modal_parser,
    build_modal_command,
    run_bench_modal,
)


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers()
    add_bench_modal_parser(sub)
    return p.parse_args(argv)


def test_parser_defaults_and_overrides() -> None:
    a = _parse(["bench-modal"])
    assert a.agents == "coding-agent,react" and a.benches == "mbpp" and a.limit == 5
    a = _parse([
        "bench-modal", "--agents", "react,reflexion", "--benches", "mbpp,livecodebench",
        "--limit", "25", "--gpu", "T4", "--model", "glm-5.2[1m]",
    ])
    assert a.agents == "react,reflexion"
    assert a.benches == "mbpp,livecodebench"
    assert a.limit == 25 and a.gpu == "T4"


def test_build_modal_command_grid() -> None:
    from pathlib import Path

    a = _parse(["bench-modal", "--agents", "react", "--benches", "mbpp", "--limit", "3"])
    cmd = build_modal_command(a, Path("/x/scripts/modal_bench_app.py"))
    assert cmd[:3] == ["modal", "run", "/x/scripts/modal_bench_app.py::grid"]
    assert "--agents" in cmd and "react" in cmd
    assert "--limit" in cmd and "3" in cmd
    assert "--gpu" not in cmd  # omitted when empty


def test_build_modal_command_gpu() -> None:
    from pathlib import Path

    a = _parse(["bench-modal", "--gpu", "A100"])
    cmd = build_modal_command(a, Path("/x/app.py"))
    assert "--gpu" in cmd and "A100" in cmd


def test_run_without_modal_cli_returns_2(monkeypatch) -> None:
    monkeypatch.setattr("chimera.cli.bench_modal.shutil.which", lambda _: None)
    a = _parse(["bench-modal"])
    assert run_bench_modal(a) == 2  # no modal CLI → clean rc 2, no crash
