"""Integration tests for CodingAgent with multiple providers.

These tests verify that CodingAgent works with different provider types.
Live tests require API keys and are skipped by default.
Structure tests verify provider construction without making API calls.

Run live tests:
    ANTHROPIC_API_KEY=... pytest tests/integration/test_providers.py -v
    OPENAI_API_KEY=... pytest tests/integration/test_providers.py -v
    OPENROUTER_API_KEY=... pytest tests/integration/test_providers.py -v
"""
from __future__ import annotations

import os

import pytest

from chimera.assembly.coding_agent import CodingAgent
from chimera.core.loop_events import LoopEventType


# ---------------------------------------------------------------------------
# Structure tests (no API calls — always run)
# ---------------------------------------------------------------------------


class TestProviderStructure:
    """Verify providers can be constructed and injected into CodingAgent."""

    def test_anthropic_provider_constructs(self):
        from chimera.providers.anthropic import AnthropicProvider
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="test")
        assert p.model_name == "claude-sonnet-4-20250514"

    def test_openai_provider_constructs(self):
        from chimera.providers.openai import OpenAIProvider
        p = OpenAIProvider(model="gpt-4o", api_key="test")
        assert p.model_name == "gpt-4o"

    def test_compatible_provider_constructs(self):
        from chimera.providers.compatible import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(
            model="any-model",
            base_url="https://openrouter.ai/api/v1",
            api_key="test",
        )
        assert p.model_name == "any-model"

    def test_ollama_provider_constructs(self):
        from chimera.providers.ollama import OllamaProvider
        p = OllamaProvider(model="llama3.2")
        assert p.model_name == "llama3.2"

    def test_coding_agent_accepts_injected_provider(self):
        from chimera.providers.openai import OpenAIProvider
        p = OpenAIProvider(model="gpt-4o-mini", api_key="test")
        agent = CodingAgent(provider=p, preset="minimal", project_dir=".")
        assert agent.provider is p
        assert len(agent.tools) == 4

    def test_coding_agent_accepts_compatible_provider(self):
        from chimera.providers.compatible import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(
            model="deepseek-chat",
            base_url="https://openrouter.ai/api/v1",
            api_key="test",
        )
        agent = CodingAgent(provider=p, preset="minimal", project_dir=".")
        assert agent.provider.model_name == "deepseek-chat"

    def test_factory_infers_anthropic(self):
        from chimera.providers.factory import create_provider
        p = create_provider(model="claude-sonnet-4-20250514", api_key="test")
        assert "claude" in p.model_name

    def test_factory_infers_openai(self):
        from chimera.providers.factory import create_provider
        p = create_provider(model="gpt-4o", api_key="test")
        assert p.model_name == "gpt-4o"

    def test_factory_infers_ollama(self):
        from chimera.providers.factory import create_provider
        p = create_provider(model="llama3.2")
        assert p.model_name == "llama3.2"


# ---------------------------------------------------------------------------
# Live tests (require API keys — skipped by default)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"),
    reason="needs ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN",
)
class TestAnthropicLive:
    @pytest.mark.asyncio
    async def test_coding_agent_anthropic(self):
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        agent = CodingAgent(model=model, preset="minimal", project_dir=".")
        async for event in agent.run("What is 2+2? One word."):
            if event.type == LoopEventType.result:
                assert event.data.reason == "completed"


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="needs OPENAI_API_KEY",
)
class TestOpenAILive:
    @pytest.mark.asyncio
    async def test_coding_agent_openai(self):
        from chimera.providers.factory import create_provider
        p = create_provider(model="gpt-4o-mini")
        agent = CodingAgent(provider=p, preset="minimal", project_dir=".")
        async for event in agent.run("What is 2+2? One word."):
            if event.type == LoopEventType.result:
                assert event.data.reason == "completed"


@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="needs OPENROUTER_API_KEY",
)
class TestOpenRouterLive:
    @pytest.mark.asyncio
    async def test_coding_agent_openrouter(self):
        from chimera.providers.compatible import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(
            model="anthropic/claude-3.5-haiku",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
        agent = CodingAgent(provider=p, preset="minimal", project_dir=".")
        async for event in agent.run("What is 2+2? One word."):
            if event.type == LoopEventType.result:
                assert event.data.reason == "completed"


@pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="needs GOOGLE_API_KEY",
)
class TestGoogleLive:
    @pytest.mark.asyncio
    async def test_coding_agent_google(self):
        from chimera.providers.factory import create_provider
        p = create_provider(model="gemini-2.0-flash")
        agent = CodingAgent(provider=p, preset="minimal", project_dir=".")
        async for event in agent.run("What is 2+2? One word."):
            if event.type == LoopEventType.result:
                assert event.data.reason == "completed"
