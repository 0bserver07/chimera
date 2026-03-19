#!/usr/bin/env python3
"""Run HumanEval-style tasks through a Chimera provider.

Creates a provider from env vars, asks the model to implement functions,
then runs the test cases to check correctness.

Usage:
    source .env
    python examples/humaneval_run.py
    python examples/humaneval_run.py --count 5 --model glm-5
    python examples/humaneval_run.py --dataset path/to/humaneval.json
"""
from __future__ import annotations

import argparse
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chimera.eval.benchmarks.human_eval import HumanEval
from chimera.eval.metrics import pass_at_k
from chimera.providers.factory import create_provider
from chimera.types import Message

# ---------------------------------------------------------------------------
# Inline HumanEval-style problems
# ---------------------------------------------------------------------------

SAMPLE_PROBLEMS: list[dict] = [
    {
        "id": "HumanEval/0",
        "prompt": textwrap.dedent("""\
            def add(a: int, b: int) -> int:
                \"\"\"Return the sum of a and b.\"\"\"
        """),
        "test": textwrap.dedent("""\
            assert add(1, 2) == 3
            assert add(-1, 1) == 0
            assert add(0, 0) == 0
            assert add(100, 200) == 300
        """),
        "entry_point": "add",
    },
    {
        "id": "HumanEval/1",
        "prompt": textwrap.dedent("""\
            def max_of_three(a: int, b: int, c: int) -> int:
                \"\"\"Return the maximum of three integers.\"\"\"
        """),
        "test": textwrap.dedent("""\
            assert max_of_three(1, 2, 3) == 3
            assert max_of_three(3, 2, 1) == 3
            assert max_of_three(1, 3, 2) == 3
            assert max_of_three(-1, -2, -3) == -1
            assert max_of_three(5, 5, 5) == 5
        """),
        "entry_point": "max_of_three",
    },
    {
        "id": "HumanEval/2",
        "prompt": textwrap.dedent("""\
            def factorial(n: int) -> int:
                \"\"\"Return the factorial of n (n >= 0).\"\"\"
        """),
        "test": textwrap.dedent("""\
            assert factorial(0) == 1
            assert factorial(1) == 1
            assert factorial(5) == 120
            assert factorial(10) == 3628800
        """),
        "entry_point": "factorial",
    },
    {
        "id": "HumanEval/3",
        "prompt": textwrap.dedent("""\
            def is_palindrome(s: str) -> bool:
                \"\"\"Return True if s is a palindrome (case-insensitive, ignoring spaces).\"\"\"
        """),
        "test": textwrap.dedent("""\
            assert is_palindrome("racecar") == True
            assert is_palindrome("hello") == False
            assert is_palindrome("A man a plan a canal Panama".replace(" ", "")) == True
            assert is_palindrome("") == True
            assert is_palindrome("a") == True
        """),
        "entry_point": "is_palindrome",
    },
    {
        "id": "HumanEval/4",
        "prompt": textwrap.dedent("""\
            def fibonacci(n: int) -> int:
                \"\"\"Return the n-th Fibonacci number (0-indexed).
                fibonacci(0) = 0, fibonacci(1) = 1, fibonacci(2) = 1, ...\"\"\"
        """),
        "test": textwrap.dedent("""\
            assert fibonacci(0) == 0
            assert fibonacci(1) == 1
            assert fibonacci(2) == 1
            assert fibonacci(5) == 5
            assert fibonacci(10) == 55
        """),
        "entry_point": "fibonacci",
    },
    {
        "id": "HumanEval/5",
        "prompt": textwrap.dedent("""\
            def flatten(lst: list) -> list:
                \"\"\"Flatten a nested list of integers into a single list.
                Example: flatten([1, [2, [3, 4]], 5]) -> [1, 2, 3, 4, 5]\"\"\"
        """),
        "test": textwrap.dedent("""\
            assert flatten([1, [2, [3, 4]], 5]) == [1, 2, 3, 4, 5]
            assert flatten([]) == []
            assert flatten([1, 2, 3]) == [1, 2, 3]
            assert flatten([[1], [2], [3]]) == [1, 2, 3]
        """),
        "entry_point": "flatten",
    },
    {
        "id": "HumanEval/6",
        "prompt": textwrap.dedent("""\
            def count_vowels(s: str) -> int:
                \"\"\"Return the number of vowels (a, e, i, o, u) in s (case-insensitive).\"\"\"
        """),
        "test": textwrap.dedent("""\
            assert count_vowels("hello") == 2
            assert count_vowels("AEIOU") == 5
            assert count_vowels("xyz") == 0
            assert count_vowels("") == 0
        """),
        "entry_point": "count_vowels",
    },
    {
        "id": "HumanEval/7",
        "prompt": textwrap.dedent("""\
            def unique_elements(lst: list) -> list:
                \"\"\"Return a list of unique elements preserving first-occurrence order.\"\"\"
        """),
        "test": textwrap.dedent("""\
            assert unique_elements([1, 2, 2, 3, 1]) == [1, 2, 3]
            assert unique_elements([]) == []
            assert unique_elements([1, 1, 1]) == [1]
            assert unique_elements([1, 2, 3]) == [1, 2, 3]
        """),
        "entry_point": "unique_elements",
    },
    {
        "id": "HumanEval/8",
        "prompt": textwrap.dedent("""\
            def gcd(a: int, b: int) -> int:
                \"\"\"Return the greatest common divisor of a and b.\"\"\"
        """),
        "test": textwrap.dedent("""\
            assert gcd(12, 8) == 4
            assert gcd(7, 13) == 1
            assert gcd(100, 25) == 25
            assert gcd(0, 5) == 5
        """),
        "entry_point": "gcd",
    },
    {
        "id": "HumanEval/9",
        "prompt": textwrap.dedent("""\
            def matrix_transpose(matrix: list[list[int]]) -> list[list[int]]:
                \"\"\"Return the transpose of a 2D matrix.\"\"\"
        """),
        "test": textwrap.dedent("""\
            assert matrix_transpose([[1, 2], [3, 4]]) == [[1, 3], [2, 4]]
            assert matrix_transpose([[1, 2, 3]]) == [[1], [2], [3]]
            assert matrix_transpose([[1], [2], [3]]) == [[1, 2, 3]]
        """),
        "entry_point": "matrix_transpose",
    },
]

