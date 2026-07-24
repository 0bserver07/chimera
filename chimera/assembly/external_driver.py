"""Race real third-party coding-agent CLIs as multiplexer lanes (issue #169).

:class:`ExternalAgentDriver` satisfies the same driver contract the multiplexer
drives for a Chimera lane (:class:`~chimera.assembly.driver.DriverProtocol`),
but each turn spawns a **real external agent CLI** as a subprocess inside the
lane's isolated workspace and translates its output into ``LoopEvent``s. That
closes the comparison loop: the multiplexer can race the actual agents
themselves — not just Chimera's replications of them — under identical tasks
and workspace isolation, with the same scoreboard and cohort artifact.

Profiles
--------
A lane is configured by an :class:`ExternalAgentProfile`: the command template
(``{task}`` and ``{workdir}`` placeholders), the output protocol, an optional
env passthrough allowlist, and a timeout. One profile ships built in —
`claude` (the `claude` CLI, whose ``--print --output-format stream-json``
mode this repo already integrates against). Users register more under
``[external_agents.<name>]`` tables in ``~/.chimera/config.toml`` (the same
config chain every Chimera CLI reads; ``$CHIMERA_CONFIG_HOME`` honored)::

    [external_agents.myagent]
    command = ["myagent", "--task", "{task}"]   # {task} is required
    protocol = "text"                            # default: "stream-json"
    env = ["MYAGENT_API_KEY"]                    # optional allowlist
    timeout = 600                                # seconds, default 900

Protocols
---------
- ``stream-json`` — newline-delimited JSON events in the `claude` CLI's
  stream-json vocabulary (``system``/``assistant``/``user``/``result`` lines;
  the mapping table lives on :class:`_StreamJsonParser`). Cost, token usage,
  and step counts are parsed from the ``result`` line, so telemetry is real.
- ``text`` — plain stdout, streamed as assistant text; the exit code decides
  the terminal reason. No cost/steps telemetry exists on this path — the lane
  reports honest zeros and says so with a system note.

Honest limits (documented, not hidden): steering and follow-up queueing do not
reach an external CLI (a polite system note is emitted instead of a crash);
each turn is a fresh CLI invocation (no cross-turn conversation memory unless
the external tool provides it itself); history is reconstructed minimally
(user text + final assistant text) for the cohort artifact and resume display.
Stdlib only — no new dependencies, no TUI-extra imports.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from chimera.core.loop_events import LoopEvent, LoopEventType, LoopResult
from chimera.types import Message, ToolCall, ToolResult

__all__ = [
    "BUILTIN_PROFILES",
    "EXTERNAL_LANE_PREFIX",
    "EXTERNAL_LANE_PRESET",
    "ExternalAgentDriver",
    "ExternalAgentProfile",
    "load_external_profiles",
    "resolve_external_profile",
]

#: Lane-spec prefix that selects an external lane (``--models ext:claude,…``).
EXTERNAL_LANE_PREFIX = "ext"
#: Marker recorded as the lane's ``preset`` in manifests (external lanes have
#: no assembly preset; resume detects them by the ``ext:`` model prefix).
EXTERNAL_LANE_PRESET = "external"

_PROTOCOLS = ("stream-json", "text")
#: Baseline env vars an allowlisted subprocess still needs to run at all.
_BASE_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "LC_ALL", "TMPDIR")
#: Grace window between SIGTERM and the last-resort kill (ACPClient parity).
_TERM_GRACE_S = 8.0
_STDERR_TAIL_LINES = 40


@dataclass(frozen=True)
class ExternalAgentProfile:
    """How to run one external coding-agent CLI as a lane.

    Args:
        name: Profile name — what ``--models ext:<name>`` selects. Profile
            names are user data; only brand-safe names ship built in.
        command: Argv template. ``{task}`` (required somewhere) is replaced
            with the turn's task text; ``{workdir}`` with the lane workspace
            path. The subprocess always runs with ``cwd=<workspace>`` whether
            or not the template uses ``{workdir}``.
        protocol: ``"stream-json"`` (newline-JSON events, real telemetry) or
            ``"text"`` (plain stdout, telemetry unavailable).
        env_allow: ``None`` inherits the full parent environment (default).
            A list means the subprocess sees ONLY those variables plus a
            baseline (PATH, HOME, TERM, …) — use it to keep credentials from
            leaking into third-party tools.
        timeout: Per-turn wall-clock cap in seconds; on expiry the process
            group gets SIGTERM and the turn ends with reason ``timeout``.
    """

    name: str
    command: tuple[str, ...]
    protocol: str = "stream-json"
    env_allow: tuple[str, ...] | None = None
    timeout: float = 900.0

    @classmethod
    def from_config(cls, name: str, cfg: dict[str, Any]) -> ExternalAgentProfile:
        """Build a profile from one ``[external_agents.<name>]`` config table.

        Args:
            name: The table's profile name.
            cfg: The parsed TOML table.

        Returns:
            A validated profile.

        Raises:
            ValueError: On a missing/empty command, a command without a
                ``{task}`` placeholder, or an unknown protocol.
        """
        raw_cmd = cfg.get("command")
        if not isinstance(raw_cmd, list) or not raw_cmd or not all(
            isinstance(part, str) for part in raw_cmd
        ):
            raise ValueError(
                f"external agent profile {name!r}: 'command' must be a non-empty "
                f"list of strings"
            )
        if not any("{task}" in part for part in raw_cmd):
            raise ValueError(
                f"external agent profile {name!r}: 'command' needs a {{task}} "
                f"placeholder so the lane can deliver its task"
            )
        protocol = str(cfg.get("protocol", "stream-json"))
        if protocol not in _PROTOCOLS:
            raise ValueError(
                f"external agent profile {name!r}: unknown protocol {protocol!r} "
                f"(choose from {list(_PROTOCOLS)})"
            )
        raw_env = cfg.get("env")
        env_allow: tuple[str, ...] | None = None
        if isinstance(raw_env, list):
            env_allow = tuple(str(v) for v in raw_env)
        try:
            timeout = float(cfg.get("timeout", 900.0))
        except (TypeError, ValueError):
            timeout = 900.0
        return cls(
            name=name, command=tuple(raw_cmd), protocol=protocol,
            env_allow=env_allow, timeout=timeout,
        )


#: Profiles that work out of the box. Only `claude` ships committed: the
#: `claude` CLI is already integrated against throughout this repo and its
#: name is safe to carry in source. Other agents' CLIs are added by users via
#: config (profile names are user data).
BUILTIN_PROFILES: dict[str, ExternalAgentProfile] = {
    "claude": ExternalAgentProfile(
        name="claude",
        command=(
            "claude", "-p", "{task}",
            "--output-format", "stream-json", "--verbose",
            # File edits proceed without an interactive prompt; anything
            # riskier still follows the CLI's own permission rules.
            "--permission-mode", "acceptEdits",
        ),
        protocol="stream-json",
    ),
}


def load_external_profiles() -> dict[str, ExternalAgentProfile]:
    """Built-in profiles merged under the user's ``[external_agents]`` tables.

    Reads the same ``~/.chimera/config.toml`` chain as every Chimera CLI
    (``$CHIMERA_CONFIG_HOME`` honored). A user table with a built-in's name
    overrides it. Malformed tables are skipped here (config discovery must
    never crash startup); referencing one by name gets the loud error from
    :func:`resolve_external_profile`.

    Returns:
        Mapping of profile name to profile.
    """
    from chimera.cli.config_loader import load_config

    profiles = dict(BUILTIN_PROFILES)
    table = load_config().get("external_agents")
    if isinstance(table, dict):
        for name, cfg in table.items():
            if not isinstance(cfg, dict):
                continue
            try:
                profiles[str(name)] = ExternalAgentProfile.from_config(str(name), cfg)
            except ValueError:
                continue  # loud on resolve, silent on discovery
    return profiles


def resolve_external_profile(name: str) -> ExternalAgentProfile:
    """Resolve a profile name to a profile, loudly.

    Args:
        name: The ``ext:<name>`` profile name.

    Returns:
        The user-configured or built-in profile.

    Raises:
        ValueError: If the name is unknown, or its config table is invalid
            (the underlying validation error is surfaced).
    """
    from chimera.cli.config_loader import load_config

    table = load_config().get("external_agents")
    if isinstance(table, dict) and isinstance(table.get(name), dict):
        return ExternalAgentProfile.from_config(name, table[name])
    if name in BUILTIN_PROFILES:
        return BUILTIN_PROFILES[name]
    known = sorted(load_external_profiles())
    raise ValueError(
        f"unknown external agent profile {name!r}; known profiles: {known} "
        f"(add [external_agents.{name}] to ~/.chimera/config.toml)"
    )


@dataclass
class _AssistantMessage:
    """Minimal ``assistant``-event payload: what Lane/render read from it."""

    content: str
    usage: dict[str, Any] = field(default_factory=dict)


def _stringify_block_content(data: Any) -> str:
    """Flatten a tool-result ``content`` field (string or block list) to text."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        parts: list[str] = []
        for block in data:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(json.dumps(block, default=str))
        return "\n".join(parts)
    return str(data)


