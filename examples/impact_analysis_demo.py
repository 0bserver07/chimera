#!/usr/bin/env python3
"""Impact analysis: show what depends on a function before editing it.

No LLM required -- uses AST-based static analysis to find callers, importers,
and related test files for a given symbol inside a project directory.
"""
import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chimera.training.impact import ImpactAnalyzer


def main():
    # Build a tiny project in a temp dir so the demo is self-contained
    with tempfile.TemporaryDirectory() as d:
        # --- utils.py: the module we will analyze ---
        with open(os.path.join(d, "utils.py"), "w") as f:
            f.write(
                "def calculate_tax(amount, rate=0.1):\n"
                "    return amount * rate\n"
                "\n"
                "def format_currency(value):\n"
                "    return f'${value:.2f}'\n"
            )

        # --- billing.py: imports both helpers ---
        with open(os.path.join(d, "billing.py"), "w") as f:
            f.write(
                "from utils import calculate_tax, format_currency\n"
                "\n"
                "def process_invoice(items):\n"
                "    subtotal = sum(items)\n"
                "    tax = calculate_tax(subtotal)\n"
                "    return format_currency(subtotal + tax)\n"
            )

        # --- reports.py: imports only calculate_tax ---
        with open(os.path.join(d, "reports.py"), "w") as f:
            f.write(
                "from utils import calculate_tax\n"
                "\n"
                "def tax_report(amounts):\n"
                "    return [calculate_tax(a) for a in amounts]\n"
            )

        # --- tests/test_billing.py ---
        os.makedirs(os.path.join(d, "tests"), exist_ok=True)
        with open(os.path.join(d, "tests", "test_billing.py"), "w") as f:
            f.write(
                "from utils import calculate_tax\n"
                "\n"
                "def test_tax():\n"
                "    assert calculate_tax(100) == 10\n"
            )

        # --- Run impact analysis ---
        analyzer = ImpactAnalyzer(d)
        report = analyzer.analyze("utils.py", "calculate_tax")

        print("Impact analysis for: calculate_tax() in utils.py")
        print()
        print(f"Callers ({len(report.callers)}):")
        for c in report.callers:
            print(f"  {c.function}() in {c.file} line {c.line}")
        print(f"\nImporters: {report.importers}")
        print(f"Tests:     {report.tests}")
        print()
        print("Prompt section (injected into agent context):")
        print(report.to_prompt_section())


if __name__ == "__main__":
    main()
