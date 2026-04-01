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
