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

from chimera.providers.cost import PRICING, PRICING_OVERRIDES
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


# A models.dev-shaped comparison source. Exercises first-party vs reseller
# provenance, an output-less (embedding) record, a record without a numeric
# input, and a float-noise record.
FAKE_UPSTREAM: dict[str, dict[str, Any]] = {
    "match-fp": {"input": 1.0, "output": 2.0, "provider": "openai"},
    "drift-fp": {"input": 9.0, "output": 3.0, "provider": "google"},
    "drift-in-fp": {"input": 9.0, "output": 2.0, "provider": "openai"},
    "reseller": {"input": 5.0, "output": 9.0, "provider": "reseller-x"},
    "embed": {"input": 0.1, "output": None, "provider": "openai"},
    "noinput": {"input": None, "output": 2.0, "provider": "openai"},
    "round": {"input": 0.3, "output": 1.1, "provider": "openai"},
    "ovr": {"input": 1.0, "output": 1.0, "provider": "openai"},
}
FAKE_FIRST_PARTY = frozenset({"openai", "google"})
FAKE_HAND: dict[str, tuple[float, float]] = {
    "match-fp": (1.0, 2.0),      # equals first-party upstream -> clean
    "drift-fp": (1.0, 2.0),      # first-party disagrees -> DRIFT (input+output)
    "drift-in-fp": (1.0, 2.0),   # first-party input disagrees -> DRIFT (input)
    "reseller": (1.0, 2.0),      # only reseller offers it -> reseller_only (gated)
    "embed": (0.1, 88.0),        # input matches; upstream has no output -> clean
    "noinput": (1.0, 2.0),       # upstream input non-numeric -> no_upstream
    "round": (0.30, 1.10),       # float noise vs 0.3 / 1.1 -> clean
    "missing": (1.0, 2.0),       # no upstream id at all -> no_upstream
    "ovr": (777.0, 888.0),       # marked override -> skipped despite wild gap
}
FAKE_OVERRIDES = frozenset({"ovr"})


class TestAuditCore:
    def test_first_party_drift_is_flagged(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        drifted = {d.prefix for d in report.drifts}
        assert drifted == {"drift-fp", "drift-in-fp"}

    def test_drift_records_which_fields_moved(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        by_prefix = {d.prefix: d for d in report.drifts}
        assert by_prefix["drift-fp"].fields == ("input", "output")
        assert by_prefix["drift-in-fp"].fields == ("input",)
        # The Drift carries both sides + provenance for the report.
        assert by_prefix["drift-fp"].hand == (1.0, 2.0)
        assert by_prefix["drift-fp"].upstream == (9.0, 3.0)
        assert by_prefix["drift-fp"].provider == "google"

    def test_override_is_never_flagged(self) -> None:
        # ``ovr`` diverges wildly (777/888 vs 1/1) but is a marked override:
        # it must land in skipped_overrides and never in drifts. This is the
        # hand-corrections-win guarantee.
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        assert "ovr" in report.skipped_overrides
        assert all(d.prefix != "ovr" for d in report.drifts)

    def test_reseller_only_ids_are_gated_by_default(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        assert report.reseller_only == ["reseller"]
        assert all(d.prefix != "reseller" for d in report.drifts)

    def test_include_resellers_compares_reseller_ids(self) -> None:
        # With no first-party gate, the reseller id IS compared and drifts.
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=None
        )
        assert report.reseller_only == []
        assert "reseller" in {d.prefix for d in report.drifts}

    def test_missing_and_non_numeric_input_go_to_no_upstream(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        assert report.no_upstream == ["missing", "noinput"]

    def test_output_only_compared_when_upstream_publishes_one(self) -> None:
        # ``embed`` upstream has output=None; the hand output (88.0) differs but
        # must not drift — only the (matching) input is comparable.
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        assert all(d.prefix != "embed" for d in report.drifts)

    def test_float_noise_is_not_drift(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        assert all(d.prefix != "round" for d in report.drifts)

    def test_checked_count_and_has_drift(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        # Compared: match-fp, drift-fp, drift-in-fp, embed, round.
        assert report.checked == 5
        assert report.has_drift is True

    def test_clean_table_has_no_drift(self) -> None:
        clean_hand = {"match-fp": (1.0, 2.0), "ovr": (1.0, 1.0)}
        report = aud.audit_pricing(
            clean_hand, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        assert report.has_drift is False
        assert report.drifts == []

    def test_audit_does_not_mutate_inputs(self) -> None:
        hand_copy = dict(FAKE_HAND)
        aud.audit_pricing(FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY)
        assert FAKE_HAND == hand_copy


class TestReportRendering:
    def test_text_report_flags_and_summarises(self) -> None:
        report = aud.audit_pricing(
            FAKE_HAND, FAKE_OVERRIDES, FAKE_UPSTREAM, first_party=FAKE_FIRST_PARTY
        )
        text = aud.format_text(report, source="fixture")
        assert "DRIFT" in text
        assert "drift-fp" in text
        assert "reseller" in text  # surfaced in the reseller-only note
        assert "skipped 1 override" in text

    def test_text_report_ok_when_clean(self) -> None:
        clean = aud.audit_pricing(
            {"match-fp": (1.0, 2.0)}, frozenset(), FAKE_UPSTREAM,
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
        assert {d["prefix"] for d in payload["drifts"]} == {"drift-fp", "drift-in-fp"}
        assert "reseller" in payload["reseller_only"]


class TestCli:
    def test_main_offline_reports_drift_and_exits_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Offline mode reconciles the REAL hand table against the committed
        # snapshot — which currently drifts — so main() must return 1.
        rc = aud.main([])
        out = capsys.readouterr().out
        assert rc == 1
        assert "deepseek-chat" in out

    def test_main_json_flag_emits_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = aud.main(["--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 1
        assert payload["summary"]["drift"] >= 1


class TestOverrideConvention:
    def test_every_override_is_a_real_pricing_prefix(self) -> None:
        # A marker for a prefix that isn't in PRICING is dead weight — guard it.
        assert PRICING_OVERRIDES <= set(PRICING)

    def test_real_snapshot_catches_deepseek_drift(self) -> None:
        # CANARY (network-free): the hand DeepSeek rates are stale against the
        # committed first-party models.dev figure. This proves the audit catches
        # a genuine in-repo drift. If DeepSeek pricing is reconciled (hand fixed
        # or the prefix moved into PRICING_OVERRIDES), update this expectation.
        assert PRICING["deepseek-chat"] == (0.27, 1.10)
        assert MODEL_CATALOG["deepseek-chat"]["input"] == 0.14
        assert "deepseek-chat" not in PRICING_OVERRIDES  # deliberately audited

        report = aud.audit_pricing(
            PRICING, PRICING_OVERRIDES, MODEL_CATALOG, first_party=aud._first_party_set()
        )
        assert "deepseek-chat" in {d.prefix for d in report.drifts}

    def test_real_snapshot_does_not_flag_any_marked_override(self) -> None:
        # No override prefix may ever appear as drift, on the real data.
        report = aud.audit_pricing(
            PRICING, PRICING_OVERRIDES, MODEL_CATALOG, first_party=aud._first_party_set()
        )
        flagged = {d.prefix for d in report.drifts}
        assert flagged.isdisjoint(PRICING_OVERRIDES)
