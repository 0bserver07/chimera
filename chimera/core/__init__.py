from chimera.core.agent import Agent
from chimera.core.approval import ApprovalPolicy, AutoApprove, AlwaysDeny, AllowList
from chimera.core.compression import ContextCompressor
from chimera.core.context import Context
from chimera.core.demonstration import DemonstrationPrompt, Example
from chimera.core.instruction import InstructionLayer
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.middleware import (
    EnsureToolCallMiddleware,
    LoggingMiddleware,
    LoopMiddleware,
    MiddlewareChain,
    SafetyNetMiddleware,
)
from chimera.core.loop_detection import LoopDetector
from chimera.core.prompt import Prompt
from chimera.core.streaming import StreamHandler, PrintStreamHandler, CollectStreamHandler
from chimera.core.tool import BaseTool, ContextAwareTool, tool
from chimera.core.tool_group import ToolGroup

# WHY (W2 circular-import bug): DEFAULT_TOOLS / AGENT_TOOLS are exposed via
# ``__getattr__`` rather than imported eagerly. Eager ``from
# chimera.core.tool_group import DEFAULT_TOOLS`` would invoke that module's
# ``__getattr__`` at ``chimera.core`` import time, instantiating tool classes
# while ``chimera.tools.*`` is still being initialised. Lazy attribute lookup
# defers the build to first real consumer access.


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name in {"DEFAULT_TOOLS", "AGENT_TOOLS"}:
        from chimera.core import tool_group as _tg
        return getattr(_tg, name)
    raise AttributeError(f"module 'chimera.core' has no attribute {name!r}")

__all__ = [
    "AGENT_TOOLS", "Agent", "ApprovalPolicy", "AutoApprove", "AlwaysDeny", "AllowList",
    "BaseTool", "CollectStreamHandler", "Context", "ContextAwareTool", "ContextCompressor",
    "DEFAULT_TOOLS", "DemonstrationPrompt", "EnsureToolCallMiddleware", "Example",
    "InstructionLayer", "LoggingMiddleware", "LoopConfig", "LoopDetector",
    "LoopMiddleware", "MiddlewareChain", "PrintStreamHandler", "Prompt", "ReAct",
    "SafetyNetMiddleware", "StreamHandler", "tool", "ToolGroup",
]
