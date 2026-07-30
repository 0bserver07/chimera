"""Plan-gate policy pack: no write / edit / shell tool calls before a plan.

A worked policy on two interception seams:

- ``tool_call`` (fail-closed): calls whose tool name is in *gated_tools*
  are blocked — with an instructive reason the model can act on — until
  the gate is open; a call to any tool in *plan_tools* opens it.
- ``context`` (fail-open): watches the outgoing message list and, before
  every provider call, re-derives the calling conversation's gate state
  from the messages themselves — so every user turn starts gated again
  and concurrent conversations never share a gate.
"""
from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from chimera.core.interception import InterceptDecision, Interceptors
from chimera.plugins.base import BasePlugin
from chimera.plugins.registry import PluginExtensionRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from chimera.plugins.base import ComponentRegistry
    from chimera.types import Message, ToolCall

__all__ = ["DEFAULT_GATED_TOOLS", "DEFAULT_PLAN_TOOLS", "PlanGatePlugin"]

#: Tool names blocked until a plan is recorded (the write/edit/shell class).
DEFAULT_GATED_TOOLS: frozenset[str] = frozenset({
    "apply_patch",
    "bash",
    "edit_file",
    "replace_in_file",
    "write_file",
})

#: Tool names whose call counts as recording a plan.
DEFAULT_PLAN_TOOLS: frozenset[str] = frozenset({"think", "todo"})

#: Soft cap on tracked execution lanes; least-recently-written entries are
#: evicted past it (an evicted live lane re-arms — fail-closed — and is
#: rebuilt on its next ``context`` firing).
_MAX_TRACKED_LANES = 256

#: A lane key: ``(thread ident, id(asyncio task) or None)``.
_LaneKey = tuple[int, int | None]


