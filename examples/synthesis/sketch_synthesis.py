#!/usr/bin/env python3
"""Sketch synthesis: agent fills holes in partial code.

Provides a code skeleton with # HOLE markers. The agent fills
only the holes, preserving the structure.

Usage:
    source .env
    python examples/sketch_synthesis.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import chimera
from chimera.training.sketch import SketchSpec

SKETCH = '''\
"""Stack data structure."""


class Stack:
    def __init__(self):
        # HOLE: initialize internal storage
        pass

    def push(self, item):
        # HOLE: add item to top of stack
        pass

    def pop(self):
        # HOLE: remove and return top item, raise IndexError if empty
        pass

    def peek(self):
        # HOLE: return top item without removing, raise IndexError if empty
        pass

    def is_empty(self) -> bool:
        # HOLE: return True if stack has no items
        pass

    def __len__(self) -> int:
        # HOLE: return number of items
        pass
'''

TESTS = '''\
import pytest
from stack import Stack

def test_push_pop():
    s = Stack()
    s.push(1)
    s.push(2)
    assert s.pop() == 2
    assert s.pop() == 1

def test_peek():
    s = Stack()
    s.push(42)
    assert s.peek() == 42
    assert len(s) == 1

def test_empty():
    s = Stack()
    assert s.is_empty()
    s.push(1)
    assert not s.is_empty()

def test_pop_empty():
    s = Stack()
    with pytest.raises(IndexError):
        s.pop()

def test_len():
    s = Stack()
    assert len(s) == 0
    s.push("a")
    s.push("b")
    assert len(s) == 2
'''


def main():
    try:
        provider = chimera.create_provider()
    except ValueError as _e:
        import sys
        print(f"Setup error: {_e}", file=sys.stderr)
        print("Set ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN + ANTHROPIC_MODEL before running.", file=sys.stderr)
        sys.exit(1)
    workdir = tempfile.mkdtemp(prefix="chimera-sketch-")

    # Write sketch
    with open(os.path.join(workdir, "stack.py"), "w") as f:
        f.write(SKETCH)

    # Write tests
    tests_dir = os.path.join(workdir, "tests")
    os.makedirs(tests_dir)
    with open(os.path.join(tests_dir, "test_stack.py"), "w") as f:
        f.write(TESTS)

    # Parse sketch
    sketch = SketchSpec({"stack.py": SKETCH}, description="Fill the holes in the Stack class.")
    print(f"Model: {provider.model_name}")
    print(f"Holes found: {len(sketch.holes)}")
    for h in sketch.holes:
        print(f"  Hole {h.id}: {h.description} (line {h.line})")
    print()

    env = chimera.LocalEnvironment(workdir=workdir)
    env.setup()

    agent = chimera.Agent(
        provider=provider,
        tools=list(chimera.AGENT_TOOLS),
        loop=chimera.ReAct(max_steps=10),
    )

    # Use the sketch as the spec
    trainer = chimera.Trainer(spec=sketch, agent=agent, env=env)
    result = trainer.synthesize(strategy=chimera.TestConvergence(max_iterations=3))

    print(f"\n{'='*50}")
    print(f"Converged: {result.converged}")
    print(f"Iterations: {result.iterations}")
    print(f"Cost: ${result.total_cost:.4f}")

    # Show result
    stack_path = os.path.join(workdir, "stack.py")
    if os.path.exists(stack_path):
        print("\n--- Generated stack.py ---")
        print(open(stack_path).read())

    env.cleanup()


if __name__ == "__main__":
    main()
