#!/usr/bin/env python3
"""Real-world demo: AI-generated Conventional Commits message from your staged diff.

Reads your `git diff --staged` (or falls back to `git diff` if nothing is
staged) and sends it to an Ollama cloud model via Chimera's Anthropic-compatible
provider. Prints a clean Conventional Commits message ready to pipe into
`git commit -F -`.

This is a useful everyday tool: stage the changes you want to ship, run this,
and you get a well-structured commit message that explains the WHY.

Setup (first time):

  ollama signin                          # or set OLLAMA_API_KEY
  export ANTHROPIC_BASE_URL=https://ollama.com          # cloud
  export ANTHROPIC_AUTH_TOKEN=$OLLAMA_API_KEY

Usage:

  python examples/ollama_commit_message.py                     # staged diff
  python examples/ollama_commit_message.py --include-files     # add file list
  python examples/ollama_commit_message.py --type fix          # force type
  python examples/ollama_commit_message.py --scope providers   # force scope
  python examples/ollama_commit_message.py --breaking          # mark breaking
  python examples/ollama_commit_message.py --copy              # copy to clipboard
  python examples/ollama_commit_message.py | git commit -F -   # pipe directly

Exit codes:
  0 — message generated
  1 — no diff to summarize
  2 — provider or network error
  3 — could not reach Ollama
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import chimera

DEFAULT_MODEL = "kimi-k2.6"
DEFAULT_BASE_URL = "https://ollama.com"
DEFAULT_MAX_DIFF_LINES = 2000
DEFAULT_MAX_TOKENS = 400

VALID_TYPES = (
    "feat",
    "fix",
    "chore",
    "docs",
    "refactor",
    "test",
    "ci",
    "build",
    "perf",
    "style",
)

SYSTEM_PROMPT = """\
You write Conventional Commits messages. You will be given a git diff and
optionally a list of changed files. Produce ONE commit message with this exact
structure:

<type>(<scope>): <short imperative summary>
<BLANK LINE>
- <why bullet 1>
- <why bullet 2>
- <why bullet 3>
<optional BLANK LINE and footer>

Rules:
- First line: type(scope): summary. Summary is imperative ("add", not "added"),
  lowercase after the colon, no trailing period, and the WHOLE first line is
  <= 72 characters.
- Valid types: feat, fix, chore, docs, refactor, test, ci, build, perf, style.
- Scope is a short noun (e.g. providers, env, cli). Omit parentheses if no
  meaningful scope: `type: summary`.
- Breaking changes: append `!` to the type (e.g. `feat!:`) AND include a
  `BREAKING CHANGE: <description>` footer.
- Body: 2 to 4 short bullets explaining WHY the change was made, not a
  line-by-line recap of the diff. Each bullet on one line, no trailing period.
- Footer (optional): `Closes: #123`, `Refs: #456`, or `BREAKING CHANGE: ...`.
- No markdown. No backticks unless referencing a real symbol. No emoji.
- Do NOT wrap the message in code fences. Output raw text only.

Only output the commit message. No preamble, no explanation, no trailing notes.
"""


def _mask(tok: str) -> str:
    if not tok:
        return "(empty)"
    return f"{tok[:4]}...{tok[-4:]}" if len(tok) > 10 else "***"


def _run_git(args: list[str]) -> tuple[int, str, str]:
    """Run a git command. Returns (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    )
    return result.returncode, result.stdout, result.stderr


def get_diff() -> tuple[str, bool]:
    """Return (diff_text, used_fallback). Prefer staged; fall back to unstaged."""
    rc, staged, err = _run_git(["diff", "--staged"])
    if rc != 0:
        raise RuntimeError(f"git diff --staged failed: {err.strip()}")
    if staged.strip():
        return staged, False
    rc, unstaged, err = _run_git(["diff"])
    if rc != 0:
        raise RuntimeError(f"git diff failed: {err.strip()}")
    return unstaged, True


def get_file_list(staged: bool) -> str:
    """Return `git diff --name-status` output for staged or unstaged changes."""
    args = ["diff", "--name-status"]
    if staged:
        args.insert(1, "--staged")
    rc, out, _ = _run_git(args)
    if rc != 0:
        return ""
    return out.strip()


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


