"""Command handlers for the JSON-RPC server."""
from __future__ import annotations

from typing import Any, Callable

from chimera.rpc.types import (
    CancelCommand,
    CompactCommand,
    ErrorEvent,
    GetStateCommand,
    MessageEvent,
    PromptCommand,
    RpcResponse,
    StateResponse,
    SteerCommand,
)


class RpcHandler:
    """Maps RPC commands to agent/session operations.

    Args:
        server: The :class:`~chimera.rpc.server.RpcServer` instance whose
            ``_session`` and ``_emit`` will be used.
    """

    def __init__(self, server: Any) -> None:
        self._server = server

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def handle_prompt(self, cmd: PromptCommand) -> None:
        """Process a user message through the session.

        Args:
            cmd: Inbound prompt command.
        """
        session = self._server._session
        try:
            result = session.chat(cmd.message)
            self._server._emit(
                MessageEvent(role="assistant", content=result.output, done=True)
            )
        except Exception as e:
            self._server._emit(ErrorEvent(message=str(e)))
            return
        self._server._emit(RpcResponse(command="prompt", id=cmd.id))

    def handle_steer(self, cmd: SteerCommand) -> None:
        """Inject a mid-turn steering message.

        Args:
            cmd: Inbound steer command.
        """
        if hasattr(self._server._session, "steer"):
            self._server._session.steer(cmd.message)
        self._server._emit(RpcResponse(command="steer", id=cmd.id))

    def handle_cancel(self, cmd: CancelCommand) -> None:
        """Cancel the current agent turn.

        Args:
            cmd: Inbound cancel command.
        """
        if hasattr(self._server._session, "cancel"):
            self._server._session.cancel()
        self._server._emit(RpcResponse(command="cancel", id=cmd.id))

    def handle_get_state(self, cmd: GetStateCommand) -> None:
        """Return the current session state.

        Args:
            cmd: Inbound get_state command.
        """
        session = self._server._session
        agent = getattr(session, "_agent", None)
        model = (
            agent.provider.model_name if agent and hasattr(agent, "provider") else ""
        )
        self._server._emit(
            StateResponse(
                id=cmd.id,
                messages=[
                    {"role": m.role, "content": m.content}
                    for m in session.messages
                ],
                model=model,
            )
        )

    def handle_compact(self, cmd: CompactCommand) -> None:
        """Trigger context compaction on the session.

        Args:
            cmd: Inbound compact command.
        """
        if hasattr(self._server._session, "compact"):
            self._server._session.compact()
        self._server._emit(RpcResponse(command="compact", id=cmd.id))

    # ------------------------------------------------------------------
    # Handler registry
    # ------------------------------------------------------------------

    @property
    def handlers(self) -> dict[str, Callable[..., Any]]:
        """Return the command-type → handler mapping.

        Returns:
            Dict suitable for passing to
            :meth:`~chimera.rpc.server.RpcServer.set_handlers`.
        """
        return {
            "prompt": self.handle_prompt,
            "steer": self.handle_steer,
            "cancel": self.handle_cancel,
            "get_state": self.handle_get_state,
            "compact": self.handle_compact,
        }
