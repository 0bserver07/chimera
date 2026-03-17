#!/usr/bin/env python3
"""Spec inference: auto-generate regression tests from existing code.

No LLM required -- uses AST analysis to detect patterns (return types,
non-null returns, docstrings, argument counts) and emit pytest skeletons.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chimera.training.spec_inference import SpecInferrer

SOURCE = '''\
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

def greet(name: str) -> str:
    """Return a greeting."""
    return f"Hello, {name}!"

def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("division by zero")
    return a / b

def process(items):
    result = []
    for item in items:
        result.append(item.strip().lower())
    return result
'''


def main():
    inferrer = SpecInferrer()
    invariants = inferrer.analyze(SOURCE, "utils.py")

    print(f"Inferred {len(invariants)} invariants:\n")
    for inv in invariants:
        print(f"  {inv.function}() -- {inv.invariant}"
              f"  (confidence: {inv.confidence}, pattern: {inv.pattern})")

    print("\n--- Generated test file ---")
    print(inferrer.generate_test_file())


if __name__ == "__main__":
    main()
