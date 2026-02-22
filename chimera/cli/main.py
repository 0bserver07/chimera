"""Chimera CLI -- command-line interface for code synthesis."""

from __future__ import annotations

import argparse
import sys


def create_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="chimera",
        description="Chimera -- the Keras of agentic coding. Synthesize codebases from specifications.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_get_version()}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # chimera synthesize
    synth = subparsers.add_parser(
        "synthesize",
        help="Synthesize a codebase from a specification",
        aliases=["synth"],
    )
    synth.add_argument(
        "--spec",
        type=str,
        help="Path to spec file or inline spec text",
    )
    synth.add_argument(
        "--tests",
        type=str,
        help="Path to test directory (tests ARE the spec)",
    )
    synth.add_argument(
        "--output",
        "-o",
        type=str,
        default="./output",
        help="Output directory for generated code (default: ./output)",
    )
    synth.add_argument(
        "--model",
        type=str,
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )
    synth.add_argument(
        "--provider",
        type=str,
        default="anthropic",
        choices=["anthropic"],
        help="LLM provider (default: anthropic)",
    )
    synth.add_argument(
        "--strategy",
        type=str,
        default="convergence",
        choices=["convergence"],
        help="Synthesis strategy (default: convergence)",
    )
    synth.add_argument(
        "--max-iterations",
        type=int,
        default=50,
        help="Maximum synthesis iterations (default: 50)",
    )
    synth.add_argument(
        "--patience",
        type=int,
        default=5,
        help="Epochs without improvement before stopping (default: 5)",
    )
    synth.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Maximum cost in USD before stopping",
    )

    return parser


def _get_version() -> str:
    """Get the package version string."""
    try:
        from chimera import __version__

        return __version__
    except ImportError:
        return "unknown"


def run_synthesize(args: argparse.Namespace) -> int:
    """Execute the synthesize command."""
    from pathlib import Path

    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.env.local import LocalEnvironment
    from chimera.tools.read import ReadFileTool
    from chimera.tools.write import WriteFileTool
    from chimera.tools.bash import BashTool
    from chimera.training.callbacks import CostLimit, HistoryRecorder
    from chimera.training.spec import Spec
    from chimera.training.strategies.convergence import TestConvergence
    from chimera.training.trainer import Trainer

    # Validate inputs
    if not args.spec and not args.tests:
        print("Error: Either --spec or --tests must be provided.", file=sys.stderr)
        return 1

    # Build spec
    if args.tests:
        description = ""
        if args.spec:
            # spec could be inline text or file path
            spec_path = Path(args.spec)
            if spec_path.exists():
                description = spec_path.read_text()
            else:
                description = args.spec
        spec = Spec.from_tests(args.tests, description=description)
    else:
        spec_path = Path(args.spec)
        if spec_path.exists():
            spec = Spec.from_file(args.spec)
        else:
            spec = Spec.from_string(args.spec)

    # Build provider
    if args.provider == "anthropic":
        try:
            from chimera.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider(model=args.model)
        except ImportError:
            print(
                "Error: Anthropic provider requires 'anthropic' package. "
                "Install with: pip install chimera-ai[anthropic]",
                file=sys.stderr,
            )
            return 1
        except Exception as e:
            print(f"Error initializing provider: {e}", file=sys.stderr)
            return 1
    else:
        print(f"Error: Unknown provider '{args.provider}'", file=sys.stderr)
        return 1

    # Build agent
    agent = Agent(
        provider=provider,
        tools=[ReadFileTool(), WriteFileTool(), BashTool()],
        loop=ReAct(max_steps=100),
    )

    # Build environment
    test_cmd = f"python -m pytest {args.tests}" if args.tests else "python -m pytest"
    env = LocalEnvironment(workdir=args.output, test_cmd=test_cmd)
    env.setup()

    # Build strategy
    strategy = TestConvergence(
        max_iterations=args.max_iterations,
        patience=args.patience,
    )

    # Build callbacks
    callbacks: list = [HistoryRecorder()]
    if args.max_cost is not None:
        callbacks.append(CostLimit(max_cost=args.max_cost))

    # Build trainer
    trainer = Trainer(spec=spec, agent=agent, env=env)

    # Run synthesis
    print(f"Chimera -- synthesizing from {'tests' if args.tests else 'spec'}...")
    print(f"  Output: {args.output}")
    print(f"  Model: {args.model}")
    print(f"  Strategy: {args.strategy} (max_iter={args.max_iterations}, patience={args.patience})")
    print()

    try:
        result = trainer.synthesize(strategy=strategy, callbacks=callbacks)
    except KeyboardInterrupt:
        print("\nSynthesis interrupted by user.")
        return 130
    except Exception as e:
        print(f"\nSynthesis failed: {e}", file=sys.stderr)
        return 1

    # Report results
    if result.converged:
        print(f"\nConverged in {result.iterations} iterations!")
        print(f"  Pass rate: {result.best_pass_rate:.1%}")
        print(f"  Total cost: ${result.total_cost:.4f}")
        print(f"  Output: {args.output}/")
        return 0
    else:
        print(f"\nDid not converge after {result.iterations} iterations.")
        print(f"  Best pass rate: {result.best_pass_rate:.1%}")
        print(f"  Reason: {result.failure_reason}")
        return 1


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command in ("synthesize", "synth"):
        return run_synthesize(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
