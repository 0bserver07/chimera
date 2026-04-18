"""Coordinator mode for multi-agent task dispatch.

Provides :class:`CoordinatorMode` which uses :class:`FeatureFlags` to
gate availability and delegates task execution to an
:class:`~chimera.core.agent_spawner.AgentSpawner`.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from chimera.core.feature_flags import FeatureFlags

if TYPE_CHECKING:
    from chimera.core.agent_context import AgentContext
    from chimera.core.agent_definition import AgentDefinition
    from chimera.core.agent_spawner import AgentSpawner

__all__ = ["CoordinatorMode"]


class CoordinatorMode:
    """Dispatch tasks to specialised agents when the feature flag is enabled.

    Args:
        spawner: Agent spawner for creating sub-agents.
        agent_definitions: Mapping of agent type names to their definitions.
    """

    def __init__(
        self,
        spawner: AgentSpawner | None,
        agent_definitions: dict[str, AgentDefinition],
    ) -> None:
        self._spawner = spawner
        self._definitions = agent_definitions
        self._active_agents: dict[str, asyncio.Task[None]] = {}

    @property
    def is_enabled(self) -> bool:
        """Whether coordinator mode is gated on."""
        return FeatureFlags.enabled("COORDINATOR_MODE")

    async def dispatch(
        self,
        task: str,
        agent_type: str,
        parent_context: AgentContext,
        *,
        run_in_background: bool = True,
    ) -> str:
        """Dispatch *task* to an agent of *agent_type*.

        Returns the agent ID assigned to the spawned agent.

        When *run_in_background* is ``True``, the spawned agent is wrapped
        in an :class:`asyncio.Task` and stored for later status checks and
        cancellation.

        Raises:
            RuntimeError: If coordinator mode is disabled or the spawner
                is not configured.
            KeyError: If *agent_type* is not in the known definitions.
        """
        if not self.is_enabled:
            raise RuntimeError("Coordinator mode is not enabled")
        if self._spawner is None:
            raise RuntimeError("No spawner configured")

        definition = self._definitions[agent_type]
        agent_id = str(uuid.uuid4())

        if run_in_background:
            async def _run() -> None:
                async for _event in self._spawner.spawn(
                    definition,
                    task,
                    parent_context,
                    run_in_background=run_in_background,
                ):
                    pass  # Events consumed internally

            async_task = asyncio.create_task(_run())
            self._active_agents[agent_id] = async_task
        else:
            # Foreground: consume events synchronously
            async for _event in self._spawner.spawn(
                definition,
                task,
                parent_context,
                run_in_background=run_in_background,
            ):
                pass  # Events consumed internally

        return agent_id

    async def get_status(self, agent_id: str) -> str:
        """Check the status of a dispatched agent.

        Args:
            agent_id: The agent ID returned by :meth:`dispatch`.

        Returns:
            ``"running"``, ``"done"``, or ``"unknown"`` if the agent
            was not dispatched via background mode or is not tracked.
        """
        task = self._active_agents.get(agent_id)
        if task is None:
            return "unknown"
        if task.done():
            return "done"
        return "running"

    async def cancel(self, agent_id: str) -> None:
        """Cancel a running background agent task.

        Args:
            agent_id: The agent ID returned by :meth:`dispatch`.

        Does nothing if the agent is not tracked or already finished.
        """
        task = self._active_agents.get(agent_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