SYSTEM_PROMPT = """\
You are a code completion assistant. The user gives you a Python function \
signature with a docstring. You must output ONLY the complete function \
definition (including the signature line). Output raw Python code with no \
markdown fences, no explanation, no extra text.
"""


def extract_code(raw: str) -> str:
    """Extract Python code from LLM output, stripping markdown fences."""
    code = raw.strip()
    if "```python" in code:
        code = code.split("```python", 1)[1].split("```", 1)[0]
    elif "```" in code:
        code = code.split("```", 1)[1].split("```", 1)[0]
    return code.strip()


def run_test(code: str, test: str) -> bool:
    """Execute code + test and return True if all assertions pass."""
    full = f"{code}\n\n{test}"
    try:
        exec(full, {})  # noqa: S102
        return True
    except Exception:
        return False


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Print a simple ASCII table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        return " | ".join(c.ljust(w) for c, w in zip(cells, col_widths))

    separator = "-+-".join("-" * w for w in col_widths)
    print(fmt_row(headers))
    print(separator)
    for row in rows:
        print(fmt_row(row))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run HumanEval problems through a Chimera provider",
    )
    parser.add_argument("--count", type=int, default=10, help="Number of problems (default: 10)")
    parser.add_argument("--model", type=str, default=None, help="Model name (default: from env)")
    parser.add_argument("--dataset", type=str, default=None, help="Path to HumanEval JSON file")
    args = parser.parse_args()

    model = args.model or os.environ.get("ANTHROPIC_MODEL", "glm-5")
    print(f"Model:    {model}")
    print(f"Problems: {args.count}")
    print()

    provider = create_provider(model=model)

    # Load problems
    if args.dataset:
        bench = HumanEval(dataset_path=args.dataset, limit=args.count)
        problems = bench.tasks()
    else:
        problems = SAMPLE_PROBLEMS[:args.count]

    if not problems:
        print("No problems loaded. Provide --dataset or use defaults (max 10).")
        sys.exit(1)

    print(f"Loaded {len(problems)} HumanEval problems.")
    print()

    results_rows: list[list[str]] = []
    total_cost = 0.0
    passed_count = 0

    for i, problem in enumerate(problems, 1):
        task_id = problem.get("id", f"task_{i}")
        prompt_code = problem["prompt"]
        test_code = problem.get("test", "")
        print(f"[{i}/{len(problems)}] {task_id} ... ", end="", flush=True)

        messages = [
            Message.system(SYSTEM_PROMPT),
            Message.user(f"Complete this function:\n\n{prompt_code}"),
        ]
        response = provider.complete(messages, max_tokens=512)
        raw_output = response.content
        code = extract_code(raw_output)

        cost = 0.0
        usage = response.usage
        if usage:
            # Rough cost estimate — varies by model
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cost = (input_tokens * 0.003 + output_tokens * 0.015) / 1000

        passed = run_test(code, test_code) if test_code else False

        status = "PASS" if passed else "FAIL"
        print(f"{status} (cost=${cost:.4f})")

        if passed:
            passed_count += 1
        total_cost += cost

        results_rows.append([
            task_id,
            status,
            f"${cost:.4f}",
            code[:50].replace("\n", " ") + ("..." if len(code) > 50 else ""),
        ])

    # Summary
    print()
    print("=" * 72)
    print("RESULTS")
    print("=" * 72)
    print()

    print_table(
        ["Problem ID", "Status", "Cost", "Code Preview"],
        results_rows,
    )

    n = len(problems)
    c = passed_count
    pass_1 = pass_at_k(n=n, c=c, k=1)

    print()
    print(f"pass@1:     {pass_1:.3f} ({c}/{n})")
    print(f"Avg cost:   ${total_cost/n:.4f}")
    print(f"Total cost: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
