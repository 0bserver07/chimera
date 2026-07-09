"""Dataset staging for benchmark adapters — ``chimera bench-fetch``.

Benchmark *adapters* ship with Chimera; benchmark *datasets* deliberately do
not (multi-GB payloads, upstream licenses — see the vendoring note in
``chimera/shrew/benchmarks/__init__.py``). This module closes the resulting
"wired but not runnable" gap for the benches whose datasets are publicly
redistributable: ``chimera bench-fetch <name>`` downloads them once into
``~/.chimera/datasets/<bench>/`` with stdlib-only HTTP, and
``_load_benchmark`` auto-discovers staged files so a fetched bench runs with
no ``--dataset`` flag at all.

Two fetch kinds:

* ``url`` — a single direct file download.
* ``hf-rows`` — page through the Hugging Face datasets-server ``/rows`` API
  (100 rows per request) and write one JSON object per line, which is exactly
  the JSONL every adapter here accepts.

Benches whose datasets are gated (authenticated download or unclear license)
are intentionally absent — staging those stays a manual, documented step.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["FetchSpec", "FETCHES", "available", "fetch", "staged_path", "staging_dir"]

#: Environment override for the staging root (used by tests; respected
#: everywhere so users can relocate the cache).
_ENV_DIR = "CHIMERA_DATASETS_DIR"

#: Rows per datasets-server page (the API maximum).
_HF_PAGE = 100

# Indirection so tests can monkeypatch network access in one place.
_urlopen = urllib.request.urlopen


def staging_dir() -> Path:
    """Return the dataset staging root (``~/.chimera/datasets`` by default)."""
    override = os.environ.get(_ENV_DIR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".chimera" / "datasets"


@dataclass(frozen=True)
class FetchSpec:
    """One stageable dataset.

    Attributes:
        bench: The ``_BENCHMARKS`` registry key this dataset feeds.
        out: File path relative to :func:`staging_dir` the fetch writes.
        kind: ``"url"`` (direct file) or ``"hf-rows"`` (paginated
            datasets-server download written as JSONL).
        source: Direct URL (``url``) or HF dataset id (``hf-rows``).
        config: datasets-server config name (``hf-rows`` only).
        split: datasets-server split name (``hf-rows`` only).
        note: Provenance/license line printed when fetching.
    """

    bench: str
    out: str
    kind: str
    source: str
    config: str = "default"
    split: str = "test"
    note: str = ""


#: Fetchable datasets, keyed by canonical benchmark registry name.
FETCHES: dict[str, FetchSpec] = {
    "mbpp": FetchSpec(
        bench="mbpp",
        out="mbpp/sanitized-mbpp.json",
        kind="url",
        source=(
            "https://raw.githubusercontent.com/google-research/google-research/"
            "master/mbpp/sanitized-mbpp.json"
        ),
        note="MBPP sanitized split (Google Research; CC-BY-4.0).",
    ),
    "humaneval-plus": FetchSpec(
        bench="humaneval-plus",
        out="humaneval-plus/test.jsonl",
        kind="hf-rows",
        source="evalplus/humanevalplus",
        note="EvalPlus HumanEval+ (Apache-2.0), via the HF datasets-server API.",
    ),
    "mbpp-plus": FetchSpec(
        bench="mbpp-plus",
        out="mbpp-plus/test.jsonl",
        kind="hf-rows",
        source="evalplus/mbppplus",
        note=(
            "EvalPlus MBPP+ (Apache-2.0), 378 problems, via the HF "
            "datasets-server API (rows served untruncated). Each row carries "
            "the base MBPP `test_list` (3-6 asserts) AND EvalPlus's expanded "
            "`test` harness. The MBPP adapter grades from `test_list` only, so "
            "grading here is BASE-strength (equivalent to MBPP-sanitized). The "
            "plus `test` script is staged verbatim in every row for a future "
            "plus-strength adapter but is NOT executed by the current adapter."
        ),
    ),
    "swe-bench": FetchSpec(
        bench="swe-bench",
        out="swe-bench/lite-test.jsonl",
        kind="hf-rows",
        source="princeton-nlp/SWE-bench_Lite",
        note=(
            "SWE-bench Lite test split (tasks from public GitHub repos), via "
            "the HF datasets-server API."
        ),
    ),
    "livecodebench": FetchSpec(
        bench="livecodebench",
        out="livecodebench/release_v6-new.json",
        kind="url-jsonl-transform",
        source=(
            "https://huggingface.co/datasets/livecodebench/code_generation_lite/"
            "resolve/main/test6.jsonl"
        ),
        note=(
            "LiveCodeBench code_generation_lite, NEWEST release slice only "
            "(test6.jsonl, ~128 MB download -> a few MB staged). Rows are "
            "transformed to adapter-ready tasks; the pickled private_test_cases "
            "are dropped, so grading uses PUBLIC test cases only (weaker than "
            "the official harness). Full v1-v6 union (~4.3 GB) is a manual "
            "--dataset stage."
        ),
    ),
    "human-eval": FetchSpec(
        bench="human-eval",
        out="human-eval/test.jsonl",
        kind="hf-rows",
        source="openai/openai_humaneval",
        config="openai_humaneval",
        split="test",
        note=(
            "OpenAI HumanEval base (MIT), 164 problems, via the HF "
            "datasets-server API. Each row carries the function-signature "
            "`prompt`, a `test` harness defining check(<entry_point>), and "
            "`entry_point`. The plus variant is staged separately as "
            "`humaneval-plus`."
        ),
    ),
    "bigcodebench": FetchSpec(
        bench="bigcodebench",
        out="bigcodebench/v0.1.4.jsonl",
        kind="hf-rows",
        source="bigcode/bigcodebench",
        config="default",
        split="v0.1.4",
        note=(
            "BigCodeBench v0.1.4 (Apache-2.0), 1,140 practical Python tasks "
            "exercising library calls, via the HF datasets-server API (12 "
            "pages of 100). Each row carries both complete/instruct prompts "
            "and a unittest `test`; the adapter defaults to the instruct "
            "split. Other release splits (v0.1.0_hf..v0.1.3) are a manual "
            "--dataset stage."
        ),
    ),
    "humaneval-x": FetchSpec(
        bench="humaneval-x",
        out="humaneval-x/python.jsonl",
        kind="url",
        source=(
            "https://huggingface.co/datasets/THUDM/humaneval-x/resolve/main/"
            "data/python/data/humaneval.jsonl"
        ),
        note=(
            "HumanEval-X Python split (Apache-2.0), 164 problems, fetched as "
            "direct JSONL from the dataset repo (this script-based dataset has "
            "no datasets-server parquet). Only the adapter's Python execution "
            "path is wired; the other four languages are a scaffold. Stage a "
            "different language from data/<lang>/data/humaneval.jsonl via a "
            "manual --dataset."
        ),
    ),
    "aimo": FetchSpec(
        bench="aimo",
        out="aimo/aime.jsonl",
        kind="hf-rows",
        source="AI-MO/aimo-validation-aime",
        config="default",
        split="train",
        note=(
            "AIMO validation AIME set (Apache-2.0), 90 olympiad problems with "
            "integer answers, via the HF datasets-server API — a public proxy "
            "for the private AIMO competition set. The AMC proxy "
            "(AI-MO/aimo-validation-amc, 83 problems) is equally stageable via "
            "a manual --dataset."
        ),
    ),
}

#: Hyphenless registry aliases resolve to the same spec.
_ALIASES: dict[str, str] = {
    "humanevalplus": "humaneval-plus",
    "mbppplus": "mbpp-plus",
    "swebench": "swe-bench",
    "swe-bench-lite": "swe-bench",
    "lcb": "livecodebench",
    "humaneval": "human-eval",
    "humanevalx": "humaneval-x",
}


def _canonical(name: str) -> str:
    """Resolve a registry alias to its canonical fetch key."""
    return _ALIASES.get(name, name)


def available() -> list[str]:
    """Return the canonical fetchable benchmark names, sorted."""
    return sorted(FETCHES)


def staged_path(name: str) -> Path | None:
    """Return the staged dataset file for *name* if it exists, else ``None``.

    Args:
        name: A benchmark registry name (aliases accepted).
    """
    spec = FETCHES.get(_canonical(name))
    if spec is None:
        return None
    path = staging_dir() / spec.out
    return path if path.exists() else None


def _fetch_url(spec: FetchSpec, dest: Path) -> None:
    """Download a direct-URL dataset to *dest*."""
    with _urlopen(spec.source, timeout=120) as resp:  # noqa: S310 - fixed https URLs
        dest.write_bytes(resp.read())


def _transform_lcb_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce one raw LiveCodeBench row to an adapter-ready task dict.

    Decodes the ``public_test_cases`` JSON-string into ``test_cases``
    (the key :class:`~chimera.eval.benchmarks.livecodebench.LiveCodeBench`
    grades from), builds a stdin/stdout ``prompt``, and drops the pickled
    ``private_test_cases`` payload (the bulk of the upstream file).
    Returns ``None`` for rows without decodable public tests.
    """
    try:
        cases = json.loads(row.get("public_test_cases") or "[]")
    except (TypeError, ValueError):
        return None
    if not cases:
        return None
    starter = (row.get("starter_code") or "").strip()
    prompt = (
        f"{row.get('question_title', '')}\n\n{row.get('question_content', '')}\n\n"
        + (
            f"Complete the following starter code:\n```python\n{starter}\n```\n"
            if starter
            else "Write a complete Python program that reads from stdin and writes to stdout.\n"
        )
        + "Return the full program as your final answer."
    )
    return {
        "id": row.get("question_id") or row.get("question_title", ""),
        "prompt": prompt,
        "test_cases": [
            {
                "input": c.get("input", ""),
                "output": c.get("output", ""),
                "testtype": c.get("testtype", ""),
            }
            for c in cases
        ],
        "difficulty": row.get("difficulty", ""),
        "contest_date": row.get("contest_date", ""),
        "platform": row.get("platform", ""),
        "starter_code": starter,
    }


