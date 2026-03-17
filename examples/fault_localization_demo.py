#!/usr/bin/env python3
"""Fault localization: rank suspicious code locations from test failures.

No LLM required -- this is pure test-output analysis using an Ochiai-inspired
scoring method.  Feed it raw pytest output and it tells you where to look first.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chimera.training.fault_localization import FaultLocalizer

# Simulate pytest output with failures pointing to specific files
TEST_OUTPUT = """
FAILED tests/test_billing.py::test_total - AssertionError: assert 90 == 100
    billing.py:15: in calculate_total
        return subtotal * tax_rate
    tests/test_billing.py:8: AssertionError
FAILED tests/test_billing.py::test_discount - AssertionError: assert 85 == 80
    billing.py:15: in calculate_total
        return subtotal * tax_rate
    billing.py:22: in apply_discount
        return total - discount
    tests/test_billing.py:12: AssertionError
FAILED tests/test_reports.py::test_summary - KeyError: 'total'
    reports.py:5: in generate_summary
        return data['total']
    tests/test_reports.py:6: KeyError
"""


def main():
    localizer = FaultLocalizer()

    # --- Localize faults ---
    locations = localizer.localize(TEST_OUTPUT)

    print("Suspicious locations (ranked by score):")
    for loc in locations:
        func = f" ({loc.function})" if loc.function else ""
        print(f"  {loc.file}:{loc.line}{func} -- score {loc.score:.0%} -- {loc.reason}")

    # --- Augment a prompt with the results ---
    print()
    augmented = localizer.augment_prompt("Fix the failing tests.", locations)
    print("Augmented prompt sent to agent:")
    print(augmented)


if __name__ == "__main__":
    main()
