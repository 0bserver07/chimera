#!/usr/bin/env python3
"""Reconcile Chimera's hand-maintained model pricing against the models.dev catalog.

The companion :mod:`scripts.generate_model_catalog` keeps the *generated*
fallback catalog (``chimera/providers/model_catalog.py``) fresh, and its
``--check`` mode guards that snapshot against upstream. But the numbers Chimera
actually bills live in the small **hand-maintained** table
``chimera.providers.cost.PRICING`` — and *that* table is the one that silently
goes stale. A vendor cuts a price, the public models.dev figure follows, and the
hand entry keeps quoting last year's rate with nobody the wiser.

This dev-only reconciler closes that gap. It compares every hand ``PRICING``
prefix against the public models.dev figure for the same model id and reports
where they diverge. It **never rewrites** pricing — hand corrections always win
over upstream (that is the whole design of
:func:`chimera.providers.cost.get_model_pricing`), so the tool's job is to
*surface* a divergence for a human to adjudicate, not to clobber a deliberate
override.

Override convention
-------------------
Some hand rates diverge from upstream **on purpose** — placeholders pending a
vendor rate sheet, cross-endpoint billing nuances, or local/open-weight families
billed at ``$0``. Those prefixes are listed in
:data:`chimera.providers.cost.PRICING_OVERRIDES`; this reconciler reads that set
and skips them, so a conscious divergence is never reported as drift. To silence
a newly-flagged entry, either correct the hand rate or add its prefix to
``PRICING_OVERRIDES`` with an inline reason — never edit this script.

First-party authority
---------------------
The models.dev catalog lists the same id under many providers, from the
manufacturer down to marked-up resellers. A hand rate that disagrees with a
*reseller* markup is not drift — it is the reseller's margin. So by default the
audit only compares against a **first-party** (manufacturer / lab) figure and
buckets reseller-only ids as "not authoritatively comparable". Pass
``--include-resellers`` to compare against whatever provider the catalog id
resolved to.

Usage::

    python scripts/audit_model_pricing.py               # audit vs the committed snapshot
    python scripts/audit_model_pricing.py --live         # audit vs a fresh models.dev fetch
    python scripts/audit_model_pricing.py --json          # machine-readable report
    python scripts/audit_model_pricing.py --include-resellers  # also compare reseller ids
    python scripts/audit_model_pricing.py --url URL       # override the source (implies --live)

Exit code is ``1`` when there is anything to resolve — a drifted rate **or an
expired placeholder** — and ``0`` when the hand table is clean. It is
intentionally **not** wired into CI: run it by hand when refreshing prices, or
adopt it as a scheduled guard later.

Two design points that are easy to get wrong, both learned the hard way:

* **Authority is per model, not per provider.** models.dev lists a model under
  every provider that serves it, so a first-party provider of *something* is not
  an authority on *this* model — ``alibaba-cn`` makes Qwen and resells GLM. See
  :data:`MODEL_VENDORS`.
* **A placeholder override must expire.** An override silences a prefix forever;
  that is correct for a permanent reason and wrong for "until the vendor
  publishes". See ``PRICING_PLACEHOLDERS`` in ``chimera.providers.cost``.

The default (offline) mode reconciles against the committed
``chimera/providers/model_catalog.py`` snapshot and touches no network, so it is
deterministic and safe to run anywhere. ``--live`` fetches
``https://models.dev/api.json`` through the generator's stdlib ``urllib`` path.
The comparison core (:func:`audit_pricing`) is a pure function over plain dicts,
so the test-suite exercises it against fixtures without any network access.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import textwrap
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import ModuleType

MODELS_DEV_URL = "https://models.dev/api.json"

# Repo root (parent of scripts/), so ``import chimera`` resolves when the script
# is run from a source checkout without an installed package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Prices are dollars-per-million tokens rounded to at most this many decimals
# (matching the generator's normalisation); compare at the same precision so
# float representation noise (0.075, 0.3 vs 0.30) never masquerades as drift.
_PRICE_DECIMALS = 6


@dataclass(frozen=True)
class Drift:
    """One hand-table prefix whose price disagrees with a first-party upstream.

    Attributes:
        prefix: The ``PRICING`` prefix / model id that was compared.
        hand: The hand ``(input, output)`` rate, dollars per million tokens.
        upstream: The models.dev ``(input, output)`` rate; ``output`` may be
            ``None`` when the source publishes an input-only (e.g. embedding)
            price.
        provider: The upstream provider id the figure was taken from, or
            ``None``.
        fields: Which fields drifted — a subset of ``{"input", "output"}``.
    """

    prefix: str
    hand: tuple[float, float]
    upstream: tuple[float, float | None]
    provider: str | None
    fields: tuple[str, ...]


@dataclass
class AuditReport:
    """The outcome of reconciling ``PRICING`` against an upstream catalog.

    The buckets partition every audited hand prefix exactly once (``drifts`` is
    the subset of the ``checked`` count that disagreed).

    Attributes:
        drifts: Prefixes whose audited hand rate disagrees with a first-party
            upstream figure.
        stale_placeholders: Prefixes marked as *temporary* placeholders
            (``PRICING_PLACEHOLDERS``) for which upstream has since published a
            first-party rate. The override is silencing a comparison that can
            now be made, so the reason has expired — whether or not the rates
            happen to agree.
        skipped_overrides: Prefixes skipped because they are marked as
            deliberate overrides (``PRICING_OVERRIDES``).
        reseller_only: Prefixes whose only exact upstream id is reseller-sourced
            (not authoritatively comparable) — informational, not drift.
        no_upstream: Audited prefixes with no exact upstream id to compare
            against (too new, or a Chimera-internal tag) — informational.
        checked: Count of prefixes compared against a first-party upstream.
    """

    drifts: list[Drift] = field(default_factory=list)
    stale_placeholders: list[Drift] = field(default_factory=list)
    skipped_overrides: list[str] = field(default_factory=list)
    reseller_only: list[str] = field(default_factory=list)
    no_upstream: list[str] = field(default_factory=list)
    checked: int = 0

    @property
    def has_drift(self) -> bool:
        """True when at least one audited prefix drifted from upstream."""
        return bool(self.drifts)

    @property
    def has_findings(self) -> bool:
        """True when anything needs resolving — drift or an expired placeholder.

        A stale placeholder counts: it is the failure mode that hid a real
        26%/152% DeepSeek overcharge behind a silenced comparison.
        """
        return bool(self.drifts) or bool(self.stale_placeholders)


def _round(value: float) -> float:
    """Round a price to the catalog's precision for tolerant comparison."""
    return round(float(value), _PRICE_DECIMALS)


