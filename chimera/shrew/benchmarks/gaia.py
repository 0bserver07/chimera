"""GAIA adapter for ``chimera shrew bench gaia``.

GAIA is a research-task suite (open-domain question answering with
attached files / web evidence). Tasks ship with a single string gold
answer; we score the agent's final reply by extracting an
``Answer: <value>`` line and comparing it to the gold using
GAIA-style normalisation (accent stripping, lowercasing, article
removal, list/numeric awareness).

We deliberately re-implement the scorer locally rather than depending on
an upstream gaia_scorer module so the adapter remains stdlib-only. The
normalisation rules are faithful to GAIA's official scorer; if the
grader is wrong on a specific task, record the raw answer and flag it
for manual review rather than loosening the scorer.

Dataset layout (we do **not** vendor upstream — the dataset is gated):

    ~/.chimera/datasets/gaia/
        tasks.json              # list of task dicts (see schema below)

Override the root via ``CHIMERA_GAIA_PATH=/abs/path``.

Per-task schema (one entry of ``tasks.json``):

    {
      "task_id": "abc-123",                  # required, used as task_id
      "Question": "What is ...",             # required; the prompt body
      "Final answer": "42",                  # gold answer (validation only)
      "Level": 1,                            # informational
      "file_name": "data.xlsx"               # optional attachment ref
    }

Both ``"Question"`` / ``"question"`` and ``"Final answer"`` /
``"final_answer"`` keys are accepted to match the upstream parquet
schema.

Trademark hygiene: ``GAIA`` is a third-party benchmark name, not the
upstream small-model coding agent brand, so naming it directly is fine.
"""
from __future__ import annotations

import os
import re
import string
import unicodedata
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark

__all__ = [
    "GAIA",
    "DEFAULT_DATASET_DIR",
    "ENV_DATASET_PATH",
    "default_dataset_path",
    "dataset_available",
    "setup_hint",
    "extract_final_answer",
    "score_answer",
]


DEFAULT_DATASET_DIR = "~/.chimera/datasets/gaia"
"""Default on-disk location for the staged GAIA dataset."""

ENV_DATASET_PATH = "CHIMERA_GAIA_PATH"
"""Environment variable that overrides :data:`DEFAULT_DATASET_DIR`."""

# Articles dropped during normalisation (matches GAIA scorer convention).
_ARTICLES = frozenset({"a", "an", "the"})

# Numeric pattern (signed int or float, no exponent) — matches the
# upstream GAIA scorer.
_NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+")


def default_dataset_path() -> Path:
    """Return the resolved GAIA dataset root.

    Reads :data:`ENV_DATASET_PATH` when set; otherwise falls back to
    :data:`DEFAULT_DATASET_DIR` expanded against the user's home dir.
    The path may or may not exist — callers should pre-flight with
    :func:`dataset_available` for a friendly skip.

    Returns:
        Absolute :class:`Path` to the dataset root (existence not
        guaranteed).
    """
    raw = os.environ.get(ENV_DATASET_PATH) or DEFAULT_DATASET_DIR
    return Path(raw).expanduser()


def dataset_available(path: Path | None = None) -> bool:
    """Return ``True`` when a usable GAIA dataset is staged.

    A dataset is considered available when ``<root>/tasks.json`` exists
    and is non-empty.

    Args:
        path: Optional dataset root override. When ``None``, the resolved
            :func:`default_dataset_path` is used.

    Returns:
        Whether a ``tasks.json`` file is present at the expected path.
    """
    base = path or default_dataset_path()
    return (base / "tasks.json").is_file()


def setup_hint(path: Path | None = None) -> str:
    """Return a multiline setup-instructions string for the user.

    Used by the CLI when the dataset is missing.

    Args:
        path: Optional dataset root to embed in the hint. When ``None``,
            uses :func:`default_dataset_path`.

    Returns:
        A user-facing string with staging steps and the env-var override.
    """
    resolved = path or default_dataset_path()
    return (
        "GAIA dataset not staged.\n"
        f"  expected dir:  {resolved}\n"
        "  expected file: tasks.json (list of {task_id, Question,"
        " Final answer, ...})\n"
        f"  override:      {ENV_DATASET_PATH}=/abs/path/to/dir\n"
        "  setup:\n"
        "    1. Request access to the gated GAIA repo on the upstream"
        " hub.\n"
        "    2. Export the validation split's metadata.parquet to JSON.\n"
        "    3. Save it as tasks.json under the expected dir.\n"
        "  note: we do NOT vendor upstream — the dataset is gated."
    )


