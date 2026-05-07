"""``chimera tier-status`` -- feature x tier readiness report.

Reads ``docs/tier-status.json`` and renders one of three outputs:

* default (no flags) -- print an aligned text table to stdout.
* ``--regen`` -- (re)write ``docs/tier-status.md`` from the JSON manifest.
* ``--json`` -- dump the JSON manifest to stdout.

The manifest tracks every Chimera feature against three tiers:

* **Tier 1** -- live-verified (real model calls, real network, real CI
  evidence on file).
* **Tier 2** -- tests pass against mocks/fakes/fixtures; live verification
  not yet on file.
* **Tier 3** -- scaffolded (code exists; skeletons or placeholders, or
  not fully wired).

The CLI is stdlib-only and never imports any provider/agent modules so it
remains safe to invoke from doctor-style early-boot contexts.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

__all__ = [
    "Feature",
    "TierManifest",
    "add_subparser",
    "default_json_path",
    "default_md_path",
    "load_manifest",
    "render_markdown",
    "render_text",
    "run",
]


# ---------------------------------------------------------------------------
# Schema + paths
# ---------------------------------------------------------------------------


# WHY: the json/md pair lives next to the rest of the user-facing docs so
# anyone can read it without a Python install. The cli module exposes
# helpers (``default_json_path`` / ``default_md_path``) so tests can ask
# the package itself for the canonical location instead of hard-coding it.
def default_json_path() -> Path:
    """Return the canonical path of the JSON manifest.

    Resolves relative to the chimera package so the answer is the same
    whether the package is installed editable or from a wheel. Falls
    back to ``docs/tier-status.json`` under the current working directory
    if the package install does not include the docs (e.g. wheel install
    without docs/).
    """
    pkg_root = Path(__file__).resolve().parents[2]
    candidate = pkg_root / "docs" / "tier-status.json"
    if candidate.exists():
        return candidate
    return Path("docs") / "tier-status.json"


def default_md_path() -> Path:
    """Return the canonical path of the rendered Markdown table."""
    pkg_root = Path(__file__).resolve().parents[2]
    return pkg_root / "docs" / "tier-status.md"


@dataclasses.dataclass(frozen=True)
class Feature:
    """One row in the tier-status manifest.

    Attributes:
        name: Human-readable feature label (no upstream brand strings).
        tier: 1 (live-verified), 2 (tests-pass-only), or 3 (scaffolded).
        category: Bucket from the manifest's documented vocabulary.
        evidence: Free-form pointer to the artifact backing the tier
            (test path, research report, README claim, etc.).
    """

    name: str
    tier: int
    category: str
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tier": self.tier,
            "category": self.category,
            "evidence": self.evidence,
        }


@dataclasses.dataclass(frozen=True)
class TierManifest:
    """Parsed shape of ``docs/tier-status.json``."""

    schema_version: int
    generated_at: str
    tier_definitions: Mapping[str, str]
    features: tuple[Feature, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "tier_definitions": dict(self.tier_definitions),
            "features": [f.to_dict() for f in self.features],
        }


# ---------------------------------------------------------------------------
# Loading + parsing
# ---------------------------------------------------------------------------


_VALID_TIERS = (1, 2, 3)


def _coerce_feature(entry: Mapping[str, Any]) -> Feature:
    """Validate one entry from the manifest's ``features`` array."""
    try:
        name = entry["name"]
        tier = entry["tier"]
        category = entry["category"]
        evidence = entry["evidence"]
    except KeyError as exc:  # noqa: BLE001
        raise ValueError(f"feature missing required field: {exc}") from exc
    if not isinstance(name, str) or not name:
        raise ValueError(f"feature name must be a non-empty string: {entry!r}")
    if not isinstance(tier, int) or tier not in _VALID_TIERS:
        raise ValueError(
            f"feature tier must be 1, 2, or 3 (got {tier!r}) in {entry!r}"
        )
    if not isinstance(category, str) or not category:
        raise ValueError(f"feature category must be a non-empty string: {entry!r}")
    if not isinstance(evidence, str):
        raise ValueError(f"feature evidence must be a string: {entry!r}")
    return Feature(name=name, tier=tier, category=category, evidence=evidence)


