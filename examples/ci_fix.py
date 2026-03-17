#!/usr/bin/env python3
"""CI fix workflow: parse a CI failure log and use an agent to fix it.

Creates a buggy Python file and a test that catches the bug, then uses
CIFixWorkflow to diagnose the failure from a simulated CI log and fix it.

Usage:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"
    python examples/ci_fix.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera
from chimera.ci import CIFixWorkflow


BUGGY_CODE = '''\
def add(a, b):
    """Add two numbers."""
    return a - b  # BUG: should be a + b
'''

TEST_CODE = '''\
from calculator import add

def test_add():
    assert add(2, 3) == 5, "Expected 5"
    assert add(0, 0) == 0, "Expected 0"
    assert add(-1, 1) == 0, "Expected 0"
'''

# Simulated CI log output from running pytest
CI_LOG = """\
============================= test session starts ==============================
collected 1 item

test_calculator.py::test_add FAILED

=================================== FAILURES ===================================
__________________________________ test_add ____________________________________

    def test_add():
>       assert add(2, 3) == 5, "Expected 5"
E       AssertionError: Expected 5
E       assert -1 == 5

test_calculator.py:4: AssertionError
=========================== short test summary info ============================
FAILED test_calculator.py::test_add - AssertionError: Expected 5
=========================== 1 failed in 0.01s ==================================
"""


def main():
    provider = chimera.create_provider()

    with tempfile.TemporaryDirectory(prefix="chimera-cifix-") as tmpdir:
        # Write the buggy code and test
        with open(os.path.join(tmpdir, "calculator.py"), "w") as f:
            f.write(BUGGY_CODE)
        with open(os.path.join(tmpdir, "test_calculator.py"), "w") as f:
            f.write(TEST_CODE)

        env = chimera.LocalEnvironment(workdir=tmpdir)
        env.setup()

        agent = chimera.Agent(
            provider=provider,
            tools=list(chimera.AGENT_TOOLS),
            loop=chimera.ReAct(max_steps=10),
        )

        print("=== CI Fix Workflow ===\n")
        print(f"Workdir: {tmpdir}")
        print(f"Bug: calculator.py uses subtraction instead of addition\n")

        # Run the CI fix workflow
        workflow = CIFixWorkflow(max_attempts=2)
        fixed = workflow.run(CI_LOG, agent=agent, env=env)

        print(f"Fixed: {fixed}")
        print(f"Attempts: {len(workflow.attempts)}")
        print(f"Total cost: ${workflow.total_cost:.4f}")

        # Show the fixed file
        fixed_path = os.path.join(tmpdir, "calculator.py")
        if os.path.exists(fixed_path):
            print(f"\n--- calculator.py (after fix) ---")
            print(open(fixed_path).read())

        env.cleanup()


if __name__ == "__main__":
    main()
