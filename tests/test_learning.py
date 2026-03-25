"""Tests for chimera.learning — adaptive learning subsystem."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chimera.events.types import ToolCallEvent, ToolResultEvent
from chimera.learning.feedback import FeedbackTracker, _error_signature
from chimera.learning.injector import LearningInjector
from chimera.learning.metrics import MetricsCollector, SessionMetrics
from chimera.learning.observation import (
    CATEGORY_THRESHOLDS,
    Observation,
    ObservationCategory,
)
from chimera.learning.store import LearningStore
from chimera.types import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp: str) -> LearningStore:
    """Create a LearningStore in a temporary directory."""
    return LearningStore(db_path=Path(tmp) / "test.db")


def _make_observation(**overrides) -> Observation:  # type: ignore[no-untyped-def]
    """Create an Observation with sensible defaults."""
    defaults = dict(
        topic="import_error",
        key="ModuleNotFoundError",
        value="Install missing package with pip install foo",
        category=ObservationCategory.ERROR,
        confidence=0.5,
        tags=["python", "import"],
        source="test",
        project_path="/test/project",
        error_signature="abc123",
    )
    defaults.update(overrides)
    return Observation(**defaults)


# ---------------------------------------------------------------------------
# LearningStore tests
# ---------------------------------------------------------------------------


class TestStoreRecordAndQuery:
    """test_store_record_and_query — record observation, query returns it."""

    def test_record_and_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            obs = _make_observation()
            store.record(obs)

            results = store.query("import_error")
            assert len(results) >= 1
            assert results[0].topic == "import_error"
            assert results[0].value == "Install missing package with pip install foo"
            store.close()


class TestStoreFTS5Search:
    """test_store_fts5_search — full-text search matches partial terms."""

    def test_fts5_partial_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            obs = _make_observation(
                topic="database_connection",
                key="ConnectionRefusedError",
                value="Check that PostgreSQL is running on port 5432",
                error_signature="db_conn_001",
            )
            store.record(obs)

            # Search by partial term
            results = store.query("PostgreSQL")
            assert len(results) >= 1
            assert "PostgreSQL" in results[0].value

            # Search by topic
            results = store.query("database")
            assert len(results) >= 1
            store.close()


class TestStoreDedupBySignature:
    """test_store_dedup_by_signature — same signature updates instead of inserting."""

    def test_dedup_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            obs1 = _make_observation(
                value="First fix attempt",
                error_signature="dup_sig_001",
            )
            store.record(obs1)

            obs2 = _make_observation(
                value="Better fix approach",
                error_signature="dup_sig_001",
            )
            store.record(obs2)

            # Should have only one observation for this signature
            result = store.query_by_signature("dup_sig_001")
            assert result is not None
            assert result.value == "Better fix approach"
            store.close()


class TestStoreConfidenceUpdateSuccess:
    """test_store_confidence_update_success — +0.10, clamped at 1.0."""

    def test_success_increment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            obs = _make_observation(confidence=0.5, error_signature="conf_s_001")
            store.record(obs)
            recorded = store.query_by_signature("conf_s_001")
            assert recorded is not None
            assert recorded.id is not None

            new_conf = store.update_confidence(recorded.id, success=True)
            assert abs(new_conf - 0.6) < 1e-9

            store.close()

    def test_success_clamp_at_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            obs = _make_observation(confidence=0.95, error_signature="conf_s_002")
            store.record(obs)
            recorded = store.query_by_signature("conf_s_002")
            assert recorded is not None
            assert recorded.id is not None

            new_conf = store.update_confidence(recorded.id, success=True)
            assert new_conf == 1.0
            store.close()


class TestStoreConfidenceUpdateFailure:
    """test_store_confidence_update_failure — -0.15, clamped at 0.0."""

    def test_failure_decrement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            obs = _make_observation(confidence=0.5, error_signature="conf_f_001")
            store.record(obs)
            recorded = store.query_by_signature("conf_f_001")
            assert recorded is not None
            assert recorded.id is not None

            new_conf = store.update_confidence(recorded.id, success=False)
            assert abs(new_conf - 0.35) < 1e-9
            store.close()

    def test_failure_clamp_at_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            obs = _make_observation(confidence=0.05, error_signature="conf_f_002")
            store.record(obs)
            recorded = store.query_by_signature("conf_f_002")
            assert recorded is not None
            assert recorded.id is not None

            new_conf = store.update_confidence(recorded.id, success=False)
            assert new_conf == 0.0
            store.close()


class TestStoreQueryMinConfidence:
    """test_store_query_min_confidence — filters below threshold."""

    def test_min_confidence_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            obs_low = _make_observation(
                topic="low_conf_topic",
                confidence=0.2,
                error_signature="min_conf_001",
            )
            obs_high = _make_observation(
                topic="high_conf_topic",
                confidence=0.8,
                error_signature="min_conf_002",
            )
            store.record(obs_low)
            store.record(obs_high)

            # Only high confidence should match
            results = store.query("conf_topic", min_confidence=0.5)
            assert all(r.confidence >= 0.5 for r in results)
            store.close()


class TestStoreQueryProjectScoped:
    """test_store_query_project_scoped — project_path filtering works."""

    def test_project_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            obs_a = _make_observation(
                topic="scoped_error",
                project_path="/project/a",
                error_signature="scope_001",
            )
            obs_b = _make_observation(
                topic="scoped_error",
                project_path="/project/b",
                error_signature="scope_002",
            )
            store.record(obs_a)
            store.record(obs_b)

            results = store.query("scoped_error", project_path="/project/a")
            assert len(results) >= 1
            assert all(r.project_path == "/project/a" for r in results)
            store.close()


class TestStorePrune:
    """test_store_prune — removes old low-confidence observations."""

    def test_prune_removes_old_low_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            obs = _make_observation(confidence=0.05, error_signature="prune_001")
            store.record(obs)

            # Manually backdate the last_seen to > 90 days ago
            import sqlite3
            from datetime import datetime, timedelta, timezone

            old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
            conn = sqlite3.connect(str(Path(tmp) / "test.db"))
            conn.execute(
                "UPDATE observations SET last_seen = ? WHERE error_signature = ?",
                (old_date, "prune_001"),
            )
            conn.commit()
            conn.close()

            # Reconnect store to see the updated data
            store.close()
            store = _make_store(tmp)

            removed = store.prune(max_age_days=90, min_confidence=0.1)
            assert removed == 1

            # Should be gone
            result = store.query_by_signature("prune_001")
            assert result is None
            store.close()

    def test_prune_keeps_high_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            obs = _make_observation(confidence=0.9, error_signature="prune_002")
            store.record(obs)

            # Backdate
            import sqlite3
            from datetime import datetime, timedelta, timezone

            old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
            conn = sqlite3.connect(str(Path(tmp) / "test.db"))
            conn.execute(
                "UPDATE observations SET last_seen = ? WHERE error_signature = ?",
                (old_date, "prune_002"),
            )
            conn.commit()
            conn.close()

            store.close()
            store = _make_store(tmp)

            removed = store.prune(max_age_days=90, min_confidence=0.1)
            assert removed == 0

            result = store.query_by_signature("prune_002")
            assert result is not None
            store.close()


# ---------------------------------------------------------------------------
# FeedbackTracker tests
# ---------------------------------------------------------------------------


class TestFeedbackTrackerSuccessPath:
    """test_feedback_tracker_success_path — error disappears = success."""

    def test_error_disappears_is_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            # Pre-populate an observation
            error_text = "ModuleNotFoundError: No module named 'foo'"
            sig = _error_signature(error_text)
            obs = _make_observation(
                value="pip install foo",
                error_signature=sig,
                confidence=0.5,
            )
            store.record(obs)
            recorded = store.query_by_signature(sig)
            assert recorded is not None

            tracker = FeedbackTracker(store, window_size=3)

            # Trigger the error
            error_event = ToolResultEvent(
                success=False,
                output=error_text,
            )
            tracker.on_tool_result(error_event)

            # 3 successful tool results (error gone)
            for _ in range(3):
                ok_event = ToolResultEvent(success=True, output="Success")
                tracker.on_tool_result(ok_event)

            # Confidence should have increased
            updated = store.query_by_signature(sig)
            assert updated is not None
            assert updated.confidence > 0.5
            store.close()


class TestFeedbackTrackerFailurePath:
    """test_feedback_tracker_failure_path — error persists = failure."""

    def test_error_persists_is_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            error_text = "ConnectionRefusedError: connection refused"
            sig = _error_signature(error_text)
            obs = _make_observation(
                value="Check database connection",
                error_signature=sig,
                confidence=0.5,
            )
            store.record(obs)

            tracker = FeedbackTracker(store, window_size=3)

            # Trigger the error
            error_event = ToolResultEvent(success=False, output=error_text)
            tracker.on_tool_result(error_event)

            # Same error reappears within window
            ok_event = ToolResultEvent(success=True, output="OK")
            tracker.on_tool_result(ok_event)

            error_again = ToolResultEvent(success=False, output=error_text)
            tracker.on_tool_result(error_again)

            # One more to close the window
            ok_event2 = ToolResultEvent(success=True, output="OK")
            tracker.on_tool_result(ok_event2)

            # Confidence should have decreased
            updated = store.query_by_signature(sig)
            assert updated is not None
            assert updated.confidence < 0.5
            store.close()


class TestFeedbackTrackerNoPriorMatch:
    """test_feedback_tracker_no_prior_match — records new observation."""

    def test_new_error_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            tracker = FeedbackTracker(store, window_size=3)

            error_text = "TypeError: unsupported operand type"
            sig = _error_signature(error_text)

            # No prior observation exists
            assert store.query_by_signature(sig) is None

            # Trigger the error
            error_event = ToolResultEvent(success=False, output=error_text)
            tracker.on_tool_result(error_event)

            # Should now be recorded
            recorded = store.query_by_signature(sig)
            assert recorded is not None
            assert recorded.confidence == 0.5
            assert recorded.category == ObservationCategory.ERROR
            store.close()


# ---------------------------------------------------------------------------
# LearningInjector tests
# ---------------------------------------------------------------------------


class TestInjectorAboveThreshold:
    """test_injector_above_threshold — injects high-confidence matches."""

    def test_injects_above_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            # ERROR threshold is 0.50, so 0.8 is above
            obs = _make_observation(
                topic="import_error",
                value="Use pip install missing_pkg",
                confidence=0.8,
                error_signature="inject_001",
            )
            store.record(obs)

            injector = LearningInjector(store, max_injections=3)
            context = [Message.user("I got an import_error")]
            injections = injector.get_injections(context)

            assert len(injections) >= 1
            assert "import_error" in injections[0]
            store.close()


class TestInjectorBelowThreshold:
    """test_injector_below_threshold — skips low-confidence matches."""

    def test_skips_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            # ERROR threshold is 0.50, so 0.3 is below
            obs = _make_observation(
                topic="flaky_error",
                value="Sometimes works, sometimes not",
                confidence=0.3,
                error_signature="inject_002",
            )
            store.record(obs)

            injector = LearningInjector(store, max_injections=3)
            context = [Message.user("I got a flaky_error")]
            injections = injector.get_injections(context)

            # Should not inject the low-confidence observation
            assert len(injections) == 0
            store.close()


class TestInjectorMaxInjections:
    """test_injector_max_injections — respects limit."""

    def test_respects_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            # Record multiple high-confidence observations
            for i in range(5):
                obs = _make_observation(
                    topic="common_error",
                    key=f"error_variant_{i}",
                    value=f"Fix #{i} for common error",
                    confidence=0.9,
                    error_signature=f"inject_max_{i:03d}",
                )
                store.record(obs)

            injector = LearningInjector(store, max_injections=2)
            context = [Message.user("I got a common_error")]
            injections = injector.get_injections(context)

            assert len(injections) <= 2
            store.close()


# ---------------------------------------------------------------------------
# MetricsCollector tests
# ---------------------------------------------------------------------------


class TestMetricsCollectorCounts:
    """test_metrics_collector_counts — counts tool calls, errors, files."""

    def test_counts_tool_calls(self) -> None:
        collector = MetricsCollector()
        collector.start_session("test-session-001")

        # Simulate tool calls
        call_event = ToolCallEvent(tool_name="bash", arguments={"command": "ls"})
        collector.on_tool_call(call_event)
        collector.on_tool_call(call_event)

        assert collector.metrics.tool_calls == 2
        assert collector.metrics.session_id == "test-session-001"

    def test_counts_errors(self) -> None:
        collector = MetricsCollector()
        collector.start_session("test-session-002")

        # Simulate error
        error_event = ToolResultEvent(success=False, output="Error!")
        collector.on_tool_result(error_event)

        ok_event = ToolResultEvent(success=True, output="OK")
        collector.on_tool_result(ok_event)

        assert collector.metrics.errors_encountered == 1

    def test_counts_files_modified(self) -> None:
        collector = MetricsCollector()
        collector.start_session("test-session-003")

        # Simulate file modifications
        write_event = ToolCallEvent(
            tool_name="write",
            arguments={"file_path": "/tmp/test.py"},
        )
        collector.on_tool_call(write_event)

        edit_event = ToolCallEvent(
            tool_name="edit",
            arguments={"file_path": "/tmp/other.py"},
        )
        collector.on_tool_call(edit_event)

        # Same file again — should not double-count
        collector.on_tool_call(write_event)

        assert collector.metrics.files_modified == 2
        assert collector.metrics.tool_calls == 3

    def test_cost_tracking(self) -> None:
        from chimera.events.types import StepCostEvent

        collector = MetricsCollector()
        collector.start_session("test-session-004")

        cost_event = StepCostEvent(cost=0.05)
        collector.on_step_cost(cost_event)
        collector.on_step_cost(cost_event)

        assert abs(collector.metrics.total_cost - 0.10) < 1e-9


# ---------------------------------------------------------------------------
# Observation / Category tests
# ---------------------------------------------------------------------------


class TestObservationCategory:
    """Verify enum values and thresholds."""

    def test_category_values(self) -> None:
        assert ObservationCategory.ERROR.value == "error"
        assert ObservationCategory.DEBUG.value == "debug"
        assert ObservationCategory.DESIGN.value == "design"
        assert ObservationCategory.REVIEW.value == "review"
        assert ObservationCategory.EFFECTIVENESS.value == "effectiveness"

    def test_thresholds(self) -> None:
        assert CATEGORY_THRESHOLDS[ObservationCategory.ERROR] == 0.50
        assert CATEGORY_THRESHOLDS[ObservationCategory.DEBUG] == 0.60
        assert CATEGORY_THRESHOLDS[ObservationCategory.DESIGN] == 0.70
        assert CATEGORY_THRESHOLDS[ObservationCategory.REVIEW] == 0.70
        assert CATEGORY_THRESHOLDS[ObservationCategory.EFFECTIVENESS] == 0.50


class TestSessionMetrics:
    """Verify SessionMetrics defaults."""

    def test_defaults(self) -> None:
        m = SessionMetrics(session_id="s1")
        assert m.session_id == "s1"
        assert m.tool_calls == 0
        assert m.files_modified == 0
        assert m.errors_encountered == 0
        assert m.errors_resolved == 0
        assert m.observations_recorded == 0
        assert m.total_cost == 0.0
        assert m.start_time  # non-empty


# ---------------------------------------------------------------------------
# LoopConfig integration
# ---------------------------------------------------------------------------


class TestLoopConfigLearningFields:
    """Verify learning fields exist on LoopConfig."""

    def test_fields_default_none(self) -> None:
        from chimera.core.loop_config import LoopConfig

        config = LoopConfig()
        assert config.learning is None
        assert config.feedback_tracker is None
        assert config.learning_injector is None

    def test_fields_assignable(self) -> None:
        from chimera.core.loop_config import LoopConfig

        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            tracker = FeedbackTracker(store)
            injector = LearningInjector(store)

            config = LoopConfig(
                learning=store,
                feedback_tracker=tracker,
                learning_injector=injector,
            )
            assert config.learning is store
            assert config.feedback_tracker is tracker
            assert config.learning_injector is injector
            store.close()
