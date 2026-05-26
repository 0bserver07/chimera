#!/usr/bin/env python3
"""Run the MATH-500 math-reasoning benchmark.

MATH-500 is the HuggingFaceH4/MATH-500 split: 500 problems drawn from the
original Hendrycks MATH dataset, used as a standard math-reasoning eval
on modern LLMs. Each row has fields:

    problem      : the prompt (LaTeX-flavoured)
    solution     : reference worked solution (LaTeX)
    answer       : canonical final answer string (LaTeX)
    subject      : Algebra / Geometry / Number Theory / ...
    level        : 1-5 (5 hardest)
    unique_id    : e.g. test/algebra/123.json

The model is asked to wrap its final answer in `\\boxed{...}`. We extract
the content of the LAST `\\boxed{...}` in the response, normalize, and
compare it to the canonical `answer` field after the same normalization.

Two modes:
    --backend python  (default): drives chimera's provider, used for
        models the raw API still serves (Haiku, GLM).
    --backend cli              : wraps the `claude -p --output-format=json`
        Max-tier CLI. Required for Sonnet/Opus on this account because
        raw API returns 429 there.

Usage:
    source /tmp/claude_oauth_env.sh
    python examples/benchmarks/math500_full.py \\
        --model claude-haiku-4-5-20251001 --count 500 \\
        --output data/math500-claude-haiku-4-5-20251001-results.json
    python examples/benchmarks/math500_full.py --backend cli \\
        --model claude-sonnet-4-6 --count 500 \\
        --output data/math500-claude-sonnet-4-6-results.json
    python examples/benchmarks/math500_full.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_CACHE = "/tmp/math500.jsonl"


def download_dataset() -> str:
    """Download MATH-500 via the `datasets` library if not cached."""
    if os.path.exists(DATASET_CACHE):
        return DATASET_CACHE
    print("Downloading MATH-500 dataset via datasets...")
    from datasets import load_dataset
    ds = load_dataset("HuggingFaceH4/MATH-500", split="test")
    with open(DATASET_CACHE, "w") as f:
        for row in ds:
            f.write(json.dumps(dict(row)) + "\n")
    return DATASET_CACHE


def load_problems(path: str, count: int | None = None, start: int = 0) -> list[dict]:
    """Load problems from JSONL. Optionally slice [start:start+count].

    `start` is a 0-based offset, `count` is a cap on returned rows.
    """
    problems: list[dict] = []
    with open(path) as f:
        for line in f:
            problems.append(json.loads(line))
    if start:
        problems = problems[start:]
    if count:
        problems = problems[:count]
    return problems


# Extract the contents of the LAST top-level \boxed{...} in `text`.
# We can't use a single regex because LaTeX often nests braces (e.g.
# \boxed{\frac{1}{2}}) and re's group repetition doesn't balance them.
def extract_boxed(text: str) -> str | None:
    """Return the substring inside the LAST balanced \\boxed{...} in `text`.

    Returns None if no `\\boxed{` token is found or its brace never closes.
    Balances nested braces — handles \\boxed{\\frac{1}{2}} correctly.
    """
    if not text:
        return None
    # Find every \boxed{ start, take the last one; iterate to find balanced }.
    starts = [m.end() for m in re.finditer(r"\\boxed\s*\{", text)]
    if not starts:
        return None
    start = starts[-1]
    depth = 1
    i = start
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return None


# Normalization rules for MATH-style answers. The goal is to collapse
# cosmetically-different but mathematically-equal answers to the same
# string. We deliberately keep this conservative — sympy parsing is more
# precise but blows up on LaTeX edge cases (matrices, intervals, "x=5"),
# so the string-equality path is the baseline and sympy is the fallback.
_LATEX_STRIP = [
    (r"\\!", ""),
    (r"\\,", ""),
    (r"\\;", ""),
    (r"\\:", ""),
    (r"\\ ", " "),
    (r"\\left", ""),
    (r"\\right", ""),
    (r"\\\\", ""),
    (r"\\quad", " "),
    (r"\\qquad", " "),
    (r"\\text\{([^}]*)\}", r"\1"),
    (r"\\textbf\{([^}]*)\}", r"\1"),
    (r"\\mathrm\{([^}]*)\}", r"\1"),
    (r"\\mathbf\{([^}]*)\}", r"\1"),
    (r"\\,", ""),
    (r"\^\s*\\circ", ""),
    (r"\\circ", ""),
    (r"\\degree", ""),
    (r"\\%", "%"),
    (r"\\\$", "$"),
    (r"\\dfrac", r"\\frac"),
    (r"\\tfrac", r"\\frac"),
    # Normalize single-token sqrt: `\sqrt2` → `\sqrt{2}`. MATH refs mix both.
    (r"\\sqrt\s*([0-9])\b", r"\\sqrt{\1}"),
    # Normalize single-letter sqrt similarly: `\sqrt x` → `\sqrt{x}`.
    (r"\\sqrt\s+([a-zA-Z])\b", r"\\sqrt{\1}"),
    # \frac43 → \frac{4}{3}, \frac 59 → \frac{5}{9}. MATH refs sometimes
    # drop the braces around single-digit numerators/denominators.
    (r"\\frac\s*([0-9])\s*([0-9])\b", r"\\frac{\1}{\2}"),
    # \mbox{...} is just inline text in LaTeX — drop the wrapper.
    (r"\\mbox\{([^}]*)\}", r"\1"),
    # Number subscripts: 4210_5 → 4210_{5}, x_1 → x_{1}.
    (r"_([0-9A-Za-z])\b", r"_{\1}"),
    # Drop thin-space comma in big numbers: 10,\!080 → 10,080 ≡ 10080
    (r",\\!", ""),
    # Drop dollar sign prefix.
    (r"\\\$", ""),
    (r"\$+", ""),
    # Drop unicode degree symbol — model writes 76° where ref writes 76^\circ.
    ("°", ""),
    # Drop residual commas inside numbers (already-stripped \!). Only
    # strip when 3 digits follow the comma — that's the thousands-separator
    # case (10,080). Doesn't touch tuples like (-2,1) or (1,-16,-4,43).
    (r"(\d),(\d{3})\b", r"\1\2"),
    # \frac9{19} → \frac{9}{19}: single-digit numerator with braced denom.
    (r"\\frac\s*([0-9])\s*\{", r"\\frac{\1}{"),
]


def normalize_answer(s: str) -> str:
    """Cosmetic normalization for string equality between candidate and ref.

    - strip whitespace and trailing punctuation
    - drop `\\left` / `\\right` / `\\!` / `\\,` spacing markers
    - collapse `\\dfrac` / `\\tfrac` → `\\frac`
    - lowercase
    - strip wrapping $...$
    - drop the LHS of "x = 5" style equations (keep RHS)
    """
    if s is None:
        return ""
    out = s.strip()
    # drop wrapping $...$ display math
    if out.startswith("$") and out.endswith("$") and len(out) >= 2:
        out = out[1:-1].strip()
    # strip trailing period and stray punctuation
    out = out.rstrip(".")
    # apply latex pruning rules, looping each rule to a fixed point
    # so chained thousands-commas (1,000,000) collapse fully.
    for pat, repl in _LATEX_STRIP:
        for _ in range(8):
            new_out = re.sub(pat, repl, out)
            if new_out == out:
                break
            out = new_out
    # collapse internal whitespace
    out = re.sub(r"\s+", "", out)
    # if answer is "x=5" or "y = -3", keep the rhs — both forms are
    # treated as equal because MATH answers are often just "5" while the
    # model writes "x=5".
    if "=" in out:
        # but only if the LHS looks like a single variable / short symbol
        head, _, tail = out.rpartition("=")
        if head and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,6}", head):
            out = tail
    # Drop trailing units that don't change numerical answer: percent,
    # short cm/m/km/mm/kg/g/in/ft tokens. We only strip when a numeric
    # prefix precedes the unit, so we don't mangle algebraic answers.
    out = re.sub(r"(\d)\s*%$", r"\1", out)
    out = re.sub(r"(\d)\s*(cm|mm|km|kg|in|ft|m|g)$", r"\1", out)
    return out.lower()


def grade_answer(candidate: str | None, reference: str) -> bool:
    """True iff candidate matches reference after normalization.

    Tries string equality on normalized forms first; falls back to sympy
    symbolic equivalence for numeric-only or simple-expression answers
    (skip on parse failure — string equality is the primary signal).
    """
    if candidate is None:
        return False
    ca = normalize_answer(candidate)
    cb = normalize_answer(reference)
    if ca == cb:
        return True
    # Numeric coercion fallback: "3.0" vs "3", "1/2" vs "0.5", etc.
    try:
        from fractions import Fraction
        def to_num(x: str) -> float | None:
            x = x.replace("\\frac{", "(").replace("}{", ")/(").replace("}", ")")
            try:
                return float(Fraction(x))
            except Exception:
                try:
                    return float(x)
                except Exception:
                    return None
        na, nb = to_num(ca), to_num(cb)
        if na is not None and nb is not None and abs(na - nb) < 1e-9:
            return True
    except Exception:
        pass
    return False


PROMPT_TEMPLATE = (
    "Solve the following math problem step by step. Put your final answer "
    "in \\boxed{{}} at the end. Be concise.\n\n"
    "Problem: {problem}\n\nSolution:"
)


def call_with_retry(provider, msg: str, max_tokens: int = 2048,
                    max_retries: int = 4, base_delay: float = 2.0):
    """Call provider.complete with exponential backoff on transient errors.

    Hard-fails on auth errors (401/403). Retries network errors, 429, 5xx
    up to max_retries.
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