class _StreamJsonParser:
    """Newline-JSON → ``LoopEvent`` mapping (`claude` CLI vocabulary).

    Mapping (one emitted line → zero or more events, in order):

    ========================================  =====================================
    stream-json line                          LoopEvent(s)
    ========================================  =====================================
    ``system``/``init``                       ``system`` ("external agent ready…")
    ``system``/other, ``rate_limit_event``    dropped (transport noise)
    ``assistant`` msg ``thinking`` block      ``thinking_chunk`` per block
    ``assistant`` msg ``text`` block          ``assistant_chunk`` per block
    ``assistant`` msg (text or usage seen)    one ``assistant`` (content + usage)
    ``assistant`` msg ``tool_use`` block      ``tool_use`` (ToolCall) per block
    ``user`` msg ``tool_result`` block        ``tool_result`` ((call, ToolResult))
    ``result``                                folded into the terminal ``result``
    non-JSON line                             ``system`` (verbatim, dim)
    ========================================  =====================================

    The terminal ``result`` line carries real telemetry — ``total_cost_usd``,
    ``usage`` (input/output tokens), ``num_turns``, ``duration_ms`` — which
    :meth:`finish` folds into the lane's single ``LoopResult``.
    """

    def __init__(self) -> None:
        self._pending: dict[str, ToolCall] = {}
        self._turn = 0
        self._text_parts: list[str] = []
        self._final_text: str | None = None
        self._saw_result = False
        self._reason: str | None = None
        self._cost = 0.0
        self._usage: dict[str, Any] = {}
        self._num_turns = 0
        self._duration_ms: float | None = None

    def start(self) -> list[LoopEvent]:
        return []

    def feed(self, line: str) -> list[LoopEvent]:
        line = line.strip()
        if not line:
            return []
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            # Not an event — a stray banner/warning. Surface it dimly rather
            # than corrupting assistant prose or dropping evidence.
            return [LoopEvent(LoopEventType.system, line, self._turn)]
        if not isinstance(data, dict):
            return [LoopEvent(LoopEventType.system, line, self._turn)]
        kind = data.get("type")
        if kind == "system":
            if data.get("subtype") == "init":
                model = str(data.get("model") or "")
                note = "· external agent ready" + (f" ({model})" if model else "")
                return [LoopEvent(LoopEventType.system, note, self._turn)]
            return []  # hook chatter, token estimates, …
        if kind == "assistant":
            return self._on_assistant(data)
        if kind == "user":
            return self._on_user(data)
        if kind == "result":
            self._on_result(data)
            return []
        return []  # rate_limit_event and future kinds: drop, never crash

    def _on_assistant(self, data: dict[str, Any]) -> list[LoopEvent]:
        msg = data.get("message") or {}
        if not isinstance(msg, dict):
            return []
        content = msg.get("content")
        events: list[LoopEvent] = []
        tool_events: list[LoopEvent] = []
        text_here: list[str] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "thinking":
                    thought = str(block.get("thinking", ""))
                    if thought:
                        events.append(
                            LoopEvent(LoopEventType.thinking_chunk, thought, self._turn)
                        )
                elif btype == "text":
                    text = str(block.get("text", ""))
                    if text:
                        events.append(
                            LoopEvent(LoopEventType.assistant_chunk, text, self._turn)
                        )
                        text_here.append(text)
                elif btype == "tool_use":
                    args = block.get("input")
                    call = ToolCall(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "?")),
                        arguments=dict(args) if isinstance(args, dict) else {},
                    )
                    if call.id:
                        self._pending[call.id] = call
                    tool_events.append(LoopEvent(LoopEventType.tool_use, call, self._turn))
        elif isinstance(content, str) and content:
            events.append(LoopEvent(LoopEventType.assistant_chunk, content, self._turn))
            text_here.append(content)
        usage = msg.get("usage")
        usage_dict = dict(usage) if isinstance(usage, dict) else {}
        if text_here or usage_dict:
            # One per-step ``assistant`` event: commits the streamed chunks and
            # carries the step's usage (feeds the live context gauge).
            self._turn += 1
            events.append(LoopEvent(
                LoopEventType.assistant,
                _AssistantMessage("".join(text_here), usage_dict),
                self._turn,
            ))
            self._text_parts.extend(text_here)
        for tool_event in tool_events:  # stamp with the step they belong to
            tool_event.turn = self._turn
        return events + tool_events

    def _on_user(self, data: dict[str, Any]) -> list[LoopEvent]:
        msg = data.get("message") or {}
        if not isinstance(msg, dict):
            return []
        content = msg.get("content")
        events: list[LoopEvent] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                call = self._pending.pop(str(block.get("tool_use_id") or ""), None)
                result = ToolResult(
                    output=_stringify_block_content(block.get("content")),
                    error="tool failed" if block.get("is_error") else None,
                )
                events.append(
                    LoopEvent(LoopEventType.tool_result, (call, result), self._turn)
                )
        return events

    def _on_result(self, data: dict[str, Any]) -> None:
        self._saw_result = True
        subtype = str(data.get("subtype") or "")
        if subtype == "success" and not data.get("is_error"):
            self._reason = "completed"
        else:
            self._reason = subtype or "error"
        try:
            self._cost = float(data.get("total_cost_usd") or 0.0)
        except (TypeError, ValueError):
            self._cost = 0.0
        usage = data.get("usage")
        self._usage = dict(usage) if isinstance(usage, dict) else {}
        try:
            self._num_turns = int(data.get("num_turns") or 0)
        except (TypeError, ValueError):
            self._num_turns = 0
        try:
            self._duration_ms = float(data.get("duration_ms") or 0.0) or None
        except (TypeError, ValueError):
            self._duration_ms = None
        final = data.get("result")
        if isinstance(final, str) and final.strip():
            self._final_text = final

    def finish(
        self, *, code: int, cancelled: bool, timed_out: bool,
        stderr_tail: str, duration_ms: float, timeout: float,
    ) -> tuple[list[LoopEvent], LoopResult]:
        """Fold process exit into pre-result events + the terminal LoopResult."""
        events: list[LoopEvent] = []
        if cancelled:
            reason = "cancelled"
        elif timed_out:
            reason = "timeout"
            events.append(LoopEvent(
                LoopEventType.error,
                f"external agent timed out after {timeout:.0f}s", self._turn,
            ))
        elif self._saw_result:
            reason = self._reason or "completed"
        elif code == 0:
            reason = "completed"
        else:
            reason = "error"
            detail = f"external agent exited with code {code}"
            if stderr_tail:
                detail += f": {stderr_tail[-500:]}"
            events.append(LoopEvent(LoopEventType.error, detail, self._turn))
        result = LoopResult(
            reason=reason,
            messages=[],
            usage=self._usage,
            cost_usd=self._cost,
            duration_ms=self._duration_ms if self._duration_ms is not None else duration_ms,
            turn_count=self._num_turns or self._turn,
        )
        return events, result

    def final_text(self) -> str:
        return self._final_text or "".join(self._text_parts)


