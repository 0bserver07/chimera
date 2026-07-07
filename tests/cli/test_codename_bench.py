"""T4.7 — codename `bench` delegates to the canonical harness (one harness)."""

from __future__ import annotations

import argparse

from chimera.cli.codename_bench import dispatch_codename_bench


def test_list_path_returns_zero(capsys) -> None:
    args = argparse.Namespace(sub_action="list")
    rc = dispatch_codename_bench(args, "ferret", runner=lambda ns: 99)
    assert rc == 0
    err = capsys.readouterr().err
    assert "registered suites" in err
    assert "human-eval" in err  # a known registered bench name


def test_no_suite_lists(capsys) -> None:
    args = argparse.Namespace(sub_action=None)
    assert dispatch_codename_bench(args, "stoat", runner=lambda ns: 99) == 0
    assert "stoat bench" in capsys.readouterr().err


def test_unknown_suite_returns_two(capsys) -> None:
    args = argparse.Namespace(sub_action="not-a-real-bench")
    rc = dispatch_codename_bench(args, "badger", runner=lambda ns: 99)
    assert rc == 2
    assert "unknown suite" in capsys.readouterr().err


def test_known_suite_delegates_with_defaults() -> None:
    captured: dict[str, object] = {}

    def fake_runner(ns: argparse.Namespace) -> int:
        captured["ns"] = ns
        return 0

    args = argparse.Namespace(sub_action="human-eval")
    rc = dispatch_codename_bench(args, "ferret", runner=fake_runner)
    assert rc == 0
    ns = captured["ns"]
    assert ns.benchmarks == "human-eval"
    assert ns.agents == "react"  # default single agent
    assert ns.limit == 1
    assert ns.max_cost == 0.15
    assert ns.env_kind == "local"


def test_known_suite_honors_frontend_overrides() -> None:
    captured: dict[str, object] = {}

    args = argparse.Namespace(
        sub_action="human-eval", model="glm-5.2[1m]", limit=5, agents="react,reflexion",
    )
    dispatch_codename_bench(args, "badger", runner=lambda ns: captured.setdefault("ns", ns) or 0)
    ns = captured["ns"]
    assert ns.model == "glm-5.2[1m]"
    assert ns.limit == 5
    assert ns.agents == "react,reflexion"
