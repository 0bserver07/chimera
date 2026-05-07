"""Tests for wave-11 C6: ``chimera tier-status`` feature x tier report.

Covers the manifest's shape, the renderer's outputs, and the CLI's
``--regen`` write-out behaviour.
"""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from chimera.cli import tier_status
from chimera.cli.tier_status import (
    Feature,
    TierManifest,
    add_subparser,
    default_json_path,
    default_md_path,
    load_manifest,
    render_markdown,
    render_text,
    run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_canonical_json() -> dict[str, object]:
    """Load ``docs/tier-status.json`` directly (raw, before validation)."""
    path = default_json_path()
    return json.loads(path.read_text(encoding="utf-8"))


def _make_manifest(*features: Feature) -> TierManifest:
    """Build a tiny synthetic manifest for renderer tests."""
    return TierManifest(
        schema_version=1,
        generated_at="2026-05-05",
        tier_definitions={
            "1": "Live-verified.",
            "2": "Tests pass with mocks.",
            "3": "Scaffolded.",
        },
        features=tuple(features),
    )


# ---------------------------------------------------------------------------
# Schema + content of docs/tier-status.json
# ---------------------------------------------------------------------------


def test_json_loads_valid() -> None:
    """``docs/tier-status.json`` is valid JSON with the expected schema fields."""
    raw = _read_canonical_json()
    assert isinstance(raw, dict)
    # Top-level required keys.
    for key in ("schema_version", "generated_at", "features"):
        assert key in raw, f"missing required key: {key}"
    assert isinstance(raw["features"], list)
    assert len(raw["features"]) > 0
    # Every feature has the required fields.
    for entry in raw["features"]:
        assert isinstance(entry, dict)
        assert set(entry.keys()) >= {"name", "tier", "category", "evidence"}
        assert isinstance(entry["name"], str) and entry["name"]
        assert isinstance(entry["category"], str) and entry["category"]
        assert isinstance(entry["evidence"], str)
        assert entry["tier"] in (1, 2, 3)


def test_load_manifest_returns_typed_object() -> None:
    manifest = load_manifest()
    assert isinstance(manifest, TierManifest)
    assert manifest.schema_version == 1
    assert manifest.features
    for f in manifest.features:
        assert isinstance(f, Feature)
        assert f.tier in (1, 2, 3)


def test_all_three_tiers_present() -> None:
    """At least one feature exists in each of tier 1, 2, 3."""
    manifest = load_manifest()
    tiers = {f.tier for f in manifest.features}
    assert tiers == {1, 2, 3}, (
        f"expected all three tiers represented; saw {sorted(tiers)}"
    )


def test_categories_covered() -> None:
    """At least 8 distinct categories appear in the manifest."""
    manifest = load_manifest()
    categories = {f.category for f in manifest.features}
    assert len(categories) >= 8, (
        f"expected >=8 categories; saw {len(categories)}: {sorted(categories)}"
    )


def test_manifest_has_at_least_50_features() -> None:
    """C6 brief asks for ~50 entries; assert a comfortable lower bound."""
    manifest = load_manifest()
    assert len(manifest.features) >= 50, (
        f"manifest has only {len(manifest.features)} features; "
        f"task brief asks for ~50."
    )


# ---------------------------------------------------------------------------
# Validation paths
# ---------------------------------------------------------------------------


def test_load_manifest_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(FileNotFoundError):
        load_manifest(missing)


def test_load_manifest_rejects_invalid_tier(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-01",
                "features": [
                    {"name": "x", "tier": 4, "category": "providers", "evidence": "e"}
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_manifest(bad)


def test_load_manifest_rejects_missing_field(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-01",
                "features": [{"name": "x", "tier": 1, "category": "providers"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_manifest(bad)


def test_load_manifest_rejects_non_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    with pytest.raises(ValueError):
        load_manifest(bad)


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def test_render_markdown_table_shape() -> None:
    """The Markdown output has the documented table headers and a row per feature."""
    manifest = load_manifest()
    md = render_markdown(manifest)
    # Tier definitions header.
    assert "## Tier definitions" in md
    # Summary table headers.
    assert "| Tier | Count |" in md
    # Per-category feature tables.
    assert "| Tier | Feature | Evidence |" in md
    # Each feature appears as a row in some category table.
    for f in manifest.features:
        assert f.name in md, f"feature missing from markdown: {f.name}"
    # Table-row count >= feature count (each feature is one data row;
    # header rows add to the total but pure feature names won't appear
    # in headers).
    feature_rows = sum(
        1 for line in md.splitlines() if line.startswith("| ") and "Feature" not in line
    )
    # Every feature contributes a data row + 3 summary rows + 1 totals row.
    assert feature_rows >= len(manifest.features) + 3


def test_render_markdown_contains_tier_definitions() -> None:
    manifest = load_manifest()
    md = render_markdown(manifest)
    assert "Tier 1" in md
    assert "Tier 2" in md
    assert "Tier 3" in md


def test_render_markdown_groups_categories_alphabetically() -> None:
    manifest = _make_manifest(
        Feature("Z feature", 1, "zeta", "zev"),
        Feature("A feature", 2, "alpha", "aev"),
        Feature("M feature", 3, "mu", "mev"),
    )
    md = render_markdown(manifest)
    alpha_pos = md.find("### alpha")
    mu_pos = md.find("### mu")
    zeta_pos = md.find("### zeta")
    assert 0 < alpha_pos < mu_pos < zeta_pos


def test_render_markdown_sorts_by_tier_within_category() -> None:
    manifest = _make_manifest(
        Feature("Late T1", 1, "providers", "ev1"),
        Feature("Late T3", 3, "providers", "ev3"),
        Feature("Late T2", 2, "providers", "ev2"),
    )
    md = render_markdown(manifest)
    # In the providers section, T1 row precedes T2 row precedes T3 row.
    t1_pos = md.find("Late T1")
    t2_pos = md.find("Late T2")
    t3_pos = md.find("Late T3")
    assert 0 < t1_pos < t2_pos < t3_pos


def test_render_markdown_escapes_pipes_in_evidence() -> None:
    manifest = _make_manifest(
        Feature("Pipe", 1, "providers", "a | b | c"),
    )
    md = render_markdown(manifest)
    # Pipes inside evidence are escaped so the table renders correctly.
    assert "a \\| b \\| c" in md


# ---------------------------------------------------------------------------
# Text renderer (default stdout)
# ---------------------------------------------------------------------------


def test_render_text_has_summary_line() -> None:
    manifest = load_manifest()
    text = render_text(manifest)
    assert "summary:" in text
    assert "Tier 1" in text
    assert "Tier 2" in text
    assert "Tier 3" in text


def test_render_text_includes_every_feature() -> None:
    manifest = load_manifest()
    text = render_text(manifest)
    for f in manifest.features:
        assert f.name in text


# ---------------------------------------------------------------------------
# CLI --regen
# ---------------------------------------------------------------------------


def test_regen_updates_file(tmp_path: Path) -> None:
    """Calling --regen writes a Markdown file at the requested path."""
    out_path = tmp_path / "tier-status.md"
    args = argparse.Namespace(
        regen=True,
        as_json=False,
        manifest=None,
        out=str(out_path),
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run(args)
    assert rc == 0
    assert out_path.exists()
    body = out_path.read_text(encoding="utf-8")
    assert "# Chimera tier-status" in body
    # Stdout reports the count.
    assert "wrote" in buf.getvalue()


def test_regen_with_custom_manifest(tmp_path: Path) -> None:
    """--manifest + --regen lets a caller render an arbitrary JSON file."""
    fake = tmp_path / "fake.json"
    fake.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-01-01",
                "tier_definitions": {
                    "1": "live",
                    "2": "tests",
                    "3": "scaffold",
                },
                "features": [
                    {
                        "name": "Anthropic provider",
                        "tier": 1,
                        "category": "providers",
                        "evidence": "test path",
                    },
                    {
                        "name": "OpenAI provider",
                        "tier": 2,
                        "category": "providers",
                        "evidence": "mocks",
                    },
                    {
                        "name": "Modal provider",
                        "tier": 3,
                        "category": "providers",
                        "evidence": "scaffold",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "tier-status.md"
    args = argparse.Namespace(
        regen=True,
        as_json=False,
        manifest=str(fake),
        out=str(out_path),
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run(args)
    assert rc == 0
    body = out_path.read_text(encoding="utf-8")
    assert "Anthropic provider" in body
    assert "OpenAI provider" in body
    assert "Modal provider" in body


# ---------------------------------------------------------------------------
# CLI --json
# ---------------------------------------------------------------------------


def test_run_json_round_trips() -> None:
    """``chimera tier-status --json`` dumps the manifest as JSON."""
    args = argparse.Namespace(
        regen=False,
        as_json=True,
        manifest=None,
        out=None,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run(args)
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["schema_version"] == 1
    assert isinstance(payload["features"], list)
    assert len(payload["features"]) >= 50


# ---------------------------------------------------------------------------
# CLI default (text)
# ---------------------------------------------------------------------------


def test_run_default_prints_text_table() -> None:
    args = argparse.Namespace(
        regen=False,
        as_json=False,
        manifest=None,
        out=None,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run(args)
    assert rc == 0
    out = buf.getvalue()
    assert "chimera tier-status" in out
    assert "CATEGORY" in out
    assert "TIER" in out
    assert "summary:" in out


def test_run_missing_manifest_returns_2(tmp_path: Path) -> None:
    args = argparse.Namespace(
        regen=False,
        as_json=False,
        manifest=str(tmp_path / "nope.json"),
        out=None,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run(args)
    assert rc == 2
    assert "tier-status" in buf.getvalue()


# ---------------------------------------------------------------------------
# argparse wiring (add_subparser)
# ---------------------------------------------------------------------------


def test_add_subparser_registers_flags() -> None:
    parser = argparse.ArgumentParser(prog="chimera")
    sub = parser.add_subparsers(dest="command")
    tier_parser = add_subparser(sub)
    assert tier_parser is not None
    # No flags = defaults.
    args = parser.parse_args(["tier-status"])
    assert args.command == "tier-status"
    assert args.regen is False
    assert args.as_json is False
    assert args.manifest is None
    assert args.out is None


def test_add_subparser_accepts_regen_and_json() -> None:
    parser = argparse.ArgumentParser(prog="chimera")
    sub = parser.add_subparsers(dest="command")
    add_subparser(sub)
    args = parser.parse_args([
        "tier-status",
        "--regen",
        "--manifest",
        "x.json",
        "--out",
        "y.md",
    ])
    assert args.regen is True
    assert args.manifest == "x.json"
    assert args.out == "y.md"

    args2 = parser.parse_args(["tier-status", "--json"])
    assert args2.as_json is True


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def test_default_paths_resolve_inside_repo() -> None:
    json_path = default_json_path()
    md_path = default_md_path()
    # Either the package install includes docs/, or the cwd-relative
    # fallback path is used. In either case the JSON path must point at
    # something sensible (suffix .json) and the md path at .md.
    assert json_path.suffix == ".json"
    assert md_path.suffix == ".md"


def test_module_exports() -> None:
    """Public API surface stays stable."""
    expected = {
        "Feature",
        "TierManifest",
        "add_subparser",
        "default_json_path",
        "default_md_path",
        "load_manifest",
        "render_markdown",
        "render_text",
        "run",
    }
    assert expected <= set(tier_status.__all__)
