"""``chimera ferret review <target>`` — non-interactive code review.

Threads the existing :class:`chimera.review.orchestrator.ReviewOrchestrator`
into ferret's surface so a user can run::

    chimera ferret review src/foo.py
    chimera ferret review HEAD~1..HEAD          # git revspec
    chimera ferret review path/to/changed/dir/

and receive a structured review summary on stdout. The orchestrator
already implements the multi-perspective (logic / security / tests /
architecture) loop, so this module's responsibility is purely the
ferret-flavored adapter:

1. Resolve ``target`` into a unified diff (file path → ``git diff``,
   git rev-spec → ``git diff <spec>``, plain path → file contents
   wrapped as a "new file" pseudo-diff).
2. Build a reviewer :class:`chimera.core.agent.Agent` via the ferret
   provider chain.
3. Run :meth:`ReviewOrchestrator.run` and surface the feedback.

The author Agent (the one ReviewOrchestrator would normally tell to
"fix these comments") is intentionally a no-op shim: ``ferret review``
is a *report*, not a guided fix loop. Callers who want the fix loop
should jump to ``chimera ferret -p`` with the review text fed in.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "resolve_target_to_diff",
    "run_review",
]


# ---------------------------------------------------------------------------
# Target -> unified-diff resolution
# ---------------------------------------------------------------------------


def _is_git_revspec(target: str) -> bool:
    """Heuristic: does *target* look like a git rev-spec rather than a path?

    The check is conservative — a real ``git rev-parse`` round-trip would
    be more precise but requires that we already ``cd`` into a repo.
    The heuristics below cover the spec's documented forms (rev ranges,
    bare commit ids, branch names with ``..`` or ``...``) without ever
    misclassifying a real on-disk path that the user typed.
    """
    if not target:
        return False
    if os.path.exists(target):
        return False  # real path — never a rev-spec
    return ".." in target or "..." in target or target.startswith("@")


def _read_file_as_pseudo_diff(path: Path) -> str:
    """Render *path* as a "new file" unified diff for review."""
    try:
        body = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(
            f"ferret review: cannot read {path}: {exc}"
        ) from exc
    rel = str(path)
    header = (
        f"diff --git a/{rel} b/{rel}\n"
        f"--- a/{rel}\n+++ b/{rel}\n"
    )
    body_lines = body.splitlines()
    hunk = f"@@ -0,0 +1,{len(body_lines)} @@\n"
    diff_body = "\n".join(f"+{line}" for line in body_lines)
    return header + hunk + diff_body + ("\n" if body_lines else "")


def _git_diff(
    target: str,
    *,
    cwd: str,
    runner: Any = None,
) -> str:
    """Run ``git diff <target>`` and return the captured stdout.

    A non-zero return from git is bubbled up as a :class:`RuntimeError`
    so the caller can surface a friendly stderr message.
    """
    runner = runner or subprocess.run
    completed = runner(
        ["git", "diff", target],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    rc = int(getattr(completed, "returncode", 1))
    stdout = str(getattr(completed, "stdout", "") or "")
    stderr = str(getattr(completed, "stderr", "") or "")
    if rc != 0:
        raise RuntimeError(
            f"git diff {target!r} failed (rc={rc}): {stderr.strip()}"
        )
    return stdout


def resolve_target_to_diff(
    target: str,
    *,
    cwd: str | None = None,
    runner: Any = None,
) -> str:
    """Resolve *target* into a unified-diff string.

    Resolution order:

    1. ``target`` looks like a git rev-spec (``..`` / ``...`` / ``@``):
       run ``git diff <target>``.
    2. ``target`` is an existing path *and* is inside a git work tree:
       run ``git diff -- <target>`` so reviewers see the actual change.
       When ``git diff`` produces no output (no pending change), fall
       through to step 3 so the file is reviewed as-is.
    3. ``target`` is an existing file: render its body as a new-file
       pseudo-diff (so the orchestrator's perspectives still see file
       contents in the standard ``diff --git`` shape).

    Args:
        target: The ``<target>`` positional from the ferret CLI.
        cwd: Optional working dir for the git invocation.
        runner: ``subprocess.run``-compatible callable for tests.

    Returns:
        A unified-diff string. Never empty — empty diffs raise
        :class:`ValueError` so the caller can short-circuit.

    Raises:
        FileNotFoundError: When *target* is a path that doesn't exist
            and isn't a recognisable rev-spec.
        ValueError: When *target* resolves to an empty diff.
        RuntimeError: When ``git diff`` itself fails.
    """
    workdir = cwd or os.getcwd()
    if _is_git_revspec(target):
        diff = _git_diff(target, cwd=workdir, runner=runner)
        if not diff.strip():
            raise ValueError(
                f"ferret review: rev-spec {target!r} resolved to an empty diff"
            )
        return diff

    path = Path(target)
    if not path.exists():
        raise FileNotFoundError(
            f"ferret review: target not found: {target!r}"
        )

    # Existing path — try ``git diff`` first so reviewers see pending
    # changes rather than the entire file.
    try:
        diff = _git_diff(f"-- {target}", cwd=workdir, runner=runner)
    except RuntimeError:
        diff = ""
    if diff.strip():
        return diff

    # Fall through: pseudo-diff of the file contents.
    if path.is_dir():
        # Build a pseudo-diff by concatenating each file's body. We
        # cap at 64 files to keep the prompt size sane; reviewers can
        # narrow the target if they need full coverage.
        chunks: list[str] = []
        for child in sorted(path.rglob("*"))[:64]:
            if child.is_file():
                try:
                    chunks.append(_read_file_as_pseudo_diff(child))
                except FileNotFoundError:
                    continue
        if not chunks:
            raise ValueError(
                f"ferret review: directory {target!r} has no readable files"
            )
        return "\n".join(chunks)

    return _read_file_as_pseudo_diff(path)


# ---------------------------------------------------------------------------
# Author shim — non-interactive review never actually edits files
# ---------------------------------------------------------------------------


class _NoOpAuthor:
    """Author placeholder for :class:`ReviewOrchestrator`.

    The orchestrator's ``run`` invokes ``author.run(prompt, env)`` when
    a perspective requests fixes. Non-interactive review must not edit
    files, so this shim simply records the prompt and returns a
    sentinel result that satisfies the orchestrator's downstream
    ``mark_fixed`` call.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, prompt: str, env: Any | None = None) -> Any:
        self.calls.append(prompt)
        _ = env  # WHY: protocol parity — never inspected, see class doc.

        class _Result:
            output = ""
            success = True

        return _Result()


