"""stdin/stdout JSON-RPC interface for headless agent control."""
from __future__ import annotations

from chimera.rpc.handler import RpcHandler
from chimera.rpc.server import RpcServer
from chimera.rpc.types import (
    CancelCommand,
    CompactCommand,
    ErrorEvent,
    GetStateCommand,
    MessageEvent,
    PromptCommand,
    RpcCommand,
    RpcEvent,
    RpcResponse,
    SetModelCommand,
    StateResponse,
    SteerCommand,
    ToolExecutionEvent,
)

__all__ = [
    "RpcServer",
    "RpcHandler",
    "RpcCommand",
    "RpcEvent",
    "RpcResponse",
    "ErrorEvent",
    "PromptCommand",
    "SteerCommand",
    "CancelCommand",
    "GetStateCommand",
    "CompactCommand",
    "SetModelCommand",
    "MessageEvent",
    "ToolExecutionEvent",
    "StateResponse",
]
