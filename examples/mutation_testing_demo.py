#!/usr/bin/env python3
"""Mutation testing: find weak tests by mutating code.

No LLM required -- uses AST transforms (operator swaps, condition negation)
to generate mutants and a callback to simulate test results.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chimera.training.mutation import MutationTester

SOURCE = '''\
def add(a, b):
    return a + b

def is_positive(x):
    if x > 0:
        return True
    return False

def discount(price, rate):
    return price - price * rate
'''


def main():
    tester = MutationTester(max_mutants=20)

    # --- Step 1: generate mutants ---
    mutants = tester.generate_mutants(SOURCE, "calculator.py")
    print(f"Generated {len(mutants)} mutants:\n")
    for m in mutants:
        print(f"  [{m.operator}] line {m.line}: {m.original!r} -> {m.mutated!r}")

    # --- Step 2: run with a simulated test oracle ---
    # Pretend our test suite catches arithmetic changes in add() but misses
    # everything else.
    def test_fn(mutation):
        """Return True if tests PASS (mutation survives), False if caught."""
        if mutation.line <= 2:
            return False  # tests fail -> mutation killed
        return True       # tests pass -> mutation survived (weak tests!)

    result = tester.run(SOURCE, test_fn=test_fn, file_path="calculator.py")
    print(f"\nMutation score: {result.mutation_score:.0%}"
          f" ({result.killed} killed, {result.survived} survived)")

    if result.survivors:
        print("\nSurviving mutations (tests are too weak to catch):")
        for s in result.survivors:
            print(f"  line {s.line}: {s.original!r} [{s.operator}]")


if __name__ == "__main__":
    main()