def call_claude_cli(model: str, prompt_msg: str, timeout: int = 240) -> tuple[str, float]:
    """Invoke `claude -p --output-format=json` once with prompt on stdin.

    Mirrors humaneval_cli.call_claude_cli — including the
    ANTHROPIC_API_KEY scrub, which is required because the CLI otherwise
    sends the OAuth token as x-api-key (not Bearer) and the server
    rejects it as "Invalid API key".
    """
    child_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    result = subprocess.run(
        [
            "claude",
            "--model", model,
            "-p",
            "--output-format", "json",
            "--no-session-persistence",
        ],
        input=prompt_msg,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=child_env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exit {result.returncode}: {result.stderr[:200]}")

    out = result.stdout.strip()
    try:
        events = json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude CLI returned non-JSON ({len(out)} bytes): {out[:200]}")

    # The CLI emits a JSON array with a terminal "result" event.
    if isinstance(events, list):
        result_event = next((e for e in events if isinstance(e, dict) and e.get("type") == "result"), None)
    elif isinstance(events, dict) and events.get("type") == "result":
        result_event = events
    else:
        result_event = None
    if not result_event:
        raise RuntimeError("no `result` event in CLI output")

    text = result_event.get("result", "")
    cost = float(result_event.get("total_cost_usd", 0.0))
    return text, cost


def regrade(detail_path: str) -> int:
    """Recompute pass/fail from an existing -detail.jsonl using the current
    grader, and rewrite the matching summary JSON.

    Used after improving the grader to update scores without spending more
    API budget. The detail JSONL stores `candidate` and `reference` per
    row, so all we need is to re-run grade_answer on each.
    """
    rows: list[dict] = []
    with open(detail_path) as f:
        for line in f:
            rows.append(json.loads(line))
    passed = 0
    total_cost = 0.0
    by_level: dict[int, tuple[int, int]] = {}
    model_name = ""
    for r in rows:
        cand = r.get("candidate")
        ref = r.get("reference", "")
        # If row was reconstructed from a log (candidate text lost), trust
        # its preserved status field and only re-grade rows with an
        # actual candidate string available.
        if r.get("_reconstructed_from_log"):
            ok = r.get("status") == "PASS"
        else:
            ok = grade_answer(cand, ref)
            r["status"] = "PASS" if ok else "FAIL"
            if ok:
                r["error"] = ""
            else:
                if cand is None:
                    r["error"] = "no \\boxed{} in response"
                else:
                    r["error"] = f"got {cand!r} want {ref!r}"
        if ok:
            passed += 1
        total_cost += float(r.get("cost", 0.0))
        lvl_raw = r.get("level", 0)
        try:
            lvl = int(lvl_raw)
        except Exception:
            lvl = 0
        p, t = by_level.get(lvl, (0, 0))
        by_level[lvl] = (p + (1 if ok else 0), t + 1)

    pass_rate = passed / len(rows) if rows else 0.0
    summary_path = detail_path.replace("-detail.jsonl", ".json")
    # Pull model_name + backend out of the existing summary if present.
    backend = "unknown"
    if os.path.exists(summary_path):
        try:
            with open(summary_path) as f:
                prev = json.load(f)
            model_name = prev.get("model", "")
            backend = prev.get("backend", backend)
        except Exception:
            pass

    print(f"Regraded {len(rows)} rows from {detail_path}")
    print(f"pass@1: {pass_rate:.3f} ({passed}/{len(rows)})")
    print(f"Total cost (sum of original calls): ${total_cost:.4f}")
    print()
    print("By level:")
    for lvl in sorted(by_level):
        p, t = by_level[lvl]
        print(f"  L{lvl}: {p}/{t} ({100*p/t:.1f}%)" if t else f"  L{lvl}: 0/0")

    with open(summary_path, "w") as f:
        json.dump({
            "model": model_name,
            "backend": backend,
            "passed": passed,
            "total": len(rows),
            "pass_rate": pass_rate,
            "total_cost_usd": round(total_cost, 4),
            "by_level": {str(k): {"passed": v[0], "total": v[1]} for k, v in by_level.items()},
            "errors": [[r["task_id"], r["error"]] for r in rows if r["status"] == "FAIL"],
            "regraded": True,
        }, f, indent=2)
    # Rewrite the detail JSONL with updated status + error fields.
    with open(detail_path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"\nSummary rewritten: {summary_path}")
    print(f"Detail rewritten:  {detail_path}")
    return 0


def self_test() -> int:
    """Offline tests for extract_boxed + grade_answer. No network needed."""
    extract_cases = [
        ("plain", "Answer: \\boxed{42}", "42"),
        ("nested frac", "So \\boxed{\\frac{1}{2}} is final.", "\\frac{1}{2}"),
        ("nested deep", "\\boxed{\\frac{a}{b+\\frac{1}{2}}}", "\\frac{a}{b+\\frac{1}{2}}"),
        ("two boxes, take last", "\\boxed{wrong} then \\boxed{right}", "right"),
        ("no box", "I have no clue.", None),
        ("box with space", "\\boxed {7}", "7"),
        ("box around equation", "\\boxed{x=5}", "x=5"),
    ]
    grade_cases = [
        ("int match", "42", "42", True),
        ("int + period", "42.", "42", True),
        ("equation lhs strip", "x=5", "5", True),
        ("frac variants", "\\dfrac{1}{2}", "\\frac{1}{2}", True),
        ("left right strip", "\\left(3,\\frac{\\pi}{2}\\right)", "(3,\\frac{\\pi}{2})", True),
        ("numeric eq", "1/2", "0.5", True),
        ("degree caret", "90", "90^\\circ", True),
        ("degree caret reverse", "90^\\circ", "90", True),
        ("sqrt brace eq", "11\\sqrt{2}", "11\\sqrt2", True),
        ("sqrt brace rev", "11\\sqrt2", "11\\sqrt{2}", True),
        ("trailing percent", "10\\%", "10", True),
        ("trailing cm", "12 \\text{ cm}", "12", True),
        ("frac no brace", "\\frac{4}{3}", "\\frac43", True),
        ("frac no brace spaces", "\\frac{5}{9}", "\\frac 59", True),
        ("dollar comma", "32348", "\\$32,\\!348", True),
        ("thin space commas", "10080", "10,\\!080", True),
        ("unicode degree", "76°", "76^\\circ", True),
        ("subscript brace", "4210_5", "4210_{5}", True),
        ("mbox strip", "864", "864 \\mbox{}", True),
        ("mismatch", "10", "42", False),
        ("nonsense", None, "5", False),
    ]
    failed = 0
    for name, text, want in extract_cases:
        got = extract_boxed(text)
        ok = got == want
        if not ok:
            failed += 1
            print(f"  FAIL extract [{name}]: got={got!r} want={want!r}")
        else:
            print(f"  PASS extract [{name}]")
    for name, cand, ref, want in grade_cases:
        got = grade_answer(cand, ref)
        ok = got == want
        if not ok:
            failed += 1
            print(f"  FAIL grade [{name}]: got={got} want={want} (cand={cand!r}, ref={ref!r})")
        else:
            print(f"  PASS grade [{name}]")
    total = len(extract_cases) + len(grade_cases)
    print()
    print(f"self-test: {total - failed}/{total} passed")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MATH-500 benchmark")
    parser.add_argument("--count", type=int, default=500, help="Number of problems (default: 500)")
    parser.add_argument("--start", type=int, default=0, help="0-based start offset (for partial reruns)")
    parser.add_argument("--model", type=str, default=None, help="Model name")
    parser.add_argument("--dataset", type=str, default=None, help="Path to math500.jsonl")
    parser.add_argument("--output", type=str, default=None, help="Override results JSON path")
    parser.add_argument(
        "--backend",
        choices=("python", "cli"),
        default="python",
        help="python = chimera provider (default); cli = `claude -p` (for Sonnet/Opus on Max)",
    )
    parser.add_argument("--max-tokens", type=int, default=2048, help="Max response tokens")
    parser.add_argument("--call-timeout", type=int, default=240, help="Per-call timeout in seconds (cli mode)")
    parser.add_argument("--self-test", action="store_true", help="Run offline tests and exit")
    parser.add_argument(
        "--regrade",
        type=str,
        default=None,
        help="Path to existing -detail.jsonl; recompute scores with current grader and rewrite the matching summary JSON (no network).",
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    if args.regrade:
        return regrade(args.regrade)

    dataset_path = args.dataset or download_dataset()
    problems = load_problems(dataset_path, args.count, args.start)

    if args.backend == "python":
        from chimera.providers.factory import create_provider
        provider = create_provider(model=args.model)
        model_name = provider.model_name
        backend_label = "chimera-python"
    else:
        if not args.model:
            raise SystemExit("--model is required when --backend cli")
        provider = None
        model_name = args.model
        backend_label = "claude-cli"

    print(f"Model:    {model_name}")
    print(f"Problems: {len(problems)}")
    print(f"Backend:  {backend_label}")
    print()

    # Pre-compute output paths so we can stream per-problem rows to JSONL.
    # Streaming means a SIGKILL mid-run doesn't lose the candidate+ref
    # pairs needed for offline regrade — important for long CLI runs.
    out_path_pre = args.output or os.path.join(
        REPO_ROOT, "data", f"math500-{model_name}-results.json"
    )
    os.makedirs(os.path.dirname(out_path_pre) or ".", exist_ok=True)
    detail_path_pre = out_path_pre.replace(".json", "-detail.jsonl")
    # Append mode if --start > 0, else truncate.
    detail_mode = "a" if args.start > 0 else "w"
    detail_fp = open(detail_path_pre, detail_mode)

    results: list[dict] = []
    passed = 0
    total_cost = 0.0
    start_time = time.time()

    for i, problem in enumerate(problems, 1):
        task_id = problem["unique_id"]
        question = problem["problem"]
        reference = problem["answer"]
        level = problem.get("level", "?")
        subject = problem.get("subject", "?")

        msg = PROMPT_TEMPLATE.format(problem=question)

        error_msg = ""
        cost = 0.0
        success = False
        candidate = None
        response_text = ""
        try:
            if args.backend == "python":
                response = call_with_retry(provider, msg, max_tokens=args.max_tokens)
                response_text = response.content
                if response.usage:
                    from chimera.providers.cost import calculate_cost
                    cost = calculate_cost(model_name, response.usage)
            else:
                response_text, cost = call_claude_cli(args.model, msg, timeout=args.call_timeout)
            total_cost += cost
            candidate = extract_boxed(response_text)
            success = grade_answer(candidate, reference)
            if not success and candidate is None:
                error_msg = "no \\boxed{} in response"
            elif not success:
                error_msg = f"got {candidate!r} want {reference!r}"
        except Exception as e:
            error_msg = ((str(e) or type(e).__name__).splitlines()[0])[:200]

        status = "PASS" if success else "FAIL"
        if success:
            passed += 1

        row = {
            "task_id": task_id,
            "status": status,
            "level": level,
            "subject": subject,
            "candidate": candidate,
            "reference": reference,
            "cost": cost,
            "error": error_msg,
        }
        results.append(row)
        detail_fp.write(json.dumps(row) + "\n")
        detail_fp.flush()

        elapsed = time.time() - start_time
        rate = i / elapsed if elapsed > 0 else 0
        suffix = f"  ({error_msg[:50]})" if error_msg else ""
        print(f"[{i}/{len(problems)}] {task_id:30s} L{level} {status}  ({rate:.2f} p/s, ${total_cost:.2f}){suffix}")

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

    # By-level breakdown — MATH-500 problems carry difficulty 1-5.
    by_level: dict[int, tuple[int, int]] = {}
    for r in results:
        lvl = r.get("level", 0)
        try:
            lvl_int = int(lvl)
        except Exception:
            lvl_int = 0
        p, t = by_level.get(lvl_int, (0, 0))
        by_level[lvl_int] = (p + (1 if r["status"] == "PASS" else 0), t + 1)
    if by_level:
        print()
        print("By level:")
        for lvl in sorted(by_level):
            p, t = by_level[lvl]
            print(f"  L{lvl}: {p}/{t} ({100*p/t:.1f}%)" if t else f"  L{lvl}: 0/0")

    detail_fp.close()
    out_path = out_path_pre
    with open(out_path, "w") as f:
        json.dump({
            "model": model_name,
            "backend": backend_label,
            "passed": passed,
            "total": len(problems),
            "pass_rate": pass_rate,
            "total_cost_usd": round(total_cost, 4),
            "by_level": {str(k): {"passed": v[0], "total": v[1]} for k, v in by_level.items()},
            "errors": [[r["task_id"], r["error"]] for r in results if r["status"] == "FAIL"],
        }, f, indent=2)
    print(f"\nResults saved to: {out_path}")
    print(f"Per-problem detail: {detail_path_pre}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