class _TextParser:
    """Plain-stdout fallback: stream lines as assistant text; exit code rules.

    No cost/token/step telemetry exists on this path. The lane reports honest
    zeros and announces it once with a system note — never a fabricated number.
    """

    def __init__(self) -> None:
        self._parts: list[str] = []

    def start(self) -> list[LoopEvent]:
        return [LoopEvent(
            LoopEventType.system,
            "· cost/steps telemetry unavailable for this lane (text protocol)", 0,
        )]

    def feed(self, line: str) -> list[LoopEvent]:
        self._parts.append(line)
        return [LoopEvent(LoopEventType.assistant_chunk, line, 0)]

    def finish(
        self, *, code: int, cancelled: bool, timed_out: bool,
        stderr_tail: str, duration_ms: float, timeout: float,
    ) -> tuple[list[LoopEvent], LoopResult]:
        events: list[LoopEvent] = [LoopEvent(
            LoopEventType.assistant, _AssistantMessage("".join(self._parts), {}), 0,
        )]
        if cancelled:
            reason = "cancelled"
        elif timed_out:
            reason = "timeout"
            events.append(LoopEvent(
                LoopEventType.error,
                f"external agent timed out after {timeout:.0f}s", 0,
            ))
        elif code == 0:
            reason = "completed"
        else:
            reason = "error"
            detail = f"external agent exited with code {code}"
            if stderr_tail:
                detail += f": {stderr_tail[-500:]}"
            events.append(LoopEvent(LoopEventType.error, detail, 0))
        result = LoopResult(
            reason=reason, messages=[], usage={},
            cost_usd=0.0, duration_ms=duration_ms, turn_count=0,
        )
        return events, result

    def final_text(self) -> str:
        return "".join(self._parts)