def load_manifest(path: Path | str | None = None) -> TierManifest:
    """Read and validate the JSON manifest.

    Args:
        path: Override the manifest path; defaults to
            :func:`default_json_path`.

    Returns:
        A fully-typed :class:`TierManifest`.

    Raises:
        FileNotFoundError: The path does not exist.
        ValueError: JSON is malformed or schema is violated.
    """
    p = Path(path) if path is not None else default_json_path()
    if not p.exists():
        raise FileNotFoundError(f"tier-status manifest not found: {p}")
    raw = p.read_text(encoding="utf-8")
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"tier-status manifest is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("tier-status manifest must be a JSON object at the root")

    schema_version = data.get("schema_version", 1)
    if not isinstance(schema_version, int):
        raise ValueError("schema_version must be an int")
    generated_at = data.get("generated_at", "")
    if not isinstance(generated_at, str):
        raise ValueError("generated_at must be a string")
    tier_definitions = data.get("tier_definitions") or {}
    if not isinstance(tier_definitions, dict):
        raise ValueError("tier_definitions must be an object")

    raw_features = data.get("features")
    if not isinstance(raw_features, list):
        raise ValueError("features must be an array")
    features = tuple(_coerce_feature(cast(Mapping[str, Any], f)) for f in raw_features)

    # WHY: tier_definitions keys are stored as JSON strings ("1", "2", "3").
    coerced_defs: OrderedDict[str, str] = OrderedDict()
    for key in ("1", "2", "3"):
        val = tier_definitions.get(key, "")
        if not isinstance(val, str):
            raise ValueError(f"tier_definitions[{key!r}] must be a string")
        coerced_defs[key] = val

    return TierManifest(
        schema_version=schema_version,
        generated_at=generated_at,
        tier_definitions=coerced_defs,
        features=features,
    )


# ---------------------------------------------------------------------------
# Sorting + grouping
# ---------------------------------------------------------------------------


def _group_by_category(
    features: Iterable[Feature],
) -> "OrderedDict[str, list[Feature]]":
    """Sort features by (category, tier, name) and group by category.

    The category key order in the returned ``OrderedDict`` is determined
    by first appearance in the (sorted) list, so callers see categories
    in alphabetical order. Within each category, rows are sorted by
    ascending tier (1 first), then by feature name for stable output.
    """
    sorted_features = sorted(
        features, key=lambda f: (f.category, f.tier, f.name.lower())
    )
    out: OrderedDict[str, list[Feature]] = OrderedDict()
    for f in sorted_features:
        out.setdefault(f.category, []).append(f)
    return out


# ---------------------------------------------------------------------------
# Rendering -- text table (stdout default)
# ---------------------------------------------------------------------------


def render_text(manifest: TierManifest) -> str:
    """Render the manifest as an aligned text table for stdout."""
    lines: list[str] = []
    lines.append(
        f"chimera tier-status (schema v{manifest.schema_version}, "
        f"generated {manifest.generated_at}):"
    )
    lines.append("")
    for tier_key in ("1", "2", "3"):
        defn = manifest.tier_definitions.get(tier_key, "")
        if defn:
            lines.append(f"  Tier {tier_key}: {defn}")
    lines.append("")

    # Column widths.
    cat_w = max(len("CATEGORY"), max((len(f.category) for f in manifest.features), default=8))
    name_w = max(len("FEATURE"), max((len(f.name) for f in manifest.features), default=7))
    tier_w = len("TIER")
    header = (
        f"  {'CATEGORY':<{cat_w}}  {'TIER':<{tier_w}}  "
        f"{'FEATURE':<{name_w}}  EVIDENCE"
    )
    lines.append(header)
    lines.append(
        "  " + "-" * cat_w + "  " + "-" * tier_w + "  "
        + "-" * name_w + "  " + "-" * 8
    )

    grouped = _group_by_category(manifest.features)
    for category, rows in grouped.items():
        for row in rows:
            lines.append(
                f"  {category:<{cat_w}}  {row.tier:<{tier_w}}  "
                f"{row.name:<{name_w}}  {row.evidence}"
            )
    lines.append("")
    counts = _tier_counts(manifest.features)
    lines.append(
        f"  summary: {counts[1]} Tier 1, {counts[2]} Tier 2, "
        f"{counts[3]} Tier 3 ({len(manifest.features)} features total)"
    )
    return "\n".join(lines)


def _tier_counts(features: Iterable[Feature]) -> dict[int, int]:
    """Count features per tier; always returns keys 1, 2, 3."""
    out = {1: 0, 2: 0, 3: 0}
    for f in features:
        out[f.tier] = out.get(f.tier, 0) + 1
    return out


# ---------------------------------------------------------------------------
# Rendering -- Markdown (docs/tier-status.md)
# ---------------------------------------------------------------------------


