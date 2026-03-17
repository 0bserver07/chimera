#!/usr/bin/env python3
"""CEGIS synthesis: fix one test failure at a time.

Compares CEGIS (counterexample-guided) vs regular TestConvergence
on the same spec. CEGIS focuses the agent on ONE failing test per
epoch instead of dumping all failures.

Usage:
    source .env
    python examples/cegis_synthesis.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera
from chimera.training.callbacks import TrainingCurveCallback
from chimera.training.strategies.cegis import CEGISStrategy

TESTS = '''\
from string_utils import reverse, is_palindrome, capitalize_words, count_vowels

def test_reverse():
    assert reverse("hello") == "olleh"

def test_reverse_empty():
    assert reverse("") == ""

def test_palindrome_true():
    assert is_palindrome("racecar") is True

def test_palindrome_false():
    assert is_palindrome("hello") is False

def test_palindrome_case():
    assert is_palindrome("Racecar") is True

def test_capitalize():
    assert capitalize_words("hello world") == "Hello World"

def test_count_vowels():
    assert count_vowels("hello") == 2

def test_count_vowels_empty():
    assert count_vowels("") == 0
'''


def main():
    provider = chimera.create_provider()
    workdir = tempfile.mkdtemp(prefix="chimera-cegis-")

    tests_dir = os.path.join(workdir, "tests")
    os.makedirs(tests_dir)
    with open(os.path.join(tests_dir, "test_string_utils.py"), "w") as f:
        f.write(TESTS)

    env = chimera.LocalEnvironment(workdir=workdir)
    env.setup()

    agent = chimera.Agent(
        provider=provider,
        tools=list(chimera.AGENT_TOOLS),
        loop=chimera.ReAct(max_steps=15),
    )

    spec = chimera.Spec.from_tests(tests_dir, "Build a string_utils module.")
    curve = TrainingCurveCallback()

    trainer = chimera.Trainer(spec=spec, agent=agent, env=env)

    print(f"Model: {provider.model_name}")
    print(f"Strategy: CEGIS (one failure at a time)")
    print(f"Tests: 8 tests for string_utils")
    print()

    result = trainer.synthesize(
        strategy=CEGISStrategy(max_iterations=8, patience=4),
        callbacks=[curve],
    )

    print(f"\n{'='*50}")
    print(f"Converged: {result.converged}")
    print(f"Iterations: {result.iterations}")
    print(f"Best pass rate: {result.best_pass_rate:.0%}")
    print(f"Cost: ${result.total_cost:.4f}")
    print(f"\n--- Training Curve ---")
    print(curve.summary())

    # Show generated code
    code_path = os.path.join(workdir, "string_utils.py")
    if os.path.exists(code_path):
        print(f"\n--- Generated string_utils.py ---")
        print(open(code_path).read())

    env.cleanup()


if __name__ == "__main__":
    main()