# ---------------------------------------------------------------------------
# Provider + Agent construction
# ---------------------------------------------------------------------------


def _build_reviewer_agent(args: argparse.Namespace) -> Any:
    """Build a reviewer :class:`Agent` using the ferret provider chain."""
    from chimera.core.agent import Agent
    from chimera.core.prompt import Prompt

    # Late-bind ferret's provider helper so the same fallthrough logic the
    # ``-p`` path uses applies here.
    try:
        from chimera.ferret import providers as _ferret_providers
    except Exception:  # noqa: BLE001
        _ferret_providers = None  # type: ignore[assignment]

    if _ferret_providers is not None and hasattr(
        _ferret_providers, "build_provider"
    ):
        provider = _ferret_providers.build_provider(args)
    else:
        from chimera.providers.factory import create_provider

        provider = create_provider(model=getattr(args, "model", None))

    prompt = Prompt.from_string(
        "You are Ferret's reviewer agent. Read the diff and emit feedback "
        "scoped to logic, security, tests, and architecture concerns."
    )
    return Agent(provider=provider, tools=[], loop=None, prompt=prompt)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_review(args: argparse.Namespace) -> int:
    """Run a non-interactive review against ``args.sub_action``.

    Reads ``args.sub_action`` as the target (the cli puts the first
    positional after the subcommand into the ``sub_action`` slot).
    ``--max-rounds`` falls through to the orchestrator default.

    Returns:
        Process exit code: 0 when the review approved, 1 when at least
        one perspective flagged a comment, 2 on usage / resolve error.
    """
    target = getattr(args, "sub_action", None) or getattr(args, "sub_target", None)
    if not target:
        sys.stderr.write(
            "ferret review: missing TARGET. "
            "Usage: chimera ferret review <path|revspec>\n"
        )
        return 2

    cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())
    try:
        diff = resolve_target_to_diff(str(target), cwd=cwd)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        sys.stderr.write(f"ferret review: {exc}\n")
        return 2

    # Late-bind ReviewOrchestrator so a missing chimera.review module
    # doesn't crash this dispatcher at import time.
    try:
        from chimera.review.orchestrator import ReviewOrchestrator
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"ferret review: ReviewOrchestrator unavailable ({exc})\n"
        )
        return 2

    try:
        reviewer = _build_reviewer_agent(args)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"ferret review: provider build failed ({exc})\n")
        return 2

    author: Any = _NoOpAuthor()
    orchestrator = ReviewOrchestrator(max_rounds=1)
    try:
        approved = orchestrator.run(diff, reviewer=reviewer, author=author)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"ferret review: orchestrator run failed ({exc})\n")
        return 1

    sys.stdout.write(
        f"[ferret review] target={target!r} approved={approved} "
        f"comments={orchestrator.total_comments}\n"
    )
    for round_obj in orchestrator.rounds:
        for comment in round_obj.feedback.comments:
            summary = getattr(comment, "summary", "")
            severity = getattr(comment, "severity", "info")
            sys.stdout.write(f"  - [{severity}] {summary}\n")
    return 0 if approved else 1
