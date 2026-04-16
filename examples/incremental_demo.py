#!/usr/bin/env python3
"""Incremental synthesis: fix only the functions that are broken."""
import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera
from chimera.training.strategies.incremental import IncrementalStrategy
from chimera.training.callbacks import TrainingCurveCallback

# Pre-write a buggy module — the agent should fix only the broken function
BUGGY_CODE = '''
def add(a, b):
    return a + b  # correct

def subtract(a, b):
    return a + b  # BUG: should be a - b

def multiply(a, b):
    return a * b  # correct
'''

TESTS = '''
from math_ops import add, subtract, multiply
def test_add(): assert add(2, 3) == 5
def test_subtract(): assert subtract(10, 4) == 6
def test_multiply(): assert multiply(3, 7) == 21
'''

def main():
    workdir = tempfile.mkdtemp(prefix="chimera-incr-")

    # Write buggy code
    with open(os.path.join(workdir, "math_ops.py"), "w") as f:
        f.write(BUGGY_CODE)

    tests_dir = os.path.join(workdir, "tests")
    os.makedirs(tests_dir)
    with open(os.path.join(tests_dir, "test_math.py"), "w") as f:
        f.write(TESTS)

    env = chimera.LocalEnvironment(workdir=workdir)
    env.setup()

    try:
        provider = chimera.create_provider()
    except ValueError as _e:
        import sys
        print(f"Setup error: {_e}", file=sys.stderr)
        print("Set ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN + ANTHROPIC_MODEL before running.", file=sys.stderr)
        sys.exit(1)
    agent = chimera.Agent(provider=provider, tools=list(chimera.AGENT_TOOLS), loop=chimera.ReAct(max_steps=10))
    spec = chimera.Spec.from_tests(tests_dir, "Fix the bugs in math_ops.py")

    curve = TrainingCurveCallback()
    trainer = chimera.Trainer(spec=spec, agent=agent, env=env)
    result = trainer.synthesize(
        strategy=IncrementalStrategy(max_iterations=5, patience=3),
        callbacks=[curve],
    )

    print(f"Converged: {result.converged}")
    print(f"Iterations: {result.iterations}")
    print(f"Cost: ${result.total_cost:.4f}")
    print("\nTraining curve:")
    print(curve.summary())

    # Show the fixed code
    print("\n--- Fixed math_ops.py ---")
    print(open(os.path.join(workdir, "math_ops.py")).read())

    env.cleanup()

if __name__ == "__main__":
    main()
