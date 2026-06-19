"""One-shot codegen + compile-repair rebuild strategy for ProgramBench.

ProgramBench inverts SWE-bench: the agent rebuilds a program from scratch given
only a compiled binary and its docs. The default react-agent loop fails here —
it over-explores (``list_files``/``read_file``/``repo_map``) and never commits to
writing (observed live with both reasoning and coder models).

This module takes the opposite approach: ask the model to emit the *whole* source
tree in a single completion — including the required ``compile.sh -> ./executable``
build contract — then grade it, feed the focused compile/test errors back,
merge-repair, and repeat until it grades or the repair budget runs out.

Design:
    * :func:`rebuild` is the pure loop (generate -> parse -> merge-repair). Grading
      is injected via a ``grade_fn`` callback, so the loop is unit-testable with no
      Docker and no LLM.
    * Helpers (:func:`assemble_spec`, :func:`parse_file_blocks`,
      :func:`focus_errors`, the prompt builders) are exposed for reuse and testing.
    * Zero third-party deps — stdlib plus a chimera
      :class:`~chimera.providers.base.Provider`.

The matching grader for real ProgramBench tasks lives in
:mod:`chimera.eval.benchmarks.programbench` (``ProgramBench.rebuild_instance``),
which wires this loop to ``package_submission`` + ``evaluate``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from chimera.eval.benchmarks.rebuild_docs import DocProvider
    from chimera.providers.base import Provider

# A model emits each file as ``>>>> FILE: <path>\n<content>>>>> ENDFILE``.
_FILE_BLOCK = re.compile(r">>>>\s*FILE:\s*(.+?)\r?\n(.*?)>>>>\s*ENDFILE", re.DOTALL)

# Lines worth surfacing to the model when a build fails (compilers print the
# real diagnostics at the *end*, after dependency-download noise).
_ERR_LINE = re.compile(
    r"error|cannot find|expected|undefined|not found|failed|unresolved|"
    r"mismatch|no method|no function|trait|panic|linker",
    re.I,
)

# Image/binary suffixes to skip when assembling the spec from ``_inputs/``.
_BINARY_SUFFIXES = frozenset(
    {".png", ".gif", ".jpg", ".jpeg", ".ico", ".bmp", ".o", ".a", ".so", ".pdf"}
)

# The submission build contract — the single most important instruction. Without
# a ``compile.sh`` that produces ``./executable`` the grader cannot build at all.
_CONTRACT = (
    "CRITICAL submission contract — the grader runs `chmod +x ./compile.sh && "
    "./compile.sh`, then invokes `./executable` with CLI args and compares its "
    "output to the original program:\n"
    "  - Ship `compile.sh` at the workspace ROOT. It must build your source and "
    "place the runnable program at `./executable` (that EXACT name), then "
    "`chmod +x executable`.\n"
    "  - Example compile.sh for C:\n"
    "      #!/bin/bash\n"
    "      set -e\n"
    "      gcc -O2 -o executable src/*.c <libs>   # or: make && cp <bin> executable\n"
    "      chmod +x executable\n"
    "  - For Rust: `cargo build --release && cp target/release/<bin> executable`.\n"
    "  - `./executable` must accept the same CLI flags and reproduce the "
    "documented behavior.\n"
    "  - The build step CAN fetch dependencies (cargo/go resolve crates/modules "
    "normally) — use the same libraries the original uses, pinning versions that "
    "still exist."
)

# Output-format instruction. The model defaults to prose on repair; force files.
_FORMAT = (
    "Output ONLY the files — NO explanation, NO prose, NO commentary before or "
    "after, NO markdown fences. Each file delimited EXACTLY like this:\n"
    ">>>> FILE: <relative/path>\n"
    "<full verbatim file content>\n"
    ">>>> ENDFILE"
)


@dataclass
class GradeOutcome:
    """Result of grading one candidate file set.

    Attributes:
        resolved: ``True`` iff the submission passes (all tests in all branches).
        errors: Focused build/test error text to feed back on the next repair
            round (empty when resolved).
        summary: Free-form grade summary (e.g. ``{"passed": n, "total": m,
            "error_code": ...}``) — used to track the best attempt.
    """

    resolved: bool
    errors: str = ""
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class RebuildAttempt:
    """One generate->grade round, recorded for observability."""

    index: int
    files: list[str]
    resolved: bool
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class RebuildResult:
    """Outcome of a full rebuild run.

    Attributes:
        files: The final candidate file set (path -> content).
        resolved: Whether any attempt fully passed.
        attempts: Per-round history.
        best_summary: The grade summary of the best (most tests passed) attempt.
    """

    files: dict[str, str]
    resolved: bool
    attempts: list[RebuildAttempt]
    best_summary: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers (exposed for reuse + testing)
# ---------------------------------------------------------------------------


def parse_file_blocks(text: str) -> dict[str, str]:
    """Parse ``>>>> FILE: path ... >>>> ENDFILE`` blocks into ``{path: content}``.

    Tolerant of the model wrapping a block's body in a ``` fence and of stray
    backticks around the path. Prose outside the blocks is ignored.
    """
    files: dict[str, str] = {}
    for match in _FILE_BLOCK.finditer(text):
        path = match.group(1).strip().strip("`").strip()
        content = match.group(2)
        if content.startswith("```"):
            content = re.sub(r"^```[^\n]*\n", "", content)
            content = re.sub(r"\n```\s*$", "\n", content)
        if path:
            files[path] = content
    return files


def focus_errors(text: str, limit: int = 3000) -> str:
    """Condense a build log to the lines that matter.

    Compilers print real diagnostics at the *end*, after dependency-download
    noise (e.g. cargo's ``Downloaded ...`` spam). Naively truncating from the
    head feeds the model the noise and hides the error. This keeps the
    error-looking lines plus the tail.
    """
    lines = text.splitlines()
    errly = [ln for ln in lines if _ERR_LINE.search(ln)]
    body = ("KEY LINES:\n" + "\n".join(errly[-50:]) + "\n\n") if errly else ""
    return (body + "TAIL:\n" + "\n".join(lines[-50:]))[-limit:]


def assemble_spec(inputs_dir: str | Path, *, max_file_chars: int = 16000) -> str:
    """Build the spec text from a task's ``_inputs/`` directory.

    Reads every text doc (README, man pages, usage, examples), skipping the
    ``.git`` tree, the ``executable`` oracle binary, and image/binary files.

    Args:
        inputs_dir: The extracted ``_inputs/`` directory.
        max_file_chars: Per-file cap so one large doc can't dominate the prompt.

    Returns:
        A single string with each file under a ``=== <relpath> ===`` header.
    """
    root = Path(inputs_dir)
    parts: list[str] = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or ".git" in f.parts or f.name == "executable":
            continue
        if f.suffix.lower() in _BINARY_SUFFIXES:
            continue
        try:
            txt = f.read_text(errors="replace")
        except OSError:
            continue
        parts.append(f"=== {f.relative_to(root)} ===\n{txt[:max_file_chars]}")
    return "\n\n".join(parts)


def build_initial_prompt(project: str, language: str, spec: str) -> str:
    """Render the first-shot rebuild prompt (with the build contract baked in)."""
    return (
        f"Rebuild the program **{project}** (language: {language}) from scratch "
        f"using its documentation as the spec.\n\nSPEC:\n{spec}\n\n{_CONTRACT}\n\n"
        f"{_FORMAT}\n\nInclude compile.sh + every build/source file needed. "
        f"Begin now."
    )


def build_repair_prompt(
    project: str, language: str, files: dict[str, str], errors: str
) -> str:
    """Render a repair prompt: current files + focused errors + fix instruction."""
    dump = "\n\n".join(
        f">>>> FILE: {p}\n{c}\n>>>> ENDFILE" for p, c in files.items()
    )
    return (
        f"Your previous rebuild of **{project}** ({language}) FAILED when the "
        f"grader built/ran it.\n\n{_CONTRACT}\n\nCURRENT FILES:\n{dump}\n\n"
        f"GRADER ERROR OUTPUT:\n{errors}\n\nDiagnose and FIX it (common causes: "
        f"compile.sh missing or not producing ./executable; wrong build command; "
        f"missing source; link errors; dependency/version conflicts; real compile "
        f"errors in the source). Re-output the FULL content of every file you "
        f"change (always include the file(s) named in the error); files you omit "
        f"are kept unchanged.\n\n{_FORMAT}\n\nBegin now."
    )


def _is_better(summary: dict[str, Any], best: dict[str, Any]) -> bool:
    """Return True if *summary* passed more tests than the current best."""
    if not best:
        return True
    return int(summary.get("passed", 0) or 0) > int(best.get("passed", 0) or 0)


def _augment_with_docs(
    doc_provider: DocProvider, errors: str, language: str, files: dict[str, str]
) -> str:
    """Prepend fetched library docs to *errors* when the build named unknown
    symbols. No-op (returns *errors*) if nothing parses or nothing is fetched."""
    from chimera.eval.benchmarks.rebuild_docs import (
        crates_from_cargo_toml,
        parse_missing_symbols,
    )

    symbols = parse_missing_symbols(errors, language)
    if not symbols:
        return errors
    cargo = files.get("Cargo.toml") or files.get("cargo.toml") or ""
    crates = crates_from_cargo_toml(cargo) if cargo else []
    docs = doc_provider.fetch(symbols, crates)
    if not docs:
        return errors
    return (
        "RELEVANT LIBRARY DOCS (use these EXACT APIs — your previous code "
        "referenced symbols that do not exist):\n" + docs + "\n\n" + errors
    )


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def rebuild(
    provider: Provider,
    *,
    project: str,
    language: str,
    spec: str,
    grade_fn: Callable[[dict[str, str]], GradeOutcome],
    max_repair: int = 4,
    max_tokens: int = 8192,
    doc_provider: DocProvider | None = None,
    on_attempt: Callable[[RebuildAttempt], None] | None = None,
) -> RebuildResult:
    """Generate a source tree and repair it against grader feedback.

    Args:
        provider: Chimera provider used for codegen (a coder model such as
            ``qwen3-coder-next`` works well; reasoning models need the generous
            ``max_tokens`` below to avoid empty turns).
        project: Human project name (used in the prompt).
        language: Task language (``"c"``, ``"rust"``, ...).
        spec: Documentation text — see :func:`assemble_spec`.
        grade_fn: Callback that writes + grades a candidate file set and returns
            a :class:`GradeOutcome`. Must not raise (wrap failures into the
            outcome's ``errors``). This is the only side-effecting dependency,
            which keeps the loop unit-testable.
        max_repair: Number of repair rounds after the first attempt (so up to
            ``max_repair + 1`` generations / grades).
        max_tokens: Per-completion output budget. 8192+ keeps reasoning models
            from truncating mid-thought.
        doc_provider: Optional RAG hook — on a build failure with unknown-symbol
            errors, its fetched library docs are prepended to the feedback (see
            :mod:`chimera.eval.benchmarks.rebuild_docs`).
        on_attempt: Optional callback invoked with each :class:`RebuildAttempt`
            for progress reporting.

    Returns:
        A :class:`RebuildResult` with the final files, whether it resolved, the
        per-round history, and the best grade summary seen.
    """
    from chimera.types import Message

    files: dict[str, str] = {}
    errors = ""
    attempts: list[RebuildAttempt] = []
    best: dict[str, Any] = {}

    for index in range(max_repair + 1):
        prompt = (
            build_initial_prompt(project, language, spec)
            if not files
            else build_repair_prompt(project, language, files, errors)
        )
        raw = provider.complete([Message.user(prompt)], max_tokens=max_tokens).content
        new_files = parse_file_blocks(raw)

        if not new_files:
            # Model replied with prose instead of file blocks — nudge and retry,
            # keeping any prior files so we never regress to an empty tree.
            attempt = RebuildAttempt(index, sorted(files), False, {"error": "no_file_blocks"})
            attempts.append(attempt)
            if on_attempt is not None:
                on_attempt(attempt)
            errors = (
                "Your last reply contained NO files in the required format — only "
                "prose. Re-output the fix as >>>> FILE: ... >>>> ENDFILE blocks "
                "ONLY, no prose.\n\n" + errors
            )
            continue

        files = new_files if not files else {**files, **new_files}
        outcome = grade_fn(files)
        attempt = RebuildAttempt(index, sorted(files), outcome.resolved, outcome.summary)
        attempts.append(attempt)
        if on_attempt is not None:
            on_attempt(attempt)
        if _is_better(outcome.summary, best):
            best = outcome.summary
        if outcome.resolved:
            return RebuildResult(files, True, attempts, best)
        errors = outcome.errors
        if doc_provider is not None and errors:
            errors = _augment_with_docs(doc_provider, errors, language, files)

    return RebuildResult(files, False, attempts, best)


__all__ = [
    "GradeOutcome",
    "RebuildAttempt",
    "RebuildResult",
    "assemble_spec",
    "build_initial_prompt",
    "build_repair_prompt",
    "focus_errors",
    "parse_file_blocks",
    "rebuild",
]
