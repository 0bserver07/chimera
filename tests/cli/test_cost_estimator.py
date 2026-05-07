"""Tests for the wave-11 A8 pre-flight cost-estimation helper + CLI flags.

Covers two surfaces:

1. The pure helper module ``chimera.cli.cost_estimator`` — the
   :func:`estimate_cost` math, the unknown-model convention, and the
   :func:`format_estimate` rendering.
2. The ``chimera otter`` wiring — ``--estimate-cost`` prints + exits 0,
   ``--max-cost`` refuses with rc=2 when exceeded and proceeds otherwise.

Tests stay hermetic: no live provider, no network. The ``--max-cost
proceeds when under`` test stubs the agent/provider boundary so we
never reach a real ``async_run``.
"""
from __future__ import annotations

import argparse
import json

import pytest

from chimera.cli.cost_estimator import (
    CostEstimate,
    ModelNotPriced,
    estimate_cost,
    format_estimate,
)
from chimera.otter import cli as otter_cli


# ---------------------------------------------------------------------------
# Helper: pure cost-estimation math
# ---------------------------------------------------------------------------


def test_estimate_known_model() -> None:
    """``glm-5`` returns a positive cost matching the manual calc.

    glm-5 is priced at $2/MTOK input, $8/MTOK output (see PRICING).
    With a 40-char prompt → 10 input tokens + 2048 output tokens:
        input  = 10   * 2 / 1e6 = 0.00002
        output = 2048 * 8 / 1e6 = 0.016384
        total  = 0.016404
    """
    prompt = "x" * 40  # 40 chars → 10 input tokens via chars-÷-4
    est = estimate_cost("glm-5", prompt, expected_output_tokens=2048)

    assert isinstance(est, CostEstimate)
    assert est.model == "glm-5"
    assert est.input_tokens == 10
    assert est.output_tokens == 2048
    assert est.total_usd > 0
    # Tight tolerance: the math is fully deterministic.
    assert est.input_cost_usd == pytest.approx(10 * 2 / 1_000_000)
    assert est.output_cost_usd == pytest.approx(2048 * 8 / 1_000_000)
    assert est.total_usd == pytest.approx(
        est.input_cost_usd + est.output_cost_usd
    )


def test_estimate_unknown_model_raises() -> None:
    """Convention chosen for A8: unknown models RAISE ``ModelNotPriced``.

    Rationale: returning a zero-cost estimate would let ``--max-cost``
    silently pass against uncosted models, defeating the budget guard.
    Raising lets the CLI catch and print a friendly message.
    """
    with pytest.raises(ModelNotPriced):
        estimate_cost("totally-fake-model-9000", "hello")
    # Subclasses KeyError so legacy ``except KeyError`` still works.
    with pytest.raises(KeyError):
        estimate_cost("totally-fake-model-9000", "hello")


def test_chars_div_4_heuristic_floor() -> None:
    """Input-token estimate uses ``max(1, len(prompt) // 4)``.

    The ``max(1, ...)`` floor protects single-char prompts from a
    zero-token estimate (which would be misleading in the printed
    output even if it doesn't underflow downstream cost math).
    """
    # 0 chars → still 1 (max floor)
    est0 = estimate_cost("glm-5", "")
    assert est0.input_tokens == 1
    # 1 char → 1 (max floor)
    est1 = estimate_cost("glm-5", "a")
    assert est1.input_tokens == 1
    # 4 chars → exactly 1
    est4 = estimate_cost("glm-5", "abcd")
    assert est4.input_tokens == 1
    # 5 chars → 1 (integer division)
    est5 = estimate_cost("glm-5", "abcde")
    assert est5.input_tokens == 1
    # 8 chars → 2
    est8 = estimate_cost("glm-5", "abcdefgh")
    assert est8.input_tokens == 2
    # 100 chars → 25
    est100 = estimate_cost("glm-5", "x" * 100)
    assert est100.input_tokens == 25


def test_estimate_uses_longest_prefix_match() -> None:
    """Lookup follows the same longest-prefix rule as ``calculate_cost``.

    ``claude-opus-4-7`` and ``claude-opus-4`` share a prefix; the
    longer one must be matched first so the estimator never under-
    or over-charges due to dispatch order.
    """
    # Both opus-4 entries cost $15/$75 today, so we just verify the
    # lookup succeeds and yields a positive cost (regression guard
    # against an accidental dict-iteration-order bug).
    est = estimate_cost("claude-opus-4-7", "x" * 40)
    assert est.total_usd > 0
    est_alias = estimate_cost("claude-opus-4-1", "x" * 40)
    assert est_alias.total_usd > 0


# ---------------------------------------------------------------------------
# Helper: format_estimate rendering
# ---------------------------------------------------------------------------


def test_format_text() -> None:
    """Text format is a single human-readable line."""
    est = estimate_cost("glm-5", "x" * 40, expected_output_tokens=2048)
    rendered = format_estimate(est, output="text")
    assert "Estimated cost:" in rendered
    assert "glm-5" in rendered
    assert "input tokens" in rendered
    assert "output tokens" in rendered
    # Dollar figure should be 4-digit precision (regression guard).
    assert "$0." in rendered or "$1." in rendered


