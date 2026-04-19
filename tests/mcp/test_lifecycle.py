from unittest.mock import patch

import pytest

from chimera.mcp.lifecycle import MCPServerLifecycle

@pytest.mark.asyncio
async def test_memoized_connection():
    lc = MCPServerLifecycle()
    config = {"url": "http://localhost:3000", "name": "test"}
    c1 = await lc.connect(config)
    c2 = await lc.connect(config)
    assert c1 is c2

@pytest.mark.asyncio
async def test_cleanup_agent_removes_owned():
    lc = MCPServerLifecycle()
    config = {"url": "http://localhost:3001", "name": "agent_specific"}
    await lc.connect_for_agent(config, "agent1")
    assert len(lc._connections) == 1
    await lc.cleanup_agent("agent1")
    assert len(lc._connections) == 0

@pytest.mark.asyncio
async def test_shared_connection_not_removed():
    lc = MCPServerLifecycle()
    config = {"url": "http://localhost:3002", "name": "shared"}
    await lc.connect_for_agent(config, "agent1")
    await lc.connect_for_agent(config, "agent2")
    await lc.cleanup_agent("agent1")
    assert len(lc._connections) == 1  # Still used by agent2

@pytest.mark.asyncio
async def test_cleanup_all():
    lc = MCPServerLifecycle()
    await lc.connect({"url": "a"})
    await lc.connect({"url": "b"})
    await lc.cleanup_all()
    assert len(lc._connections) == 0


@pytest.mark.asyncio
async def test_connect_default_does_not_call_connect_all():
    """Default behaviour: connect() registers transport but does NOT dial."""
    lc = MCPServerLifecycle()
    with patch("chimera.mcp.client.MCPClient.connect_all") as mock_connect_all:
        await lc.connect({"url": "http://localhost:3000"})
        mock_connect_all.assert_not_called()


@pytest.mark.asyncio
async def test_connect_eager_connect_calls_connect_all():
    """Bug fix: eager_connect=True must actually bring the transport up."""
    lc = MCPServerLifecycle()
    with patch("chimera.mcp.client.MCPClient.connect_all") as mock_connect_all:
        await lc.connect({"url": "http://localhost:3000"}, eager_connect=True)
        mock_connect_all.assert_called_once()


@pytest.mark.asyncio
async def test_connect_eager_connect_raises_on_failure():
    """When connect_all() fails, the lifecycle raises ConnectionError and
    forgets the registration so the caller can retry.
    """
    lc = MCPServerLifecycle()
    with patch(
        "chimera.mcp.client.MCPClient.connect_all",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(ConnectionError, match="boom"):
            await lc.connect(
                {"url": "http://localhost:3000"}, eager_connect=True
            )
    # Registration rolled back so the cache doesn't remember a broken client.
    assert len(lc._connections) == 0


@pytest.mark.asyncio
async def test_connect_eager_connect_memoizes_connect_all():
    """Second call with same config must not re-dial connect_all()."""
    lc = MCPServerLifecycle()
    cfg = {"url": "http://localhost:3000"}
    with patch("chimera.mcp.client.MCPClient.connect_all") as mock_connect_all:
        await lc.connect(cfg, eager_connect=True)
        await lc.connect(cfg, eager_connect=True)
        assert mock_connect_all.call_count == 1


@pytest.mark.asyncio
async def test_connect_for_agent_eager_connect_calls_connect_all():
    """Eager connect also flows through connect_for_agent."""
    lc = MCPServerLifecycle()
    with patch("chimera.mcp.client.MCPClient.connect_all") as mock_connect_all:
        await lc.connect_for_agent(
            {"url": "http://localhost:3000"}, "agent1", eager_connect=True
        )
        mock_connect_all.assert_called_once()
