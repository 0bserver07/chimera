"""Tests for FileMutationQueue (per-path lock serialization)."""
from __future__ import annotations

import asyncio

import pytest

from chimera.core.file_mutation_queue import FileMutationQueue


@pytest.mark.asyncio
async def test_same_file_serialized():
    """Acquiring the same path twice should block the second acquire until release."""
    fmq = FileMutationQueue()
    order: list[str] = []

    async def first():
        await fmq.acquire("/tmp/test.txt")
        order.append("first_acquired")
        await asyncio.sleep(0.05)
        order.append("first_releasing")
        fmq.release("/tmp/test.txt")

    async def second():
        # Give first() a head start
        await asyncio.sleep(0.01)
        order.append("second_waiting")
        await fmq.acquire("/tmp/test.txt")
        order.append("second_acquired")
        fmq.release("/tmp/test.txt")

    await asyncio.gather(first(), second())

    # second_acquired must come after first_releasing
    assert order.index("second_acquired") > order.index("first_releasing")


@pytest.mark.asyncio
async def test_different_files_parallel():
    """Acquiring two different paths should not block each other."""
    fmq = FileMutationQueue()
    acquired: list[str] = []

    async def grab(path: str):
        await fmq.acquire(path)
        acquired.append(path)
        # Hold it briefly
        await asyncio.sleep(0.01)
        fmq.release(path)

    await asyncio.gather(grab("/a.txt"), grab("/b.txt"))
    assert set(acquired) == {"/a.txt", "/b.txt"}


@pytest.mark.asyncio
async def test_context_manager():
    """The lock() context manager should acquire and release properly."""
    fmq = FileMutationQueue()
    order: list[str] = []

    async def worker(label: str):
        async with fmq.lock("/shared.txt"):
            order.append(f"{label}_in")
            await asyncio.sleep(0.02)
            order.append(f"{label}_out")

    async def delayed_worker(label: str):
        await asyncio.sleep(0.005)
        await worker(label)

    await asyncio.gather(worker("a"), delayed_worker("b"))

    # b should enter only after a exits, so b_in must be after a_out
    assert order.index("b_in") > order.index("a_out")