def test_format_json_round_trip() -> None:
    """JSON format round-trips through ``json.loads`` with all fields."""
    est = estimate_cost("glm-5", "hello world", expected_output_tokens=512)
    rendered = format_estimate(est, output="json")
    payload = json.loads(rendered)

    assert payload["model"] == "glm-5"
    assert payload["input_tokens"] == est.input_tokens
    assert payload["output_tokens"] == 512
    assert payload["total_usd"] == pytest.approx(est.total_usd)
    # Both per-bucket fields are present so callers can re-derive total.
    assert "input_cost_usd" in payload
    assert "output_cost_usd" in payload


def test_format_unknown_output_raises() -> None:
    """Unknown ``output`` argument raises ``ValueError``."""
    est = estimate_cost("glm-5", "hi")
    with pytest.raises(ValueError):
        format_estimate(est, output="yaml")


# ---------------------------------------------------------------------------
# CLI wiring: --estimate-cost / --max-cost on the one-shot -p path
# ---------------------------------------------------------------------------


def _build_otter_namespace(**overrides: object) -> argparse.Namespace:
    """Build a fully-defaulted otter argparse Namespace for tests.

    Mirrors the helper used in ``tests/otter/test_cli.py`` so we get
    every flag default in sync with :func:`otter_cli.add_arguments`.
    """
    parser = argparse.ArgumentParser(prog="chimera otter")
    otter_cli.add_arguments(parser)
    args = parser.parse_args([])
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def test_estimate_cost_flag_prints_and_exits(capsys) -> None:
    """``-p "hi" --estimate-cost --model glm-5`` exits 0 with a stdout estimate.

    Runs :func:`otter_cli.run` directly (no subprocess) so the test
    stays fast and hermetic. The pre-flight gate fires before any
    provider / env setup so we never need to mock the agent stack.
    """
    args = _build_otter_namespace(
        print_mode="hi",
        estimate_cost=True,
        model="glm-5",
    )
    rc = otter_cli.run(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "Estimated cost" in captured.out
    assert "glm-5" in captured.out


def test_estimate_cost_flag_json_output(capsys) -> None:
    """``--estimate-cost`` honors ``--output-format json``."""
    args = _build_otter_namespace(
        print_mode="hello world",
        estimate_cost=True,
        model="glm-5",
        output_format="json",
    )
    rc = otter_cli.run(args)
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert payload["model"] == "glm-5"
    assert payload["total_usd"] > 0


def test_estimate_cost_unknown_model_exits_2(capsys) -> None:
    """Unknown model + ``--estimate-cost`` prints a friendly stderr msg."""
    args = _build_otter_namespace(
        print_mode="hi",
        estimate_cost=True,
        model="totally-fake-model-9000",
    )
    rc = otter_cli.run(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "no entry in the pricing table" in captured.err
    assert "totally-fake-model-9000" in captured.err


def test_max_cost_refuses_when_exceeded(capsys) -> None:
    """``--max-cost`` below the estimate refuses with rc=2 and stderr."""
    args = _build_otter_namespace(
        print_mode="hi" * 200,  # ~400 chars → 100 input tokens
        max_cost=0.000001,  # 1e-6 USD — way below any real estimate
        model="glm-5",
    )
    rc = otter_cli.run(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "Refusing to run" in captured.err
    assert "--max-cost" in captured.err


def test_max_cost_refuses_for_uncosted_model(capsys) -> None:
    """``--max-cost`` against an uncosted model fails closed (rc=2)."""
    args = _build_otter_namespace(
        print_mode="hi",
        max_cost=100.0,  # generous cap, but the model is uncosted
        model="totally-fake-model-9000",
    )
    rc = otter_cli.run(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "Refusing to run" in captured.err
    assert "uncosted model" in captured.err


def test_max_cost_proceeds_when_under(monkeypatch, capsys) -> None:
    """Small prompt + generous ``--max-cost`` falls through to the agent.

    We stub :func:`otter_cli._run_print_mode` *itself* after the gate
    has been exercised separately (the gate test above) so this test
    verifies the *fall-through* contract: when the estimate is under
    the cap, the gate returns ``None`` and the rest of the one-shot
    pipeline runs. Stubbing the post-gate body keeps the test off
    the network without monkeypatching the entire provider stack.
    """
    args = _build_otter_namespace(
        print_mode="hi",
        max_cost=100.0,  # huge cap; estimate is well under
        model="glm-5",
    )
    # First, exercise the gate directly to confirm it returned None.
    gate_rc = otter_cli._maybe_apply_cost_gate(args)  # noqa: SLF001
    assert gate_rc is None

    # Now stub the agent-driven body so otter_cli.run falls through
    # without touching a real provider, and assert the dispatch path.
    sentinel = {"called": False}

    def _fake_print_mode(_args: argparse.Namespace) -> int:
        sentinel["called"] = True
        return 0

    monkeypatch.setattr(otter_cli, "_run_print_mode", _fake_print_mode)
    rc = otter_cli.run(args)
    assert rc == 0
    assert sentinel["called"] is True
