"""Tests for chimera.agents.dispatch — smart dispatch subsystem."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chimera.agents.config import AgentConfig
from chimera.agents.dispatch.classifier import Complexity, RequestClassifier
from chimera.agents.dispatch.dispatcher import Dispatcher
from chimera.agents.dispatch.index import AgentIndex
from chimera.agents.dispatch.router import AgentRouter
from chimera.agents.dispatch.rules import ForceRoute, RouteRule
from chimera.agents.registry import AgentRegistry


# ---------------------------------------------------------------------------
# Helpers: build a test registry with 3-4 agents
# ---------------------------------------------------------------------------

def _make_registry() -> AgentRegistry:
    """Create a mock registry with test agent configs."""
    registry = AgentRegistry()

    registry.register(AgentConfig(
        name="build",
        description="Build and implement code changes",
        system_prompt="You are a build agent.",
        tools=["read_file", "write_file", "bash"],
        triggers=["build", "implement", "create", "code", "write", "feature"],
    ))
    registry.register(AgentConfig(
        name="review",
        description="Review code for quality and correctness",
        system_prompt="You are a review agent.",
        tools=["read_file", "search"],
        triggers=["review", "check", "audit", "quality", "inspect"],
    ))
    registry.register(AgentConfig(
        name="explore",
        description="Explore and understand codebases",
        system_prompt="You are an explore agent.",
        tools=["read_file", "search", "list_files"],
        triggers=["explore", "understand", "find", "search", "navigate"],
    ))
    registry.register(AgentConfig(
        name="plan",
        description="Plan complex multi-step tasks",
        system_prompt="You are a planning agent.",
        tools=["read_file", "search"],
        triggers=["plan", "design", "architect", "strategy", "organize"],
    ))

    return registry


# ---------------------------------------------------------------------------
# Classifier tests
# ---------------------------------------------------------------------------

class TestRequestClassifier:
    def test_classifier_trivial(self) -> None:
        c = RequestClassifier()
        result = c.classify("what is this?")
        assert result is Complexity.TRIVIAL

    def test_classifier_simple(self) -> None:
        c = RequestClassifier()
        result = c.classify("fix the typo in readme")
        assert result is Complexity.SIMPLE

    def test_classifier_moderate(self) -> None:
        c = RequestClassifier()
        result = c.classify("refactor the auth module")
        assert result is Complexity.MODERATE

    def test_classifier_complex(self) -> None:
        c = RequestClassifier()
        result = c.classify(
            "implement a new provider, add tests, and update the docs"
        )
        assert result is Complexity.COMPLEX

    def test_classifier_deterministic(self) -> None:
        c = RequestClassifier()
        requests = [
            "what is this?",
            "fix the typo in readme",
            "refactor the auth module",
            "implement a new provider, add tests, and update the docs",
            "build a feature and also integrate with the API",
        ]
        for req in requests:
            first = c.classify(req)
            for _ in range(10):
                assert c.classify(req) is first, (
                    f"Non-deterministic for {req!r}: "
                    f"expected {first}, got {c.classify(req)}"
                )


# ---------------------------------------------------------------------------
# Rules tests
# ---------------------------------------------------------------------------

class TestForceRoute:
    def test_force_route_regex(self) -> None:
        fr = ForceRoute(
            pattern=r"\bci[- ]?fix\b",
            agent_name="ci-fix",
            reason="CI fix requests go to ci-fix agent",
        )
        assert fr.matches("please ci-fix the build") is True
        assert fr.matches("CI Fix needed") is True
        assert fr.matches("cifix the pipeline") is True
        assert fr.matches("update the docs") is False

    def test_force_route_case_insensitive(self) -> None:
        fr = ForceRoute(
            pattern=r"deploy",
            agent_name="deploy",
            reason="Deploy requests",
        )
        assert fr.matches("DEPLOY to prod") is True
        assert fr.matches("Deploy the app") is True
        assert fr.matches("we need to deploy") is True


# ---------------------------------------------------------------------------
# Router tests
# ---------------------------------------------------------------------------

class TestAgentRouter:
    def test_router_scoring(self) -> None:
        """More trigger overlap = higher score."""
        registry = _make_registry()
        router = AgentRouter(registry)

        # "build and create a feature" matches 3 of build's 6 triggers
        results = router.route("build and create a feature")
        assert len(results) > 0
        # Build agent should be top (most overlap)
        assert results[0].agent_config.name == "build"
        assert results[0].score > 0

    def test_router_no_match_returns_empty(self) -> None:
        """Unrelated request = no results."""
        registry = _make_registry()
        router = AgentRouter(registry)
        results = router.route("xyzzy quantum flux capacitor")
        assert results == []

    def test_router_force_first(self) -> None:
        """Force routes checked before scoring."""
        registry = _make_registry()
        force_routes = [
            ForceRoute(
                pattern=r"urgent",
                agent_name="build",
                reason="Urgent requests go to build",
            ),
        ]
        router = AgentRouter(registry, force_routes=force_routes)

        # "urgent review" would normally score for review, but force route wins
        results = router.route("urgent review needed")
        assert len(results) == 1
        assert results[0].agent_config.name == "build"
        assert results[0].score == 1.0

    def test_force_route_overrides_scoring(self) -> None:
        """Pattern match -> agent selected regardless of score."""
        registry = _make_registry()
        force_routes = [
            ForceRoute(
                pattern=r"^review\b",
                agent_name="plan",
                reason="Requests starting with 'review' go to plan",
            ),
        ]
        router = AgentRouter(registry, force_routes=force_routes)

        # "review" would normally score highest for review agent,
        # but force route overrides to plan agent
        results = router.route("review the entire codebase")
        assert len(results) == 1
        assert results[0].agent_config.name == "plan"
        assert results[0].score == 1.0


# ---------------------------------------------------------------------------
# Index tests
# ---------------------------------------------------------------------------

class TestAgentIndex:
    def test_index_build_and_lookup(self) -> None:
        """Build from registry, lookup returns results."""
        registry = _make_registry()
        index = AgentIndex(registry)
        index.build()

        results = index.lookup(["build", "code"])
        assert len(results) > 0
        # build agent should be in results
        names = [name for name, _ in results]
        assert "build" in names

    def test_index_save_load(self) -> None:
        """JSON round-trip."""
        registry = _make_registry()
        index = AgentIndex(registry)
        index.build()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "index.json"
            index.save(path)

            # Verify file is valid JSON
            data = json.loads(path.read_text())
            assert "inverted" in data
            assert "agent_triggers" in data

            # Load and verify same results
            loaded = AgentIndex.load(path, registry)
            original_results = index.lookup(["build", "review"])
            loaded_results = loaded.lookup(["build", "review"])
            assert original_results == loaded_results

    def test_index_fallback_to_description(self) -> None:
        """Agents without triggers use description keywords."""
        registry = AgentRegistry()
        registry.register(AgentConfig(
            name="doc-agent",
            description="Generate documentation from source code",
            system_prompt="You generate docs.",
            tools=["read_file"],
            # No triggers — should fall back to description keywords
        ))
        index = AgentIndex(registry)
        index.build()

        results = index.lookup(["documentation", "generate"])
        names = [name for name, _ in results]
        assert "doc-agent" in names


# ---------------------------------------------------------------------------
# Dispatcher tests
# ---------------------------------------------------------------------------

class TestDispatcher:
    def test_dispatcher_facade(self) -> None:
        """dispatch() returns a configured Agent."""
        registry = _make_registry()
        provider = MagicMock()
        mock_agent = MagicMock()

        with patch.object(AgentConfig, "build", return_value=mock_agent):
            dispatcher = Dispatcher(registry)
            agent = dispatcher.dispatch("build a new feature", provider)
            assert agent is mock_agent

    def test_dispatcher_explain(self) -> None:
        """explain() returns a formatted string."""
        registry = _make_registry()
        dispatcher = Dispatcher(registry)

        explanation = dispatcher.explain("build a new feature")
        assert "Complexity:" in explanation
        assert "Agent:" in explanation
        assert "Score:" in explanation
        assert "Reason:" in explanation

    def test_dispatcher_no_match_raises(self) -> None:
        """No matching agent -> ValueError."""
        registry = _make_registry()
        dispatcher = Dispatcher(registry)

        with pytest.raises(ValueError, match="No agent matches"):
            provider = MagicMock()
            dispatcher.dispatch("xyzzy quantum flux capacitor", provider)

    def test_dispatcher_explain_no_match(self) -> None:
        """explain() handles no matches gracefully."""
        registry = _make_registry()
        dispatcher = Dispatcher(registry)

        explanation = dispatcher.explain("xyzzy quantum flux capacitor")
        assert "none" in explanation.lower()

    def test_dispatcher_with_learning_store(self) -> None:
        """Dispatcher logs to learning_store when present."""
        registry = _make_registry()
        store = MagicMock()
        mock_agent = MagicMock()

        with patch.object(AgentConfig, "build", return_value=mock_agent):
            dispatcher = Dispatcher(registry, learning_store=store)
            dispatcher.dispatch("build a feature", MagicMock())
            store.log.assert_called_once()

    def test_dispatcher_learning_store_error_ignored(self) -> None:
        """Learning store errors are silently ignored."""
        registry = _make_registry()
        store = MagicMock()
        store.log.side_effect = RuntimeError("store broken")
        mock_agent = MagicMock()

        with patch.object(AgentConfig, "build", return_value=mock_agent):
            dispatcher = Dispatcher(registry, learning_store=store)
            # Should not raise despite store.log() failing
            agent = dispatcher.dispatch("build a feature", MagicMock())
            assert agent is mock_agent


# ---------------------------------------------------------------------------
# RouteRule tests
# ---------------------------------------------------------------------------

class TestRouteRule:
    def test_route_rule_dataclass(self) -> None:
        rule = RouteRule(pattern=r"test", agent_name="tester", weight=0.5)
        assert rule.pattern == r"test"
        assert rule.agent_name == "tester"
        assert rule.weight == 0.5

    def test_route_rule_default_weight(self) -> None:
        rule = RouteRule(pattern=r"test", agent_name="tester")
        assert rule.weight == 1.0
