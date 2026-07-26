"""Tests for the hand-pricing reconciler ``scripts/audit_model_pricing.py``.

The reconciler compares ``chimera.providers.cost.PRICING`` against the public
models.dev figures and reports where a hand rate has drifted — while never
flagging a prefix marked as a deliberate override
(``chimera.providers.cost.PRICING_OVERRIDES``). Coverage:

* the pure :func:`audit_pricing` core over small fixtures — drift detection,
  override preservation, first-party gating, output-less models, float
  tolerance, and the informational buckets;
* report rendering (text + JSON) and the CLI exit code;
* a **network-free canary** proving the audit catches the real, in-repo drift
  of the DeepSeek hand rates against the committed models.dev snapshot.

Every test runs offline: the pure core takes injected dicts, and the canary
reads the committed ``MODEL_CATALOG`` — no test touches the network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from chimera.providers.cost import PRICING, PRICING_OVERRIDES, PRICING_PLACEHOLDERS
from chimera.providers.model_catalog import MODEL_CATALOG

# ---------------------------------------------------------------------------
# Load the reconciler script (scripts/ is not an importable package).
# ---------------------------------------------------------------------------
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_model_pricing.py"
_spec = importlib.util.spec_from_file_location("audit_model_pricing", _SCRIPT)
assert _spec is not None and _spec.loader is not None
aud = importlib.util.module_from_spec(_spec)
# Register before exec: the module's dataclasses resolve string annotations via
# ``sys.modules[cls.__module__]`` at class-creation time.
sys.modules["audit_model_pricing"] = aud
_spec.loader.exec_module(aud)


# A models.dev-shaped comparison source. Exercises vendor vs reseller
# provenance, an output-less (embedding) record, a record without a numeric
# input, and a float-noise record.
#
# Prefixes deliberately sit in REAL ``MODEL_VENDORS`` families (gpt-* -> openai,
# gemini-* -> google), because authority is model-scoped: a provider is
# authoritative only for models it actually makes. A synthetic name in no family
# is uncomparable by design, which is what ``unmapped-*`` pins below.
FAKE_UPSTREAM: dict[str, dict[str, Any]] = {
    "gpt-match": {"input": 1.0, "output": 2.0, "provider": "openai"},
    "gemini-drift": {"input": 9.0, "output": 3.0, "provider": "google"},
    "gpt-drift-in": {"input": 9.0, "output": 2.0, "provider": "openai"},
    "gpt-reseller": {"input": 5.0, "output": 9.0, "provider": "reseller-x"},
    "gpt-embed": {"input": 0.1, "output": None, "provider": "openai"},
    "gpt-noinput": {"input": None, "output": 2.0, "provider": "openai"},
    "gpt-round": {"input": 0.3, "output": 1.1, "provider": "openai"},
    "gpt-ovr": {"input": 1.0, "output": 1.0, "provider": "openai"},
    # A first-party provider (openai) serving a model from a family it does not
    # own — the reseller-masquerading-as-vendor case.
    "unmapped-model": {"input": 4.0, "output": 8.0, "provider": "openai"},
}
FAKE_FIRST_PARTY = frozenset({"openai", "google"})
FAKE_HAND: dict[str, tuple[float, float]] = {
    "gpt-match": (1.0, 2.0),      # equals the vendor's upstream -> clean
    "gemini-drift": (1.0, 2.0),   # vendor disagrees -> DRIFT (input+output)
    "gpt-drift-in": (1.0, 2.0),   # vendor input disagrees -> DRIFT (input)
    "gpt-reseller": (1.0, 2.0),   # only a reseller offers it -> reseller_only
    "gpt-embed": (0.1, 88.0),     # input matches; upstream has no output -> clean
    "gpt-noinput": (1.0, 2.0),    # upstream input non-numeric -> no_upstream
    "gpt-round": (0.30, 1.10),    # float noise vs 0.3 / 1.1 -> clean
    "gpt-missing": (1.0, 2.0),    # no upstream id at all -> no_upstream
    "gpt-ovr": (777.0, 888.0),    # marked override -> skipped despite wild gap
    "unmapped-model": (1.0, 2.0), # family has no known vendor -> not comparable
}
FAKE_OVERRIDES = frozenset({"gpt-ovr"})


class TestAuditCore:
    def test_first_party_drift_is_flagged(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        drifted = {d.prefix for d in report.drifts}
        assert drifted == {"gemini-drift", "gpt-drift-in"}

    def test_drift_records_which_fields_moved(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        by_prefix = {d.prefix: d for d in report.drifts}
        assert by_prefix["gemini-drift"].fields == ("input", "output")
        assert by_prefix["gpt-drift-in"].fields == ("input",)
        # The Drift carries both sides + provenance for the report.
        assert by_prefix["gemini-drift"].hand == (1.0, 2.0)
        assert by_prefix["gemini-drift"].upstream == (9.0, 3.0)
        assert by_prefix["gemini-drift"].provider == "google"

    def test_override_is_never_flagged(self) -> None:
        # ``ovr`` diverges wildly (777/888 vs 1/1) but is a marked override:
        # it must land in skipped_overrides and never in drifts. This is the
        # hand-corrections-win guarantee.
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        assert "gpt-ovr" in report.skipped_overrides
        assert all(d.prefix != "gpt-ovr" for d in report.drifts)

    def test_reseller_only_ids_are_gated_by_default(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        # Two ways to be non-authoritative: a reseller provider, or a provider
        # that is first-party for *other* families but not this model's.
        assert report.reseller_only == ["gpt-reseller", "unmapped-model"]
        assert all(d.prefix != "gpt-reseller" for d in report.drifts)

    def test_include_resellers_compares_reseller_ids(self) -> None:
        # With no first-party gate, the reseller id IS compared and drifts.
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=None
        )
        assert report.reseller_only == []
        assert "gpt-reseller" in {d.prefix for d in report.drifts}

    def test_missing_and_non_numeric_input_go_to_no_upstream(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        assert report.no_upstream == ["gpt-missing", "gpt-noinput"]

    def test_output_only_compared_when_upstream_publishes_one(self) -> None:
        # ``embed`` upstream has output=None; the hand output (88.0) differs but
        # must not drift — only the (matching) input is comparable.
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        assert all(d.prefix != "gpt-embed" for d in report.drifts)

    def test_float_noise_is_not_drift(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        assert all(d.prefix != "gpt-round" for d in report.drifts)

    def test_checked_count_and_has_drift(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        # Compared: gpt-match, gemini-drift, gpt-drift-in, embed, round.
        assert report.checked == 5
        assert report.has_drift is True

    def test_clean_table_has_no_drift(self) -> None:
        clean_hand = {"gpt-match": (1.0, 2.0), "gpt-ovr": (1.0, 1.0)}
        report = aud.audit_pricing(
            clean_hand, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        assert report.has_drift is False
        assert report.drifts == []

    def test_audit_does_not_mutate_inputs(self) -> None:
        hand_copy = dict(FAKE_HAND)
        aud.audit_pricing(FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY)
        assert FAKE_HAND == hand_copy


class TestModelScopedAuthority:
    """Authority is a (model family, provider) relationship, not a provider set.

    models.dev lists a model under every provider that serves it. Treating
    "provider is first-party" as "provider is authoritative for this model"
    compares a hand rate against a *resale* price — and the report then
    instructs the reader to write the markup in as a correction.
    """

    def test_vendor_of_the_family_is_authoritative(self) -> None:
        assert aud.vendors_for("gpt-4o") == frozenset({"openai"})
        assert aud.vendors_for("glm-5.2") == frozenset({"zai", "zhipuai"})
        assert aud.vendors_for("deepseek-v4-pro") == frozenset({"deepseek"})

    def test_longest_prefix_wins(self) -> None:
        # ``gpt-oss`` is open-weight OpenAI; it must not resolve via bare ``gpt``
        # by accident of ordering.
        assert aud.vendors_for("gpt-oss-120b") == aud.MODEL_VENDORS["gpt-oss"]

    def test_unmapped_family_is_never_authoritative(self) -> None:
        assert aud.vendors_for("wholly-unknown-model") is None
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        # openai serves it, and openai IS first-party — but not for this family.
        assert "unmapped-model" in report.reseller_only
        assert all(d.prefix != "unmapped-model" for d in report.drifts)

    def test_first_party_provider_of_another_family_is_a_reseller(self) -> None:
        # THE REGRESSION: alibaba-cn is first-party (it makes Qwen) and lists
        # glm-5.2 at its own markup. Comparing GLM against it reported drift and
        # told us to replace Zhipu's $2.00/$8.00 with Alibaba's $1.10/$3.851.
        upstream = {"glm-5.2": {"input": 1.10, "output": 3.851, "provider": "alibaba-cn"}}
        first_party = frozenset({"alibaba-cn", "zai"})
        report = aud.audit_pricing(
            {"glm-5.2": (2.0, 8.0)}, frozenset(), upstream, first_party=first_party
        )
        assert report.drifts == []
        assert report.reseller_only == ["glm-5.2"]

    def test_the_actual_vendor_is_still_compared(self) -> None:
        # Same model, same numbers — but published by Zhipu, so it IS drift.
        upstream = {"glm-5.2": {"input": 1.10, "output": 3.851, "provider": "zai"}}
        report = aud.audit_pricing(
            {"glm-5.2": (2.0, 8.0)}, frozenset(), upstream,
            first_party=frozenset({"alibaba-cn", "zai"}),
        )
        assert {d.prefix for d in report.drifts} == {"glm-5.2"}

    def test_include_resellers_still_bypasses_the_gate(self) -> None:
        upstream = {"glm-5.2": {"input": 1.10, "output": 3.851, "provider": "alibaba-cn"}}
        report = aud.audit_pricing(
            {"glm-5.2": (2.0, 8.0)}, frozenset(), upstream, first_party=None
        )
        assert {d.prefix for d in report.drifts} == {"glm-5.2"}


class TestStalePlaceholders:
    """A temporary override must stop being silent once the vendor publishes."""

    def test_placeholder_with_upstream_is_flagged_even_when_rates_agree(self) -> None:
        report = aud.audit_pricing(
            {"gpt-match": (1.0, 2.0)}, frozenset({"gpt-match"}), FAKE_UPSTREAM,
            first_party=FAKE_FIRST_PARTY, placeholders=frozenset({"gpt-match"}),
        )
        stale = {d.prefix for d in report.stale_placeholders}
        assert stale == {"gpt-match"}
        # Rates agree, so no field is listed — the finding is "retire the marker".
        assert report.stale_placeholders[0].fields == ()
        assert report.skipped_overrides == []  # bucketed once, not twice

    def test_placeholder_with_wrong_rate_reports_the_fields(self) -> None:
        report = aud.audit_pricing(
            {"gemini-drift": (1.0, 2.0)}, frozenset({"gemini-drift"}), FAKE_UPSTREAM,
            first_party=FAKE_FIRST_PARTY, placeholders=frozenset({"gemini-drift"}),
        )
        assert report.stale_placeholders[0].fields == ("input", "output")
        assert report.stale_placeholders[0].upstream == (9.0, 3.0)

    def test_permanent_override_stays_silent(self) -> None:
        # An override that is NOT a placeholder keeps its exemption forever,
        # even with an exact upstream match — local $0 models, bridge billing.
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM,
            first_party=FAKE_FIRST_PARTY, placeholders=frozenset(),
        )
        assert "gpt-ovr" in report.skipped_overrides
        assert report.stale_placeholders == []

    def test_placeholder_without_upstream_is_not_stale(self) -> None:
        # Still waiting on the vendor — the reason has not expired.
        report = aud.audit_pricing(
            {"gpt-missing": (1.0, 2.0)}, frozenset({"gpt-missing"}), FAKE_UPSTREAM,
            first_party=FAKE_FIRST_PARTY, placeholders=frozenset({"gpt-missing"}),
        )
        assert report.stale_placeholders == []
        assert report.skipped_overrides == ["gpt-missing"]

    def test_reseller_listing_does_not_expire_a_placeholder(self) -> None:
        # The GLM case again: a placeholder waiting on Zhipu is not retired by
        # Alibaba deciding to resell the model.
        upstream = {"glm-5.2": {"input": 1.10, "output": 3.851, "provider": "alibaba-cn"}}
        report = aud.audit_pricing(
            {"glm-5.2": (2.0, 8.0)}, frozenset({"glm-5.2"}), upstream,
            first_party=frozenset({"alibaba-cn", "zai"}),
            placeholders=frozenset({"glm-5.2"}),
        )
        assert report.stale_placeholders == []
        assert report.skipped_overrides == ["glm-5.2"]

    def test_text_report_names_the_stale_prefix_and_the_fix(self) -> None:
        report = aud.audit_pricing(
            {"gpt-match": (1.0, 2.0)}, frozenset({"gpt-match"}), FAKE_UPSTREAM,
            first_party=FAKE_FIRST_PARTY, placeholders=frozenset({"gpt-match"}),
        )
        text = aud.format_text(report, source="fixture")
        assert "STALE PLACEHOLDER" in text
        assert "gpt-match" in text
        assert "PRICING_PLACEHOLDERS" in text
        assert "OK:" not in text  # never claim clean while a finding stands


class TestReportRendering:
    def test_text_report_flags_and_summarises(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        text = aud.format_text(report, source="fixture")
        assert "DRIFT" in text
        assert "gemini-drift" in text
        assert "gpt-reseller" in text  # surfaced in the reseller-only note
        assert "skipped 1 override" in text

    def test_text_report_ok_when_clean(self) -> None:
        clean = aud.audit_pricing(
            {"gpt-match": (1.0, 2.0)}, frozenset(), FAKE_UPSTREAM,
            first_party=FAKE_FIRST_PARTY,
        )
        text = aud.format_text(clean, source="fixture")
        assert "OK:" in text
        assert "DRIFT" not in text

    def test_json_report_is_valid_and_shaped(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        payload = json.loads(aud.format_json(report, source="fixture"))
        assert payload["source"] == "fixture"
        assert payload["summary"]["drift"] == 2
        assert payload["summary"]["skipped_overrides"] == 1
        assert {d["prefix"] for d in payload["drifts"]} == {"gemini-drift", "gpt-drift-in"}
        assert "gpt-reseller" in payload["reseller_only"]


class TestCli:
    def test_main_offline_is_clean_and_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Offline mode reconciles the REAL hand table against the committed
        # snapshot. It is currently reconciled — no drift and no expired
        # placeholder — so main() must return 0. If this starts failing, a hand
        # rate has genuinely gone stale: read the report, don't relax the test.
        rc = aud.main([])
        out = capsys.readouterr().out
        assert rc == 0, f"audit reported findings:\n{out}"
        assert "OK:" in out

    def test_main_json_flag_emits_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = aud.main(["--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["summary"]["drift"] == 0
        assert payload["summary"]["stale_placeholders"] == 0
        assert payload["summary"]["checked"] >= 1  # it really did compare things

    def test_exit_code_covers_stale_placeholders_not_just_drift(self) -> None:
        # A placeholder whose rates AGREE produces zero drift but must still
        # fail the run — otherwise an expired marker stays silent forever, which
        # is the exact hole that hid the DeepSeek V4 overcharge.
        hand = {"gpt-match": (1.0, 2.0)}
        report = aud.audit_pricing(
            hand, frozenset({"gpt-match"}), FAKE_UPSTREAM,
            first_party=FAKE_FIRST_PARTY, placeholders=frozenset({"gpt-match"}),
        )
        assert report.has_drift is False
        assert report.has_findings is True


class TestOverrideConvention:
    def test_every_override_is_a_real_pricing_prefix(self) -> None:
        # A marker for a prefix that isn't in PRICING is dead weight — guard it.
        assert PRICING_OVERRIDES <= set(PRICING)

    def test_deepseek_rates_match_first_party_upstream(self) -> None:
        # REGRESSION (network-free): the DeepSeek hand rates were $0.27/$1.10
        # (chat) and $0.55/$2.19 (reasoner) while DeepSeek's own published
        # figure — and the committed first-party snapshot — said $0.14/$0.28.
        # Both are deprecated aliases of deepseek-v4-flash and share its rate.
        assert PRICING["deepseek-chat"] == (0.14, 0.28)
        assert PRICING["deepseek-reasoner"] == (0.14, 0.28)
        assert PRICING["deepseek-v4-flash"] == (0.14, 0.28)
        assert MODEL_CATALOG["deepseek-chat"]["input"] == 0.14
        # Deliberately audited, not silenced behind an override.
        assert "deepseek-chat" not in PRICING_OVERRIDES
        assert "deepseek-reasoner" not in PRICING_OVERRIDES

    def test_deepseek_v4_pro_uses_the_published_v4_rate(self) -> None:
        # The entry that the override mechanism hid: pinned to a copy of the
        # reasoner placeholder ($0.55/$2.19) long after DeepSeek published
        # $0.435/$0.87 — 26% high on input, 152% high on output. It must now
        # carry the real rate and must NOT be a placeholder any more.
        assert PRICING["deepseek-v4-pro"] == (0.435, 0.87)
        assert "deepseek-v4-pro" not in PRICING_PLACEHOLDERS

    def test_real_snapshot_is_fully_reconciled(self) -> None:
        # CANARY: the whole hand table agrees with first-party upstream, with no
        # expired placeholders. A failure here is a real finding — print it.
        report = aud.audit_pricing(
            PRICING, PRICING_OVERRIDES, MODEL_CATALOG,
            first_party=aud._first_party_set(), placeholders=PRICING_PLACEHOLDERS,
        )
        assert report.drifts == [], [
            (d.prefix, d.hand, d.upstream) for d in report.drifts
        ]
        assert report.stale_placeholders == [], [
            (d.prefix, d.hand, d.upstream) for d in report.stale_placeholders
        ]
        assert report.checked >= 15  # the audit is actually comparing, not idle

    def test_placeholders_are_a_subset_of_overrides(self) -> None:
        # A placeholder that isn't an override silences nothing, so the marker
        # would be meaningless; keep the two sets coherent.
        assert PRICING_PLACEHOLDERS <= PRICING_OVERRIDES
        assert PRICING_PLACEHOLDERS <= set(PRICING)

    def test_real_snapshot_does_not_flag_any_marked_override(self) -> None:
        # No override prefix may ever appear as drift, on the real data.
        report = aud.audit_pricing(
            PRICING, PRICING_OVERRIDES, MODEL_CATALOG, first_party=aud._first_party_set()
        )
        flagged = {d.prefix for d in report.drifts}
        assert flagged.isdisjoint(PRICING_OVERRIDES)
