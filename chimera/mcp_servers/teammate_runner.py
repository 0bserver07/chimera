#!/usr/bin/env python3
"""``chimera-team-run`` — drive any MCP-capable coding agent as a teammate.

The runner is a small polling loop that does one job:

* Watch the team's task list on disk.
* When an open task appears, spawn the configured external coding agent
  command with a teammate prompt that tells it exactly which team_*
  tools to call.
* When the subprocess exits, check the team state, and loop.
* Exit cleanly after the team has been idle for ``--idle-timeout`` seconds.

The runner is intentionally **agent-agnostic**: you supply a command
template (``--cmd``) with two placeholders:

* ``{prompt}`` — the teammate workflow prompt (single line, escaped)
* ``{prompt_file}`` — a path to a tempfile containing the multi-line prompt
  (handy for agents that take a prompt file rather than a CLI arg)

The runner exports ``CHIMERA_TEAM``, ``CHIMERA_AGENT``, and
``CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1`` into the subprocess environment.
The chimera-team MCP server's env-var fallback picks these up, so your
``mcp.json`` doesn't need per-team config.

Sample::

    # one-time: add to ~/.codex/mcp.json (Codex CLI)
    {
      "mcpServers": {
        "chimera-team": {
          "command": "chimera-team-mcp",
          "env": {"CHIMERA_EXPERIMENTAL_AGENT_TEAMS": "1"}
        }
      }
    }

    # per-session: run the runner
    chimera-team-run --team review-pr --agent codex-1 \\
        --cmd 'codex exec --prompt-file {prompt_file}'

    chimera-team-run --team review-pr --agent opencode-1 \\
        --cmd 'opencode run "{prompt}"'

Session reuse (ACP)
-------------------

For agents that speak Agent Client Protocol over stdio, pass
``--reuse-session --runtime acp`` to keep a single subprocess alive
across N tasks. The runner spawns the external agent once via
:class:`chimera.acp.client.ACPClient`, then sends one
``session/sendMessage`` per task instead of paying the cold-start cost
on every iteration. If the subprocess crashes mid-session the runner
tears down the dead client and respawns on the next iteration —
existing stuck-claim recovery still applies. The persistent session is
gracefully stopped when the loop idles out::

    chimera-team-run --team review-pr --agent opencode-1 \\
        --runtime acp --reuse-session \\
        --cmd 'opencode acp'

When ``--reuse-session`` is passed but ``--runtime`` is not ``acp`` the
flag is downgraded with a warning and the runner falls back to
spawn-per-task — preserving the legacy behavior so misconfigured
invocations still make progress.

Permission propagation (issue #150)
-----------------------------------

``--policy read-only|workspace-write|dangerous`` gives this teammate a
posture; omitting it inherits whatever the lead set on the team, so one
``chimera team create --policy ...`` governs every teammate instead of
each runtime being configured separately. The resolved posture is
exported as ``CHIMERA_TEAM_POLICY`` (a Chimera teammate then binds
itself to it in-process) and translated into the external runtime's own
flags through :mod:`chimera.mcp_servers.team_policy`, spliced in via a
``{policy_args}`` placeholder in ``--cmd``. With no policy anywhere,
nothing changes.

Real-time mail (issue #149)
---------------------------

In persistent-session mode the runner also watches this teammate's
mailbox (:class:`chimera.mcp_servers.team_push.MailboxWatcher`) and
pushes new messages straight into the live session instead of waiting
for the agent's next ``team_recv_messages`` call. Delivery is
acknowledged per message, so anything the push path could not deliver
stays queued for the ordinary pull path. Spawn-per-task runs have no
live session to push into and are unchanged; ``--no-push`` turns the
watcher off explicitly.
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol, TextIO

from chimera.cli.agent_teams import ENV_FLAG, Team, TeamMailbox
from chimera.mcp_servers.team_policy import (
    apply_policy_args,
    detect_runtime,
    translate_policy,
)
from chimera.mcp_servers.team_push import DEFAULT_WATCH_INTERVAL, MailboxWatcher

if TYPE_CHECKING:
    from chimera.acp.types import ACPSessionConfig


class ACPClientLike(Protocol):
    """Structural protocol the runner needs from an ACP client.

    The real :class:`chimera.acp.client.ACPClient` satisfies this, and so
    does any test fake that implements ``start``, ``stop``, and
    ``send_message``. Using a Protocol keeps the production code agnostic
    to test doubles without sacrificing type checking — mypy / Pyright
    accept structural subtyping here.
    """

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def send_message(self, text: str) -> object: ...


#: Type alias for a factory that constructs an ACP-client-like object
#: from a prepared :class:`ACPSessionConfig`. The structural
#: :class:`ACPClientLike` lets tests inject fakes that only implement
#: the three methods the runner actually calls.
ACPClientFactory = Callable[["ACPSessionConfig"], ACPClientLike]


@dataclass
class _SessionState:
    """Mutable state for the persistent-session (ACP) code path.

    Attributes:
        client: The live ACP client, or ``None`` if not yet started or
            after a crash forced teardown.
        lock: Serialises everything that talks to ``client``. The task
            loop and the mailbox watcher are different threads sharing
            one stdio pipe, so exactly one of them may be inside a
            ``session/sendMessage`` at a time.
    """

    client: ACPClientLike | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class _ACPSteerSink:
    """Deliver team mail into a live ACP session.

    ACP's ``session/sendMessage`` is turn-scoped: a push that lands
    while the teammate is mid-turn is delivered as the next message in
    the same session, as soon as the current turn returns. That is a
    turn-boundary guarantee, not a preemption — the honest bound is
    "before the teammate starts its next task", versus the pull path's
    "when the agent next chooses to check its inbox".

    Raises rather than swallowing, so
    :class:`~chimera.mcp_servers.team_push.MailboxWatcher` leaves
    undelivered mail in the mailbox for the pull path.

    Args:
        state: The shared persistent-session state.
    """

    def __init__(self, state: _SessionState) -> None:
        self._state = state

    def steer(self, text: str) -> None:
        """Send *text* to the live session.

        Raises:
            RuntimeError: If no session is currently alive (the runner
                has not spawned one yet, or a crash tore it down).
        """
        with self._state.lock:
            client = self._state.client
            if client is None:
                raise RuntimeError("no live ACP session to push into")
            client.send_message(text)


def _build_acp_config(
    cmd_template: str, env: dict[str, str],
) -> "ACPSessionConfig":
    """Parse a ``--cmd`` string into an :class:`ACPSessionConfig`.

    The reuse-session path doesn't use the ``{prompt}`` / ``{prompt_file}``
    placeholders — prompts arrive via ``session/sendMessage`` — so the
    template is parsed as a literal ``command + args`` via ``shlex.split``.

    Args:
        cmd_template: The ``--cmd`` value (no placeholder substitution).
        env: Environment variables to pass through to the subprocess
            (already includes ``CHIMERA_TEAM`` / ``CHIMERA_AGENT`` /
            ``CHIMERA_EXPERIMENTAL_AGENT_TEAMS``).

    Returns:
        A configured :class:`ACPSessionConfig` ready to hand to
        :class:`ACPClient`.

    Raises:
        ValueError: If ``cmd_template`` is empty after shlex parsing.
    """
    from chimera.acp.types import ACPSessionConfig

    parts = shlex.split(cmd_template)
    if not parts:
        raise ValueError("--cmd must contain at least one token")
    return ACPSessionConfig(
        command=[parts[0]],
        args=parts[1:],
        env=env,
    )


def _acp_run_one_task(
    state: _SessionState,
    prompt: str,
    cmd_template: str,
    env: dict[str, str],
    log: TextIO,
    client_factory: ACPClientFactory | None = None,
) -> int:
    """Send one task's prompt to the persistent ACP session.

    On the first call (or after a crash forced teardown) this spawns the
    ACP subprocess and creates a fresh session. On subsequent calls it
    reuses the same subprocess — that's the entire point of the
    reuse-session flag. If ``send_message`` raises, the dead client is
    closed and ``state.client`` is reset to ``None`` so the *next*
    iteration of the outer loop respawns.

    Args:
        state: Mutable session state shared across iterations.
        prompt: Teammate workflow prompt to deliver via
            ``session/sendMessage``.
        cmd_template: Original ``--cmd`` value, used only when we need
            to spawn (or respawn) the subprocess.
        env: Environment variables for the subprocess.
        log: Stream to write status messages to.
        client_factory: Optional injection point for tests; defaults to
            ``lambda cfg: ACPClient(cfg)``.

    Returns:
        ``0`` on a successful send; ``-1`` when the ACP subprocess
        crashed or failed to start (the outer loop will respawn next
        iteration).
    """
    if client_factory is None:
        # Defer the import so tests with their own factory don't pay
        # the cost (and so import-cycle risk stays bounded).
        from chimera.acp.client import ACPClient as _DefaultACPClient

        def _default_factory(cfg: "ACPSessionConfig") -> ACPClientLike:
            return _DefaultACPClient(cfg)

        factory: ACPClientFactory = _default_factory
    else:
        factory = client_factory

    # One writer at a time on the session's stdio: the mailbox watcher
    # pushes team mail through the same client from another thread.
    with state.lock:
        if state.client is None:
            try:
                cfg = _build_acp_config(cmd_template, env)
                state.client = factory(cfg)
                state.client.start()
                print(
                    "chimera-team-run: started persistent ACP session.",
                    file=log,
                )
            except Exception as e:
                print(
                    f"chimera-team-run: failed to start ACP session ({e!r}); "
                    "will retry next iteration.",
                    file=log,
                )
                # Best-effort teardown so we don't leak a half-started process.
                client = state.client
                state.client = None
                if client is not None:
                    try:
                        client.stop()
                    except Exception:
                        pass
                return -1

        try:
            state.client.send_message(prompt)
            return 0
        except (RuntimeError, OSError, BrokenPipeError) as e:
            print(
                f"chimera-team-run: ACP session crashed ({e!r}); "
                "will respawn next iteration.",
                file=log,
            )
            client = state.client
            state.client = None
            if client is not None:
                try:
                    client.stop()
                except Exception:
                    pass
            return -1


__all__ = ["TEAMMATE_PROMPT", "run_loop", "main", "ACPClientFactory"]


TEAMMATE_PROMPT = """\
You are teammate '{agent_id}' in team '{team}'.

