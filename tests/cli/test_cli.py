"""Tests for chimera.cli.main — CLI argument parsing and basic behavior."""

from __future__ import annotations

from chimera.cli.main import create_parser, main


def test_parser_no_command():
    """No command should return 0 (prints help)."""
    result = main([])
    assert result == 0


def test_parser_synthesize_basic():
    """Parses --spec flag for synthesize command."""
    parser = create_parser()
    args = parser.parse_args(["synthesize", "--spec", "Build a calculator"])
    assert args.command == "synthesize"
    assert args.spec == "Build a calculator"


def test_parser_synthesize_tests():
    """Parses --tests flag for synthesize command."""
    parser = create_parser()
    args = parser.parse_args(["synthesize", "--tests", "./tests/"])
    assert args.tests == "./tests/"


def test_parser_synthesize_all_flags():
    """Parses all flags together for synthesize command."""
    parser = create_parser()
    args = parser.parse_args([
        "synthesize",
        "--spec", "spec.md",
        "--tests", "./tests/",
        "--output", "./out",
        "--model", "claude-opus-4-20250514",
        "--max-iterations", "100",
        "--patience", "10",
        "--max-cost", "5.0",
    ])
    assert args.spec == "spec.md"
    assert args.tests == "./tests/"
    assert args.output == "./out"
    assert args.model == "claude-opus-4-20250514"
    assert args.max_iterations == 100
    assert args.patience == 10
    assert args.max_cost == 5.0


def test_parser_synthesize_alias():
    """'synth' works as alias for 'synthesize'."""
    parser = create_parser()
    args = parser.parse_args(["synth", "--spec", "Build something"])
    assert args.command == "synth"
    assert args.spec == "Build something"


def test_run_synthesize_no_spec_no_tests(capsys):
    """Should return 1 when neither --spec nor --tests provided."""
    result = main(["synthesize"])
    assert result == 1
    captured = capsys.readouterr()
    assert "Error" in captured.err


def test_main_no_args(capsys):
    """main([]) returns 0 and prints help."""
    result = main([])
    assert result == 0


def test_parser_synthesize_defaults():
    """Default values are set correctly."""
    parser = create_parser()
    args = parser.parse_args(["synthesize", "--spec", "something"])
    assert args.output == "./output"
    assert args.model == "claude-sonnet-4-20250514"
    assert args.provider == "anthropic"
    assert args.strategy == "convergence"
    assert args.max_iterations == 50
    assert args.patience == 5
    assert args.max_cost is None


from unittest.mock import patch
from chimera.training.strategies.base import SynthesisResult


def test_run_synthesize_calls_synthesize_function():
    """CLI synthesize should call chimera.synthesize.synthesize()."""
    mock_result = SynthesisResult(
        converged=True,
        iterations=3,
        total_cost=0.05,
        best_pass_rate=1.0,
        history=[],
    )
    with patch("chimera.cli.main.synthesize_fn", return_value=mock_result) as mock_synth:
        result = main(["synthesize", "--spec", "Build a calc", "--tests", "./tests/", "--model", "claude-sonnet-4-20250514"])
    assert result == 0
    mock_synth.assert_called_once()
    call_kwargs = mock_synth.call_args
    assert call_kwargs[0][0] == "Build a calc"  # spec positional
    assert call_kwargs[1]["tests"] == "./tests/"
    assert call_kwargs[1]["model"] == "claude-sonnet-4-20250514"


def test_run_synthesize_reports_failure():
    """CLI reports non-zero exit on failed synthesis."""
    mock_result = SynthesisResult(
        converged=False,
        iterations=50,
        total_cost=5.0,
        best_pass_rate=0.6,
        history=[],
        failure_reason="Max iterations reached",
    )
    with patch("chimera.cli.main.synthesize_fn", return_value=mock_result):
        result = main(["synthesize", "--spec", "Build something"])
    assert result == 1