def copy_to_clipboard(text: str) -> bool:
    """Try pbcopy (macOS) / wl-copy (Wayland) / xclip (X11). Silent on failure."""
    candidates: list[list[str]] = []
    if shutil.which("pbcopy"):
        candidates.append(["pbcopy"])
    if shutil.which("wl-copy"):
        candidates.append(["wl-copy"])
    if shutil.which("xclip"):
        candidates.append(["xclip", "-selection", "clipboard"])
    for cmd in candidates:
        try:
            proc = subprocess.run(cmd, input=text, text=True, check=False, capture_output=True)
            if proc.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def build_user_message(
    diff: str,
    file_list: str | None,
    forced_type: str | None,
    forced_scope: str | None,
    breaking: bool,
) -> str:
    """Assemble the user turn with explicit constraints the model must honor."""
    parts: list[str] = []
    constraints: list[str] = []
    if forced_type:
        marker = "!" if breaking else ""
        constraints.append(f"- Use type: `{forced_type}{marker}` (do not change it).")
    elif breaking:
        constraints.append("- This is a BREAKING change: append `!` to the type and add a `BREAKING CHANGE:` footer.")
    if forced_scope:
        constraints.append(f"- Use scope: `{forced_scope}` (do not change it).")
    if constraints:
        parts.append("Constraints:\n" + "\n".join(constraints))
    if file_list:
        parts.append("Changed files (git diff --name-status):\n" + file_list)
    parts.append("Diff:\n```diff\n" + diff + "\n```")
    parts.append("Write the Conventional Commits message now. Output only the message.")
    return "\n\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a Conventional Commits message from your staged diff",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--include-files",
        action="store_true",
        help="Include `git diff --name-status` output in the prompt",
    )
    parser.add_argument(
        "--type",
        dest="commit_type",
        choices=VALID_TYPES,
        help="Force a commit type (default: model chooses)",
    )
    parser.add_argument("--scope", help="Force a scope (default: model chooses)")
    parser.add_argument(
        "--breaking",
        action="store_true",
        help="Mark the commit as breaking (appends `!` to the type)",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy the generated message to the clipboard",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print diagnostic info (model, base url, token count) to stderr",
    )

    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=os.environ.get("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("OLLAMA_API_KEY") or "ollama",
    )
    parser.add_argument("--max-diff-lines", type=int, default=DEFAULT_MAX_DIFF_LINES)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    args = parser.parse_args()

    # Gather the diff first; if there's nothing to summarize, don't burn tokens.
    try:
        diff, used_fallback = get_diff()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not diff.strip():
        print("No changes to commit.", file=sys.stderr)
        print("Hint: edit files and `git add` them, then rerun.", file=sys.stderr)
        return 1

    if used_fallback:
        print(
            "Warning: nothing staged — falling back to unstaged changes. "
            "Did you forget `git add`?",
            file=sys.stderr,
        )

    file_list = get_file_list(staged=not used_fallback) if args.include_files else None
    diff, truncated = truncate_diff(diff, args.max_diff_lines)

    os.environ["ANTHROPIC_BASE_URL"] = args.base_url
    os.environ["ANTHROPIC_AUTH_TOKEN"] = args.auth_token
    os.environ.setdefault("ANTHROPIC_API_KEY", "")

    if args.verbose:
        print(f"Model:    {args.model}", file=sys.stderr)
        print(f"Base URL: {args.base_url}", file=sys.stderr)
        print(f"Auth:     {_mask(args.auth_token)}", file=sys.stderr)
        src = "unstaged (fallback)" if used_fallback else "staged"
        print(
            f"Diff:     {len(diff.splitlines())} lines from {src}"
            f"{' (truncated)' if truncated else ''}",
            file=sys.stderr,
        )
        if file_list:
            print(f"Files:    {len(file_list.splitlines())} entries", file=sys.stderr)
        print("", file=sys.stderr)

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

    user_message = build_user_message(
        diff=diff,
        file_list=file_list,
        forced_type=args.commit_type,
        forced_scope=args.scope,
        breaking=args.breaking,
    )

    messages = [
        chimera.Message.system(SYSTEM_PROMPT),
        chimera.Message.user(user_message),
    ]

    try:
        response = provider.complete(messages, max_tokens=args.max_tokens)
    except Exception as exc:
        print(f"Commit message request failed: {exc}", file=sys.stderr)
        return 2

    message = (response.content or "").strip()
    # Defensively strip stray code fences if the model ignored the instruction.
    if message.startswith("```"):
        lines = message.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        message = "\n".join(lines).strip()

    if not message:
        print("Model returned an empty message.", file=sys.stderr)
        return 2

    print(message)

    if args.copy:
        if copy_to_clipboard(message):
            if args.verbose:
                print("\nCopied to clipboard.", file=sys.stderr)
        elif args.verbose:
            print(
                "\nClipboard tool not found (tried pbcopy, wl-copy, xclip).",
                file=sys.stderr,
            )

    if args.verbose:
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
        print(f"\nTokens:   {detail}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