You have access to a `chimera-team` MCP server with the following tools:
  team_recv_messages  — drain your mailbox (call FIRST)
  team_claim_task     — claim the next open task ({{}} = auto, or pass task_id)
  team_complete_task  — mark a claimed task complete
  team_release_task   — release a claimed task back to the pool
  team_send_message   — DM another teammate
  team_list_members   — see who else is on the team
  team_list_tasks     — list tasks (filter: open|claimed|completed|mine)
  team_status         — team summary

Workflow for THIS run — work exactly ONE task and STOP:

  1. Call `team_recv_messages` first to drain any messages waiting for you.
  2. Call `team_claim_task` (no args) to atomically claim the next open task.
     If the result is `{{"claimed": false, ...}}`, exit immediately —
     there is no work for you.
  3. Read the claimed task's description carefully and do the work using
     your normal tools (file editing, bash, etc.).
  4. When the work is genuinely done, call `team_complete_task` with the
     task_id and a brief result summary describing what changed.
  5. Optionally call `team_send_message` to inform another teammate of
     anything they need to know.
  6. Stop. Do not pick up another task — the runner will spawn a fresh
     session for the next one.

Important:
* Only call `team_complete_task` when the work is genuinely done. If you
  can't finish, call `team_release_task` so another teammate can pick it up.