class PlanGatePlugin(BasePlugin):
    """Block write / edit / shell tool calls until the agent records a plan.

    What "a plan exists" means here — the honest heuristic: the model has
    **issued** a call to a planning tool (``think`` or ``todo`` by
    default) since the most recent user message. Issuing is enough: the
    gate opens when the planning call is proposed on the ``tool_call``
    seam, before it executes, so the gate works even when no planning
    tool is installed (the call errors, the plan still counts). The gate
    does not read the plan or judge its quality. Any new user-role
    message — the next turn, or a mid-run steering injection — re-arms
    the gate.

    Conversation identity — how concurrent agents are kept apart: the
    seams carry no session id, so the gate keys its state by *execution
    lane*, the ``(thread, asyncio task)`` pair the loop runs on, and
    re-derives each lane's truth from the message list itself on every
    ``context`` firing (a planning call issued after the last user-role
    message opens the gate; anything else arms it). Every shipped loop
    fires the ``context`` and ``tool_call`` seams inline on the lane
    that drives it, so concurrent agents — multiplexer lanes as asyncio
    tasks on one thread, REPL turns on worker threads, strategy loops on
    their bridge threads, or plain sequential reuse of one thread — each
    get an independent gate.

    Honest limits:

    - Conversation identity is inferred from the execution lane, not
      passed in. A lane with no recorded state fails closed (armed). The
      one topology outside the model: a host that manually interleaves
      two conversations' event streams inside a single asyncio task (or
      migrates one step across threads mid-step) makes them share a lane
      for at most one step — the next provider call recomputes from the
      conversation itself. No shipped runner does either.
    - The gate trusts the conversation record: a compaction that
      rewrites away this turn's planning call re-arms the gate — it
      fails closed (the model must plan again), never open.
    - Tool names are matched exactly as the loop dispatches them. The
      defaults cover Chimera's built-in spellings; namespaced variants
      (e.g. MCP-style ``mcp__<server>__<tool>``) need explicit entries in
      *gated_tools* / *plan_tools*.
    - The re-arm watcher rides the ``context`` seam, which fires before
      each provider call; a run that never reaches a second provider call
      keeps whatever gate state its lane had. Registering the
      ``tool_call`` gate without the watcher (hand-picked chains) leaves
      each lane armed until that lane issues a planning call.

    Args:
        gated_tools: Tool names to block until a plan exists. Defaults to
            :data:`DEFAULT_GATED_TOOLS`.
        plan_tools: Tool names whose call opens the gate. Defaults to
            :data:`DEFAULT_PLAN_TOOLS`.

    Example:
        ```python
        from chimera.plugins import PluginManager
        from chimera.plugins.packs import PlanGatePlugin

        PluginManager().load_plugin(PlanGatePlugin())
        # Every assembled agent now refuses writes until it has planned.
        ```
    """

    version = "1.0.0"
    description = "Block write/edit/shell tool calls until a plan is recorded."
    author = "Chimera Contributors"

    def __init__(
        self,
        *,
        gated_tools: Iterable[str] | None = None,
        plan_tools: Iterable[str] | None = None,
    ) -> None:
        self._gated = (
            frozenset(gated_tools) if gated_tools is not None else DEFAULT_GATED_TOOLS
        )
        self._plan_tools = (
            frozenset(plan_tools) if plan_tools is not None else DEFAULT_PLAN_TOOLS
        )
        # Gate state per execution lane, insertion-ordered for eviction.
        self._plan_seen_by_lane: dict[_LaneKey, bool] = {}
        self._state_lock = threading.Lock()
        self._registered: list[tuple[str, Callable[..., InterceptDecision | None]]] = []

    @property
    def name(self) -> str:
        """Unique plugin name."""
        return "plan-gate"

    # -- interceptors ----------------------------------------------------

    def interceptors(self) -> Interceptors:
        """This pack's chains as one bundle, for host-side use.

        Returns:
            An :class:`~chimera.core.interception.Interceptors` carrying
            the gate (``tool_call``) and the re-arm watcher (``context``)
            — pass it to ``CodingAgent(interceptors=...)`` or a
            ``LoopConfig`` to use the policy without the plugin system.
        """
        return Interceptors(
            tool_call=[self._gate_tool_call],
            context=[self._watch_context],
        )

    def register_interceptors(self, registry: ComponentRegistry) -> None:
        """Register the gate and the re-arm watcher on their seams."""
        self._registered = [
            ("tool_call", self._gate_tool_call),
            ("context", self._watch_context),
        ]
        for seam, fn in self._registered:
            PluginExtensionRegistry.register_interceptor(seam, fn)

    def deactivate(self) -> None:
        """Withdraw the pack's chains and reset every lane's gate."""
        for seam, fn in self._registered:
            PluginExtensionRegistry.unregister_interceptor(seam, fn)
        self._registered = []
        with self._state_lock:
            self._plan_seen_by_lane.clear()

    # -- lane identity ----------------------------------------------------

    @staticmethod
    def _lane_key() -> _LaneKey:
        """Identity of the current execution lane: ``(thread, asyncio task)``.

        Both shipped loops fire the ``context`` and ``tool_call`` seams
        inline in whatever iterates them, so within one step the two
        seams observe the same lane: sync loops run on one thread (task
        component ``None``); the async loop's generator resumes inside
        the task that drives it, so concurrent lanes on one event-loop
        thread are told apart by their task.
        """
        try:
            task = asyncio.current_task()
        except RuntimeError:  # no running event loop on this thread
            task = None
        return (threading.get_ident(), id(task) if task is not None else None)

    def _plan_recorded(self, messages: list[Message]) -> bool:
        """True when a plan-tool call was issued after the last user message.

        The pure recompute behind the gate: scans the conversation the
        loop is about to send for an assistant tool call whose name is in
        *plan_tools*, strictly after the last user-role message (or
        anywhere, when no user message exists). Issued is enough — the
        loops record the assistant message with its proposed tool calls
        whether or not the tool exists or its execution succeeded.
        """
        last_user = -1
        for index, message in enumerate(messages):
            if getattr(message, "role", None) == "user":
                last_user = index
        for message in messages[last_user + 1:]:
            for tc in getattr(message, "tool_calls", None) or []:
                if getattr(tc, "name", None) in self._plan_tools:
                    return True
        return False

    def _set_lane(self, key: _LaneKey, plan_seen: bool) -> None:
        """Record *plan_seen* for *key* (most-recently-written last)."""
        with self._state_lock:
            self._plan_seen_by_lane.pop(key, None)
            self._plan_seen_by_lane[key] = plan_seen
            self._prune_locked(key)

    def _prune_locked(self, current: _LaneKey) -> None:
        """Drop dead-thread entries and bound the table. Caller holds the lock."""
        alive = {t.ident for t in threading.enumerate()}
        for key in [k for k in self._plan_seen_by_lane if k[0] not in alive]:
            del self._plan_seen_by_lane[key]
        while len(self._plan_seen_by_lane) > _MAX_TRACKED_LANES:
            oldest = next(iter(self._plan_seen_by_lane))
            if oldest == current:
                break
            del self._plan_seen_by_lane[oldest]

    # -- seam callables --------------------------------------------------

    def _watch_context(self, messages: list[Message]) -> InterceptDecision | None:
        """``context`` seam: bind this lane's gate to the calling conversation.

        Fires before every provider call; the recompute both re-arms on a
        new user message (nothing follows it yet) and clears any stale
        state a reused thread or recycled task id might have left behind.
        """
        self._set_lane(self._lane_key(), self._plan_recorded(messages))
        return None

    def _gate_tool_call(self, call: ToolCall) -> InterceptDecision | None:
        """``tool_call`` seam: open on plan tools, block gated tools until then."""
        key = self._lane_key()
        if call.name in self._plan_tools:
            self._set_lane(key, True)
            return None
        if call.name in self._gated:
            with self._state_lock:
                plan_seen = self._plan_seen_by_lane.get(key, False)
            if not plan_seen:
                plan_names = ", ".join(sorted(self._plan_tools))
                return InterceptDecision.block(
                    f"plan-gate: no plan recorded this turn — call one of "
                    f"[{plan_names}] to record a plan before using {call.name}"
                )
        return None
