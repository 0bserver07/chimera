#!/usr/bin/env python3
"""Run the full official HumanEval benchmark (164 problems).

Downloads the dataset if not cached, sends each prompt to the LLM,
executes the generated code against the official test suite.

Usage:
    source .env
    python examples/humaneval_full.py
    python examples/humaneval_full.py --count 164   # full run
    python examples/humaneval_full.py --count 20    # quick subset
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chimera.providers.factory import create_provider
from chimera.types import Message

DATASET_URL = "https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz"
DATASET_CACHE = "/tmp/HumanEval.jsonl"


def download_dataset() -> str:
    """Download HumanEval dataset if not cached."""
    if os.path.exists(DATASET_CACHE):
        return DATASET_CACHE

    print("Downloading HumanEval dataset...")
    gz_path = DATASET_CACHE + ".gz"
    urllib.request.urlretrieve(DATASET_URL, gz_path)
    import gzip
    with gzip.open(gz_path, "rb") as f_in:
        with open(DATASET_CACHE, "wb") as f_out:
            f_out.write(f_in.read())
    os.unlink(gz_path)
    return DATASET_CACHE


def load_problems(path: str, count: int | None = None) -> list[dict]:
    """Load HumanEval problems from JSONL file."""
    problems = []
    with open(path) as f:
        for line in f:
            problems.append(json.loads(line))
    if count:
        problems = problems[:count]
    return problems


def extract_code(response_text: str, prompt: str) -> str:
    """Extract the function implementation from the LLM response."""
    text = response_text.strip()

    # If response contains ```python blocks, extract them
    if "```python" in text:
        blocks = text.split("```python")
        for block in blocks[1:]:
            code = block.split("```")[0].strip()
            if code:
                return code
    if "```" in text:
        blocks = text.split("```")
        for i, block in enumerate(blocks):
            if i % 2 == 1:  # odd blocks are code
                code = block.strip()
                if code.startswith("python\n"):
                    code = code[7:]
                if code:
                    return code

    # Response might be just the function body — prepend the prompt
    if not text.startswith("def ") and not text.startswith("from ") and not text.startswith("import "):
        return prompt + text

    return text


def run_test(code: str, test_code: str, entry_point: str, timeout: float = 10.0) -> bool:
    """Execute the generated code against the test suite."""
    # The official test format defines check(candidate)
    # We need to combine: imports + generated code + test code + check(entry_point)
    full_code = f"{code}\n\n{test_code}\n\ncheck({entry_point})\n"

    try:
        exec_globals: dict = {}
        exec(compile(full_code, "<humaneval>", "exec"), exec_globals)
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Run HumanEval benchmark")
    parser.add_argument("--count", type=int, default=164, help="Number of problems (default: all 164)")
    parser.add_argument("--model", type=str, default=None, help="Model name")
    parser.add_argument("--dataset", type=str, default=None, help="Path to HumanEval.jsonl")
    args = parser.parse_args()

    # Setup
    provider = create_provider(model=args.model)
    dataset_path = args.dataset or download_dataset()
    problems = load_problems(dataset_path, args.count)

    print(f"Model:    {provider.model_name}")
    print(f"Problems: {len(problems)}")
    print()

    # Run
    results: list[dict] = []
    passed = 0
    total_cost = 0.0
    start_time = time.time()

    for i, problem in enumerate(problems, 1):
        task_id = problem["task_id"]
        prompt = problem["prompt"]
        test_code = problem["test"]
        entry_point = problem["entry_point"]

        # Ask the model to complete the function
        msg = (
            f"Complete the following Python function. Return ONLY the complete function implementation, "
            f"nothing else. No explanation, no markdown, just the Python code.\n\n{prompt}"
        )

        try:
            response = provider.complete([Message.user(msg)], max_tokens=1024)
            code = extract_code(response.content, prompt)
            cost = 0.0  # GLM-5 doesn't report real cost
            if response.usage:
                from chimera.providers.cost import calculate_cost
                cost = calculate_cost(provider.model_name, response.usage)
            total_cost += cost

            success = run_test(code, test_code, entry_point)
        except Exception:
            code = ""
            success = False
            cost = 0.0

        status = "PASS" if success else "FAIL"
        if success:
            passed += 1

        results.append({
            "task_id": task_id,
            "status": status,
            "cost": cost,
        })

        elapsed = time.time() - start_time
        rate = i / elapsed if elapsed > 0 else 0
        print(f"[{i}/{len(problems)}] {task_id:30s} {status}  ({rate:.1f} prob/s)")

    # Report
    elapsed = time.time() - start_time
    pass_rate = passed / len(problems) if problems else 0

    print()
    print("=" * 72)
    print("RESULTS")
    print("=" * 72)
    print(f"pass@1:      {pass_rate:.3f} ({passed}/{len(problems)})")
    print(f"Total cost:  ${total_cost:.4f}")
    print(f"Avg cost:    ${total_cost / len(problems):.4f}" if problems else "")
    print(f"Time:        {elapsed:.1f}s ({elapsed / len(problems):.1f}s/problem)" if problems else "")
    print()

    # Failures
    failures = [r for r in results if r["status"] == "FAIL"]
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  {f['task_id']}")

    # Save results
    results_path = f"/tmp/humaneval_results_{provider.model_name}.jsonl"
    with open(results_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"\nResults saved to: {results_path}")


if __name__ == "__main__":
    main()
