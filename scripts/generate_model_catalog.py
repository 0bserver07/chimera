#!/usr/bin/env python3
"""Generate ``chimera/providers/model_catalog.py`` from the public models.dev catalog.

The hand-maintained pricing table in :mod:`chimera.providers.cost` drifts stale
(its GLM/DeepSeek rates are literally commented as placeholders). This generator
pulls the community-maintained models.dev catalog — which auto-syncs pricing and
context limits across ~150 providers — and emits a *pure data* Python module that
:mod:`chimera.providers.cost` consults as a fallback behind the hand dict.

Usage::

    python scripts/generate_model_catalog.py            # rewrite the module
    python scripts/generate_model_catalog.py --check    # CI drift check (no write)
    python scripts/generate_model_catalog.py --url URL  # override the source

The module is stdlib-only (``urllib``) so it can run in any environment. It is
structured to be importable — :func:`generate` accepts an injectable ``fetch``
callable — so the test-suite can exercise it without touching the network.

Collision rule: the same model id (e.g. ``claude-opus-4-5``) is offered by many
providers at differing prices. Each record is taken from the first-party /
manufacturer provider when one is recognised (see :data:`FIRST_PARTY`), else the
alphabetically-first provider id. This keeps regeneration deterministic and
prefers authoritative rates over marked-up reseller ones.
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable

MODELS_DEV_URL = "https://models.dev/api.json"

# Output lives at chimera/providers/model_catalog.py, resolved from this script's
# location so the generator works regardless of the caller's working directory.
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent.parent
    / "chimera"
    / "providers"
    / "model_catalog.py"
)

# Providers that are the model's manufacturer / lab, not inference resellers or
# gateways. When one model id is offered by several providers, a first-party
# entry wins so the catalog carries authoritative rates (e.g. deepseek's real
# $0.14/$0.28 rather than a reseller's markup). Ordering among first parties is
# alphabetical-by-id and rarely matters (a model seldom spans two labs).
FIRST_PARTY = frozenset({
    "alibaba", "alibaba-cn", "anthropic", "cohere", "deepseek", "google",
    "inception", "llama", "longcat", "minimax", "minimax-cn", "mistral",
    "moonshotai", "moonshotai-cn", "openai", "perplexity", "sarvam", "stepfun",
    "stepfun-ai", "upstage", "xai", "xiaomi", "zai", "zhipuai",
})

# The one line in the emitted module that legitimately changes on every run.
# --check strips it from both sides before diffing so a date-only delta is not
# reported as drift.
_VOLATILE_PREFIX = "Generated: "


def fetch_json(url: str = MODELS_DEV_URL) -> dict[str, Any]:
    """Fetch and parse the models.dev catalog.

    Args:
        url: Source URL. Defaults to :data:`MODELS_DEV_URL`.

    Returns:
        The parsed JSON object (provider id -> provider record).

    Raises:
        RuntimeError: If the source is unreachable or returns non-JSON. The
            caller should surface this and stop — there is no fallback source.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "chimera-model-catalog/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (trusted https)
            raw = resp.read()
    except Exception as exc:  # pragma: no cover - network failure path
        raise RuntimeError(f"failed to fetch models.dev catalog from {url}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - malformed source
        raise RuntimeError(f"models.dev returned non-JSON from {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"models.dev returned unexpected top-level {type(data).__name__}")
    return data


def _num(value: Any) -> float | int | None:
    """Normalise a cost/limit number: round float noise, keep ints as ints.

    Returns ``None`` for anything non-numeric (or a bool, which ``isinstance``
    would otherwise treat as an int).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    rounded = round(float(value), 6)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def _provider_rank(provider_id: str) -> tuple[int, str]:
    """Sort key that floats first-party providers ahead of everything else."""
    return (0 if provider_id in FIRST_PARTY else 1, provider_id)


def build_catalog(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Transform the raw models.dev payload into the normalised catalog.

    Args:
        data: Parsed models.dev JSON (provider id -> {..., "models": {...}}).

    Returns:
        Mapping of model id -> normalised record. Only models with a numeric
        ``cost.input`` are included; collisions resolve to the first-party
        provider (see :data:`FIRST_PARTY`).
    """
    # model id -> list of (provider_id, model_record)
    candidates: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for provider_id, provider in sorted(data.items()):
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for model_id, model in models.items():
            if not isinstance(model, dict):
                continue
            cost = model.get("cost")
            if not isinstance(cost, dict) or _num(cost.get("input")) is None:
                continue
            candidates.setdefault(model_id, []).append((provider_id, model))

    catalog: dict[str, dict[str, Any]] = {}
    for model_id, entries in candidates.items():
        provider_id, model = min(entries, key=lambda t: _provider_rank(t[0]))
        cost = model["cost"]
        limit = model.get("limit") if isinstance(model.get("limit"), dict) else {}
        context = limit.get("context") if isinstance(limit, dict) else None
        catalog[model_id] = {
            "input": _num(cost.get("input")),
            "output": _num(cost.get("output")),
            "cache_read": _num(cost.get("cache_read")),
            "cache_write": _num(cost.get("cache_write")),
            "context": context if isinstance(context, int) and not isinstance(context, bool) else None,
            "provider": provider_id,
        }
    return catalog


def _literal(value: Any) -> str:
    """Render a scalar as a Python literal (double-quoted strings, ``None``)."""
    if value is None:
        return "None"
    if isinstance(value, str):
        return json.dumps(value)  # double-quoted, properly escaped
    if isinstance(value, float):
        return repr(value)
    return str(value)  # int


def _record_literal(record: dict[str, Any]) -> str:
    """Render one catalog record as a single-line dict literal (fixed key order)."""
    parts = [
        f'"input": {_literal(record["input"])}',
        f'"output": {_literal(record["output"])}',
        f'"cache_read": {_literal(record["cache_read"])}',
        f'"cache_write": {_literal(record["cache_write"])}',
        f'"context": {_literal(record["context"])}',
        f'"provider": {_literal(record["provider"])}',
    ]
    return "{" + ", ".join(parts) + "}"


def render_module(catalog: dict[str, dict[str, Any]], *, source: str, date: str) -> str:
    """Render the full generated ``model_catalog.py`` source text.

    Args:
        catalog: Normalised catalog from :func:`build_catalog`.
        source: Source URL to record in the header.
        date: Generation date (``YYYY-MM-DD``) for the header.

    Returns:
        The complete module source, with model ids sorted for deterministic diffs.
    """
    lines = [
        '"""Generated model pricing & context catalog — DO NOT EDIT BY HAND.',
        "",
        "Emitted by ``scripts/generate_model_catalog.py`` from the public models.dev",
        "catalog. Regenerate with::",
        "",
        "    python scripts/generate_model_catalog.py            # rewrite this file",
        "    python scripts/generate_model_catalog.py --check    # CI drift check",
        "",
        "Each value maps a model id to a pricing / context record. Costs are US",
        "dollars per **million** tokens — the same convention as",
        "``chimera.providers.cost.PRICING`` — and ``None`` marks a field the source",
        "did not publish. When a model id is offered by several providers the record",
        "is taken from the first-party provider where known (e.g. ``anthropic`` for",
        "``claude-*``), else the alphabetically-first provider id.",
        "",
        f"Source: {source}",
        f"{_VOLATILE_PREFIX}{date}",
        f"Models: {len(catalog)}",
        '"""',
        "from __future__ import annotations",
        "",
        "MODEL_CATALOG: dict[str, dict[str, float | int | str | None]] = {",
    ]
    for model_id in sorted(catalog):
        lines.append(f"    {json.dumps(model_id)}: {_record_literal(catalog[model_id])},")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def generate(fetch: Callable[[], dict[str, Any]] = fetch_json, *, date: str | None = None) -> str:
    """Fetch, build, and render the catalog module in one call.

    Args:
        fetch: Zero-arg callable returning the parsed models.dev payload. Injected
            by tests to avoid the network; defaults to :func:`fetch_json`.
        date: Generation date override (``YYYY-MM-DD``). Defaults to today.

    Returns:
        The rendered module source.
    """
    data = fetch()
    catalog = build_catalog(data)
    stamp = date if date is not None else datetime.date.today().isoformat()
    return render_module(catalog, source=MODELS_DEV_URL, date=stamp)


def _strip_volatile(text: str) -> str:
    """Drop the generation-date line so date-only deltas aren't flagged as drift."""
    return "\n".join(
        line for line in text.splitlines() if not line.startswith(_VOLATILE_PREFIX)
    )


def check(path: Path, fetch: Callable[[], dict[str, Any]] = fetch_json) -> tuple[bool, str]:
    """Compare the committed module against a fresh regeneration.

    Args:
        path: Path to the committed ``model_catalog.py``.
        fetch: Injectable payload fetcher (see :func:`generate`).

    Returns:
        ``(ok, diff)`` — ``ok`` is True when they match (ignoring the date line);
        ``diff`` is a unified diff of the difference, empty when ``ok``.
    """
    regenerated = _strip_volatile(generate(fetch=fetch))
    committed = _strip_volatile(path.read_text()) if path.exists() else ""
    if regenerated == committed:
        return True, ""
    diff = "\n".join(
        difflib.unified_diff(
            committed.splitlines(),
            regenerated.splitlines(),
            fromfile=f"{path} (committed)",
            tofile="regenerated",
            lineterm="",
        )
    )
    return False, diff


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed file is current; no write")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="target module path")
    parser.add_argument("--url", default=MODELS_DEV_URL, help="source catalog URL")
    args = parser.parse_args(argv)

    def fetch() -> dict[str, Any]:
        return fetch_json(args.url)

    if args.check:
        ok, diff = check(args.output, fetch=fetch)
        if ok:
            print(f"OK: {args.output} is up to date")
            return 0
        print(f"DRIFT: {args.output} is stale — regenerate with "
              "`python scripts/generate_model_catalog.py`", file=sys.stderr)
        print(diff, file=sys.stderr)
        return 1

    text = generate(fetch=fetch)
    args.output.write_text(text)
    count = next(
        (line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("Models: ")),
        "?",
    )
    print(f"wrote {args.output} ({count} models)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
