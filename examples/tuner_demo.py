#!/usr/bin/env python3
"""Hyperparameter search: find the best synthesis config."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera
from chimera.training.tuner import SearchSpace, SynthesisTuner

TESTS = '''
from calc import add, multiply
def test_add(): assert add(2,3) == 5
def test_mul(): assert multiply(3,4) == 12
'''

def main():
    base_dir = tempfile.mkdtemp(prefix="chimera-tuner-")
    tests_dir = os.path.join(base_dir, "tests")
    os.makedirs(tests_dir)
    with open(os.path.join(tests_dir, "test_calc.py"), "w") as f:
        f.write(TESTS)

    spec = chimera.Spec.from_tests(tests_dir, "Build calc module with add and multiply.")

    def env_factory():
        d = tempfile.mkdtemp(prefix="chimera-trial-")
        # Copy tests
        td = os.path.join(d, "tests")
        os.makedirs(td)
        with open(os.path.join(td, "test_calc.py"), "w") as f:
            f.write(TESTS)
        e = chimera.LocalEnvironment(workdir=d)
        e.setup()
        return e

    space = SearchSpace()
    space.choice("max_steps", [5, 15])  # just 2 trials

    tuner = SynthesisTuner(spec=spec, env_factory=env_factory)
    result = tuner.search(space, max_trials=2)

    print(f"Best config: {result.best_config}")
    print(f"Best score: {result.best_score:.0%}")
    print(f"Total cost: ${result.total_cost:.4f}")
    print(f"Trials: {len(result.trials)}")
    for t in result.trials:
        print(f"  {t.config} → {t.score:.0%} (${t.synthesis_result.total_cost:.4f})")

if __name__ == "__main__":
    main()
