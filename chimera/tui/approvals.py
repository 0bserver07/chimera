"""Permission approvals surfaced as TUI modals (issue #171, spec §9 R-OVER-1).

The assembled TUI stack auto-approves everything by default: ``CodingAgent``
swaps its loaded permission context to BYPASS mode when no
``permission_callback`` is supplied, and the loop's ASK branch had no handler
seam. This module is the explicit opt-in bridge:

- :class:`ApprovalBroker` hands each lane's driver an async
  ``permission_callback``. When the agent's permission checker returns ASK,
  the loop invokes it; the broker queues a :class:`PendingApproval` and the
  requesting turn waits.
- The multiplexer polls :meth:`ApprovalBroker.next_pending` and pushes an
  :class:`ApprovalModal` naming the lane — one modal at a time, FIFO, so
  several lanes hitting gates queue up instead of stacking screens.
- The user's decision (with optional one-line feedback) is folded into a wire
  :class:`~chimera.wire.types.ApprovalResponse` and routed back to the
  waiting tool execution; deny feedback becomes the denial reason the model
  sees. "Allow for session" is backed by the existing
  :class:`~chimera.permissions.interactive.ApprovalMemory` (per lane, per
  tool, in-memory only — nothing new is persisted).

Thread-safety (the handoff): a lane's turn may run as a Textual worker on the
app's asyncio loop today, or on a foreign thread/loop tomorrow (strategy-loop
bridge, REPL thread). The broker therefore never shares asyncio primitives
across contexts: the requesting side polls a ``threading.Event`` with
``asyncio.sleep`` — the same cooperative-poll idiom the agent loop uses for
abort signals — and the UI side resolves that event from wherever it runs. An
optional timeout converts an unserviced request into a denial, so a missing
UI pump can never deadlock a turn.
"""
from __future__ import annotations

import asyncio
import itertools
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

try:
    from rich.text import Text
    from textual import on
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Button, Input, Static
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Chimera approval modals need the 'tui' extra:\n"
        "  pip install 'chimera-run[tui]'   (or: pip install textual)"
    ) from exc

from chimera.permissions.interactive import ApprovalMemory
from chimera.permissions.risk import RiskLevel, classify_risk
from chimera.wire.types import ApprovalRequest, ApprovalResponse

__all__ = [
    "ApprovalBroker",
    "ApprovalModal",
    "ApprovalOutcome",
    "PendingApproval",
    "approvals_enabled",
    "format_args_preview",
]

#: Environment opt-in for the TUI approval path (see :func:`approvals_enabled`).
APPROVALS_ENV_VAR = "CHIMERA_TUI_APPROVALS"