class ExternalAgentDriver:
    """Drive a real external coding-agent CLI as a multiplexer lane.

    Satisfies :class:`~chimera.assembly.driver.DriverProtocol`: ``send`` runs
    one turn (one subprocess) and streams ``LoopEvent``s; ``cancel`` terminates
    the process group (SIGTERM first, kill only after a grace window — the
    same posture as :class:`~chimera.acp.client.ACPClient`); ``steer`` /
    ``queue_follow_up`` emit an honest "not supported" system note instead of
    crashing; ``history`` is a minimal user/assistant reconstruction for the
    cohort artifact and resume.

    The subprocess IO runs on a worker thread (the
    :mod:`~chimera.assembly.loop_adapter` bridge pattern) so blocking reads
    never stall the TUI's event loop, and events hop back via
    ``call_soon_threadsafe``. Exactly one ``result`` event ends every turn.

    Args:
        profile: Which CLI to run and how to parse it.
        workdir: The lane's isolated workspace — the subprocess ``cwd``, so
            every file the external agent writes lands in the lane's diff.
    """

    def __init__(self, profile: ExternalAgentProfile, workdir: str) -> None:
        self._profile = profile
        self._workdir = str(workdir)
        self._history: list[Message] = []
        self._total_cost = 0.0
        self._turn_count = 0
        self._proc: subprocess.Popen[str] | None = None
        self._owns_group = False
        self._cancelled = False
        self._timed_out = False
        self._state_lock = threading.Lock()
        self._queue: Any = None  # asyncio.Queue[LoopEvent | None] while a turn runs
        self._aio_loop: Any = None
        self._pending_notes: list[str] = []

    # -- state ----------------------------------------------------------
    @property
    def model(self) -> str:
        return f"{EXTERNAL_LANE_PREFIX}:{self._profile.name}"

    @property
    def profile(self) -> ExternalAgentProfile:
        return self._profile

    @property
    def tools(self) -> list[Any]:
        return []  # the external agent's tools are its own affair

    @property
    def total_cost(self) -> float:
        return self._total_cost

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def history(self) -> list[Any]:
        return list(self._history)

    @property
    def context_window(self) -> int | None:
        return None  # unknown for an external CLI — hide, never estimate

    @property
    def auto_compaction(self) -> bool:
        return False

    # -- driving --------------------------------------------------------
    async def send(self, text: str) -> AsyncIterator[LoopEvent]:
        """Run one external-CLI turn, yielding loop events.

        Args:
            text: The task, substituted into the command template's ``{task}``.

        Yields:
            ``LoopEvent``s in stream order, ending with exactly one ``result``.
        """
        import asyncio

        self._cancelled = False
        self._timed_out = False
        queue: asyncio.Queue[LoopEvent | None] = asyncio.Queue()
        with self._state_lock:
            self._queue = queue
            self._aio_loop = asyncio.get_running_loop()
            notes = self._pending_notes
            self._pending_notes = []
        self._history.append(Message.user(text))
        for note in notes:
            yield LoopEvent(LoopEventType.system, note, 0)
        worker = threading.Thread(
            target=self._run_turn, args=(text,), daemon=True,
            name=f"ext-lane-{self._profile.name}",
        )
        worker.start()
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            with self._state_lock:
                self._queue = None
                self._aio_loop = None
            # Consumer gone before the turn finished (teardown/GeneratorExit):
            # reap the subprocess so nothing outlives the lane. No-op after a
            # normal finish (_finalize already dropped the process handle).
            self._terminate()

    def steer(self, text: str) -> None:
        """Steering cannot reach an external CLI — note it honestly."""
        self._note(
            f"external lane ({self._profile.name}): steering is not supported — "
            f"message not delivered"
        )

    def queue_follow_up(self, text: str) -> None:
        """Follow-up queueing cannot reach an external CLI — note it honestly."""
        self._note(
            f"external lane ({self._profile.name}): follow-up queueing is not "
            f"supported — message not delivered"
        )

    def cancel(self) -> None:
        """Abort the current turn: SIGTERM the process group, kill only later."""
        self._cancelled = True
        self._terminate()

    def clear(self) -> None:
        """Forget the (reconstructed) conversation."""
        self._history = []

    def load_history(self, messages: list[Any]) -> None:
        """Seed the reconstructed history (cohort resume)."""
        self._history = list(messages)

    # -- internals ------------------------------------------------------
    def _note(self, text: str) -> None:
        """Emit a system note into the live stream, or park it for next turn."""
        event = LoopEvent(LoopEventType.system, text, 0)
        if not self._emit(event):
            with self._state_lock:
                self._pending_notes.append(text)

    def _emit(self, event: LoopEvent | None) -> bool:
        with self._state_lock:
            queue, loop = self._queue, self._aio_loop
        if queue is None or loop is None:
            return False
        try:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        except RuntimeError:  # event loop already closed (app teardown)
            return False
        return True

    def _render_command(self, task: str) -> list[str]:
        return [
            part.replace("{task}", task).replace("{workdir}", self._workdir)
            for part in self._profile.command
        ]

    def _build_env(self) -> dict[str, str] | None:
        allow = self._profile.env_allow
        if allow is None:
            return None  # inherit the parent environment wholesale
        env: dict[str, str] = {}
        for key in (*_BASE_ENV, *allow):
            value = os.environ.get(key)
            if value is not None:
                env[key] = value
        return env

    def _terminate(self) -> None:
        """Graceful shutdown: SIGTERM (whole group), then kill after a grace
        window if it ignored us — the :class:`~chimera.acp.client.ACPClient`
        posture, never a first-strike kill."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if self._owns_group and hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                return

        def _escalate() -> None:
            try:
                proc.wait(timeout=_TERM_GRACE_S)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()  # last resort only, after the grace window
                except OSError:
                    pass

        threading.Thread(target=_escalate, daemon=True, name="ext-lane-escalate").start()

    def _on_timeout(self) -> None:
        self._timed_out = True
        self._terminate()

    def _run_turn(self, task: str) -> None:
        """Worker thread: spawn the CLI, stream-parse stdout, finalize."""
        started = time.monotonic()
        parser: _StreamJsonParser | _TextParser = (
            _StreamJsonParser() if self._profile.protocol == "stream-json" else _TextParser()
        )
        turn = 0
        try:
            command = self._render_command(task)
            use_group = hasattr(os, "setsid")
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=self._workdir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=self._build_env(),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    start_new_session=use_group,
                )
            except OSError as exc:
                self._emit(LoopEvent(
                    LoopEventType.error,
                    f"cannot launch external agent {self._profile.name!r}: {exc}", 0,
                ))
                self._finalize(LoopResult(
                    reason="error", messages=[], usage={},
                    cost_usd=0.0, duration_ms=0.0, turn_count=0,
                ), parser)
                return
            self._proc = proc
            self._owns_group = use_group

            stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
            stderr_thread = threading.Thread(
                target=self._drain_stderr, args=(proc, stderr_tail),
                daemon=True, name="ext-lane-stderr",
            )
            stderr_thread.start()
            watchdog: threading.Timer | None = None
            if self._profile.timeout > 0:
                watchdog = threading.Timer(self._profile.timeout, self._on_timeout)
                watchdog.daemon = True
                watchdog.start()

            try:
                for event in parser.start():
                    self._emit(event)
                assert proc.stdout is not None
                for line in proc.stdout:
                    for event in parser.feed(line):
                        turn = max(turn, event.turn)
                        self._emit(event)
                code = proc.wait()
            finally:
                if watchdog is not None:
                    watchdog.cancel()
            stderr_thread.join(timeout=2)

            events, result = parser.finish(
                code=code,
                cancelled=self._cancelled,
                timed_out=self._timed_out,
                stderr_tail="".join(stderr_tail).strip(),
                duration_ms=(time.monotonic() - started) * 1000.0,
                timeout=self._profile.timeout,
            )
            for event in events:
                self._emit(event)
            self._finalize(result, parser)
        except Exception as exc:  # noqa: BLE001 - surfaced as error + terminal result
            self._emit(LoopEvent(LoopEventType.error, str(exc), turn))
            self._finalize(LoopResult(
                reason="error", messages=[], usage={},
                cost_usd=0.0, duration_ms=(time.monotonic() - started) * 1000.0,
                turn_count=turn,
            ), parser)

    def _finalize(self, result: LoopResult, parser: _StreamJsonParser | _TextParser) -> None:
        """Book-keep the turn and emit the terminal result + sentinel."""
        self._proc = None
        self._total_cost += float(result.cost_usd or 0.0)
        self._turn_count += 1
        final_text = parser.final_text().strip()
        self._history.append(Message.assistant(final_text or "(no output)"))
        result.messages = list(self._history)
        self._emit(LoopEvent(LoopEventType.result, result, result.turn_count))
        self._emit(None)  # sentinel: the async side stops reading

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen[str], sink: deque[str]) -> None:
        try:
            assert proc.stderr is not None
            for line in proc.stderr:
                sink.append(line)
        except (OSError, ValueError):  # pipe closed mid-read
            pass
