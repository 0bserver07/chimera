"""Tests for the generated model catalog and its cost.py fallback wiring.

Covers three things:

* The committed :mod:`chimera.providers.model_catalog` imports and is a large,
  well-shaped data module.
* :func:`chimera.providers.cost.get_model_pricing` consults the hand-maintained
  ``PRICING`` table first (explicit overrides win) and only falls back to the
  generated catalog for models the hand table doesn't cover.
* The generator (``scripts/generate_model_catalog.py``) builds the catalog with
  first-party collision resolution and its ``--check`` mode detects drift while
  ignoring the volatile generation-date line — all with an injected fetch, so
  **no test touches the network**.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from chimera.providers.cost import (
    PRICING,
    calculate_cost,
    get_model_pricing,
)
from chimera.providers.model_catalog import MODEL_CATALOG

_RECORD_KEYS = {"input", "output", "cache_read", "cache_write", "context", "provider"}

# ---------------------------------------------------------------------------
# Load the generator script (scripts/ is not an importable package).
# ---------------------------------------------------------------------------
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_model_catalog.py"
_spec = importlib.util.spec_from_file_location("generate_model_catalog", _SCRIPT)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


# A minimal models.dev-shaped payload for the generator tests. Exercises
# first-party collision resolution, output-less (embedding) models, and the
# no-cost filter.
FAKE_API: dict[str, Any] = {
    "anthropic": {
        "models": {
            "claude-x": {
                "cost": {"input": 5, "output": 25, "cache_read": 0.5, "cache_write": 6.25},
                "limit": {"context": 200000},
            },
        },
    },
    "reseller-z": {  # non-first-party: same id, marked-up price, must lose
        "models": {
            "claude-x": {
                "cost": {"input": 9, "output": 40},
                "limit": {"context": 100000},
            },
        },
    },
    "openai": {
        "models": {
            "gpt-x": {"cost": {"input": 1, "output": 2}, "limit": {"context": 8000}},
            "embed-x": {"cost": {"input": 0.1}, "limit": {"context": 8000}},  # no output
            "no-price-x": {"limit": {"context": 8000}},  # no cost -> excluded
        },
    },
}


# ---------------------------------------------------------------------------
# Committed generated module
# ---------------------------------------------------------------------------
class TestGeneratedModule:
    def test_imports_and_is_nontrivially_large(self) -> None:
        assert isinstance(MODEL_CATALOG, dict)
        # The real models.dev catalog carries thousands of models; guard against
        # a truncated/empty regeneration.
        assert len(MODEL_CATALOG) >= 1000

    def test_every_record_has_the_expected_shape(self) -> None:
        for model_id, record in MODEL_CATALOG.items():
            assert isinstance(model_id, str) and model_id
            assert set(record) == _RECORD_KEYS, model_id
            # input is always numeric (the generator filters on it).
            assert isinstance(record["input"], (int, float))
            assert isinstance(record["provider"], str) and record["provider"]

    def test_stable_entry_gpt_4o(self) -> None:
        # gpt-4o is a well-known, stable OpenAI id resolved to its first party.
        record = MODEL_CATALOG["gpt-4o"]
        assert record["provider"] == "openai"
        assert record["input"] == 2.5
        assert record["output"] == 10
        assert isinstance(record["context"], int) and record["context"] > 0


# ---------------------------------------------------------------------------
# cost.py read-path integration
# ---------------------------------------------------------------------------
class TestPricingResolution:
    def test_hand_dict_override_takes_precedence(self) -> None:
        # glm-5.2 exists in BOTH the hand table ($2.00/$8.00, Zhipu's own rate)
        # and the generated catalog — but the catalog row comes from alibaba-cn,
        # which RESELLS GLM at its own markup ($1.10/$3.851). The hand value
        # must win: this is the whole reason hand entries exist, and copying the
        # catalog figure in would replace the vendor's rate with a reseller's.
        assert MODEL_CATALOG["glm-5.2"]["provider"] == "alibaba-cn"
        assert MODEL_CATALOG["glm-5.2"]["input"] == 1.10  # reseller value...
        assert PRICING["glm-5.2"] == (2.0, 8.0)  # ...hand value differs
        assert get_model_pricing("glm-5.2") == (2.0, 8.0)

    def test_hand_and_catalog_agree_where_both_are_first_party(self) -> None:
        # The complement: where the catalog row IS the vendor's own, the two
        # sources must not disagree. deepseek-chat was $0.27/$1.10 by hand
        # against DeepSeek's published $0.14/$0.28 until 2026-07-25.
        assert MODEL_CATALOG["deepseek-chat"]["provider"] == "deepseek"
        assert MODEL_CATALOG["deepseek-chat"]["input"] == 0.14
        assert PRICING["deepseek-chat"] == (0.14, 0.28)
        assert get_model_pricing("deepseek-chat") == (0.14, 0.28)

    def test_hand_dict_models_unchanged(self) -> None:
        # A representative hand-table model resolves exactly as before.
        assert get_model_pricing("gpt-4o") == (2.50, 10.0)
        assert get_model_pricing("claude-opus-4-7") == (5.0, 25.0)

    def test_catalog_fallback_for_non_hand_model(self) -> None:
        # gpt-4-turbo is NOT covered by any hand prefix but IS in the catalog.
        assert all(not "gpt-4-turbo".startswith(prefix) for prefix in PRICING)
        assert get_model_pricing("gpt-4-turbo") == (10.0, 30.0)
        # And the dollar cost flows through calculate_cost.
        cost = calculate_cost("gpt-4-turbo", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
        assert cost == pytest.approx(40.0)  # 10 + 30

    def test_catalog_fallback_matches_by_prefix(self) -> None:
        # A dated/suffixed id resolves through its base catalog entry, mirroring
        # the hand table's longest-prefix semantics.
        assert get_model_pricing("gpt-4-turbo-zzz-unreleased") == (10.0, 30.0)

    def test_unknown_model_returns_none_and_zero_cost(self) -> None:
        assert get_model_pricing("totally-unknown-model-xyz-9000") is None
        assert calculate_cost("totally-unknown-model-xyz-9000", {"input_tokens": 1000}) == 0.0

    def test_ollama_style_id_still_zero(self) -> None:
        # Grandfathered behaviour: an ollama-style tag matches nothing and bills $0.
        assert get_model_pricing("llama3.1:8b") is None
        assert calculate_cost("llama3.1:8b", {"input_tokens": 10_000, "output_tokens": 5_000}) == 0.0


# ---------------------------------------------------------------------------
# Generator: build + --check (all with an injected fetch — no network)
# ---------------------------------------------------------------------------
class TestGenerator:
    def test_build_catalog_first_party_precedence(self) -> None:
        catalog = gen.build_catalog(FAKE_API)
        record = catalog["claude-x"]
        assert record["provider"] == "anthropic"  # not reseller-z
        assert record["input"] == 5  # first-party rate, not the $9 markup
        assert record["cache_write"] == 6.25

    def test_build_catalog_filters_and_normalises(self) -> None:
        catalog = gen.build_catalog(FAKE_API)
        assert "no-price-x" not in catalog  # no cost dict -> excluded
        assert catalog["embed-x"]["output"] is None  # missing output -> None
        assert catalog["gpt-x"]["provider"] == "openai"

    def test_generate_roundtrips_with_check(self, tmp_path: Path) -> None:
        module = gen.generate(fetch=lambda: FAKE_API, date="2026-01-01")
        assert 'MODEL_CATALOG' in module and '"claude-x"' in module
        target = tmp_path / "model_catalog.py"
        target.write_text(module)
        ok, diff = gen.check(target, fetch=lambda: FAKE_API)
        assert ok, diff

    def test_check_detects_drift(self, tmp_path: Path) -> None:
        target = tmp_path / "model_catalog.py"
        target.write_text(gen.generate(fetch=lambda: FAKE_API, date="2026-01-01"))
        mutated = copy.deepcopy(FAKE_API)
        mutated["anthropic"]["models"]["claude-x"]["cost"]["input"] = 7
        ok, diff = gen.check(target, fetch=lambda: mutated)
        assert not ok
        assert diff  # non-empty unified diff

    def test_check_ignores_generation_date(self, tmp_path: Path) -> None:
        # A date-only difference between committed and regenerated is NOT drift.
        target = tmp_path / "model_catalog.py"
        target.write_text(gen.generate(fetch=lambda: FAKE_API, date="2000-01-01"))
        ok, _ = gen.check(target, fetch=lambda: FAKE_API)  # check regenerates w/ today's date
        assert ok

    def test_render_is_deterministic_and_sorted(self) -> None:
        catalog = gen.build_catalog(FAKE_API)
        text = gen.render_module(catalog, source="https://example/api.json", date="2026-01-01")
        ids = [
            line.split('"')[1]
            for line in text.splitlines()
            if line.startswith('    "')
        ]
        assert ids == sorted(ids)
        assert ids == ["claude-x", "embed-x", "gpt-x"]
