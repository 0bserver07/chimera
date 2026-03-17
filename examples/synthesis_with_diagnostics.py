#!/usr/bin/env python3
"""End-to-end synthesis with ML diagnostics.

Synthesizes a calculator from tests using:
- TrainingCurveCallback (per-epoch progress)
- Regularization (complexity penalty)
- Training Curves diagnosis (plateau, oscillation, etc)

Usage:
    source .env
    python examples/synthesis_with_diagnostics.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera
from chimera.training.callbacks import TrainingCurveCallback
from chimera.training.constraint import Constraint

TESTS = '''\
import pytest
from calculator import add, subtract, multiply, divide

def test_add():
    assert add(2, 3) == 5

def test_add_negative():
    assert add(-1, 1) == 0

def test_subtract():
    assert subtract(10, 4) == 6

def test_multiply():
    assert multiply(3, 7) == 21

def test_divide():
    assert divide(10, 2) == 5.0

def test_divide_by_zero():
    with pytest.raises(ValueError):
        divide(1, 0)
'''


def main():
    provider = chimera.create_provider()
    workdir = tempfile.mkdtemp(prefix="chimera-synth-diag-")

    # Write test file
    tests_dir = os.path.join(workdir, "tests")
    os.makedirs(tests_dir)
    with open(os.path.join(tests_dir, "test_calculator.py"), "w") as f:
        f.write(TESTS)

    env = chimera.LocalEnvironment(workdir=workdir)
    env.setup()

    agent = chimera.Agent(
        provider=provider,
        tools=list(chimera.AGENT_TOOLS),
        loop=chimera.ReAct(max_steps=15),
    )

    spec = chimera.Spec.from_tests(tests_dir, "Build a calculator module with add, subtract, multiply, divide.")

    # Set up diagnostics
    curve = TrainingCurveCallback()
    constraints = [
        Constraint.tests_pass(),
        Constraint.complexity_penalty(max_complexity=15),
    ]

    trainer = chimera.Trainer(spec=spec, agent=agent, env=env, constraints=constraints)

    print(f"Model:   {provider.model_name}")
    print(f"Workdir: {workdir}")
    print(f"Tests:   6 tests in test_calculator.py")
    print(f"Constraints: tests_pass + complexity_penalty(15)")
    print()

    result = trainer.synthesize(
        strategy=chimera.TestConvergence(max_iterations=5, patience=3),
        callbacks=[curve],
    )

    print(f"\n{'='*50}")
    print(f"Converged: {result.converged}")
    print(f"Iterations: {result.iterations}")
    print(f"Best pass rate: {result.best_pass_rate:.0%}")
    print(f"Cost: ${result.total_cost:.4f}")

    # Training curve
    print(f"\n--- Training Curve ---")
    print(curve.summary())

    # Diagnose
    warnings = curve.diagnose()
    if warnings:
        print(f"\n--- Diagnostics ---")
        for w in warnings:
            print(f"  WARNING: {w}")
    else:
        print(f"\n--- Diagnostics: clean ---")

    # Show generated code
    calc_path = os.path.join(workdir, "calculator.py")
    if os.path.exists(calc_path):
        print(f"\n--- Generated calculator.py ---")
        print(open(calc_path).read())

    env.cleanup()


if __name__ == "__main__":
    main()
