#!/usr/bin/env python3
"""Real-world demo: AI code review on your uncommitted git diff.

Pipes your current `git diff` (or `git diff --staged`, or a file) into an
Ollama cloud model via Chimera's Anthropic-compatible provider and prints a
structured review: issues grouped by severity, with file:line refs when the
model identifies them.

This is a useful everyday tool: run it before you commit to get a second pair
of eyes on your changes.

Setup (first time):

  ollama signin                          # or set OLLAMA_API_KEY
  export ANTHROPIC_BASE_URL=https://ollama.com          # cloud
  export ANTHROPIC_AUTH_TOKEN=$OLLAMA_API_KEY

Usage:

  python examples/ollama_code_review.py                 # review `git diff`
  python examples/ollama_code_review.py --staged        # review staged
  python examples/ollama_code_review.py --file my.patch # review a file
  python examples/ollama_code_review.py --model glm-5.1 # pick a model

Exit codes:
  0 — review completed (regardless of verdict)
  1 — no diff to review
  2 — provider or network error
  3 — could not reach Ollama
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera

DEFAULT_MODEL = "kimi-k2.6"
DEFAULT_BASE_URL = "https://ollama.com"
DEFAULT_MAX_DIFF_LINES = 4000
DEFAULT_MAX_TOKENS = 2000

SYSTEM_PROMPT = """\
You are a senior engineer doing code review. You will be given a git diff.
Produce a concise review with this structure:

VERDICT: one of [APPROVE, REQUEST_CHANGES, COMMENT]
SUMMARY: one or two sentences on what this diff does.

ISSUES (ordered most to least critical):
- [SEVERITY] file:line — short description
  context: why this matters, one line.

Severity levels (use exactly these): CRITICAL, HIGH, MEDIUM, LOW, NIT.

Only flag real problems. Do NOT invent issues to seem thorough. If the diff
looks clean, VERDICT: APPROVE and an empty ISSUES list is correct — say so.

Focus on: bugs, security issues, race conditions, missing error handling,
tests that don't test the behavior they claim, naming that will confuse
readers, and dead code. Skip style quibbles; trust the linter.
"""


def _mask(tok: str) -> str:
    if not tok:
        return "(empty)"
    return f"{tok[:4]}...{tok[-4:]}" if len(tok) > 10 else "***"


def get_diff(source: argparse.Namespace) -> str:
    """Return the diff text from the chosen source."""
    if source.file:
        with open(source.file, encoding="utf-8") as f:
            return f.read()
    cmd = ["git", "diff"]
    if source.staged:
        cmd.append("--staged")
    if source.ref:
        cmd.append(source.ref)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
    return result.stdout


def truncate_diff(diff: str, max_lines: int) -> tuple[str, bool]:
    """Truncate to max_lines to stay within context. Returns (text, truncated_flag)."""
    lines = diff.splitlines()
    if len(lines) <= max_lines:
        return diff, False
    head = lines[: max_lines - 20]
    return "\n".join(head) + f"\n\n... [truncated {len(lines) - len(head)} more lines]", True


def preflight(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host_root = f"{parsed.scheme}://{parsed.netloc}"
    try:
        with urllib.request.urlopen(host_root, timeout=5) as resp:
            return resp.status < 500
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI code review on your git diff",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--staged", action="store_true", help="Review staged diff (git diff --staged)")
    source_group.add_argument("--file", help="Review the diff in this patch file")
    source_group.add_argument("--ref", help="git diff <ref>..HEAD (e.g. origin/master)")

    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=os.environ.get("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("OLLAMA_API_KEY") or "ollama",
    )
    parser.add_argument("--max-diff-lines", type=int, default=DEFAULT_MAX_DIFF_LINES)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    args = parser.parse_args()

    # Gather the diff first; if there's nothing to review, don't burn tokens.
    try:
        diff = get_diff(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not diff.strip():
        print("No changes to review.", file=sys.stderr)
        print("Hint: `git diff` is empty. Try --staged or --ref main.", file=sys.stderr)
        return 1

    diff, truncated = truncate_diff(diff, args.max_diff_lines)

    os.environ["ANTHROPIC_BASE_URL"] = args.base_url
    os.environ["ANTHROPIC_AUTH_TOKEN"] = args.auth_token
    os.environ.setdefault("ANTHROPIC_API_KEY", "")

    print(f"Model:    {args.model}")
    print(f"Base URL: {args.base_url}")
    print(f"Auth:     {_mask(args.auth_token)}")
    print(f"Diff:     {len(diff.splitlines())} lines{' (truncated)' if truncated else ''}")
    print()

    if not preflight(args.base_url):
        print(f"Could not reach {args.base_url}.", file=sys.stderr)
        print("For cloud: ollama signin. For local: ollama serve.", file=sys.stderr)
        return 3

    try:
        provider = chimera.create_provider(
            model=args.model,
            api_key=args.auth_token,
            base_url=args.base_url,
        )
    except Exception as exc:
        print(f"Could not create provider: {exc}", file=sys.stderr)
        return 2

    user_message = (
        "Review the following diff. Use the VERDICT/SUMMARY/ISSUES format.\n\n"
        "```diff\n"
        f"{diff}\n"
        "```"
    )

    messages = [
        chimera.Message.system(SYSTEM_PROMPT),
        chimera.Message.user(user_message),
    ]

    print("=" * 72)
    print("CODE REVIEW")
    print("=" * 72)
    print()

    try:
        response = provider.complete(messages, max_tokens=args.max_tokens)
    except Exception as exc:
        print(f"Review request failed: {exc}", file=sys.stderr)
        return 2

    print(response.content or "(empty response)")
    print()
    print("-" * 72)
    usage = response.usage or {}
    if isinstance(usage, dict):
        input_tok = usage.get("input_tokens", "?")
        output_tok = usage.get("output_tokens", "?")
        thinking = usage.get("thinking_tokens")
        detail = f"in {input_tok} / out {output_tok}"
        if thinking:
            detail += f" / thinking {thinking}"
    else:
        detail = str(usage)
    print(f"Tokens:   {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
