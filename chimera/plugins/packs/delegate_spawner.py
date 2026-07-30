"""Delegate-spawner policy pack: route matching tool calls to a sub-agent.

A worked policy on the ``tool_call`` seam (fail-closed): when the model
issues a call whose name matches, the call is rewritten — same call id —
into a call to the ``delegate`` tool, so the loop dispatches a sub-agent
instead. Sub-agent spawning as plugin policy, with core untouched.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from chimera.core.interception import InterceptDecision, Interceptors
from chimera.plugins.base import BasePlugin
from chimera.plugins.registry import PluginExtensionRegistry
from chimera.types import ToolCall

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from chimera.plugins.base import ComponentRegistry

__all__ = ["DelegateSpawnerPlugin"]


class DelegateSpawnerPlugin(BasePlugin):
    """Rewrite matching tool calls into ``delegate`` sub-agent calls.

    Matching: a call matches when its name is in *names*, or starts with
    *prefix* (default ``spawn_``, so ``spawn_research``, ``spawn_review``,
    … all route to the sub-agent). Calls already addressed to
    *delegate_tool* are never rewritten.

    The rewritten call keeps the original call id (the ``tool_call`` seam
    guarantees it) and carries one argument, ``task``: the original call's
    ``task`` (or ``prompt``) string when present, otherwise the remaining
    arguments rendered as JSON prefixed with the original tool name — so
    the sub-agent always receives something actionable.

    Honest limits:

    - The host's tool set must actually include the delegate tool
      (:class:`~chimera.tools.delegate.DelegateTool`, or any tool
      answering to *delegate_tool*). It ships outside the default
      interactive tool set — add it via ``extra_tools=`` / a preset /
      another plugin. When it is absent, the rewritten call surfaces as
      an ``Unknown tool: delegate`` error result; the seam routes calls,
      it cannot conjure the tool.
    - The original tool name survives only inside the rendered task text
      (and always, in the JSON-fallback rendering); a sub-agent that
      needs the original arguments verbatim should receive them via that
      fallback, not via a bare ``task`` string.

    Args:
        prefix: Name prefix that marks a call for delegation. An empty
            string disables prefix matching.
        names: Exact tool names to delegate, in addition to the prefix.
        delegate_tool: Name of the delegate tool to route to.

    Example:
        ```python
        from chimera.plugins import PluginManager
        from chimera.plugins.packs import DelegateSpawnerPlugin

        PluginManager().load_plugin(
            DelegateSpawnerPlugin(names=("deep_research",))
        )
        ```
    """

    version = "1.0.0"
    description = "Rewrite matching tool calls into delegate sub-agent calls."
    author = "Chimera Contributors"

    def __init__(
        self,
        *,
        prefix: str = "spawn_",
        names: Iterable[str] | None = None,
        delegate_tool: str = "delegate",
    ) -> None:
        self._prefix = prefix
        self._names = frozenset(names) if names is not None else frozenset()
        self._delegate_tool = delegate_tool
        self._registered: list[tuple[str, Callable[..., InterceptDecision | None]]] = []

    @property
    def name(self) -> str:
        """Unique plugin name."""
        return "delegate-spawner"

    # -- interceptors ----------------------------------------------------

    def interceptors(self) -> Interceptors:
        """This pack's chain as one bundle, for host-side use.

        Returns:
            An :class:`~chimera.core.interception.Interceptors` carrying
            the rewrite on the ``tool_call`` seam.
        """
        return Interceptors(tool_call=[self._spawn])

    def register_interceptors(self, registry: ComponentRegistry) -> None:
        """Register the rewrite on the ``tool_call`` seam."""
        self._registered = [("tool_call", self._spawn)]
        for seam, fn in self._registered:
            PluginExtensionRegistry.register_interceptor(seam, fn)

    def deactivate(self) -> None:
        """Withdraw the pack's chain."""
        for seam, fn in self._registered:
            PluginExtensionRegistry.unregister_interceptor(seam, fn)
        self._registered = []

    # -- seam callable ---------------------------------------------------

    def _matches(self, tool_name: str) -> bool:
        """Whether *tool_name* marks a call for delegation."""
        if tool_name == self._delegate_tool:
            return False
        if tool_name in self._names:
            return True
        return bool(self._prefix) and tool_name.startswith(self._prefix)

    def _spawn(self, call: ToolCall) -> InterceptDecision | None:
        """``tool_call`` seam: rewrite a matching call into a delegate call."""
        if not self._matches(call.name):
            return None
        task = call.arguments.get("task") or call.arguments.get("prompt")
        if not isinstance(task, str) or not task.strip():
            rest = {
                key: value for key, value in call.arguments.items()
                if key not in ("task", "prompt")
            }
            task = f"{call.name}: {json.dumps(rest, sort_keys=True, default=str)}"
        return InterceptDecision.replace(
            ToolCall(id=call.id, name=self._delegate_tool, arguments={"task": task})
        )
