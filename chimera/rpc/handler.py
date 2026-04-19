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
    SetModelCommand,
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

    def handle_set_model(self, cmd: SetModelCommand) -> None:
        """Switch the model used by the agent mid-session.

        Delegates to ``session.set_model(model)`` if available, otherwise
        sets ``provider._model`` directly on the active agent (the
        conventional storage field used by all built-in providers).

        Args:
            cmd: Inbound set_model command.
        """
        if not cmd.model:
            self._server._emit(
                RpcResponse(
                    command="set_model",
                    id=cmd.id,
                    success=False,
                    error="model field is required",
                )
            )
            return

        session = self._server._session
        try:
            if hasattr(session, "set_model"):
                session.set_model(cmd.model)
            else:
                agent = getattr(session, "_agent", None)
                provider = getattr(agent, "provider", None) if agent else None
                if provider is None:
                    raise RuntimeError("session has no agent.provider to update")
                # Built-in providers store model in self._model; model_name
                # is a property that returns it.
                if hasattr(provider, "_model"):
                    provider._model = cmd.model
                elif hasattr(provider, "model"):
                    provider.model = cmd.model
                else:
                    raise RuntimeError(
                        f"provider {type(provider).__name__} exposes no "
                        "writable model field"
                    )
        except Exception as e:
            self._server._emit(
                RpcResponse(
                    command="set_model", id=cmd.id, success=False, error=str(e),
                )
            )
            return
        self._server._emit(RpcResponse(command="set_model", id=cmd.id))

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
            "set_model": self.handle_set_model,
        }
