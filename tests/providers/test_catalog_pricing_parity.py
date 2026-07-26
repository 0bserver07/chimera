"""Chimera has TWO pricing tables; this pins that they cannot disagree.

``chimera.providers.cost.PRICING`` is the hand-maintained table that
``scripts/audit_model_pricing.py`` reconciles against models.dev. But
``chimera.providers.catalog`` carries its own ``cost=`` on every
:class:`~chimera.providers.catalog.ModelConfig`, and
:meth:`ProviderCatalog.register` pushes each one through
``register_model_cost`` — which **writes into PRICING at runtime**. Whichever
table is applied last is what actually bills.

That is not a hypothetical split. The DeepSeek V4 rate was corrected in
``cost.py`` to the published $0.435/$0.87 while ``catalog.py`` still carried the
stale $0.55/$2.19 placeholder; constructing a default catalog put the stale
number straight back, so ``calculate_cost("deepseek-v4-pro", …)`` billed the old
rate and only failed in a *full-suite* run, where some other test had built a
catalog first. A pricing correction is not applied until BOTH tables carry it.

The pristine hand table is re-executed from source here, so pollution from an
earlier test in the same session cannot make these assertions vacuous.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from chimera.providers.catalog import _BUILTIN_ENTRIES, ProviderCatalog
from chimera.providers.cost import PRICING, calculate_cost

_COST_PATH = (
    Path(__file__).resolve().parents[2] / "chimera" / "providers" / "cost.py"
)


def _pristine_pricing() -> dict[str, tuple[float, float]]:
    """The literal ``PRICING`` table as written in ``cost.py``.

    ``register_model_cost`` mutates the live module-level dict, so a test that
    read ``cost.PRICING`` directly could end up comparing the catalog against
    values the catalog itself just wrote. Re-executing the module from source
    yields the committed table regardless of what ran earlier.

    Returns:
        A fresh copy of the source-of-truth hand table.
    """
    spec = importlib.util.spec_from_file_location("_pristine_cost", _COST_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_pristine_cost"] = module
    try:
        spec.loader.exec_module(module)
        return dict(module.PRICING)
    finally:
        sys.modules.pop("_pristine_cost", None)


def _hand_rate(
    model: str, table: dict[str, tuple[float, float]]
) -> tuple[float, float] | None:
    """Resolve *model* against *table* with the production longest-prefix rule."""
    for prefix in sorted(table, key=len, reverse=True):
        if model.startswith(prefix):
            return table[prefix]
    return None


_PRISTINE = _pristine_pricing()
_OVERLAPPING = [
    (entry.model, entry.cost, _hand_rate(entry.model, _PRISTINE))
    for entry in _BUILTIN_ENTRIES
    if entry.cost is not None and _hand_rate(entry.model, _PRISTINE) is not None
]


class TestPristineTableIsReadable:
    def test_the_fixture_actually_loaded_the_hand_table(self) -> None:
        # Guard the guard: if re-execution silently produced an empty table,
        # every parity assertion below would pass without comparing anything.
        assert len(_PRISTINE) > 20
        assert "deepseek-chat" in _PRISTINE

    def test_some_catalog_entries_really_do_overlap(self) -> None:
        # Likewise: an empty overlap list would make the parametrized test a
        # no-op that reports green.
        assert len(_OVERLAPPING) >= 5


class TestCatalogAgreesWithHandTable:
    @pytest.mark.parametrize(
        ("model", "catalog_cost", "hand_cost"),
        _OVERLAPPING,
        ids=[m for m, _, _ in _OVERLAPPING],
    )
    def test_catalog_cost_matches_hand_pricing(
        self,
        model: str,
        catalog_cost: tuple[float, float],
        hand_cost: tuple[float, float],
    ) -> None:
        assert catalog_cost == pytest.approx(hand_cost), (
            f"{model}: catalog.py bills {catalog_cost} but cost.py says "
            f"{hand_cost}. Registering the catalog overwrites PRICING, so the "
            f"catalog value is what users are actually charged — fix both."
        )


class TestRegisteringTheCatalogDoesNotChangePricing:
    def test_catalog_may_add_prices_but_never_move_a_known_one(self) -> None:
        """The regression, stated as behaviour rather than as table equality.

        The catalog legitimately *adds* rates the hand table has no opinion on
        — namespaced ids like ``bedrock/claude-sonnet-4`` and
        ``azure/gpt-4o`` exist only here, and resolve to $0 until it registers
        them. What it must never do is *move* a price the hand table already
        resolves: that silently overrides the audited source of truth, which is
        how ``calculate_cost("deepseek-v4-pro", …)`` returned $0.14+$0.28 before
        a catalog existed and $0.55+$2.19 after, in the same process.
        """
        mtok = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        known = [
            e.model
            for e in _BUILTIN_ENTRIES
            if e.cost is not None and _hand_rate(e.model, _PRISTINE) is not None
        ]
        assert known, "no overlapping models — the assertion would be vacuous"
        before = {m: calculate_cost(m, mtok) for m in known}
        ProviderCatalog.default()
        after = {m: calculate_cost(m, mtok) for m in known}
        moved = {m: (before[m], after[m]) for m in known if before[m] != after[m]}
        assert moved == {}, (
            "registering the built-in catalog overrode hand-table prices "
            f"(before, after): {moved}"
        )

    def test_deepseek_v4_pro_bills_the_published_rate_after_catalog_load(
        self,
    ) -> None:
        # The exact failing case, pinned end to end.
        ProviderCatalog.default()
        mtok = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        assert calculate_cost("deepseek-v4-pro", mtok) == pytest.approx(0.435 + 0.87)
        assert PRICING["deepseek-v4-pro"] == (0.435, 0.87)