# ---------------------------------------------------------------------------
# Scoring helpers (local re-implementation of GAIA-style normalisation)
# ---------------------------------------------------------------------------


def _strip_accents(s: str) -> str:
    """Remove combining accent marks via NFD decomposition."""
    return "".join(
        c
        for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _normalize_text(s: str) -> str:
    """Lowercase, drop accents, drop punctuation, drop articles."""
    s = _strip_accents(str(s)).lower()
    s = s.translate(str.maketrans("", "", string.punctuation))
    words = [w for w in s.split() if w not in _ARTICLES]
    return " ".join(words).strip()


def _is_numeric(s: str) -> bool:
    """Return whether ``s`` parses as a single signed int / float."""
    return bool(_NUMBER_RE.fullmatch(str(s).strip().replace(",", "")))


def _to_number(s: str) -> float:
    """Parse ``s`` as a float (commas stripped)."""
    return float(str(s).strip().replace(",", ""))


def _numeric_match(predicted: str, gold: str, tol: float = 1e-6) -> bool:
    """Compare ``predicted`` and ``gold`` as floats within ``tol``."""
    try:
        return abs(_to_number(predicted) - _to_number(gold)) <= tol
    except (ValueError, TypeError):
        return False


def _split_list(s: str) -> list[str]:
    """Split a comma- or semicolon-separated list, dropping empties."""
    parts = re.split(r"[;,]\s*", str(s).strip())
    return [p.strip() for p in parts if p.strip()]


def score_answer(predicted: str, gold: str) -> tuple[bool, str]:
    """Score ``predicted`` against ``gold`` using GAIA-style rules.

    Order of attempts:

    1. **Empty guard** — empty / ``None`` predictions or gold fail.
    2. **List match** — when gold contains a ``,`` / ``;``, both sides
       are split, normalised, sorted, and compared as sets-with-order.
    3. **Numeric match** — when gold parses as a single number, predict
       must also parse and match within ``1e-6`` tolerance.
    4. **String match** — fall back to normalised string equality.

    Args:
        predicted: The agent's final answer (already extracted).
        gold: The gold annotation from the dataset.

    Returns:
        A ``(is_correct, reason)`` pair. ``reason`` is a short tag
        useful for debugging false-fails / false-passes.
    """
    if predicted is None:
        return False, "predicted is None"
    if gold is None or gold == "":
        return False, "gold is empty"

    p_raw, g_raw = str(predicted).strip(), str(gold).strip()
    if not p_raw:
        return False, "predicted is empty"

    if "," in g_raw or ";" in g_raw:
        p_list = sorted(_normalize_text(x) for x in _split_list(p_raw))
        g_list = sorted(_normalize_text(x) for x in _split_list(g_raw))
        if p_list == g_list:
            return True, "list-match"
        return False, "list-mismatch"

    if _is_numeric(g_raw):
        if _is_numeric(p_raw) and _numeric_match(p_raw, g_raw):
            return True, "numeric-match"
        return False, "numeric-mismatch"

    if _normalize_text(p_raw) == _normalize_text(g_raw):
        return True, "string-match"
    return False, "string-mismatch"


def extract_final_answer(text: str) -> str:
    """Pull the final-answer span from the agent's raw text.

    The agent is instructed to end its reply with ``Answer: <value>`` on
    its own line. We scan from the bottom of the text for that pattern
    (matching ``final answer:`` / ``Answer -`` variants) and fall back
    to the last non-empty line when no marker is present.

    Args:
        text: The agent's final assistant text.

    Returns:
        The extracted answer (quotes stripped) or the empty string.
    """
    if not text:
        return ""
    for line in reversed(text.splitlines()):
        s = line.strip()
        if not s:
            continue
        m = re.match(r"(?i)^(?:final\s+answer|answer)\s*[:\-]\s*(.+)$", s)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    for line in reversed(text.splitlines()):
        s = line.strip()
        if s:
            return s
    return ""


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class GAIA(Benchmark):
    """GAIA adapter for the standard :class:`Benchmark` ABC.

    Loads research-task entries from a staged JSON dump and grades each
    agent reply by extracting an ``Answer:`` line and comparing it to
    the gold annotation via :func:`score_answer`.

    Args:
        dataset_path: Optional override for the dataset root directory.
            When ``None``, :func:`default_dataset_path` is used.
        limit: Optional cap on the number of tasks returned. ``None`` /
            non-positive means no cap.
        level: Optional difficulty filter (``1`` / ``2`` / ``3``).
            Compares against ``task["Level"]`` after str-coercion so a
            JSON int or string both match.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
        level: int | None = None,
    ) -> None:
        self._dataset_path = dataset_path
        self._limit = limit if (limit is None or limit > 0) else None
        self.level = level
        self._tasks: list[dict[str, Any]] | None = None

    def name(self) -> str:
        """Return the benchmark identifier (suffixed with the level)."""
        if self.level is not None:
            return f"gaia:level{self.level}"
        return "gaia"

    def tasks(self) -> list[dict[str, Any]]:
        """Return the list of tasks (cached after first call).

        Each returned dict carries an ``id`` key (from ``task_id``) and a
        ``prompt`` key (synthesised from ``Question`` + the GAIA
        protocol). The original fields (``Final answer``, ``Level``,
        ``file_name``) are preserved for :meth:`evaluate`.
        """
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Score one task by extracting the agent's final answer.

        Args:
            task: One entry from :meth:`tasks` (must include either
                ``"Final answer"`` or ``"final_answer"``).
            agent_output: The agent's raw final reply.
            env: Unused (GAIA grading is text-only).

        Returns:
            Whether :func:`score_answer` accepts the prediction. When no
            gold answer is present (test split), returns ``False`` so
            test-split runs cannot accidentally count as correct.
        """
        gold = task.get("Final answer") or task.get("final_answer") or ""
        if not gold:
            return False
        predicted = extract_final_answer(agent_output)
        ok, _reason = score_answer(predicted, str(gold))
        return ok

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _root(self) -> Path:
        """Return the resolved dataset root (no existence check)."""
        if self._dataset_path:
            return Path(self._dataset_path).expanduser()
        return default_dataset_path()

    def _load_tasks(self) -> list[dict[str, Any]]:
        """Read ``tasks.json``, apply level filter + limit + prompt build."""
        import json

        root = self._root()
        manifest = root / "tasks.json"
        if not manifest.is_file():
            return []
        try:
            data = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        records = data if isinstance(data, list) else data.get("tasks", [])
        if not isinstance(records, list):
            return []

        out: list[dict[str, Any]] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            if self.level is not None:
                rec_level = rec.get("Level") or rec.get("level")
                if str(rec_level) != str(self.level):
                    continue
            out.append(self._normalise_task(rec))

        if self._limit:
            out = out[: self._limit]
        return out

    @staticmethod
    def _normalise_task(rec: dict[str, Any]) -> dict[str, Any]:
        """Add ``id`` / ``prompt`` shims while preserving raw fields.

        The harness expects every task dict to expose ``"id"`` (used as
        ``task_id`` in the result) and ``"prompt"`` (fed to the agent).
        Upstream GAIA records use ``"task_id"`` and ``"Question"`` so we
        copy and synthesise.
        """
        question = rec.get("Question") or rec.get("question") or ""
        task_id = rec.get("task_id") or rec.get("id") or ""
        out = dict(rec)
        out["id"] = task_id
        out["prompt"] = (
            "You are solving a GAIA research question. Find the answer "
            "using your tools.\n\n"
            f"Question:\n{question}\n\n"
            "End your final reply with a single line:\n"
            "  Answer: <value>\n"
            "Numbers should be plain digits without units unless asked. "
            "Lists are comma-separated."
        )
        return out