_MD_HEADER = """# Chimera tier-status

> Auto-generated from `docs/tier-status.json` by `chimera tier-status --regen`.
> Edit the JSON, then re-run the command. Do not edit this file by hand.

Every Chimera feature lives in one of three tiers:

- **Tier 1 — live-verified.** Real model calls, real network, real CI
  evidence on file. The kind of thing you can confidently put in a
  release-note headline.
- **Tier 2 — tests pass with mocks/fakes.** Unit and integration coverage
  is green, but a live end-to-end verification has not been recorded yet.
- **Tier 3 — scaffolded.** Code exists; the surface may be a skeleton, a
  placeholder, or not yet fully wired.

Rows below are grouped by category, then sorted by ascending tier so the
gaps surface first.
"""


def render_markdown(manifest: TierManifest) -> str:
    """Render the manifest as a grouped Markdown report.

    Layout:

    1. Header explaining the three tiers (uses the manifest's own
       ``tier_definitions`` so the doc stays in sync).
    2. One ``###`` section per category, in alphabetical order.
    3. A 4-column table per category (Tier | Feature | Evidence).
    4. A summary block with the per-tier counts.
    """
    lines: list[str] = []
    lines.append(_MD_HEADER.strip())
    lines.append("")
    lines.append(
        f"_Schema version {manifest.schema_version}; "
        f"generated {manifest.generated_at}._"
    )
    lines.append("")
    lines.append("## Tier definitions (manifest)")
    lines.append("")
    for tier_key in ("1", "2", "3"):
        defn = manifest.tier_definitions.get(tier_key, "")
        if defn:
            lines.append(f"- **Tier {tier_key}** — {defn}")
    lines.append("")

    counts = _tier_counts(manifest.features)
    lines.append("## Summary")
    lines.append("")
    lines.append("| Tier | Count |")
    lines.append("| --- | ---: |")
    for tier in (1, 2, 3):
        lines.append(f"| Tier {tier} | {counts[tier]} |")
    lines.append(f"| **Total** | **{len(manifest.features)}** |")
    lines.append("")

    grouped = _group_by_category(manifest.features)
    lines.append("## Features by category")
    lines.append("")
    for category, rows in grouped.items():
        lines.append(f"### {category}")
        lines.append("")
        lines.append("| Tier | Feature | Evidence |")
        lines.append("| ---: | --- | --- |")
        for row in rows:
            evidence = _md_escape(row.evidence)
            name = _md_escape(row.name)
            lines.append(f"| {row.tier} | {name} | {evidence} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _md_escape(text: str) -> str:
    """Escape characters that would break a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def add_subparser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Register ``chimera tier-status`` on the top-level subparser action.

    Mirrors the late-binding pattern used by other wave-11 subcommands so
    a broken module never breaks ``chimera --help``.
    """
    parser = subparsers.add_parser(
        "tier-status",
        help=(
            "Print or regenerate the feature x tier readiness report "
            "(reads docs/tier-status.json)."
        ),
    )
    parser.add_argument(
        "--regen",
        action="store_true",
        help="Re-render docs/tier-status.md from the JSON manifest.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Dump the manifest as JSON to stdout (round-trips the file).",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Override the JSON manifest path (default: docs/tier-status.json).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "When used with --regen, write the rendered Markdown here "
            "(default: docs/tier-status.md)."
        ),
    )
    return cast(argparse.ArgumentParser, parser)


def run(args: argparse.Namespace) -> int:
    """Execute ``chimera tier-status`` and print or write the result."""
    manifest_path = getattr(args, "manifest", None)
    try:
        manifest = load_manifest(manifest_path)
    except FileNotFoundError as exc:
        print(f"chimera tier-status: {exc}", flush=True)
        return 2
    except ValueError as exc:
        print(f"chimera tier-status: invalid manifest -- {exc}", flush=True)
        return 2

    as_json = bool(getattr(args, "as_json", False))
    regen = bool(getattr(args, "regen", False))

    if as_json:
        print(json.dumps(manifest.to_dict(), indent=2))
        return 0

    if regen:
        out_override = getattr(args, "out", None)
        out_path = Path(out_override) if out_override else default_md_path()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_markdown(manifest), encoding="utf-8")
        counts = _tier_counts(manifest.features)
        print(
            f"wrote {out_path} -- {len(manifest.features)} features "
            f"(Tier 1: {counts[1]}, Tier 2: {counts[2]}, Tier 3: {counts[3]})"
        )
        return 0

    # Default: text table to stdout.
    print(render_text(manifest))
    return 0
