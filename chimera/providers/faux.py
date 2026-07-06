"""Deterministic, scripted test provider — zero network, zero API cost.

:class:`FauxProvider` plays a fixed *script* of completions. Each step of the
script describes exactly one :meth:`FauxProvider.complete` result: some text, a
set of tool calls, an error, or a thinking-then-text turn. Steps play in order;
when the script is exhausted the provider either repeats its last step or
returns a configurable terminal text (so agent loops stop cleanly).

Unlike an ad-hoc ``unittest.mock`` stub, a :class:`FauxProvider` is a *real*
:class:`~chimera.providers.base.Provider`: it satisfies the full ABC (sync +
async ``complete``/``stream`` via the base defaults), produces well-formed
:class:`~chimera.providers.base.Response` objects with deterministic token
usage, and keeps honest per-call cost accounting. That makes it a drop-in
replacement for hand-rolled scripted providers (the ``AlwaysToolProvider`` /
``FinishesProvider`` pattern that recurs across the test suite) and lets
agent/eval tests exercise real loop, budget, and cost code paths without a key.

Example:
    ```python
    from chimera.providers.faux import FauxProvider

    # A two-turn agent transcript: one tool call, then a fenced answer.
    provider = FauxProvider([
        {"text": "Let me check.", "tool_calls": [{"name": "ping", "arguments": {}}]},
        {"text": "```\\n42\\n```"},
    ])
    resp = provider.complete([Message.user("solve")])
    assert resp.tool_calls[0].name == "ping"
    ```

Determinism guarantees (no ``random``, no wall-clock in accounting):

* Token usage is derived from text length (``len(text) // 4``) and the input
  message sizes, so it is a pure function of the conversation.
* Tool-call ids are drawn from a monotonic counter (``faux-tc-0``, ``faux-tc-1``…).
* Cost is a flat ``cost_per_call`` per completion, accumulated on the provider.
"""

from __future__ import annotations

import itertools
import json
import time
from typing import TYPE_CHECKING, Any

from chimera.providers.base import Provider, Response
from chimera.types import Message, ToolCall

if TYPE_CHECKING:
    from chimera.providers.base import ToolSchema
    from chimera.providers.thinking import ThinkingLevel


class FauxProviderError(RuntimeError):
    """Raised when a scripted ``{"error": ...}`` step is reached.

    Subclasses :class:`RuntimeError` so callers can catch it precisely while
    generic ``except RuntimeError`` handlers still see it.
    """


# A single scripted completion. All keys optional; see module docstring.
#   {"text": str}
#   {"tool_calls": [{"name": str, "arguments": dict}, ...], "text": str?}
#   {"thinking": str, "text": str}
#   {"error": str}
#   {..., "usage": {"input_tokens": int, "output_tokens": int}}  # explicit override
ScriptStep = dict[str, Any]

# Steps play in order until exhausted, then :attr:`on_exhausted` decides what
# happens next.
Script = list[ScriptStep]


def _tokens(text: str) -> int:
    """Rough, deterministic token estimate: one token per ~4 characters."""
    return len(text) // 4


