"""Detect-and-correct quality issues in small-model output streams.

Extends :mod:`chimera.shrew.extensions.repeat_detection` with the four
quality checks the upstream small-coder reference implementation calls
``quality-monitor``:

1. **Empty / whitespace-only response.** The model returned no usable
   text or tool call. Most often a sign that the chat template ate the
   reply or the temperature pushed every token below the stop sentinel.
2. **Hallucinated tool name.** The model emitted a text-mode tool call
   referencing a tool that isn't in the live registry — typically a
   variation it saw in training data (``write_file`` instead of
   ``write``, ``run_shell`` instead of ``bash``).
3. **Self-correction language.** The model said it'll try again
   ("let me try again", "actually...", "I apologize, let me correct")
   without actually changing course. We treat this as a stuck signal:
   inject a structured corrective message that names the user's
   original goal so the next turn doesn't drift into another apology.
4. **Loop / repetition.** Delegated to
   :func:`chimera.shrew.extensions.repeat_detection.detect_short_loop`
   over a recent-action sequence.

Public surface:

* :class:`QualityIssue` — enum-like tag for the detected problem.
* :class:`QualityReport` — structured detection result + suggested
  correction text.
* :func:`assess_response` — pure detector; returns a list of issues.
* :func:`build_correction_message` — render a corrective system /
  tool-result message for the next turn given a report.
* :class:`QualityMonitor` — stateful wrapper that holds the
  recent-actions buffer for repeat detection and exposes a single
  ``observe(text, tool_calls) -> QualityReport`` entry-point.

Stdlib only. Pure-function detectors so the shrew loop can call them
inline; the stateful :class:`QualityMonitor` is the convenience class
the REPL reaches for.
"""
from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from chimera.shrew.extensions.repeat_detection import (
    DEFAULT_MIN_REPEATS,
    DEFAULT_WINDOW,
    detect_short_loop,
)

__all__ = [
    "CORRECTION_TEMPLATES",
    "DEFAULT_HISTORY_SIZE",
    "QualityIssue",
    "QualityMonitor",
    "QualityReport",
    "assess_response",
    "build_correction_message",
    "detect_correction_language",
    "detect_empty_response",
    "detect_hallucinated_tool",
]


DEFAULT_HISTORY_SIZE: Final[int] = 8
"""How many recent (assistant text + tool call) tuples the monitor keeps.

Eight is enough to catch every loop the existing
:func:`detect_short_loop` window cares about (``window=4`` × min_repeats=2
+ headroom) without growing context-bound. Each entry is a small tuple
so memory cost stays trivial.
"""


# ---------------------------------------------------------------------------
# Issue taxonomy
# ---------------------------------------------------------------------------


