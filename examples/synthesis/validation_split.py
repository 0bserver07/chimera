#!/usr/bin/env python3
"""Validation split: detect overfitting in synthesis.

Splits tests into train/val. Agent synthesizes against train only.
After convergence, runs validation tests to check generalization.

Usage:
    source .env
    python examples/validation_split.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import chimera
from chimera.training.validation import ValidationSplit

# 6 test files — some will be train, some will be val
TESTS = {
    "test_add.py": "from math_ops import add\ndef test_add(): assert add(2,3) == 5\ndef test_add_neg(): assert add(-1,-1) == -2\n",
    "test_sub.py": "from math_ops import subtract\ndef test_sub(): assert subtract(10,4) == 6\ndef test_sub_zero(): assert subtract(5,5) == 0\n",
    "test_mul.py": "from math_ops import multiply\ndef test_mul(): assert multiply(3,7) == 21\ndef test_mul_zero(): assert multiply(5,0) == 0\n",
    "test_div.py": "from math_ops import divide\nimport pytest\ndef test_div(): assert divide(10,2) == 5.0\ndef test_div_zero():\n    with pytest.raises(ValueError): divide(1,0)\n",
    "test_power.py": "from math_ops import power\ndef test_power(): assert power(2,3) == 8\ndef test_power_zero(): assert power(5,0) == 1\n",
    "test_modulo.py": "from math_ops import modulo\ndef test_mod(): assert modulo(10,3) == 1\ndef test_mod_even(): assert modulo(10,2) == 0\n",
}


def main():
    try:
        provider = chimera.create_provider()
    except ValueError as _e:
        import sys
        print(f"Setup error: {_e}", file=sys.stderr)
        print("Set ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN + ANTHROPIC_MODEL before running.", file=sys.stderr)
        sys.exit(1)
    workdir = tempfile.mkdtemp(prefix="chimera-valsplit-")

    # Write test files
    tests_dir = os.path.join(workdir, "tests")
    os.makedirs(tests_dir)
    for fname, content in TESTS.items():
        with open(os.path.join(tests_dir, fname), "w") as f:
            f.write(content)

    spec = chimera.Spec.from_tests(tests_dir, "Build a math_ops module with add, subtract, multiply, divide, power, modulo.")

    # Split: 70% train, 30% validation
    split = ValidationSplit(spec, ratio=0.3, seed=42)

    print(f"Model: {provider.model_name}")
    print(f"Total test files: {len(TESTS)}")
    print(f"Train files: {split.train_files}")
    print(f"Val files: {split.val_files}")
    print()

    env = chimera.LocalEnvironment(workdir=workdir)
    env.setup()

    agent = chimera.Agent(
        provider=provider,
        tools=list(chimera.AGENT_TOOLS),
        loop=chimera.ReAct(max_steps=15),
    )

    # Synthesize against TRAIN spec only
    trainer = chimera.Trainer(spec=split.train_spec, agent=agent, env=env)
    result = trainer.synthesize(strategy=chimera.TestConvergence(max_iterations=3))

    print(f"\n{'='*50}")
    print(f"Synthesis converged: {result.converged}")
    print(f"Iterations: {result.iterations}")
    print(f"Cost: ${result.total_cost:.4f}")

    # Now evaluate against held-out validation tests
    val_result = split.evaluate(env)

    print("\n--- Validation Results ---")
    print(f"Train pass rate: {val_result.train_pass_rate:.0%} ({val_result.train_passed}/{val_result.train_total})")
    print(f"Val pass rate:   {val_result.val_pass_rate:.0%} ({val_result.val_passed}/{val_result.val_total})")
    print(f"Overfit gap:     {val_result.overfit_gap:.0%}")

    if val_result.overfit_gap > 0.3:
        print("WARNING: significant overfitting detected!")
    elif val_result.overfit_gap > 0.1:
        print("NOTICE: mild overfitting detected.")
    else:
        print("OK: generalized well.")

    # Show generated code
    code_path = os.path.join(workdir, "math_ops.py")
    if os.path.exists(code_path):
        print("\n--- Generated math_ops.py ---")
        print(open(code_path).read())

    env.cleanup()


if __name__ == "__main__":
    main()
