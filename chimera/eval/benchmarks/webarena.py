"""WebArena adapter — web-based agent benchmark.

WebArena evaluates autonomous agents on realistic web tasks across four
self-hosted sites (e-commerce, GitLab, CMS, Reddit). Each task carries a
natural-language ``intent``, a starting URL, and a list of ``eval_types``
declaring how success is judged: exact / fuzzy ``string_match`` against a
``reference_answer``, ``url_match`` against an expected URL, and
``program_html`` (programmatic DOM checks) for stateful site mutations.

This adapter loads task definitions from a local dataset directory and
exposes them through the standard :class:`~chimera.eval.harness.Benchmark`
interface so they can be driven by :class:`~chimera.eval.harness.Harness`.

Note:
    Full WebArena execution requires the upstream sandbox sites
    (Docker images for OneStopShop, GitLab, Magento, Reddit, Wikipedia)
    plus the upstream ``webarena`` package for its DOM/accessibility
    observation pipeline. We do **not** vendor or pip-install upstream
    — the licence on the task corpus is unclear, and the sandbox sites
    are heavyweight. The adapter loads task definitions from a local
    JSON / JSONL dump and scores in-process via ``string_match`` and
    ``url_match``. ``program_html`` is recognised but deferred (returns
    ``False`` until DOM evaluation is wired).

Default dataset location: ``~/.chimera/datasets/webarena/``. Override
via the ``CHIMERA_WEBARENA_PATH`` environment variable. Files matching
``*.json`` / ``*.jsonl`` under that directory are picked up.

Reference:
    - Paper: https://arxiv.org/abs/2307.13854
    - Source: https://github.com/web-arena-x/webarena
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from chimera.eval.harness import Benchmark
from chimera.config.paths import STATE_DIRNAME, store_path

DEFAULT_DATASET_DIR = f"~/{STATE_DIRNAME}/datasets/webarena"
ENV_DATASET_PATH = "CHIMERA_WEBARENA_PATH"

#: Eval-type tags WebArena uses on each task.
EVAL_TYPE_STRING_MATCH = "string_match"
EVAL_TYPE_URL_MATCH = "url_match"
EVAL_TYPE_PROGRAM_HTML = "program_html"

SUPPORTED_EVAL_TYPES = (EVAL_TYPE_STRING_MATCH, EVAL_TYPE_URL_MATCH)


def default_dataset_path() -> Path:
    """Return the resolved dataset directory.

    Reads the ``CHIMERA_WEBARENA_PATH`` environment variable when set;
    otherwise falls back to ``~/.chimera/datasets/webarena/``. The path
    may or may not exist; callers are expected to check.
    """
    raw = os.environ.get(ENV_DATASET_PATH)
    if raw:
        return Path(raw).expanduser()
    return store_path("datasets") / "webarena"


def dataset_available(path: Path | None = None) -> bool:
    """Return True when at least one task file lives under *path*.

    When *path* is ``None``, the resolved default dataset directory is
    used.
    """
    base = path or default_dataset_path()
    if not base.exists():
        return False
    if base.is_file():
        return True
    if not base.is_dir():
        return False
    return any(base.glob("*.json")) or any(base.glob("*.jsonl"))


class WebArena(Benchmark):
    """WebArena adapter for web-agent task evaluation.

    Loads WebArena tasks from a local dataset directory or single
    JSON / JSONL file and exposes them through the standard
    :class:`Benchmark` interface. Each task is a single-page or
    multi-page web interaction; success is determined by the task's
    annotated ``eval_types`` list.

    Args:
        dataset_path: Optional path to a JSON / JSONL dump of tasks
            **or** to a directory of task files. When ``None``, the
            adapter resolves :func:`default_dataset_path`.
        limit: Optional cap on the number of tasks returned.
        sites: Optional iterable of site names (e.g. ``("shopping",)``)
            to filter tasks. When ``None``, all sites are loaded.

    Attributes:
        sites: Tuple of site filters, or ``None`` for no filtering.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
        sites: tuple[str, ...] | None = None,
    ) -> None:
        self._dataset_path = dataset_path
        self._limit = limit
        self.sites = tuple(sites) if sites else None
        self._tasks: list[dict[str, Any]] | None = None

    def name(self) -> str:
        return "webarena"

    def tasks(self) -> list[dict[str, Any]]:
        """Return the list of tasks for the configured filter.

        Tasks are loaded lazily on first call and cached. Each task dict
        contains at minimum ``id``, ``prompt`` (composed from intent +
        start_url), ``intent``, ``start_url``, ``eval_types``, and
        ``reference_answer``. When the dataset is not available, returns
        an empty list — callers should pre-flight with
        :func:`dataset_available` for a friendly skip path.
        """
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Score *agent_output* against the task's ``eval_types`` list.

        Implemented eval types:

        * ``string_match`` — checks ``reference_answer`` (and optional
          ``reference_answers`` map of ``must_include`` /
          ``fuzzy_match`` / ``exact_match``) against *agent_output*.
        * ``url_match`` — compares the agent's reported final URL
          against ``reference_url`` (parsed scheme + netloc + path).

        ``program_html`` is recognised but deferred — when it is the
        only declared eval type, we return ``False``.

        When the upstream WebArena environment is supplied via *env*
        and exposes ``evaluate_task(task, output)``, the adapter
        delegates to it (mirroring the tau-bench escape hatch).

        Args:
            task: Task dict from :meth:`tasks`.
            agent_output: The agent's final output string.
            env: WebArena environment, or ``None``.

        Returns:
            ``True`` when **all** declared, supported eval types pass.
        """
        if env is not None and hasattr(env, "evaluate_task"):
            try:
                return bool(env.evaluate_task(task, agent_output))
            except Exception:
                return False

        if not isinstance(agent_output, str):
            return False

        eval_types = task.get("eval_types") or []
        if not isinstance(eval_types, list) or not eval_types:
            return False

        # Parse agent output once: the agent may emit either plain text
        # or a JSON envelope ``{"answer": "...", "url": "..."}``. We
        # accept both.
        answer_text, final_url = _split_agent_output(agent_output)

        # We only honour eval types we explicitly support. Unknown / deferred
        # types (e.g. program_html) are treated as failures so a task
        # gated on them never falsely passes.
        applicable: list[str] = []
        for et in eval_types:
            if et in SUPPORTED_EVAL_TYPES:
                applicable.append(et)
            else:
                # Unsupported eval type — fail closed.
                return False

        if not applicable:
            return False

        for et in applicable:
            if et == EVAL_TYPE_STRING_MATCH:
                if not _string_match(task, answer_text):
                    return False
            elif et == EVAL_TYPE_URL_MATCH:
                if not _url_match(task, final_url):
                    return False
        return True

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_tasks(self) -> list[dict[str, Any]]:
        """Load tasks from the resolved dataset path."""
        path = (
            Path(self._dataset_path).expanduser()
            if self._dataset_path
            else default_dataset_path()
        )
        if not path.exists():
            return []

        raw: list[dict[str, Any]] = []
        if path.is_file():
            raw.extend(self._load_file(path))
        elif path.is_dir():
            for fp in sorted(path.iterdir()):
                if fp.suffix in (".json", ".jsonl") and fp.is_file():
                    raw.extend(self._load_file(fp))

        normalised: list[dict[str, Any]] = []
        for i, t in enumerate(raw):
            task = self._normalise_task(t, i)
            if self.sites and task.get("site") not in self.sites:
                # Also try task["sites"] (upstream sometimes uses a list)
                site_list = task.get("sites")
                if isinstance(site_list, list):
                    if not any(s in self.sites for s in site_list):
                        continue
                else:
                    continue
            normalised.append(task)

        if self._limit:
            normalised = normalised[: self._limit]
        return normalised

    @staticmethod
    def _load_file(path: Path) -> list[dict[str, Any]]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        if path.suffix == ".jsonl":
            out: list[dict[str, Any]] = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
            return out
        try:
            data = json.loads(text)
        except ValueError:
            return []
        if isinstance(data, list):
            return [t for t in data if isinstance(t, dict)]
        if isinstance(data, dict):
            wrapped = data.get("tasks", [])
            if isinstance(wrapped, list):
                return [t for t in wrapped if isinstance(t, dict)]
        return []

    def _normalise_task(self, raw: dict[str, Any], index: int) -> dict[str, Any]:
        """Coerce a raw task dict into the harness contract."""
        task = dict(raw)
        # WebArena tasks ship with a numeric ``task_id`` — preserve as id.
        if "id" not in task:
            task_id = task.get("task_id")
            task["id"] = (
                f"webarena-{task_id}" if task_id is not None else f"webarena-{index}"
            )

        # Build a prompt the agent can act on. Upstream tasks store the
        # natural-language goal in ``intent``.
        if "prompt" not in task:
            task["prompt"] = _format_prompt(task)

        # Default eval_types when not provided (shouldn't happen with
        # well-formed upstream data, but guard for synthetic fixtures).
        if "eval_types" not in task:
            ref = task.get("reference_answer")
            ref_url = task.get("reference_url")
            inferred: list[str] = []
            if ref is not None:
                inferred.append(EVAL_TYPE_STRING_MATCH)
            if ref_url is not None:
                inferred.append(EVAL_TYPE_URL_MATCH)
            task["eval_types"] = inferred

        return task


# ----------------------------------------------------------------------
# Output parsing
# ----------------------------------------------------------------------


_URL_LINE_RE = re.compile(
    r"^(?:final[_\s-]?url|url)\s*[:=]\s*(\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_ANSWER_LINE_RE = re.compile(
    r"^(?:answer|final[_\s-]?answer)\s*[:=]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _split_agent_output(output: str) -> tuple[str, str | None]:
    """Return ``(answer_text, final_url)`` from a free-form agent output.

    Accepted shapes:
        1. JSON envelope: ``{"answer": "...", "url": "..."}`` (or
           ``final_url`` instead of ``url``).
        2. Two named lines: ``ANSWER: ...`` and ``URL: ...``.
        3. Plain text: the entire output is treated as the answer; URL
           is ``None``.
    """
    stripped = output.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            answer = payload.get("answer")
            if not isinstance(answer, str):
                answer = (
                    payload.get("final_answer")
                    if isinstance(payload.get("final_answer"), str)
                    else stripped
                )
            url = payload.get("url") or payload.get("final_url")
            return answer if isinstance(answer, str) else stripped, (
                url if isinstance(url, str) else None
            )

    url_match = _URL_LINE_RE.search(output)
    final_url = url_match.group(1) if url_match else None

    answer_match = _ANSWER_LINE_RE.search(output)
    if answer_match:
        return answer_match.group(1).strip(), final_url

    return output, final_url


# ----------------------------------------------------------------------
# Scoring helpers
# ----------------------------------------------------------------------


def _string_match(task: dict[str, Any], answer: str) -> bool:
    """Score *answer* against the task's reference answer(s).

    Honours both the simple ``reference_answer`` field and the upstream
    ``reference_answers`` map with optional ``must_include`` /
    ``fuzzy_match`` / ``exact_match`` keys.
    """
    answer_norm = _normalise_text(answer)

    # Compound form first — upstream's preferred shape.
    refs = task.get("reference_answers")
    if isinstance(refs, dict):
        return _string_match_compound(refs, answer_norm)

    expected = task.get("reference_answer")
    if expected is None:
        return False
    if isinstance(expected, list):
        # Accept any one of the listed acceptable answers.
        return any(_fuzzy_eq(_normalise_text(str(e)), answer_norm) for e in expected)
    return _fuzzy_eq(_normalise_text(str(expected)), answer_norm)


def _string_match_compound(refs: dict[str, Any], answer_norm: str) -> bool:
    """Apply the upstream ``reference_answers`` rules to *answer_norm*."""
    must_include = refs.get("must_include")
    if isinstance(must_include, list):
        for needle in must_include:
            if _normalise_text(str(needle)) not in answer_norm:
                return False
        # If only ``must_include`` was specified, success.
        if "fuzzy_match" not in refs and "exact_match" not in refs:
            return True

    fuzzy = refs.get("fuzzy_match")
    if isinstance(fuzzy, list):
        for needle in fuzzy:
            if not _fuzzy_eq(_normalise_text(str(needle)), answer_norm):
                if _normalise_text(str(needle)) not in answer_norm:
                    return False
    elif isinstance(fuzzy, str):
        if not _fuzzy_eq(_normalise_text(fuzzy), answer_norm):
            if _normalise_text(fuzzy) not in answer_norm:
                return False

    exact = refs.get("exact_match")
    if isinstance(exact, str):
        if _normalise_text(exact) != answer_norm:
            return False

    # If no rules were applicable, don't claim a pass.
    return any(k in refs for k in ("must_include", "fuzzy_match", "exact_match"))


def _url_match(task: dict[str, Any], final_url: str | None) -> bool:
    """Compare *final_url* against the task's ``reference_url``."""
    expected = task.get("reference_url")
    if not expected or not isinstance(expected, str):
        return False
    if not final_url or not isinstance(final_url, str):
        return False
    return _urls_equivalent(expected, final_url)