class QualityIssue(str, Enum):
    """Categorical tags for the detector outputs.

    Subclassing ``str`` so the enum members are JSON-serialisable
    without a custom encoder — the shrew event log keeps the tag
    on every step.
    """

    EMPTY_RESPONSE = "empty_response"
    """Assistant emitted no usable text and no tool calls."""

    HALLUCINATED_TOOL = "hallucinated_tool"
    """At least one emitted tool name is not in the live registry."""

    CORRECTION_LANGUAGE = "correction_language"
    """The model is in 'apologise / try again' mode without progress."""

    LOOP_DETECTED = "loop_detected"
    """Recent actions repeat per
    :func:`chimera.shrew.extensions.repeat_detection.detect_short_loop`."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityReport:
    """Outcome of a single :func:`assess_response` / monitor call.

    Attributes:
        issues: Tuple of :class:`QualityIssue` tags detected on this
            turn. Empty when the turn looks healthy.
        unknown_tool_names: Tool names the model emitted that aren't in
            the registry. Only meaningful when
            :attr:`QualityIssue.HALLUCINATED_TOOL` is in :attr:`issues`.
        loop_cycle: Detected cycle length from the repeat detector
            (``0`` when no loop). Stored so the corrective message can
            mention "stuck on the same N-step pattern".
        correction_phrase: The matched correction-language fragment
            (lowercased) or empty string. Stored for logging and tests.
    """

    issues: tuple[QualityIssue, ...] = ()
    unknown_tool_names: tuple[str, ...] = ()
    loop_cycle: int = 0
    correction_phrase: str = ""

    @property
    def healthy(self) -> bool:
        """``True`` when there are no issues."""
        return not self.issues

    def has(self, issue: QualityIssue) -> bool:
        """Return ``True`` if ``issue`` was detected on this turn."""
        return issue in self.issues


# ---------------------------------------------------------------------------
# Empty-response detector
# ---------------------------------------------------------------------------


_WHITESPACE_ONLY: Final[re.Pattern[str]] = re.compile(r"^\s*$")


def detect_empty_response(
    text: str,
    tool_calls: Sequence[object] = (),
) -> bool:
    """Return ``True`` when the assistant produced no useful output.

    "Useful output" = at least one tool call **or** non-whitespace
    text. We deliberately accept *any* tool call as a non-empty
    signal (even a hallucinated one) because the
    :class:`QualityIssue.HALLUCINATED_TOOL` axis already covers that
    case separately and we don't want the monitor to fire two issues
    on the same root cause.
    """
    if tool_calls:
        return False
    if not text:
        return True
    return bool(_WHITESPACE_ONLY.match(text))


# ---------------------------------------------------------------------------
# Hallucinated-tool detector
# ---------------------------------------------------------------------------


def detect_hallucinated_tool(
    tool_calls: Iterable[object],
    registry: Iterable[str],
) -> tuple[str, ...]:
    """Return the tool names in ``tool_calls`` that aren't in ``registry``.

    ``tool_calls`` is duck-typed: each element may be a
    :class:`chimera.types.ToolCall`, a :class:`ParsedToolCall`, or any
    object with a ``name`` attribute. Names are compared
    case-sensitively because the registry is the source of truth — if
    a small model emits ``BASH`` instead of ``bash`` we *want* to
    surface that as a hallucination so the corrective message can
    nudge it back to the canonical case.

    Returns:
        Tuple of unknown names in input order, deduplicated. Empty
        tuple when every name is in the registry.
    """
    known = set(registry)
    seen: set[str] = set()
    unknown: list[str] = []
    for call in tool_calls:
        name = getattr(call, "name", None)
        if not isinstance(name, str) or not name:
            continue
        if name in known or name in seen:
            continue
        if name not in known:
            unknown.append(name)
            seen.add(name)
    return tuple(unknown)


# ---------------------------------------------------------------------------
# Correction-language detector
# ---------------------------------------------------------------------------


# These phrases are the textbook "stuck" signals — the model produced
# words that look like progress while taking no action. Order matters
# only insofar as the FIRST hit is reported in :class:`QualityReport`;
# otherwise any hit fires the issue.
_CORRECTION_PHRASES: Final[tuple[str, ...]] = (
    "let me try again",
    "let me try a different",
    "let me try once more",
    "let me try this differently",
    "let me retry",
    "i apologize",
    "i apologise",
    "my apologies",
    "i made a mistake",
    "let me correct",
    "let me fix that",
    "actually, let me",
    "actually let me",
    "wait, let me",
    "hmm, let me",
    "on second thought",
    "let me reconsider",
    "let me re-examine",
    "let me reread",
    "let me re-read",
    "let me start over",
    "starting over",
    "i need to reconsider",
)


def detect_correction_language(text: str) -> str:
    """Return the first correction phrase that appears in ``text``, or ``""``.

    Search is case-insensitive; whitespace runs are collapsed to single
    spaces before matching so multi-line / pasted-from-thinking text
    still hits. Returns an empty string when no phrase matches so the
    caller can use the result both as a truthiness signal and as a
    log entry.
    """
    if not text:
        return ""
    flat = " ".join(text.split()).lower()
    for phrase in _CORRECTION_PHRASES:
        if phrase in flat:
            return phrase
    return ""


# ---------------------------------------------------------------------------
# Aggregate detector
# ---------------------------------------------------------------------------


def assess_response(
    text: str,
    tool_calls: Sequence[object] = (),
    *,
    registry: Iterable[str] | None = None,
    recent_actions: Sequence[object] = (),
    loop_window: int = DEFAULT_WINDOW,
    loop_min_repeats: int = DEFAULT_MIN_REPEATS,
) -> QualityReport:
    """Run every detector and return a combined :class:`QualityReport`.

    Detectors are pure: feeding the same inputs always returns the
    same report. The state needed for loop detection
    (``recent_actions``) is supplied by the caller — the stateful
    wrapper :class:`QualityMonitor` packages that bookkeeping.

    Args:
        text: The assistant message body for this turn.
        tool_calls: Tool calls (native or parsed). Each must expose a
            ``name`` attribute.
        registry: Optional iterable of known tool names for
            hallucination detection. ``None`` skips the check (use this
            in tests where the registry isn't relevant).
        recent_actions: Sequence used by the loop detector. Each entry
            should be a hashable / comparable summary of one prior
            action (e.g. ``("bash", "ls")`` tuples). The current turn's
            action is *not* automatically appended — the caller
            controls when to push.
        loop_window: Forwarded to
            :func:`chimera.shrew.extensions.repeat_detection.detect_short_loop`.
        loop_min_repeats: Same.

    Returns:
        A :class:`QualityReport` summarising every issue found.
    """
    issues: list[QualityIssue] = []
    unknown: tuple[str, ...] = ()
    loop_cycle = 0
    phrase = ""

    if detect_empty_response(text, tool_calls):
        issues.append(QualityIssue.EMPTY_RESPONSE)

    if registry is not None:
        unknown = detect_hallucinated_tool(tool_calls, registry)
        if unknown:
            issues.append(QualityIssue.HALLUCINATED_TOOL)

    phrase = detect_correction_language(text)
    if phrase:
        issues.append(QualityIssue.CORRECTION_LANGUAGE)

    if recent_actions:
        loop_cycle = detect_short_loop(
            recent_actions,
            window=loop_window,
            min_repeats=loop_min_repeats,
        )
        if loop_cycle > 0:
            issues.append(QualityIssue.LOOP_DETECTED)

    return QualityReport(
        issues=tuple(issues),
        unknown_tool_names=unknown,
        loop_cycle=loop_cycle,
        correction_phrase=phrase,
    )


# ---------------------------------------------------------------------------
# Correction-message builder
# ---------------------------------------------------------------------------


CORRECTION_TEMPLATES: Final[dict[QualityIssue, str]] = {
    QualityIssue.EMPTY_RESPONSE: (
        "Your previous response was empty. Please continue the task: "
        "either invoke a tool to make progress or describe the next "
        "concrete step. Do not output an empty turn."
    ),
    QualityIssue.HALLUCINATED_TOOL: (
        "The tool name(s) {names} are not available in this session. "
        "Available tools: {available}. Re-issue the call using one of "
        "the available tool names, or use the closest match listed."
    ),
    QualityIssue.CORRECTION_LANGUAGE: (
        "I notice you said '{phrase}' but did not change your approach. "
        "Pick one concrete next action for the original task and execute "
        "it — either with a tool call or a single declarative step."
    ),
    QualityIssue.LOOP_DETECTED: (
        "You have repeated the same {cycle}-step pattern. Stop and "
        "summarise: what is the goal, what did you try, why did it not "
        "advance? Then propose a different approach (different tool, "
        "different argument, or ask for clarification)."
    ),
}
"""Per-issue corrective templates rendered by :func:`build_correction_message`.

Keys are :class:`QualityIssue` members; values are str.format templates.
The shrew loop sends the rendered message as a system note to the
agent before the next turn so the model has explicit instructions to
recover from the detected failure mode.
"""


def build_correction_message(
    report: QualityReport,
    *,
    available_tools: Sequence[str] = (),
) -> str:
    """Render a single corrective message covering every issue in ``report``.

    The output is one paragraph per issue, joined with blank lines so
    the rendered system note stays readable. When ``report`` is healthy
    (``report.healthy == True``) returns the empty string so the caller
    can skip the injection altogether.

    Args:
        report: The detector output.
        available_tools: Names to surface in the
            :data:`QualityIssue.HALLUCINATED_TOOL` template. When
            empty the template still renders but the substitution
            uses ``"(none configured)"``.

    Returns:
        A multi-paragraph correction message, or ``""`` when nothing
        needs correcting.
    """
    if report.healthy:
        return ""
    chunks: list[str] = []
    available = ", ".join(sorted(available_tools)) if available_tools else "(none configured)"
    for issue in report.issues:
        template = CORRECTION_TEMPLATES.get(issue)
        if not template:
            continue
        if issue is QualityIssue.HALLUCINATED_TOOL:
            names = ", ".join(report.unknown_tool_names) or "(unknown)"
            chunks.append(template.format(names=names, available=available))
        elif issue is QualityIssue.CORRECTION_LANGUAGE:
            chunks.append(template.format(phrase=report.correction_phrase))
        elif issue is QualityIssue.LOOP_DETECTED:
            chunks.append(template.format(cycle=max(1, report.loop_cycle)))
        else:
            chunks.append(template)
    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Stateful wrapper
# ---------------------------------------------------------------------------


class QualityMonitor:
    """Stateful detector that holds the recent-actions buffer.

    Lifecycle:

    1. Build one :class:`QualityMonitor` per shrew session, optionally
       passing the live tool registry (a list of names).
    2. After each turn, call :meth:`observe` with the assistant text +
       tool calls. The monitor pushes the action summary onto its
       internal deque, runs every detector, and returns a
       :class:`QualityReport`.
    3. If :attr:`QualityReport.healthy` is ``False``, the caller asks
       :meth:`build_followup` for a corrective message to send back to
       the model on the next turn.

    Thread-safety: not thread-safe. The shrew REPL touches the monitor
    only from the main thread (between agent invocations).
    """

    def __init__(
        self,
        registry: Iterable[str] | None = None,
        *,
        history_size: int = DEFAULT_HISTORY_SIZE,
        loop_window: int = DEFAULT_WINDOW,
        loop_min_repeats: int = DEFAULT_MIN_REPEATS,
    ) -> None:
        """Construct a monitor.

        Args:
            registry: Iterable of known tool names for hallucination
                detection. Stored as a frozen set so callers can mutate
                their registry without affecting the monitor's snapshot.
                ``None`` disables the hallucination check.
            history_size: Cap on the recent-actions deque. Defaults to
                :data:`DEFAULT_HISTORY_SIZE`.
            loop_window: Forwarded to the loop detector.
            loop_min_repeats: Forwarded to the loop detector.
        """
        self._registry: frozenset[str] | None = (
            frozenset(registry) if registry is not None else None
        )
        self._actions: deque[object] = deque(maxlen=max(2, int(history_size)))
        self._loop_window = max(1, int(loop_window))
        self._loop_min_repeats = max(1, int(loop_min_repeats))
        self._last_report: QualityReport = QualityReport()

    @property
    def registry(self) -> frozenset[str] | None:
        """Snapshot of the current tool registry, or ``None`` if disabled."""
        return self._registry

    @property
    def recent_actions(self) -> tuple[object, ...]:
        """Read-only view of the recent-actions deque."""
        return tuple(self._actions)

    @property
    def last_report(self) -> QualityReport:
        """The most recent :meth:`observe` output (empty before the first call)."""
        return self._last_report

    def update_registry(self, registry: Iterable[str]) -> None:
        """Replace the known-tool snapshot.

        Useful when the shrew tool set changes mid-session
        (``--allowed-tools`` toggle, plugin load).
        """
        self._registry = frozenset(registry)

    def reset(self) -> None:
        """Clear all state (recent actions, last report).

        Called by the REPL on session reset / ``/clear``.
        """
        self._actions.clear()
        self._last_report = QualityReport()

    def observe(
        self,
        text: str,
        tool_calls: Sequence[object] = (),
    ) -> QualityReport:
        """Run every detector for the just-finished turn and update state.

        The action summary appended to the recent-actions deque is the
        tuple ``(name, fingerprint)`` for each tool call, plus a
        ``("text", first_120_chars)`` entry when there were no tool
        calls. This gives the loop detector something concrete to
        compare across turns even when the model is rambling without
        invoking tools.

        Args:
            text: Assistant message body.
            tool_calls: Iterable of tool-call-shaped objects (with a
                ``name`` attribute and optionally ``arguments`` dict).

        Returns:
            The :class:`QualityReport`. Also stored on
            :attr:`last_report`.
        """
        # Record the action summary BEFORE assessing so the loop
        # detector can include this turn in the comparison.
        self._push_action_summary(text, tool_calls)
        report = assess_response(
            text,
            tool_calls,
            registry=self._registry,
            recent_actions=tuple(self._actions),
            loop_window=self._loop_window,
            loop_min_repeats=self._loop_min_repeats,
        )
        self._last_report = report
        return report

    def build_followup(self) -> str:
        """Render the corrective follow-up for the current ``last_report``.

        Returns ``""`` when the last report was healthy. Convenience
        over calling :func:`build_correction_message` directly.
        """
        return build_correction_message(
            self._last_report,
            available_tools=tuple(self._registry or ()),
        )

    # -- internals --------------------------------------------------------

    def _push_action_summary(
        self,
        text: str,
        tool_calls: Sequence[object],
    ) -> None:
        """Append a fingerprint of this turn to the recent-actions deque."""
        if tool_calls:
            for call in tool_calls:
                name = getattr(call, "name", "?")
                args = getattr(call, "arguments", None)
                fp = _fingerprint_args(args)
                self._actions.append((name, fp))
        else:
            head = (text or "").strip()[:120]
            self._actions.append(("text", head))


def _fingerprint_args(args: object) -> str:
    """Stable, short fingerprint of a tool-call arguments dict.

    Returns a comma-separated ``key=value`` list (sorted by key) capped
    at 120 chars. We keep just the *shape* of the arguments rather than
    full content so a 1MB read-result doesn't bloat the recent-actions
    buffer.
    """
    if not isinstance(args, dict):
        return ""
    parts: list[str] = []
    for k in sorted(args.keys()):
        v = args[k]
        if isinstance(v, str):
            head = v[:40]
        else:
            head = repr(v)[:40]
        parts.append(f"{k}={head}")
    out = ",".join(parts)
    if len(out) > 120:
        out = out[:117] + "..."
    return out
