"""Commit message style inference.

Analyzes recent git commits to detect the project's commit message convention
(conventional commits, gitmoji, freeform, ticket-prefixed) and generates
messages matching that style. Pure logic, no LLM needed.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import Enum


class CommitStyle(Enum):
    """Detected commit message styles."""
    CONVENTIONAL = "conventional"  # feat: / fix: / chore:
    GITMOJI = "gitmoji"            # :sparkles: / :bug:
    TICKET = "ticket"              # JIRA-123: / #42:
    FREEFORM = "freeform"          # No pattern detected


@dataclass
class StyleAnalysis:
    """Result of commit style analysis."""
    style: CommitStyle
    prefixes: list[str]  # Most common prefixes seen
    sample_count: int
    confidence: float    # 0.0–1.0


_CONVENTIONAL_RE = re.compile(r"^(feat|fix|chore|docs|style|refactor|test|build|ci|perf|revert)(\(.+?\))?!?:\s")
_GITMOJI_RE = re.compile(r"^:[a-z_]+:\s")
_TICKET_RE = re.compile(r"^[A-Z]+-\d+[:\s]|^#\d+[:\s]")


def get_recent_commits(workdir: str, count: int = 10) -> list[str]:
    """Read the last N commit messages from git log."""
    try:
        result = subprocess.run(
            ["git", "log", f"--max-count={count}", "--format=%s"],
            capture_output=True, text=True, cwd=workdir, timeout=5,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    except Exception:
        return []


def analyze_style(messages: list[str]) -> StyleAnalysis:
    """Detect the commit message style from a list of messages."""
    if not messages:
        return StyleAnalysis(CommitStyle.FREEFORM, [], 0, 0.0)

    conventional = sum(1 for m in messages if _CONVENTIONAL_RE.match(m))
    gitmoji = sum(1 for m in messages if _GITMOJI_RE.match(m))
    ticket = sum(1 for m in messages if _TICKET_RE.match(m))
    total = len(messages)

    # Collect prefixes
    prefixes: list[str] = []
    for m in messages:
        cm = _CONVENTIONAL_RE.match(m)
        if cm:
            prefixes.append(cm.group(1))

    if conventional / total >= 0.5:
        return StyleAnalysis(CommitStyle.CONVENTIONAL, prefixes, total, conventional / total)
    if gitmoji / total >= 0.5:
        return StyleAnalysis(CommitStyle.GITMOJI, [], total, gitmoji / total)
    if ticket / total >= 0.5:
        return StyleAnalysis(CommitStyle.TICKET, [], total, ticket / total)

    return StyleAnalysis(CommitStyle.FREEFORM, [], total, 1.0 - max(conventional, gitmoji, ticket) / total)


def generate_commit_message(
    style: CommitStyle,
    summary: str,
    changed_files: list[str] | None = None,
    commit_type: str = "feat",
) -> str:
    """Generate a commit message matching the detected style.

    Args:
        style: Detected commit style.
        summary: Brief description of the change.
        changed_files: List of changed file paths for the body.
        commit_type: Type prefix for conventional commits.

    Returns:
        Formatted commit message.
    """
    # Clean the summary
    summary = summary.strip().rstrip(".")

    if style == CommitStyle.CONVENTIONAL:
        msg = f"{commit_type}: {summary}"
    elif style == CommitStyle.GITMOJI:
        emoji_map = {
            "feat": ":sparkles:",
            "fix": ":bug:",
            "docs": ":memo:",
            "refactor": ":recycle:",
            "test": ":white_check_mark:",
            "chore": ":wrench:",
            "perf": ":zap:",
        }
        emoji = emoji_map.get(commit_type, ":hammer:")
        msg = f"{emoji} {summary}"
    else:
        # Freeform or ticket — just capitalize first letter
        msg = summary[0].upper() + summary[1:] if summary else summary

    if changed_files:
        files_summary = ", ".join(changed_files[:5])
        if len(changed_files) > 5:
            files_summary += f" (+{len(changed_files) - 5} more)"
        msg += f"\n\nChanged: {files_summary}"

    return msg


def infer_and_generate(
    workdir: str,
    summary: str,
    changed_files: list[str] | None = None,
    commit_type: str = "feat",
) -> str:
    """One-liner: analyze repo style and generate a matching commit message."""
    messages = get_recent_commits(workdir)
    analysis = analyze_style(messages)
    return generate_commit_message(analysis.style, summary, changed_files, commit_type)