#: Per-bench row transforms for ``url-jsonl-transform`` fetches.
_TRANSFORMS: dict[str, Any] = {
    "livecodebench": _transform_lcb_row,
}


def _fetch_jsonl_transform(spec: FetchSpec, dest: Path) -> None:
    """Stream a large JSONL source, transform each row, write a JSON list.

    Rows are processed one line at a time so multi-hundred-MB upstream files
    never sit in memory; the staged output holds only the transformed tasks.
    """
    transform = _TRANSFORMS[spec.bench]
    first = True
    with _urlopen(spec.source, timeout=600) as resp, dest.open("w", encoding="utf-8") as out:  # noqa: S310
        out.write("[\n")
        for line in resp:
            line = line.strip()
            if not line:
                continue
            task = transform(json.loads(line))
            if task is None:
                continue
            if not first:
                out.write(",\n")
            out.write(json.dumps(task))
            first = False
        out.write("\n]\n")


def _fetch_hf_rows(spec: FetchSpec, dest: Path) -> None:
    """Page through the datasets-server rows API, writing JSONL to *dest*."""
    offset = 0
    with dest.open("w", encoding="utf-8") as out:
        while True:
            query = urllib.parse.urlencode(
                {
                    "dataset": spec.source,
                    "config": spec.config,
                    "split": spec.split,
                    "offset": offset,
                    "length": _HF_PAGE,
                }
            )
            url = f"https://datasets-server.huggingface.co/rows?{query}"
            with _urlopen(url, timeout=120) as resp:  # noqa: S310 - fixed https host
                payload = json.loads(resp.read().decode("utf-8"))
            rows = payload.get("rows", [])
            for entry in rows:
                out.write(json.dumps(entry.get("row", {})) + "\n")
            if len(rows) < _HF_PAGE:
                break
            offset += _HF_PAGE


def fetch(name: str, force: bool = False) -> Path:
    """Stage the dataset for benchmark *name* and return its file path.

    Args:
        name: A fetchable benchmark registry name (aliases accepted).
        force: Re-download even when the staged file already exists.

    Returns:
        Path to the staged dataset file.

    Raises:
        ValueError: If *name* has no registered fetcher.
    """
    key = _canonical(name)
    spec = FETCHES.get(key)
    if spec is None:
        raise ValueError(
            f"No fetcher for benchmark {name!r}. Fetchable: {', '.join(available())}. "
            "Other benches need a manually staged --dataset (see docs/reference/ecosystem.md)."
        )
    dest = staging_dir() / spec.out
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if spec.kind == "url":
        _fetch_url(spec, dest)
    elif spec.kind == "hf-rows":
        _fetch_hf_rows(spec, dest)
    elif spec.kind == "url-jsonl-transform":
        _fetch_jsonl_transform(spec, dest)
    else:  # pragma: no cover - specs are module-local constants
        raise ValueError(f"unknown fetch kind {spec.kind!r}")
    return dest
