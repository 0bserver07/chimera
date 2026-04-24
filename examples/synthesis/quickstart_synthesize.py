#!/usr/bin/env python3
"""Quickstart: Synthesize a project from tests.

Creates a calculator module from test specifications, then verifies it passes.

Usage:

  # GLM-5
  export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
  export ANTHROPIC_AUTH_TOKEN="your-token-here"
  python examples/quickstart_synthesize.py --model glm-5

  # Claude
  export ANTHROPIC_API_KEY="sk-ant-..."
  python examples/quickstart_synthesize.py --model claude-sonnet-4-20250514
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import chimera

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
    parser = argparse.ArgumentParser(description="Synthesize a calculator from tests")
    parser.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL", "glm-5"))
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--provider", default=None)
    args = parser.parse_args()

    # Create a temp project with tests
    workdir = tempfile.mkdtemp(prefix="chimera-example-")
    tests_dir = os.path.join(workdir, "tests")
    os.makedirs(tests_dir)
    with open(os.path.join(tests_dir, "test_calculator.py"), "w") as f:
        f.write(TESTS)

    print(f"Workdir:    {workdir}")
    print(f"Model:      {args.model}")
    print(f"Max iters:  {args.max_iterations}")
    print(f"Tests:      {tests_dir}/test_calculator.py (6 tests)")
    print()

    # Synthesize
    result = chimera.synthesize(
        spec="Build a calculator module with add, subtract, multiply, divide functions. "
             "divide(a, 0) should raise ValueError.",
        tests=tests_dir,
        model=args.model,
        workdir=workdir,
        max_iterations=args.max_iterations,
    )

    print()
    print("=== Result ===")
    print(f"Converged:  {result.converged}")
    print(f"Iterations: {result.iterations}")
    print(f"Pass rate:  {result.best_pass_rate:.0%}")
    print(f"Cost:       ${result.total_cost:.6f}")
    print()

    # Verify by running tests ourselves
    print("=== Verification ===")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", tests_dir, "-v"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr)

    print(f"Project at: {workdir}")
    print()

    # Show what was generated
    for fname in os.listdir(workdir):
        if fname.endswith(".py"):
            print(f"--- {fname} ---")
            with open(os.path.join(workdir, fname)) as f:
                print(f.read())


if __name__ == "__main__":
    main()