class FauxProvider(Provider):
    """A :class:`~chimera.providers.base.Provider` that plays a fixed script.

    Args:
        script: The completions to play, in order. Accepts a list of step
            dicts, a single step dict, or a bare string (shorthand for
            ``[{"text": script}]``). See the module docstring for the step
            grammar.
        model: The model id reported by :attr:`model_name`. Defaults to
            ``"faux"``. Set to a real id (e.g. ``"claude-sonnet-4"``) if you
            want the loop's :func:`~chimera.providers.cost.calculate_cost` —
            which prices by ``model_name`` — to return non-zero, since ``"faux"``
            is intentionally absent from the pricing table.
        on_exhausted: What to do once the script runs out. ``"final"`` (default)
            returns a single :attr:`final_text` completion with no tool calls,
            so agent loops terminate; ``"repeat"`` replays the last scripted
            step indefinitely.
        final_text: The content returned after exhaustion when
            ``on_exhausted="final"``. Defaults to empty (an empty, tool-less
            completion ends a ReAct loop).
        cost_per_call: Flat dollar cost accrued on the provider per
            :meth:`complete` call. Surfaced via :attr:`total_cost` for
            cost/budget assertions.
        delay_sec: Optional artificial latency (``time.sleep``) applied at the
            start of every :meth:`complete`, for testing timeouts/wall-clock.
        context_window: Value reported by :attr:`context_window`.
        supports_tools: Value reported by :attr:`supports_tool_use`.

    Attributes:
        call_count: Number of successful :meth:`complete` calls served.
        total_cost: Accumulated dollar cost (``call_count * cost_per_call``).
    """

    def __init__(
        self,
        script: Script | ScriptStep | str | None = None,
        *,
        model: str = "faux",
        on_exhausted: str = "final",
        final_text: str = "",
        cost_per_call: float = 0.001,
        delay_sec: float = 0.0,
        context_window: int = 200_000,
        supports_tools: bool = True,
    ) -> None:
        if on_exhausted not in ("final", "repeat"):
            raise ValueError(
                f"on_exhausted must be 'final' or 'repeat', got {on_exhausted!r}",
            )
        self._script = self._normalize_script(script)
        self._model = model
        self._on_exhausted = on_exhausted
        self._final_text = final_text
        self._cost_per_call = cost_per_call
        self._delay_sec = delay_sec
        self._context_window = context_window
        self._supports_tools = supports_tools

        # Playback + accounting state.
        self._step_index = 0
        self._ids = itertools.count()
        self.call_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_script(script: Script | ScriptStep | str | None) -> Script:
        """Coerce the many accepted script shapes into a ``list[dict]``."""
        if script is None:
            return []
        if isinstance(script, str):
            return [{"text": script}]
        if isinstance(script, dict):
            return [script]
        return list(script)

    @classmethod
    def coding_script(
        cls,
        answer: str,
        tool_rounds: int = 1,
        *,
        tool_name: str = "bash",
        language: str = "python",
        **kwargs: Any,
    ) -> FauxProvider:
        """Build a provider that mimics a realistic coding-agent transcript.

        Produces *tool_rounds* tool-calling turns followed by a final turn whose
        text is the fenced *answer* — the common eval-test shape (the agent pokes
        around with tools, then emits the artifact in a code fence). The final
        turn has no tool calls, so a ReAct loop finishes on it and returns the
        fenced answer as its output.

        Args:
            answer: The solution text placed inside the final code fence.
            tool_rounds: How many tool-calling turns precede the answer.
            tool_name: Name of the tool invoked each round. Defaults to
                ``"bash"``; pass the name of a tool your agent actually has so
                the calls execute rather than error.
            language: Info string for the closing code fence.
            **kwargs: Forwarded to the :class:`FauxProvider` constructor
                (e.g. ``model=``, ``cost_per_call=``).

        Returns:
            A ready-to-use :class:`FauxProvider`.
        """
        script: Script = []
        for i in range(tool_rounds):
            script.append({
                "text": f"Step {i + 1}: inspecting with {tool_name}.",
                "tool_calls": [{"name": tool_name, "arguments": {}}],
            })
        script.append({"text": f"Here is the solution:\n```{language}\n{answer}\n```"})
        return cls(script, **kwargs)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    @property
    def exhausted(self) -> bool:
        """``True`` once every scripted step has been played at least once."""
        return self._step_index >= len(self._script)

    @property
    def remaining_steps(self) -> int:
        """Number of unplayed scripted steps (0 once exhausted)."""
        return max(0, len(self._script) - self._step_index)

    def reset(self) -> None:
        """Rewind playback and zero all accounting — replay from the top."""
        self._step_index = 0
        self._ids = itertools.count()
        self.call_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0

    def _next_step(self) -> ScriptStep:
        """Return the step for this call, advancing the playhead."""
        if self._step_index < len(self._script):
            step = self._script[self._step_index]
            self._step_index += 1
            return step
        # Exhausted.
        if self._on_exhausted == "repeat" and self._script:
            return self._script[-1]
        return {"text": self._final_text}

    def _build_tool_calls(self, step: ScriptStep) -> list[ToolCall]:
        """Materialise ``ToolCall`` objects with deterministic ids."""
        calls: list[ToolCall] = []
        for spec in step.get("tool_calls", []) or []:
            calls.append(ToolCall(
                id=f"faux-tc-{next(self._ids)}",
                name=spec["name"],
                arguments=dict(spec.get("arguments", {})),
            ))
        return calls

    # ------------------------------------------------------------------
    # Provider API
    # ------------------------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: ThinkingLevel | None = None,
        cancel_event: Any | None = None,
        **kwargs: Any,
    ) -> Response:
        """Play the next scripted step and return it as a :class:`Response`.

        Raises:
            FauxProviderError: If the scripted step is an ``{"error": ...}`` step.
        """
        if self._delay_sec > 0:
            time.sleep(self._delay_sec)

        step = self._next_step()

        # Error steps surface as a provider error. Accounting is untouched so a
        # failed call does not perturb call_count/cost assertions.
        if "error" in step:
            raise FauxProviderError(str(step["error"]))

        text = str(step.get("text", ""))
        thinking_text = str(step.get("thinking", ""))
        tool_calls = self._build_tool_calls(step)

        # Deterministic usage: output tokens from the produced text + serialized
        # tool calls; input tokens from the incoming conversation size.
        tool_blob = "".join(
            tc.name + json.dumps(tc.arguments, sort_keys=True) for tc in tool_calls
        )
        input_tokens = sum(_tokens(m.content or "") for m in messages)
        output_tokens = _tokens(text + tool_blob)

        usage: dict[str, int] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        if thinking_text:
            usage["thinking_tokens"] = _tokens(thinking_text)
        # Explicit per-step override wins (advanced tests that pin exact numbers).
        override = step.get("usage")
        if isinstance(override, dict):
            usage.update(override)

        # Accounting (successful calls only).
        self.call_count += 1
        self.total_input_tokens += usage["input_tokens"]
        self.total_output_tokens += usage["output_tokens"]
        self.total_cost += self._cost_per_call

        return Response(content=text, tool_calls=tool_calls, usage=usage)

    # ``stream()`` / ``async_complete()`` / ``async_stream()`` are inherited
    # unchanged from :class:`~chimera.providers.base.Provider`: the base
    # ``stream`` default calls ``complete()`` once and re-emits its content as a
    # ``text_delta`` event, one ``tool_call_start`` per tool call, and a final
    # ``done`` event carrying the usage dict — exactly the deterministic content
    # this provider produces. A scripted ``{"error": ...}`` step therefore
    # surfaces from ``stream()`` too (the base impl calls ``complete()`` on first
    # iteration, which raises). The async variants bridge these via executor/queue.

    @property
    def total_tokens(self) -> int:
        """Sum of accumulated input and output tokens across all calls."""
        return self.total_input_tokens + self.total_output_tokens

    @property
    def context_window(self) -> int:
        return self._context_window

    @property
    def supports_tool_use(self) -> bool:
        return self._supports_tools

    @property
    def model_name(self) -> str:
        return self._model


# Self-registration, mirroring the idiom at the bottom of ``anthropic.py``.
# ``create_provider(provider_type="faux", model="faux")`` resolves once this
# module has been imported. The registry's ``_ensure_builtins_registered`` does
# NOT import this module (faux is a test/dev provider, not a shipping backend),
# so importing ``chimera.providers.faux`` is what fires the registration.
from chimera.providers.registry import register_provider as _register  # noqa: E402

_register(
    "faux",
    lambda model="faux", api_key=None, base_url=None, script=None, **kw: FauxProvider(
        script, model=model or "faux", **kw,
    ),
)


__all__ = ["FauxProvider", "FauxProviderError"]