* Do not call `team_add_task` unless the lead asked you to. Stay in scope.
* A message beginning `[team mail]` may arrive mid-run. It was delivered
  straight from a teammate's mailbox, so it is already read — act on it
  and do NOT call `team_recv_messages` for it again.
"""


def _run_with_timeout(
    cmd: str, env: dict[str, str], timeout: float, log: TextIO,
) -> int:
    """Run *cmd* (shell), enforcing *timeout* with SIGTERM-then-escalate.

    Returns the process exit code, or ``-1`` if it had to be terminated.
    """
    proc = subprocess.Popen(cmd, shell=True, env=env)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(
            f"chimera-team-run: external agent exceeded task_timeout="
            f"{timeout:.0f}s; sending SIGTERM.",
            file=log,
        )
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            print(
                "chimera-team-run: agent did not exit after SIGTERM; "
                "escalating to SIGKILL.",
                file=log,
            )
            proc.kill()
            proc.wait(timeout=5)
        return -1


def run_loop(
    team_name: str,
    agent_id: str,
    cmd_template: str,
    teams_root: Path | None = None,
    idle_timeout: float = 60.0,
    task_timeout: float = 600.0,
    poll_interval: float = 2.0,
    max_nudges: int = 1,
    log: TextIO = sys.stderr,
    reuse_session: bool = False,
    runtime: str = "spawn",
    acp_client_factory: ACPClientFactory | None = None,
    push: bool = True,
    push_interval: float = DEFAULT_WATCH_INTERVAL,
    policy: str | None = None,
    policy_runtime: str | None = None,
    workspace: Path | None = None,
) -> int:
    """Poll-and-spawn loop.

    Args:
        team_name: Team to attach to (created if absent).
        agent_id: This teammate's id.
        cmd_template: Shell command with ``{prompt}`` or ``{prompt_file}``
            placeholders. In ``--runtime acp --reuse-session`` mode the
            placeholders are not required because prompts are delivered
            over ACP rather than the command line.
        teams_root: Override for ``~/.chimera/teams``.
        idle_timeout: Exit after this many seconds with no *progress*
            (no task transitioning to a new state). This covers both
            "no open tasks" and "agent keeps failing to make progress".
        task_timeout: Kill the external subprocess after this many seconds.
            Only enforced in ``runtime='spawn'`` mode; ACP reuse leaves
            timeout to the agent's own deadlines (the persistent session
            doesn't expose a clean kill point per ``session/sendMessage``).
        poll_interval: How often (seconds) to check the task list.
        max_nudges: Number of consecutive no-progress nudges to send for a
            stuck claim before the runner force-releases the task. Defaults
            to 1 (one nudge, then release on the next stuck iteration).
        log: Stream to write status messages to.
        reuse_session: When True (and ``runtime`` is ``'acp'``), keep one
            external-agent subprocess alive across tasks. When True but
            ``runtime`` is ``'spawn'``, the flag is downgraded with a
            warning and the loop falls back to spawn-per-task.
        runtime: External-agent runtime. ``'spawn'`` (default) shells out
            once per task. ``'acp'`` drives a persistent
            :class:`chimera.acp.client.ACPClient` and delivers prompts
            via ``session/sendMessage`` (requires ``reuse_session=True``
            to differ from spawn behavior).
        acp_client_factory: Optional injection point for tests; defaults
            to ``lambda cfg: ACPClient(cfg)``.
        push: Watch this teammate's mailbox and push new messages into
            the live session (issue #149). Only meaningful when a
            persistent session exists — spawn-per-task runs have nothing
            to push into and keep today's pull-only behavior regardless.
            Undeliverable mail is always left for the pull path.
        push_interval: Seconds between mailbox stats for the watcher.
        policy: Permission posture for this teammate (issue #150).
            ``None`` inherits the team's configured policy — that
            inheritance IS the propagation. When neither is set,
            permissions stay each runtime's own business, exactly as
            before.
        policy_runtime: Adapter name for translating the policy into the
            external runtime's flags. ``None`` detects it from the first
            token of ``cmd_template``.
        workspace: Directory the teammate may write to under
            ``workspace-write``. Defaults to the current directory.

    Returns:
        Exit code (0 on idle-timeout shutdown).

    Raises:
        ValueError: If a policy is in force but names an unknown posture
            or a runtime with no translation. Running a teammate at an
            unknown posture is the silent failure the policy exists to
            remove, so it fails loudly instead.
    """
    # Coerce flags: --reuse-session is only meaningful with --runtime acp.
    # A misconfigured invocation should still make progress, so we warn
    # and downgrade rather than fail.
    if reuse_session and runtime != "acp":
        print(
            f"chimera-team-run: --reuse-session requires --runtime acp "
            f"(got runtime={runtime!r}); falling back to spawn-per-task.",
            file=log,
        )
        reuse_session = False

    session_state: _SessionState | None = (
        _SessionState() if reuse_session else None
    )

    team = Team(team_name, root=teams_root)
    team.init()
    team.add_member(agent_id)
    prompt = TEAMMATE_PROMPT.format(agent_id=agent_id, team=team_name)
    mailbox = TeamMailbox(team, agent_id)

    # Real-time mail (issue #149). A watcher only makes sense when there is
    # a live session on the other end; spawn-per-task has none, so it keeps
    # the pull-only behavior it has always had.
    watcher: MailboxWatcher | None = None
    if push and session_state is not None:
        watcher = MailboxWatcher(
            mailbox,
            _ACPSteerSink(session_state),
            interval=push_interval,
            on_error=lambda exc: print(
                f"chimera-team-run: mail push deferred to the pull path "
                f"({exc!r}).",
                file=log,
            ),
        )
    elif push and reuse_session is False and runtime == "spawn":
        print(
            "chimera-team-run: spawn-per-task has no live session to push "
            "into; team mail is delivered by team_recv_messages as usual.",
            file=log,
        )

    base_env = {
        **os.environ,
        "CHIMERA_TEAM": team_name,
        "CHIMERA_AGENT": agent_id,
        ENV_FLAG: "1",
    }
    if teams_root is not None:
        base_env["CHIMERA_TEAMS_HOME"] = str(teams_root)

    # Permission propagation (issue #150). An explicit --policy wins; with
    # none, the teammate inherits whatever posture the lead set on the team.
    effective_policy = policy or team.policy
    if effective_policy is not None:
        runtime_name = policy_runtime or detect_runtime(cmd_template)
        translation = translate_policy(
            effective_policy,
            runtime_name,
            workspace=workspace,
            teams_home=teams_root,
        )
        # The posture travels the same way identity does, so a Chimera
        # teammate binds itself to it in-process with no config edit.
        base_env["CHIMERA_TEAM_POLICY"] = translation.policy
        base_env.update(translation.env)
        if translation.args:
            if "{policy_args}" in cmd_template:
                cmd_template = apply_policy_args(cmd_template, translation)
            else:
                # Guessing where flags belong in someone else's command
                # line is how you get a silently-unenforced posture.
                print(
                    f"chimera-team-run: policy {translation.policy!r} maps to "
                    f"{translation.args_string!r} for runtime "
                    f"{translation.runtime!r}, but --cmd has no "
                    f"{{policy_args}} placeholder; those flags were NOT "
                    f"applied.",
                    file=log,
                )
        print(
            f"chimera-team-run: team policy {translation.policy!r} "
            f"(runtime {translation.runtime!r}) applied to {agent_id}.",
            file=log,
        )

    def _my_completed() -> int:
        return sum(
            1 for t in team.list_tasks()
            if t.get("claimed_by") == agent_id and t.get("status") == "completed"
        )

    # Per-task counter of consecutive nudges we've sent for a stuck claim.
    # Resets to 0 (entry removed) when the task transitions out of "claimed".
    nudge_counts: dict[str, int] = {}
    # Tasks the runner has force-released back to the pool, mapped to a
    # fingerprint of their approval-relevant fields at release time. We won't
    # spawn this agent again purely on the strength of a released-by-us task
    # being open again — that would create a claim/release cycle. The entry
    # is cleared when something else acts on the task: it transitions out of
    # "open", or its record materially changes while open (e.g. the lead
    # approves a pending plan), at which point re-claiming can succeed.
    released_by_runner: dict[str, tuple[object, ...]] = {}

    def _release_fingerprint(rec: dict[str, object]) -> tuple[object, ...]:
        return (
            rec.get("plan_status"),
            rec.get("plan_feedback"),
            rec.get("proposed_plan"),
            rec.get("result"),
            rec.get("description"),
            rec.get("depends_on"),
        )

    def _handle_stuck_claims(current_tasks: list[dict[str, object]]) -> bool:
        """Nudge or force-release tasks this agent claimed but didn't finish.

        Sends at most one nudge per stuck task per call. Force-releases
        when the per-task nudge count has reached ``max_nudges``.

        Returns True iff the runner force-released at least one task.
        """
        # Reset nudge counters for any tracked task that has moved out of
        # the "claimed" state (completed or released). A task that becomes
        # unstuck (or is released and then re-claimed in a later round)
        # starts counting fresh.
        status_by_id = {t["id"]: t.get("status") for t in current_tasks}
        for tid in list(nudge_counts):
            if status_by_id.get(tid) != "claimed":
                del nudge_counts[tid]

        # Identify stuck claims: tasks this agent has claimed but not
        # completed. The agent exited without releasing them.
        stuck = [
            t for t in current_tasks
            if t.get("status") == "claimed" and t.get("claimed_by") == agent_id
        ]

        released_any = False
        for t in stuck:
            tid = str(t["id"])
            count = nudge_counts.get(tid, 0)
            description = str(t.get("description", ""))
            if count >= max_nudges:
                released = team.release_task(tid, agent_id)
                if released:
                    print(
                        f"chimera-team-run: task {tid} stuck in 'claimed' "
                        f"after {count} nudge(s); runner force-released it.",
                        file=log,
                    )
                    nudge_counts.pop(tid, None)
                    released_by_runner[tid] = _release_fingerprint(t)
                    released_any = True
            else:
                mailbox.send(
                    sender="chimera-team-run",
                    content=(
                        f"You claimed task {tid} ('{description[:80]}') but did "
                        f"not complete it. Either call team_complete_task or "
                        f"team_release_task."
                    ),
                )
                nudge_counts[tid] = count + 1
                print(
                    f"chimera-team-run: nudged {agent_id} about stuck task "
                    f"{tid} (nudge {count + 1}/{max_nudges}).",
                    file=log,
                )
        return released_any

    last_progress = time.time()

    if watcher is not None:
        watcher.start()
        print(
            f"chimera-team-run: watching mailbox for {agent_id}; new team "
            f"mail is pushed into the live session.",
            file=log,
        )

    try:
        while True:
            # Idle = no team-state progress for idle_timeout, regardless of
            # whether tasks remain open. A stuck agent that never completes
            # anything still drains to this exit rather than spinning forever.
            if time.time() - last_progress > idle_timeout:
                print(
                    f"chimera-team-run: no progress for {idle_timeout:.0f}s "
                    f"({_my_completed()} tasks completed by {agent_id}); exiting.",
                    file=log,
                )
                return 0

            tasks = team.list_tasks()
            # "open" must mean *claimable* open — mirror list_tasks("open"),
            # which excludes tasks whose depends_on are unsatisfied. Counting
            # dep-blocked tasks as spawnable cues the agent for work it
            # cannot claim yet.
            claimable_ids = {t["id"] for t in team.list_tasks(status_filter="open")}
            open_tasks = [t for t in tasks if t["id"] in claimable_ids]
            # Tasks we released earlier and that are now back in the pool —
            # we don't count those as "open work for me" to avoid an infinite
            # claim/release cycle with the same misbehaving agent.
            if released_by_runner:
                # Drop ids that transitioned out of "open" (someone else
                # acted on them) — and ids whose record materially changed
                # while open (e.g. the lead approved a pending plan): the
                # condition that made the claim stuck may be gone.
                rec_by_id = {t["id"]: t for t in tasks}
                for tid in list(released_by_runner):
                    rec = rec_by_id.get(tid)
                    if rec is None or rec.get("status") != "open":
                        released_by_runner.pop(tid)
                    elif _release_fingerprint(rec) != released_by_runner[tid]:
                        released_by_runner.pop(tid)
            spawnable_open = [t for t in open_tasks if t["id"] not in released_by_runner]
            my_stuck = [
                t for t in tasks
                if t.get("status") == "claimed" and t.get("claimed_by") == agent_id
            ]

            # Spawn when there's fresh open work OR we have stuck claims of
            # ours (re-spawn so the agent can read its mailbox and act).
            if not spawnable_open and not my_stuck:
                time.sleep(poll_interval)
                continue

            # Snapshot task ids before the spawn — we use this to detect
            # whether the agent actually made progress.
            before = {t["id"]: t.get("status") for t in tasks}

            spawn_reason = (
                f"{len(spawnable_open)} open task(s)" if spawnable_open
                else f"{len(my_stuck)} stuck claim(s)"
            )

            if session_state is not None:
                # Persistent ACP session: one subprocess, N send_message calls.
                # The dead-client respawn is handled inside the helper — we
                # only see a non-zero rc when the *next* iteration will
                # need to re-create the client.
                print(
                    f"chimera-team-run: {spawn_reason}; "
                    "sending prompt via persistent ACP session.",
                    file=log,
                )
                rc = _acp_run_one_task(
                    session_state,
                    prompt,
                    cmd_template,
                    base_env,
                    log,
                    acp_client_factory,
                )
            else:
                # Legacy spawn-per-task path: substitute placeholders and
                # shell out, paying the cold-start cost each iteration.
                with tempfile.NamedTemporaryFile(
                    "w", suffix=".txt", delete=False,
                ) as f:
                    f.write(prompt)
                    prompt_file = f.name

                try:
                    cmd = cmd_template.replace(
                        "{prompt_file}", prompt_file,
                    ).replace("{prompt}", shlex.quote(prompt))
                    print(
                        f"chimera-team-run: {spawn_reason}; "
                        "spawning external agent.",
                        file=log,
                    )
                    rc = _run_with_timeout(cmd, base_env, task_timeout, log)
                finally:
                    try:
                        os.unlink(prompt_file)
                    except OSError:
                        pass

            after_tasks = team.list_tasks()
            after = {t["id"]: t.get("status") for t in after_tasks}
            progressed = before != after

            # A runner-initiated release still resets the idle timer below
            # (the team state did change), but only *agent* progress earns an
            # immediate re-spawn — otherwise a stuck claim / force-release
            # cycle hot-loops with no sleep between iterations.
            runner_acted = _handle_stuck_claims(after_tasks)

            if progressed:
                last_progress = time.time()
                print(
                    f"chimera-team-run: agent exited rc={rc}; team state changed.",
                    file=log,
                )
            else:
                if runner_acted:
                    last_progress = time.time()
                print(
                    f"chimera-team-run: agent exited rc={rc} but team state "
                    f"did not change. Sleeping {poll_interval:.0f}s before retry.",
                    file=log,
                )
                time.sleep(poll_interval)
    finally:
        # Stop the watcher BEFORE the session it pushes into, so a push in
        # flight can never outlive the client it targets.
        if watcher is not None:
            watcher.stop()
            if watcher.delivered:
                print(
                    f"chimera-team-run: pushed {watcher.delivered} message(s) "
                    f"into the live session.",
                    file=log,
                )
        # Gracefully stop the persistent ACP subprocess on any exit path
        # (idle timeout, KeyboardInterrupt, or unexpected raise).
        if session_state is not None and session_state.client is not None:
            try:
                session_state.client.stop()
                print(
                    "chimera-team-run: stopped persistent ACP session.",
                    file=log,
                )
            except Exception as e:
                print(
                    f"chimera-team-run: failed to stop ACP session ({e!r}).",
                    file=log,
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chimera-team-run",
        description=(
            "Drive an MCP-capable coding agent (Codex, OpenCode, ...) as a "
            "Chimera teammate. Polls the team task list and spawns the "
            "configured external command per task."
        ),
    )
    parser.add_argument("--team", required=True, help="Team name.")
    parser.add_argument("--agent", required=True, help="This teammate's agent id.")
    parser.add_argument(
        "--cmd",
        required=True,
        help=(
            "Shell command template. Use {prompt} for an inline (shell-quoted) "
            "prompt or {prompt_file} for a tempfile path containing the prompt."
        ),
    )
    parser.add_argument(
        "--teams-home",
        default=None,
        help="Override teams home directory (default: $CHIMERA_TEAMS_HOME or ~/.chimera/teams).",
    )
    parser.add_argument("--idle-timeout", type=float, default=60.0)
    parser.add_argument("--task-timeout", type=float, default=600.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument(
        "--max-nudges",
        type=int,
        default=1,
        help=(
            "Consecutive no-progress nudges to send for a stuck claim before "
            "the runner force-releases the task back to the pool (default: 1)."
        ),
    )
    parser.add_argument(
        "--runtime",
        choices=["spawn", "acp"],
        default="spawn",
        help=(
            "External-agent runtime. 'spawn' (default) runs --cmd as a "
            "fresh subprocess per task. 'acp' speaks Agent Client "
            "Protocol over stdio to a persistent subprocess "
            "(combine with --reuse-session)."
        ),
    )
    parser.add_argument(
        "--reuse-session",
        action="store_true",
        help=(
            "Keep one external-agent subprocess alive across tasks "
            "instead of spawning a fresh one per task. Requires "
            "--runtime acp; with --runtime spawn the flag is "
            "downgraded with a warning."
        ),
    )
    parser.add_argument(
        "--no-push",
        dest="push",
        action="store_false",
        default=True,
        help=(
            "Don't watch the mailbox for real-time delivery into a live "
            "session; team mail then arrives only when the agent calls "
            "team_recv_messages. No effect on spawn-per-task runs, which "
            "have no live session to push into."
        ),
    )
    parser.add_argument(
        "--push-interval",
        type=float,
        default=DEFAULT_WATCH_INTERVAL,
        help=(
            "Seconds between mailbox checks for real-time delivery "
            f"(default: {DEFAULT_WATCH_INTERVAL})."
        ),
    )
    parser.add_argument(
        "--policy",
        default=None,
        help=(
            "Permission posture for this teammate: read-only, "
            "workspace-write, or dangerous. Omit to inherit the team's "
            "configured policy; with neither set, permissions stay each "
            "runtime's own business."
        ),
    )
    parser.add_argument(
        "--policy-runtime",
        default=None,
        help=(
            "Which runtime's dialect to translate the policy into. "
            "Defaults to the first token of --cmd. Declare unknown "
            "runtimes with a [team_runtimes.<name>] table in "
            "~/.chimera/config.toml."
        ),
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help=(
            "Directory the teammate may write to under workspace-write "
            "(default: the current directory)."
        ),
    )
    args = parser.parse_args(argv)

    # In reuse-session ACP mode the placeholders are not required —
    # prompts are delivered via session/sendMessage rather than the
    # command line. In every other mode they're load-bearing.
    placeholders_required = not (args.reuse_session and args.runtime == "acp")
    if (
        placeholders_required
        and "{prompt}" not in args.cmd
        and "{prompt_file}" not in args.cmd
    ):
        print(
            "chimera-team-run: --cmd must contain {prompt} or "
            "{prompt_file} (unless --reuse-session --runtime acp is set).",
            file=sys.stderr,
        )
        return 2

    root = Path(args.teams_home).expanduser() if args.teams_home else None
    workspace = Path(args.workspace).expanduser() if args.workspace else None
    try:
        return run_loop(
            team_name=args.team,
            agent_id=args.agent,
            cmd_template=args.cmd,
            teams_root=root,
            idle_timeout=args.idle_timeout,
            task_timeout=args.task_timeout,
            poll_interval=args.poll_interval,
            max_nudges=args.max_nudges,
            reuse_session=args.reuse_session,
            runtime=args.runtime,
            push=args.push,
            push_interval=args.push_interval,
            policy=args.policy,
            policy_runtime=args.policy_runtime,
            workspace=workspace,
        )
    except ValueError as exc:
        # An unresolvable posture must stop the teammate, not launch it
        # at whatever permissions it happens to have.
        print(f"chimera-team-run: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
