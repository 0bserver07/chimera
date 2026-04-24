#!/usr/bin/env python3
"""Real-world demo: explain an unfamiliar file to yourself in plain English.

Reads a source file from disk, sends it to an Ollama model via Chimera's
Anthropic-compatible provider, and prints a structured explanation: what the
file is, what it does, the important definitions, how it likely fits into the
rest of the codebase, and any gotchas.

This is the thing you reach for when you open a repo you've never seen and
need to know "what am I looking at?" before you touch anything.

Setup (first time):

  ollama signin                          # or set OLLAMA_API_KEY
  export ANTHROPIC_BASE_URL=https://ollama.com          # cloud
  export ANTHROPIC_AUTH_TOKEN=$OLLAMA_API_KEY

Usage:

  python examples/ollama_explain.py path/to/file.py
  python examples/ollama_explain.py path/to/file.py --focus "error handling"
  python examples/ollama_explain.py path/to/file.py --depth detailed
  python examples/ollama_explain.py path/to/file.py --symbol MyClass
  python examples/ollama_explain.py path/to/file.py --model glm-5.1

Exit codes:
  0 — explanation completed
  1 — input file problem (missing, empty, symbol not found)
  2 — provider or network error
  3 — could not reach Ollama
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import chimera

DEFAULT_MODEL = "kimi-k2.6"
DEFAULT_BASE_URL = "https://ollama.com"
DEFAULT_MAX_TOKENS = 1200
DEFAULT_MAX_FILE_LINES = 2000

EXT_TO_LANG = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".sh": "Shell",
    ".md": "Markdown",
}

DEPTH_INSTRUCTIONS = {
    "overview": (
        "Keep the explanation at a high level. Skip implementation minutiae. "
        "A reader should finish in under a minute and know whether this file "
        "is what they were looking for."
    ),
    "detailed": (
        "Go deeper on the important pieces. Explain notable logic, data flow, "
        "and any non-trivial control structures. Still prose, not a line walk."
    ),
    "line-by-line": (
        "Walk through the code in order, grouping related lines. Use code "
        "line ranges (e.g. 'lines 42-55') when referring to specific sections. "
        "Skip trivial lines (imports, blank lines, obvious assignments)."
    ),
}

SYSTEM_PROMPT_TEMPLATE = """\
You are explaining an unfamiliar {language} file to a working engineer who
needs to orient themselves fast. Produce a structured explanation with these
sections, each separated by a blank line:

WHAT IT IS
One sentence. No preamble.

PURPOSE
Two or three sentences in plain English. Avoid jargon unless the jargon is
the point. Do not restate the code; say what it accomplishes.

KEY PIECES
A bullet list. One line per important definition (class, function, constant,
exported symbol). Skip trivia. Format: `- name — what it does`.

HOW IT FITS
One paragraph. How is this file likely used by other code? What depends on
it? What does it depend on? Any side effects (filesystem, network, global
state)? When you are genuinely guessing (because the surrounding codebase is
not visible), say so — prefix the guess with "I'm guessing".

GOTCHAS
Anything that would surprise a reader: non-obvious behavior, hidden coupling,
unusual patterns, footguns. Omit this section entirely if nothing is notable.
Do not invent gotchas to look thorough.

{depth_instruction}
{focus_instruction}
"""


def _mask(tok: str) -> str:
    if not tok:
        return "(empty)"
    return f"{tok[:4]}...{tok[-4:]}" if len(tok) > 10 else "***"


def detect_language(path: str) -> str:
    """Map a file extension to a human-readable language name."""
    _, ext = os.path.splitext(path.lower())
    return EXT_TO_LANG.get(ext, "plain text / unknown language")


def read_file(path: str) -> str:
    """Read a file as utf-8, replacing un-decodable bytes."""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def truncate_file(text: str, max_lines: int) -> tuple[str, bool]:
    """Truncate to max_lines. Returns (text, truncated_flag)."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False
    head = lines[:max_lines]
    suffix = f"\n\n... [truncated {len(lines) - max_lines} more lines]"
    return "\n".join(head) + suffix, True


def extract_symbol(text: str, symbol: str) -> str | None:
    """Grep-based extraction of a top-level def/class named `symbol`.

    Finds the first line matching `^(def |class )<symbol>` (ignoring leading
    whitespace only if nothing matches at column zero) and returns lines up to
    the next top-level def/class or EOF. Returns None if not found.
    """
    lines = text.splitlines()
    start_prefixes = (f"def {symbol}", f"class {symbol}")
    start_idx: int | None = None

    # First pass: top-level (column 0).
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        for prefix in start_prefixes:
            if stripped.startswith(prefix) and (
                len(stripped) == len(prefix)
                or stripped[len(prefix)] in "(: "
            ):
                start_idx = i
                break
        if start_idx is not None:
            break

    # Fallback: any indent level.
    if start_idx is None:
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            for prefix in start_prefixes:
                if stripped.startswith(prefix) and (
                    len(stripped) == len(prefix)
                    or stripped[len(prefix)] in "(: "
                ):
                    start_idx = i
                    break
            if start_idx is not None:
                break

    if start_idx is None:
        return None

    # Walk forward to next top-level def/class.
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        line = lines[j]
        if line.startswith("def ") or line.startswith("class "):
            end_idx = j
            break

    return "\n".join(lines[start_idx:end_idx])