def _normalise_text(s: str) -> str:
    """Lowercase and collapse whitespace for comparison."""
    return " ".join(s.lower().split())


def _fuzzy_eq(a: str, b: str) -> bool:
    """Lenient equality: equal after normalisation, or substring overlap.

    Mirrors WebArena's "fuzzy_match" intent without pulling in an LLM.
    """
    if a == b:
        return True
    if not a or not b:
        return False
    return a in b or b in a


def _urls_equivalent(a: str, b: str) -> bool:
    """Compare two URLs by scheme + netloc + path (ignore query/fragment)."""
    pa, pb = urlparse(a), urlparse(b)
    norm_a = (pa.scheme.lower(), pa.netloc.lower(), pa.path.rstrip("/"))
    norm_b = (pb.scheme.lower(), pb.netloc.lower(), pb.path.rstrip("/"))
    if norm_a == norm_b:
        return True
    # Allow scheme-less comparison when one side omits it.
    if not pa.scheme or not pb.scheme:
        return (pa.netloc.lower(), pa.path.rstrip("/")) == (
            pb.netloc.lower(),
            pb.path.rstrip("/"),
        )
    return False


def _format_prompt(task: dict[str, Any]) -> str:
    """Compose a runnable prompt from a WebArena task record."""
    intent = task.get("intent") or task.get("instruction") or ""
    start_url = task.get("start_url") or ""
    sites = task.get("sites") or task.get("site")
    parts: list[str] = []
    if intent:
        parts.append(str(intent).strip())
    if start_url:
        parts.append(f"Start at: {start_url}")
    if sites:
        if isinstance(sites, list):
            parts.append(f"Sites: {', '.join(str(s) for s in sites)}")
        else:
            parts.append(f"Site: {sites}")
    parts.append(
        "Respond with the requested answer. If a final URL is relevant, "
        "include it on a separate line as 'URL: <url>'."
    )
    return "\n".join(parts)


# ----------------------------------------------------------------------
# Setup hint (used by the CLI smoke path)
# ----------------------------------------------------------------------


_SETUP_HINT = """\
WebArena dataset not found.

Looked under: {path}

To run this adapter end-to-end:
  1. Clone upstream tasks (we do NOT vendor or pip install upstream):
       git clone https://github.com/web-arena-x/webarena /tmp/webarena
  2. Stage the JSON task config dumps:
       mkdir -p ~/.chimera/datasets/webarena
       cp /tmp/webarena/config_files/test.raw.json \\
          ~/.chimera/datasets/webarena/test.json
  3. Stand up the upstream sandbox sites (Docker — heavyweight):
       see https://github.com/web-arena-x/webarena/blob/main/environment_docker/README.md
  4. Re-run with --limit 5 to smoke-test loading.

Override the dataset directory via CHIMERA_WEBARENA_PATH=/path/to/dir.
"""