def _as_price(value: Any) -> float | None:
    """Coerce an upstream cost field to ``float``, or ``None`` if non-numeric.

    Booleans are rejected (``isinstance(True, int)`` would otherwise slip
    through), matching the generator's ``_num`` normalisation.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# models.dev lists a model under *every* provider that serves it, so "first
# party" cannot be a property of the provider alone. ``alibaba-cn`` is the
# vendor of Qwen but merely a reseller of GLM; ``zai`` is the vendor of GLM and
# serves nothing else here. Authority is therefore a (model family, provider)
# relationship, and comparing a hand rate against whichever provider happens to
# publish the id will hand back a resale price as if it were the vendor's.
#
# This is not hypothetical. ``alibaba-cn`` lists ``glm-5.2`` at $1.10 / $3.851
# while the hand table carries Zhipu's $2.00 / $8.00, and with a provider-global
# authority test the auditor reported that as drift — instructing us to write
# Alibaba's markup into the table as a "correction" to Zhipu's own rate.
#
# Longest matching prefix wins, mirroring ``PRICING`` resolution. A family that
# is absent here is deliberately treated as *not* authoritatively comparable
# (bucketed ``reseller_only``) rather than falling back to the provider-global
# test, because that fallback is precisely the unsound comparison.
MODEL_VENDORS: dict[str, frozenset[str]] = {
    "claude": frozenset({"anthropic"}),
    "gpt-oss": frozenset({"openai"}),
    "gpt": frozenset({"openai"}),
    "o1": frozenset({"openai"}),
    "gemini": frozenset({"google"}),
    "gemma": frozenset({"google"}),
    "glm": frozenset({"zai", "zhipuai"}),
    "deepseek": frozenset({"deepseek"}),
    "grok": frozenset({"xai"}),
    "kimi": frozenset({"moonshotai", "moonshotai-cn"}),
    "qwen": frozenset({"alibaba", "alibaba-cn"}),
    "mistral": frozenset({"mistral"}),
}


def vendors_for(model: str) -> frozenset[str] | None:
    """The providers that are first-party *for this model*, or ``None``.

    Args:
        model: A hand-table prefix or full model id.

    Returns:
        The authoritative provider ids for the model's family under the longest
        matching :data:`MODEL_VENDORS` prefix, or ``None`` when the family is
        unmapped (no provider can be treated as authoritative for it).
    """
    best: str | None = None
    for prefix in MODEL_VENDORS:
        if model.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return MODEL_VENDORS[best] if best is not None else None


def _is_authoritative(
    prefix: str, provider: str | None, first_party: Collection[str] | None
) -> bool:
    """Whether *provider*'s rate for *prefix* may be treated as the vendor's.

    Args:
        prefix: The hand-table prefix being audited.
        provider: The upstream record's provider id, if any.
        first_party: ``None`` disables the gate entirely (``--include-resellers``),
            in which case every provider is accepted.

    Returns:
        ``True`` when the comparison is sound. With the gate enabled this
        requires *provider* to be a vendor of the model's family
        (:func:`vendors_for`), not merely a first-party vendor of something.
    """
    if first_party is None:
        return True
    if provider is None:
        return False
    vendors = vendors_for(prefix)
    if vendors is None:
        return False
    return provider in vendors


def _stale_placeholder(
    prefix: str,
    pricing: Mapping[str, tuple[float, float]],
    upstream: Mapping[str, Mapping[str, Any]],
    placeholders: Collection[str],
    first_party: Collection[str] | None,
) -> Drift | None:
    """Return a :class:`Drift` when a temporary placeholder has expired.

    A placeholder override is a promise to revisit: the hand rate is a stand-in
    "until the vendor publishes". This detects that the vendor *has* published,
    which retires the reason regardless of whether the numbers agree — an
    override that silences a comparison now available is the exact mechanism
    that hid a 26%/152% DeepSeek overcharge behind a green audit.

    Args:
        prefix: The hand-table prefix, already known to be in the override set.
        pricing: The hand table, for reporting the current stand-in rate.
        upstream: Model id -> record.
        placeholders: Prefixes whose override reason is temporary.
        first_party: Authoritative provider ids, or ``None`` to accept any.

    Returns:
        A :class:`Drift` describing hand-vs-upstream, or ``None`` when the
        prefix is not a placeholder or upstream still does not publish it.
    """
    if prefix not in placeholders:
        return None
    record = upstream.get(prefix)
    if record is None:
        return None
    up_in = _as_price(record.get("input"))
    if up_in is None:
        return None
    provider_raw = record.get("provider")
    provider = provider_raw if isinstance(provider_raw, str) else None
    if not _is_authoritative(prefix, provider, first_party):
        # Only a reseller lists it — not authoritative enough to retire a
        # placeholder that is waiting on the vendor's own rate sheet.
        return None
    up_out = _as_price(record.get("output"))
    hand_in, hand_out = pricing[prefix]
    fields = tuple(
        name
        for name, hand, up in (
            ("input", hand_in, up_in),
            ("output", hand_out, up_out),
        )
        if up is not None and _round(hand) != _round(up)
    )
    return Drift(
        prefix=prefix,
        hand=(hand_in, hand_out),
        upstream=(up_in, up_out),
        provider=provider,
        fields=fields,
    )


def audit_pricing(
    pricing: Mapping[str, tuple[float, float]],
    overrides: Collection[str],
    upstream: Mapping[str, Mapping[str, Any]],
    *,
    first_party: Collection[str] | None = None,
    placeholders: Collection[str] | None = None,
) -> AuditReport:
    """Reconcile a hand pricing table against an upstream catalog.

    For each prefix in *pricing*, in id order: a prefix listed in *overrides* is
    a deliberate divergence and is skipped; otherwise, if *upstream* carries a
    record under the exact same id, the ``input`` and ``output`` rates are
    compared and any mismatch is recorded as a :class:`Drift`. When *first_party*
    is given, a record from a provider outside that set is bucketed as
    ``reseller_only`` rather than compared (a hand rate disagreeing with a
    reseller markup is not drift). A prefix with no exact upstream id is reported
    as ``no_upstream``. The function is pure and never mutates its inputs, so it
    is trivially testable against fixtures.

    Args:
        pricing: The hand table, prefix -> ``(input, output)`` per Mtok. Pass
            :data:`chimera.providers.cost.PRICING`.
        overrides: Prefixes whose divergence is intentional and must not be
            flagged. Pass :data:`chimera.providers.cost.PRICING_OVERRIDES`.
        upstream: Model id -> record with at least ``input`` / ``output`` keys
            (a ``provider`` key is used for reporting and first-party gating).
            Both the committed ``MODEL_CATALOG`` and a freshly built catalog fit.
        first_party: Provider ids treated as authoritative. When ``None``, every
            provider is compared (reseller markups included).
        placeholders: Prefixes whose override reason is *temporary*. Pass
            :data:`chimera.providers.cost.PRICING_PLACEHOLDERS`. Any of these
            that upstream now publishes first-party is reported as a stale
            placeholder instead of being silently skipped.

    Returns:
        An :class:`AuditReport` partitioning every prefix into drifted, stale
        placeholder, override-skipped, reseller-only, or upstream-unmatched.
    """
    placeholder_set = frozenset(placeholders or ())
    report = AuditReport()
    for prefix in sorted(pricing):
        if prefix in overrides:
            stale = _stale_placeholder(
                prefix, pricing, upstream, placeholder_set, first_party
            )
            if stale is not None:
                report.stale_placeholders.append(stale)
            else:
                report.skipped_overrides.append(prefix)
            continue
        record = upstream.get(prefix)
        if record is None:
            report.no_upstream.append(prefix)
            continue
        up_in = _as_price(record.get("input"))
        if up_in is None:
            # Upstream has the id but no numeric input price — nothing to
            # compare against, so treat as unmatched rather than drift.
            report.no_upstream.append(prefix)
            continue
        provider_raw = record.get("provider")
        provider = provider_raw if isinstance(provider_raw, str) else None
        if not _is_authoritative(prefix, provider, first_party):
            report.reseller_only.append(prefix)
            continue
        report.checked += 1
        hand_in, hand_out = pricing[prefix]
        up_out = _as_price(record.get("output"))
        drifted: list[str] = []
        if _round(hand_in) != _round(up_in):
            drifted.append("input")
        # Only compare output when upstream publishes one (embeddings omit it).
        if up_out is not None and _round(hand_out) != _round(up_out):
            drifted.append("output")
        if drifted:
            report.drifts.append(
                Drift(
                    prefix=prefix,
                    hand=(hand_in, hand_out),
                    upstream=(up_in, up_out),
                    provider=provider,
                    fields=tuple(drifted),
                )
            )
    return report


def _price_str(value: float | None) -> str:
    """Render a price as ``$x`` per Mtok, or ``-`` when absent."""
    return "-" if value is None else f"${value:g}"


def format_text(report: AuditReport, *, source: str) -> str:
    """Render a human-readable audit report.

    Args:
        report: The reconciliation outcome from :func:`audit_pricing`.
        source: A short label for the compared-against source (a URL or path).

    Returns:
        A multi-line report: a per-prefix drift table, then a summary.
    """
    lines: list[str] = []
    lines.append(f"Hand-table pricing audit against {source}")
    lines.append(
        f"  checked {report.checked} · "
        f"skipped {len(report.skipped_overrides)} override(s) · "
        f"{len(report.reseller_only)} reseller-only · "
        f"{len(report.no_upstream)} without an upstream match"
    )
    if report.drifts:
        lines.append("")
        lines.append(f"DRIFT — {len(report.drifts)} hand price(s) disagree with first-party upstream:")
        for d in report.drifts:
            fields = "+".join(d.fields)
            prov = f" [{d.provider}]" if d.provider else ""
            lines.append(
                f"  {d.prefix}{prov}  ({fields})\n"
                f"      hand     in {_price_str(d.hand[0])}  out {_price_str(d.hand[1])}\n"
                f"      upstream in {_price_str(d.upstream[0])}  out {_price_str(d.upstream[1])}"
            )
        lines.append("")
        lines.append(
            "Resolve each: correct the hand rate in chimera/providers/cost.py, or\n"
            "if the divergence is deliberate add the prefix to PRICING_OVERRIDES "
            "with a reason."
        )
    elif not report.stale_placeholders:
        lines.append("")
        lines.append("OK: every audited hand price matches first-party upstream.")
    if report.stale_placeholders:
        lines.append("")
        lines.append(
            f"STALE PLACEHOLDER — {len(report.stale_placeholders)} override(s) are "
            "waiting on a rate\nthe vendor has since published:"
        )
        for d in report.stale_placeholders:
            prov = f" [{d.provider}]" if d.provider else ""
            verdict = (
                "rates agree — just retire the marker"
                if not d.fields
                else f"AND the rate is wrong ({'+'.join(d.fields)})"
            )
            lines.append(
                f"  {d.prefix}{prov}  {verdict}\n"
                f"      hand     in {_price_str(d.hand[0])}  out {_price_str(d.hand[1])}\n"
                f"      upstream in {_price_str(d.upstream[0])}  out {_price_str(d.upstream[1])}"
            )
        lines.append("")
        lines.append(
            "A placeholder silences the audit. Once upstream publishes, the reason\n"
            "has expired: correct the hand rate if it disagrees, then drop the prefix\n"
            "from PRICING_PLACEHOLDERS (and from PRICING_OVERRIDES unless a separate,\n"
            "permanent reason applies) so the entry is compared from now on."
        )
    if report.reseller_only:
        lines.append("")
        lines.append(
            "Not authoritatively compared (only a reseller offers the exact id; "
            "pass --include-resellers to compare anyway):"
        )
        lines.append(textwrap.fill(
            ", ".join(report.reseller_only), width=88,
            initial_indent="  ", subsequent_indent="  ",
        ))
    return "\n".join(lines)


def format_json(report: AuditReport, *, source: str) -> str:
    """Render the audit report as a stable JSON object.

    Args:
        report: The reconciliation outcome from :func:`audit_pricing`.
        source: A short label for the compared-against source.

    Returns:
        A JSON string with ``source``, ``summary``, ``drifts``, and the
        informational ``reseller_only`` / ``no_upstream`` id lists.
    """
    payload = {
        "source": source,
        "summary": {
            "checked": report.checked,
            "drift": len(report.drifts),
            "stale_placeholders": len(report.stale_placeholders),
            "skipped_overrides": len(report.skipped_overrides),
            "reseller_only": len(report.reseller_only),
            "no_upstream": len(report.no_upstream),
        },
        "drifts": [
            {
                "prefix": d.prefix,
                "fields": list(d.fields),
                "hand": {"input": d.hand[0], "output": d.hand[1]},
                "upstream": {"input": d.upstream[0], "output": d.upstream[1]},
                "provider": d.provider,
            }
            for d in report.drifts
        ],
        "stale_placeholders": [
            {
                "prefix": d.prefix,
                "fields": list(d.fields),
                "hand": {"input": d.hand[0], "output": d.hand[1]},
                "upstream": {"input": d.upstream[0], "output": d.upstream[1]},
                "provider": d.provider,
            }
            for d in report.stale_placeholders
        ],
        "reseller_only": list(report.reseller_only),
        "no_upstream": list(report.no_upstream),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def load_offline_upstream() -> dict[str, dict[str, Any]]:
    """Load the committed models.dev snapshot as the comparison source.

    Returns:
        The ``MODEL_CATALOG`` mapping (model id -> record) from
        ``chimera/providers/model_catalog.py``. Network-free and deterministic.
    """
    from chimera.providers.model_catalog import MODEL_CATALOG

    return dict(MODEL_CATALOG)


def _load_generator() -> ModuleType:
    """Import the sibling ``generate_model_catalog.py`` as a module.

    ``scripts/`` is not an importable package, so the generator (which owns the
    stdlib ``urllib`` fetch, the models.dev normalisation, and the canonical
    first-party provider set) is loaded by path — a single source of truth.

    Returns:
        The imported generator module.

    Raises:
        RuntimeError: If the generator script cannot be located or loaded.
    """
    path = Path(__file__).resolve().parent / "generate_model_catalog.py"
    spec = importlib.util.spec_from_file_location("generate_model_catalog", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load generator module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_live_upstream(url: str = MODELS_DEV_URL) -> dict[str, dict[str, Any]]:
    """Fetch models.dev live and build the comparison source.

    Reuses the generator's ``fetch_json`` + ``build_catalog`` so the live
    figures pass through the same first-party collision resolution and
    normalisation as the committed snapshot.

    Args:
        url: Source catalog URL. Defaults to :data:`MODELS_DEV_URL`.

    Returns:
        Model id -> normalised record, freshly fetched.
    """
    gen = _load_generator()
    return dict(gen.build_catalog(gen.fetch_json(url)))


def _first_party_set() -> frozenset[str]:
    """The generator's canonical first-party provider set (single source)."""
    return frozenset(_load_generator().FIRST_PARTY)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code (``1`` on drift)."""
    parser = argparse.ArgumentParser(
        description="Reconcile the hand-maintained PRICING table against models.dev.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="fetch models.dev live instead of using the committed snapshot",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="source catalog URL (implies --live)",
    )
    parser.add_argument(
        "--include-resellers",
        action="store_true",
        help="also compare ids whose only upstream is a reseller markup",
    )
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    from chimera.providers.cost import (
        PRICING,
        PRICING_OVERRIDES,
        PRICING_PLACEHOLDERS,
    )

    if args.live or args.url:
        source = args.url or MODELS_DEV_URL
        try:
            upstream = load_live_upstream(source)
        except Exception as exc:  # pragma: no cover - network failure path
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        source = "chimera/providers/model_catalog.py (committed snapshot)"
        upstream = load_offline_upstream()

    first_party = None if args.include_resellers else _first_party_set()
    report = audit_pricing(
        PRICING,
        PRICING_OVERRIDES,
        upstream,
        first_party=first_party,
        placeholders=PRICING_PLACEHOLDERS,
    )

    if args.json:
        print(format_json(report, source=source))
    else:
        print(format_text(report, source=source))

    return 1 if report.has_findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