def preflight(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host_root = f"{parsed.scheme}://{parsed.netloc}"
    try:
        with urllib.request.urlopen(host_root, timeout=5) as resp:
            return resp.status < 500
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def build_system_prompt(language: str, depth: str, focus: str | None) -> str:
    depth_instruction = DEPTH_INSTRUCTIONS.get(depth, DEPTH_INSTRUCTIONS["overview"])
    focus_instruction = ""
    if focus:
        focus_instruction = (
            f"\nFOCUS: bias the explanation toward '{focus}'. Still produce "
            "every section, but let that lens shape what you highlight."
        )
    return SYSTEM_PROMPT_TEMPLATE.format(
        language=language,
        depth_instruction=depth_instruction,
        focus_instruction=focus_instruction,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explain an unfamiliar source file in plain English",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", help="File to explain")
    parser.add_argument(
        "--focus",
        help="Bias the explanation toward a topic (e.g. 'error handling', 'thread safety')",
    )
    parser.add_argument(
        "--depth",
        choices=["overview", "detailed", "line-by-line"],
        default="overview",
        help="How deep to go. Default: overview.",
    )
    parser.add_argument(
        "--symbol",
        help="Explain only this named class or function (simple grep extraction)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=os.environ.get("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("OLLAMA_API_KEY") or "ollama",
    )
    parser.add_argument("--max-file-lines", type=int, default=DEFAULT_MAX_FILE_LINES)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    args = parser.parse_args()

    if not os.path.isfile(args.path):
        print(f"Not a file: {args.path}", file=sys.stderr)
        return 1

    try:
        text = read_file(args.path)
    except OSError as exc:
        print(f"Could not read {args.path}: {exc}", file=sys.stderr)
        return 1

    if not text.strip():
        print(f"{args.path} is empty.", file=sys.stderr)
        return 1

    language = detect_language(args.path)

    if args.symbol:
        extracted = extract_symbol(text, args.symbol)
        if extracted is None:
            print(
                f"Could not find a top-level def/class named '{args.symbol}' in {args.path}.",
                file=sys.stderr,
            )
            print(
                "Hint: the extractor is grep-based. Only `def <name>` or `class <name>` match.",
                file=sys.stderr,
            )
            return 1
        text = extracted
        truncated = False
    else:
        if len(text.splitlines()) > args.max_file_lines:
            print(
                f"Warning: {args.path} is over {args.max_file_lines} lines. "
                f"Using only the first {args.max_file_lines}.",
                file=sys.stderr,
            )
        text, truncated = truncate_file(text, args.max_file_lines)

    os.environ["ANTHROPIC_BASE_URL"] = args.base_url
    os.environ["ANTHROPIC_AUTH_TOKEN"] = args.auth_token
    os.environ.setdefault("ANTHROPIC_API_KEY", "")

    print(f"File:     {args.path}")
    print(f"Language: {language}")
    print(f"Depth:    {args.depth}")
    if args.focus:
        print(f"Focus:    {args.focus}")
    if args.symbol:
        print(f"Symbol:   {args.symbol} (extracted)")
    print(f"Model:    {args.model}")
    print(f"Base URL: {args.base_url}")
    print(f"Auth:     {_mask(args.auth_token)}")
    print(f"Content:  {len(text.splitlines())} lines{' (truncated)' if truncated else ''}")
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

    system_prompt = build_system_prompt(language, args.depth, args.focus)

    header_note = ""
    if args.symbol:
        header_note = (
            f"This is the extracted definition of `{args.symbol}` from "
            f"{args.path}. Explain only this symbol.\n\n"
        )
    elif truncated:
        header_note = (
            f"This is the first {args.max_file_lines} lines of {args.path}; "
            "the tail was truncated. Do your best with what you have and say "
            "where truncation may have hidden something.\n\n"
        )

    user_message = (
        f"{header_note}"
        f"File: {args.path}\n\n"
        "```\n"
        f"{text}\n"
        "```"
    )

    messages = [
        chimera.Message.system(system_prompt),
        chimera.Message.user(user_message),
    ]

    print("=" * 72)
    print("EXPLANATION")
    print("=" * 72)
    print()

    try:
        response = provider.complete(messages, max_tokens=args.max_tokens)
    except Exception as exc:
        print(f"Explain request failed: {exc}", file=sys.stderr)
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
