#!/usr/bin/env python3
"""Run the full official HumanEval benchmark (164 problems).

Downloads the dataset if not cached, sends each prompt to the LLM,
executes the generated code against the official test suite, and writes
results to data/humaneval-<model>-results.json (matching the schema
used by other benchmark reports under data/).

Usage:
    source .env
    python examples/benchmarks/humaneval_full.py
    python examples/benchmarks/humaneval_full.py --count 164   # full run
    python examples/benchmarks/humaneval_full.py --count 20    # quick subset
    python examples/benchmarks/humaneval_full.py --self-test   # offline extractor test
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


_TOP_LEVEL_RE = re.compile(r"^(?:def |async def |from |import |class )", re.MULTILINE)
_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)\n```", re.DOTALL)


def extract_code(response_text: str, prompt: str) -> str:
    """Extract the candidate from the LLM response, then ALWAYS prepend the
    prompt so its imports (e.g. `from typing import List`) and helper
    functions (e.g. HumanEval/32 `poly`) are in scope at exec time. The
    candidate's `def` for the entry-point overrides the prompt's stub.

    We learned the hard way that doing anything else is fragile: switching
    from in-process exec to subprocess isolation surfaced 12/20 NameErrors
    on `List` because the model returned `def foo(x: List[int])` without
    the import. Always-prepend makes the harness invariant to whether the
    model returns body-only, full-def, or fenced code.
    """
    text = response_text.strip()

    # 1. Fenced code block (with or without language tag) takes precedence.
    m = _FENCE_RE.search(text)
    if m:
        candidate = m.group(1).strip("\n")
    else:
        # No fence: skip any prose prefix by jumping to first def/import/class
        m2 = _TOP_LEVEL_RE.search(text)
        candidate = text[m2.start():] if m2 else text

    # 2. If body-only (no def/import/class), reindent unindented bodies so
    #    they sit under the prompt's def signature.
    if not _TOP_LEVEL_RE.search(candidate):
        lines = candidate.splitlines()
        if lines and lines[0] and not lines[0].startswith((" ", "\t")):
            candidate = "\n".join(("    " + ln) if ln.strip() else ln for ln in lines)

    # 3. Always prepend the prompt — its imports + helpers stay in scope,
    #    and the candidate's def for the entry-point overrides any same-
    #    named stub from the prompt.
    sep = "" if prompt.endswith("\n") else "\n"
    return prompt + sep + candidate + "\n"


def call_with_retry(provider, msg: str, max_tokens: int = 1024,
                    max_retries: int = 4, base_delay: float = 2.0):
    """Call provider.complete with exponential backoff on transient errors.

    Hard-fails on auth-style errors (401/403). Retries network errors,
    rate-limits (429), and 5xx for up to max_retries attempts.
    """
    from chimera.types import Message
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            return provider.complete([Message.user(msg)], max_tokens=max_tokens)
        except Exception as e:
            last_err = e
            es = str(e)
            if "401" in es or "403" in es or "invalid api key" in es.lower():
                raise
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise
    assert last_err is not None
    raise last_err


def run_test(code: str, test_code: str, entry_point: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Execute generated code + tests in a subprocess with a hard timeout.

    Subprocess isolation prevents an infinite-loop solution from hanging the
    whole run (we hit this once during a live run — exec() inside the parent
    process has no timeout, so a runaway `while True` candidate pegged CPU
    until killed). On timeout returns (False, "TIMEOUT (>Ns)").
    """
    import subprocess
    full_code = f"{code}\n\n{test_code}\n\ncheck({entry_point})\n"
    try:
        result = subprocess.run(
            [sys.executable, "-c", full_code],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, ""
        err = (result.stderr or "").strip().splitlines()
        return False, (err[-1] if err else f"exit {result.returncode}")[:200]
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT (>{timeout}s)"
    except Exception as e:
        return False, ((str(e) or type(e).__name__).splitlines()[0])[:200]


def self_test() -> int:
    """Offline tests for extract_code(). No network/provider needed.

    Each case must yield code that exec()s without SyntaxError. The point
    is to catch the harness bugs that caused the prior 66.5% result:
    body-only responses with no newline join, fenced code with prelude,
    and helper-function-required cases.
    """
    cases: list[tuple[str, str, str, str]] = [
        # (name, prompt, response, must_define_or_call)
        ("raw def",
         "def foo(x):\n    \"\"\"Doc.\"\"\"\n",
         "def foo(x):\n    return x + 1\n",
         "foo"),
        ("python-fenced",
         "def foo(x):\n    \"\"\"Doc.\"\"\"\n",
         "```python\ndef foo(x):\n    return x + 1\n```",
         "foo"),
        ("plain-fenced",
         "def foo(x):\n    \"\"\"Doc.\"\"\"\n",
         "```\ndef foo(x):\n    return x + 1\n```",
         "foo"),
        ("prelude-prose",
         "def foo(x):\n    \"\"\"Doc.\"\"\"\n",
         "Here is the implementation:\n\ndef foo(x):\n    return x + 1\n",
         "foo"),
        ("body only, indented",
         "def foo(x):\n    \"\"\"Doc.\"\"\"\n",
         "    return x + 1",
         "foo"),
        ("body only, unindented",
         "def foo(x):\n    \"\"\"Doc.\"\"\"\n",
         "return x + 1",
         "foo"),
        ("helper-required (HumanEval/32-style)",
         "import math\n\n"
         "def poly(xs, x):\n    return sum(c * x ** i for i, c in enumerate(xs))\n\n"
         "def find_zero(xs):\n    \"\"\"Doc.\"\"\"\n",
         "def find_zero(xs):\n    return -poly([0] + xs[1:], 1.0)\n",
         "find_zero"),
        ("entry-only, no helper needed",
         "def foo(x):\n    \"\"\"Doc.\"\"\"\n",
         "def foo(x):\n    return x * 2\n",
         "foo"),
    ]
    failed = 0
    for name, prompt, response, sym in cases:
        code = extract_code(response, prompt)
        try:
            ns: dict = {}
            exec(compile(code, "<self-test>", "exec"), ns)
            if sym not in ns:
                print(f"  FAIL [{name}]: '{sym}' not defined after exec")
                failed += 1
                continue
            print(f"  PASS [{name}]")
        except SyntaxError as e:
            print(f"  FAIL [{name}]: SyntaxError: {e}")
            print(f"    ---- extracted code ----")
            for ln in code.splitlines():
                print(f"    {ln}")
            print(f"    ----")
            failed += 1
        except Exception as e:
            print(f"  FAIL [{name}]: {type(e).__name__}: {e}")
            failed += 1
    print()
    print(f"self-test: {len(cases) - failed}/{len(cases)} passed")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run HumanEval benchmark")
    parser.add_argument("--count", type=int, default=164, help="Number of problems (default: 164)")
    parser.add_argument("--model", type=str, default=None, help="Model name")
    parser.add_argument("--dataset", type=str, default=None, help="Path to HumanEval.jsonl")
    parser.add_argument("--output", type=str, default=None, help="Override results JSON path")
    parser.add_argument("--self-test", action="store_true", help="Run offline extractor tests and exit")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    from chimera.providers.factory import create_provider

    provider = create_provider(model=args.model)
    dataset_path = args.dataset or download_dataset()
    problems = load_problems(dataset_path, args.count)

    print(f"Model:    {provider.model_name}")
    print(f"Problems: {len(problems)}")
    print()

    results: list[dict] = []
    passed = 0
    total_cost = 0.0
    start_time = time.time()

    for i, problem in enumerate(problems, 1):
        task_id = problem["task_id"]
        prompt = problem["prompt"]
        test_code = problem["test"]
        entry_point = problem["entry_point"]

        msg = (
            f"Complete the following Python function. Return ONLY the complete function implementation, "
            f"nothing else. No explanation, no markdown, just the Python code.\n\n{prompt}"
        )

        error_msg = ""
        cost = 0.0
        success = False
        try:
            response = call_with_retry(provider, msg, max_tokens=1024)
            code = extract_code(response.content, prompt)
            if response.usage:
                from chimera.providers.cost import calculate_cost
                cost = calculate_cost(provider.model_name, response.usage)
            total_cost += cost
            success, error_msg = run_test(code, test_code, entry_point)
        except Exception as e:
            error_msg = ((str(e) or type(e).__name__).splitlines()[0])[:200]

        status = "PASS" if success else "FAIL"
        if success:
            passed += 1

        results.append({
            "task_id": task_id,
            "status": status,
            "cost": cost,
            "error": error_msg,
        })

        elapsed = time.time() - start_time
        rate = i / elapsed if elapsed > 0 else 0
        suffix = f"  ({error_msg[:60]})" if error_msg else ""
        print(f"[{i}/{len(problems)}] {task_id:30s} {status}  ({rate:.1f} prob/s){suffix}")

    elapsed = time.time() - start_time
    pass_rate = passed / len(problems) if problems else 0.0

    print()
    print("=" * 72)
    print("RESULTS")
    print("=" * 72)
    print(f"pass@1:      {pass_rate:.3f} ({passed}/{len(problems)})")
    print(f"Total cost:  ${total_cost:.4f}")
    if problems:
        print(f"Avg cost:    ${total_cost / len(problems):.4f}")
        print(f"Time:        {elapsed:.1f}s ({elapsed / len(problems):.1f}s/problem)")
    print()

    failures = [r for r in results if r["status"] == "FAIL"]
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for r in failures:
            print(f"  {r['task_id']:30s}  {r['error'][:80]}")

    # Canonical JSON (matches data/*.json schema) + per-problem JSONL.
    out_path = args.output or os.path.join(
        REPO_ROOT, "data", f"humaneval-{provider.model_name}-results.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "model": provider.model_name,
            "passed": passed,
            "total": len(problems),
            "pass_rate": pass_rate,
            "errors": [[r["task_id"], r["error"]] for r in results if r["status"] == "FAIL"],
        }, f, indent=2)
    detail_path = out_path.replace(".json", "-detail.jsonl")
    with open(detail_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"\nResults saved to: {out_path}")
    print(f"Per-problem detail: {detail_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
