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
}

#: Hyphenless registry aliases resolve to the same spec.
_ALIASES: dict[str, str] = {
    "humanevalplus": "humaneval-plus",
    "swebench": "swe-bench",
    "swe-bench-lite": "swe-bench",
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
    else:  # pragma: no cover - specs are module-local constants
        raise ValueError(f"unknown fetch kind {spec.kind!r}")
    return dest