def approvals_enabled(explicit: bool | None = None) -> bool:
    """Resolve the #171 opt-in: explicit flag wins, else the env var.

    Args:
        explicit: A caller-supplied override (``--approvals`` style flag);
            ``None`` defers to the ``CHIMERA_TUI_APPROVALS`` env var.

    Returns:
        ``True`` when approval modals should be wired. Default is ``False``
        so out-of-the-box behavior is unchanged (auto-approve via BYPASS).
    """
    if explicit is not None:
        return explicit
    return os.environ.get(APPROVALS_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def format_args_preview(
    args: dict[str, Any], *, max_value: int = 160, max_total: int = 600
) -> str:
    """Render tool arguments as a safely truncated ``key = value`` block.

    Newlines collapse to ``⏎`` so one runaway value cannot blow up the modal;
    each value and the whole block are capped.

    Args:
        args: The tool-call arguments.
        max_value: Per-value character cap.
        max_total: Whole-preview character cap.

    Returns:
        A display-ready multi-line string (``"(no arguments)"`` when empty).
    """
    parts: list[str] = []
    for key, value in args.items():
        rendered = str(value).replace("\n", "⏎")
        if len(rendered) > max_value:
            rendered = rendered[: max_value - 1] + "…"
        parts.append(f"{key} = {rendered}")
    text = "\n".join(parts) or "(no arguments)"
    if len(text) > max_total:
        text = text[: max_total - 1] + "…"
    return text


@dataclass
class ApprovalOutcome:
    """What the user chose on an :class:`ApprovalModal`.

    Attributes:
        approved: ``True`` for Allow (either flavor), ``False`` for Deny.
        session: ``True`` when "allow for session" was chosen — the broker
            records it in the lane's :class:`ApprovalMemory`.
        feedback: Optional one-line note; on deny it flows back to the agent
            as the denial reason.
    """

    approved: bool
    session: bool = False
    feedback: str = ""


_pending_seq = itertools.count(1)


@dataclass
class PendingApproval:
    """One queued approval: the request plus display context and the handoff.

    Attributes:
        lane_id: Lane the request came from (pane lookup key).
        lane_label: Human lane name shown on the modal.
        request: The wire request (tool name + args + request id).
        risk_level: :func:`classify_risk` level for the call.
        risk_reason: Classifier's short reason ("recursive force delete"),
            empty when it has none.
        preview: Pre-rendered, safely truncated argument preview.
        seq: Monotonic sequence number (FIFO ordering / debugging).
    """

    lane_id: str
    lane_label: str
    request: ApprovalRequest
    risk_level: RiskLevel
    risk_reason: str
    preview: str
    seq: int = field(default_factory=lambda: next(_pending_seq))
    _event: threading.Event = field(default_factory=threading.Event, repr=False)
    _response: ApprovalResponse | None = field(default=None, repr=False)
    withdrawn: bool = False

    @property
    def resolved(self) -> bool:
        """Whether a response has been delivered."""
        return self._event.is_set()

    def resolve(self, response: ApprovalResponse) -> None:
        """Deliver *response* to the waiting turn (idempotent; first wins)."""
        if self._event.is_set():
            return
        self._response = response
        self._event.set()

    def withdraw(self) -> None:
        """Mark the request abandoned (turn aborted / timed out)."""
        self.withdrawn = True

    async def wait(
        self, *, poll_s: float = 0.05, timeout_s: float | None = None
    ) -> ApprovalResponse:
        """Await the decision by polling the thread-safe event.

        Runs on whichever event loop the requesting turn lives on; never
        touches the UI loop. On timeout the request is withdrawn and denied,
        so an unserviced gate cannot deadlock a turn. If the awaiting task is
        cancelled (lane cancel / teardown) the request is withdrawn so the UI
        can retire any modal already showing it.

        Args:
            poll_s: Poll interval in seconds.
            timeout_s: Optional deadline; ``None`` waits indefinitely.

        Returns:
            The delivered (or synthesized timeout-denial) response.
        """
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        try:
            while not self._event.is_set():
                if deadline is not None and time.monotonic() >= deadline:
                    self.withdraw()
                    return ApprovalResponse(
                        request_id=self.request.request_id,
                        approved=False,
                        reason="approval timed out (no decision)",
                    )
                await asyncio.sleep(poll_s)
        except asyncio.CancelledError:
            self.withdraw()
            raise
        assert self._response is not None  # set before the event, single writer
        return self._response


class ApprovalBroker:
    """Thread-safe bridge between waiting tool executions and the TUI.

    One broker serves a whole cohort: :meth:`handler_for` binds a lane-named
    async ``permission_callback`` for each driver, and the app drains the
    FIFO queue via :meth:`next_pending`, resolving each entry with
    :meth:`resolve_with_outcome`.

    Args:
        auto_allow_low_risk: When ``True`` (default), requests the existing
            :func:`classify_risk` classifier rates LOW (read_file, search,
            list_files, test, …) auto-approve without a modal — the "confirm
            writes and commands" posture. ``False`` prompts for everything
            the permission checker ASKs about.
        timeout_s: Optional per-request deadline; ``None`` waits until the
            user decides.
        poll_s: Poll interval for the waiting side.
    """

    def __init__(
        self,
        *,
        auto_allow_low_risk: bool = True,
        timeout_s: float | None = None,
        poll_s: float = 0.05,
    ) -> None:
        self._auto_allow_low_risk = auto_allow_low_risk
        self._timeout_s = timeout_s
        self._poll_s = poll_s
        self._pending: deque[PendingApproval] = deque()
        self._lock = threading.Lock()
        self._memories: dict[str, ApprovalMemory] = {}

    # -- driver side ------------------------------------------------------
    def handler_for(
        self, lane_id: str, lane_label: str | None = None
    ) -> Callable[[ApprovalRequest], Awaitable[ApprovalResponse]]:
        """Build the async ``permission_callback`` for one lane's driver.

        The handler auto-approves session-remembered tools and (optionally)
        LOW-risk calls, otherwise enqueues a :class:`PendingApproval` and
        waits for the UI's decision.

        Args:
            lane_id: Lane id (modal routing / pane notes).
            lane_label: Human label for the modal title; defaults to the id.

        Returns:
            An awaitable callable matching the loop's approval-handler seam.
        """
        label = lane_label or lane_id
        memory = self._memories.setdefault(lane_id, ApprovalMemory())

        async def _handle(request: ApprovalRequest) -> ApprovalResponse:
            tool_name = request.tool_name
            if memory.is_always_allowed(tool_name):
                return ApprovalResponse(
                    request_id=request.request_id,
                    approved=True,
                    reason="allowed for session",
                )
            level, why = classify_risk(tool_name, request.tool_args)
            if self._auto_allow_low_risk and level is RiskLevel.LOW:
                return ApprovalResponse(
                    request_id=request.request_id,
                    approved=True,
                    reason="auto-approved (low risk)",
                )
            pending = PendingApproval(
                lane_id=lane_id,
                lane_label=label,
                request=request,
                risk_level=level,
                risk_reason=why,
                preview=format_args_preview(request.tool_args),
            )
            with self._lock:
                self._pending.append(pending)
            return await pending.wait(poll_s=self._poll_s, timeout_s=self._timeout_s)

        return _handle

    # -- UI side ----------------------------------------------------------
    def next_pending(self) -> PendingApproval | None:
        """Pop the oldest live pending approval (skipping withdrawn/resolved)."""
        with self._lock:
            while self._pending:
                pending = self._pending.popleft()
                if not pending.withdrawn and not pending.resolved:
                    return pending
            return None

    @property
    def pending_count(self) -> int:
        """Live queued requests (withdrawn/resolved entries excluded)."""
        with self._lock:
            return sum(1 for p in self._pending if not p.withdrawn and not p.resolved)

    def remember_session_allow(self, lane_id: str, tool_name: str) -> None:
        """Record an "allow for session" in the lane's :class:`ApprovalMemory`."""
        self._memories.setdefault(lane_id, ApprovalMemory()).remember_allow(tool_name)

    def resolve_with_outcome(
        self, pending: PendingApproval, outcome: ApprovalOutcome | None
    ) -> None:
        """Fold a modal outcome into a wire response and release the turn.

        ``None`` (modal dismissed without a choice) denies. Deny feedback —
        or, failing that, a generic reason — becomes the denial reason the
        agent sees; "allow for session" is recorded before releasing.

        Args:
            pending: The entry being decided.
            outcome: The user's choice, or ``None``.
        """
        if outcome is None:
            outcome = ApprovalOutcome(approved=False, feedback="dismissed without a decision")
        if outcome.approved and outcome.session:
            self.remember_session_allow(pending.lane_id, pending.request.tool_name)
        feedback = outcome.feedback.strip()
        if outcome.approved:
            reason = feedback or (
                "allowed for session" if outcome.session else "approved by user"
            )
        else:
            reason = feedback or "denied by user"
        pending.resolve(
            ApprovalResponse(
                request_id=pending.request.request_id,
                approved=outcome.approved,
                reason=reason,
            )
        )


_RISK_STYLES = {
    RiskLevel.LOW: "green",
    RiskLevel.MEDIUM: "yellow",
    RiskLevel.HIGH: "red",
    RiskLevel.CRITICAL: "bold red",
}


class ApprovalModal(ModalScreen[ApprovalOutcome]):
    """One pending permission request as a modal (R-OVER-1).

    Shows the lane, tool, risk level (when the classifier has one), and a
    truncated argument preview. ``a`` / Allow approves once, ``s`` / "Allow
    for session" approves and remembers the tool for this lane's session
    (existing :class:`ApprovalMemory`, in-memory only), ``d`` / Deny / Esc
    denies. Submitting the feedback input denies with that text as the
    reason. Dismisses with an :class:`ApprovalOutcome` (``None`` never
    reaches callers — Esc maps to a deny outcome).

    Args:
        pending: The queued request to decide.
    """

    CSS = """
    ApprovalModal { align: center middle; }
    #approval-dialog {
        width: 72%; min-width: 46; max-width: 110;
        height: auto; max-height: 80%;
        background: $surface; border: round $warning;
    }
    #approval-title { height: 1; background: $warning; color: $text; padding: 0 1; }
    #approval-body { height: auto; padding: 0 1; }
    #approval-feedback { margin: 0 1; }
    #approval-buttons { height: auto; align-horizontal: center; }
    #approval-buttons Button { margin: 0 2; min-width: 10; }
    #approval-hints { height: 1; color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        Binding("a", "allow", "allow", show=False),
        Binding("s", "allow_session", "allow for session", show=False),
        Binding("d", "deny", "deny", show=False),
        Binding("escape", "deny", "deny", show=False),
    ]

    def __init__(self, pending: PendingApproval, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pending = pending

    def compose(self) -> ComposeResult:
        pending = self._pending
        with Vertical(id="approval-dialog"):
            yield Static(
                Text(
                    f" ⏸ approval — lane {pending.lane_label} · {pending.request.tool_name}",
                    style="bold",
                ),
                id="approval-title",
            )
            yield Static(self._body_text(), id="approval-body")
            yield Input(
                placeholder="optional note — sent to the agent if you deny",
                id="approval-feedback",
            )
            with Horizontal(id="approval-buttons"):
                yield Button("Allow", variant="success", id="approval-allow")
                yield Button("Allow for session", id="approval-allow-session")
                yield Button("Deny", variant="error", id="approval-deny")
            yield Static(
                Text("a allow · s allow for session · d/Esc deny · Enter in note = deny",
                     style="dim"),
                id="approval-hints",
            )

    def on_mount(self) -> None:
        self.query_one("#approval-allow", Button).focus()

    def _body_text(self) -> Text:
        pending = self._pending
        text = Text()
        text.append("tool  ", style="dim")
        text.append(pending.request.tool_name, style="bold")
        text.append("\nrisk  ", style="dim")
        style = _RISK_STYLES.get(pending.risk_level, "yellow")
        text.append(pending.risk_level.value, style=style)
        if pending.risk_reason:
            text.append(f" — {pending.risk_reason}", style=style)
        text.append("\n")
        text.append(pending.preview, style="cyan")
        return text

    # -- decisions ----------------------------------------------------------
    def _feedback(self) -> str:
        # Robust to a decision landing before compose finished (or during
        # teardown): no feedback field yet means no feedback.
        nodes = self.query("#approval-feedback")
        return nodes.first(Input).value.strip() if nodes else ""

    def action_allow(self) -> None:
        self.dismiss(ApprovalOutcome(approved=True, feedback=self._feedback()))

    def action_allow_session(self) -> None:
        self.dismiss(ApprovalOutcome(approved=True, session=True, feedback=self._feedback()))

    def action_deny(self) -> None:
        self.dismiss(ApprovalOutcome(approved=False, feedback=self._feedback()))

    @on(Button.Pressed, "#approval-allow")
    def _allow_pressed(self, event: Button.Pressed) -> None:
        self.action_allow()

    @on(Button.Pressed, "#approval-allow-session")
    def _allow_session_pressed(self, event: Button.Pressed) -> None:
        self.action_allow_session()

    @on(Button.Pressed, "#approval-deny")
    def _deny_pressed(self, event: Button.Pressed) -> None:
        self.action_deny()

    @on(Input.Submitted, "#approval-feedback")
    def _feedback_submitted(self, event: Input.Submitted) -> None:
        # Typing a note and hitting Enter is the deny-with-reason gesture.
        self.action_deny()
