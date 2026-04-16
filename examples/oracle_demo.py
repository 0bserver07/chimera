#!/usr/bin/env python3
"""Oracle: grow the test suite during synthesis."""
import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera
from chimera.training.oracle import OracleCallback
from chimera.training.callbacks import TrainingCurveCallback

TESTS = '''
from math_utils import factorial, fibonacci
def test_factorial(): assert factorial(5) == 120
def test_fibonacci(): assert fibonacci(6) == 8
'''

def main():
    workdir = tempfile.mkdtemp(prefix="chimera-oracle-")
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
    agent = chimera.Agent(provider=provider, tools=list(chimera.AGENT_TOOLS), loop=chimera.ReAct(max_steps=15))
    spec = chimera.Spec.from_tests(tests_dir, "Build math_utils with factorial and fibonacci.")

    oracle = OracleCallback(tests_dir=tests_dir, max_new_tests_per_epoch=2, mode="property")
    curve = TrainingCurveCallback()

    trainer = chimera.Trainer(spec=spec, agent=agent, env=env)
    result = trainer.synthesize(
        strategy=chimera.TestConvergence(max_iterations=3),
        callbacks=[oracle, curve],
    )

    print(f"Converged: {result.converged}")
    print(f"Iterations: {result.iterations}")
    print(f"Cost: ${result.total_cost:.4f}")
    print(f"Oracle generated {len(oracle.generated_tests)} new tests")
    print("\nTraining curve:")
    print(curve.summary())

    # Show what test files exist now
    print(f"\nTest files in {tests_dir}:")
    for f in sorted(os.listdir(tests_dir)):
        print(f"  {f}")

    env.cleanup()

if __name__ == "__main__":
    main()
