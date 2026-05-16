"""Tests for chimera/mcp_servers/teammate_runner.py.

The "mock external agent" is a tiny Python script invoked via shell. It
reads CHIMERA_TEAM and CHIMERA_AGENT from the environment and uses the
``Team`` class directly to claim + complete a task — this is exactly
what a real Codex/OpenCode session would do via the chimera-team MCP
server (the MCP server is just an interface over the same Team object).

Doing it this way lets us validate the runner's loop without needing a
real MCP host installed in the test environment.

The session-reuse tests use an in-process fake ``ACPClient`` injected
via ``acp_client_factory``. The fake mutates the on-disk team state
exactly like a real ACP agent would (claim + complete the next open
task) — that way the same assertions about team state hold, and we get
to count ``start`` / ``stop`` / ``send_message`` calls to prove the
runner actually reuses one subprocess across N tasks.
"""
from __future__ import annotations

import io
import shlex
import sys
import textwrap
import time
from pathlib import Path
from typing import Any, Callable

from chimera.acp.types import ACPResponse, ACPSessionConfig
from chimera.cli.agent_teams import Team, TeamMailbox
from chimera.mcp_servers.teammate_runner import (
    TEAMMATE_PROMPT,
    main,
    run_loop,
)


# A mock agent: claim next open task and complete it. Stable, fast, exits 0.
# The runner injects CHIMERA_TEAMS_HOME so Team() resolves to the right dir.
MOCK_AGENT = textwrap.dedent("""
    import os, sys
    from chimera.cli.agent_teams import Team
    team = Team(os.environ['CHIMERA_TEAM'])
    agent = os.environ['CHIMERA_AGENT']
    for rec in team.list_tasks():
        if rec.get('status') == 'open':
            if team.claim_task(rec['id'], agent):
                team.complete_task(rec['id'], agent, result='mock done')
                print(f'mock-agent: completed {rec["id"]}', file=sys.stderr)
                break
""").strip()


# A mock agent that claims a task but does NOT complete it — to test
# the no-progress branch of the runner.
MOCK_AGENT_NOOP = "import sys; print('mock-agent: did nothing', file=sys.stderr)"


# A mock agent that sleeps forever — to test task_timeout.
MOCK_AGENT_HANG = "import time; time.sleep(30)"


# A mock agent that claims the next open task but never completes it —
# simulates the "agent claimed a task but didn't finish" failure mode.
MOCK_AGENT_CLAIM_ONLY = textwrap.dedent("""
    import os, sys
    from chimera.cli.agent_teams import Team
    team = Team(os.environ['CHIMERA_TEAM'])
    agent = os.environ['CHIMERA_AGENT']
    for rec in team.list_tasks():
        if rec.get('status') == 'open':
            if team.claim_task(rec['id'], agent):
                print(f'mock-agent: claimed {rec["id"]} but bailed',
                      file=sys.stderr)
                break
""").strip()


# A mock agent whose behavior changes across invocations via a counter
# file: round 1 claims, round 2 releases (simulating an agent that reads
# its mailbox and gives up the task), round 3 claims again. Lets us
# exercise the nudge-counter reset path deterministically.
MOCK_AGENT_CLAIM_THEN_RELEASE = textwrap.dedent("""
    import os, sys
    from chimera.cli.agent_teams import Team
    team = Team(os.environ['CHIMERA_TEAM'])
    agent = os.environ['CHIMERA_AGENT']
    state_path = os.path.join(os.environ['CHIMERA_TEAMS_HOME'],
                              f'.mock_round_{agent}.txt')
    try:
        n = int(open(state_path).read().strip())
    except (FileNotFoundError, ValueError):
        n = 0
    open(state_path, 'w').write(str(n + 1))
    if n == 0 or n == 2:
        # Claim the next open task and exit without completing.
        for rec in team.list_tasks():
            if rec.get('status') == 'open':
                if team.claim_task(rec['id'], agent):
                    print(f'mock-agent r{n}: claimed {rec["id"]}',
                          file=sys.stderr)
                    break
    elif n == 1:
        # Release whatever we previously claimed (simulating responding
        # to a runner nudge by giving up the task).
        for rec in team.list_tasks():
            if (rec.get('status') == 'claimed'
                    and rec.get('claimed_by') == agent):
                team.release_task(rec['id'], agent)
                print(f'mock-agent r{n}: released {rec["id"]}',
                      file=sys.stderr)
                break
""").strip()


def _cmd_for(snippet: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(snippet)}"


class TestTeammateRunner:
    def test_runs_through_open_tasks_and_idles_out(self, tmp_path: Path) -> None:
        team = Team("rtest", root=tmp_path)
        team.init()
        for i in range(3):
            team.add_task(f"task {i}", created_by="lead")

        log = io.StringIO()
        rc = run_loop(
            team_name="rtest",
            agent_id="alice",
            cmd_template=_cmd_for(MOCK_AGENT),
            teams_root=tmp_path,
            idle_timeout=0.5,
            task_timeout=10.0,
            poll_interval=0.05,
            log=log,
        )
        assert rc == 0

        statuses = [t["status"] for t in Team("rtest", root=tmp_path).list_tasks()]
        assert statuses.count("completed") == 3
        assert statuses.count("open") == 0
        assert "3 tasks completed by alice" in log.getvalue()

    def test_exits_immediately_when_no_open_tasks(self, tmp_path: Path) -> None:
        team = Team("empty", root=tmp_path)
        team.init()

        log = io.StringIO()
        start = time.time()
        rc = run_loop(
            team_name="empty",
            agent_id="alice",
            cmd_template=_cmd_for(MOCK_AGENT),
            teams_root=tmp_path,
            idle_timeout=0.3,
            poll_interval=0.05,
            log=log,
        )
        elapsed = time.time() - start
        assert rc == 0
        # Should idle out near idle_timeout, not longer
        assert elapsed < 2.0
        assert "0 tasks completed by alice" in log.getvalue()

    def test_no_progress_branch_logs_warning_and_idles_out(self, tmp_path: Path) -> None:
        # MOCK_AGENT_NOOP doesn't claim or complete anything. Runner should
        # detect no-progress, sleep, retry, then idle-timeout out.
        team = Team("noop", root=tmp_path)
        team.init()
        team.add_task("never gets done", created_by="lead")

        log = io.StringIO()
        rc = run_loop(
            team_name="noop",
            agent_id="alice",
            cmd_template=_cmd_for(MOCK_AGENT_NOOP),
            teams_root=tmp_path,
            idle_timeout=0.3,
            poll_interval=0.05,
            log=log,
        )
        # Should still exit cleanly via idle_timeout despite the task
        # staying open the whole time.
        assert rc == 0
        assert "team state did not change" in log.getvalue()
        # The open task should still be open.
        tasks = Team("noop", root=tmp_path).list_tasks()
        assert all(t["status"] == "open" for t in tasks)

    def test_stuck_claim_gets_nudged(self, tmp_path: Path) -> None:
        # An agent that claims a task and exits without completing it should
        # receive a nudge message in its mailbox. With max_nudges set high
        # enough that idle_timeout fires first, we see nudges but never a
        # force-release.
        team = Team("nudge1", root=tmp_path)
        team.init()
        team.add_task("claim-and-bail", created_by="lead")

        log = io.StringIO()
        rc = run_loop(
            team_name="nudge1",
            agent_id="alice",
            cmd_template=_cmd_for(MOCK_AGENT_CLAIM_ONLY),
            teams_root=tmp_path,
            idle_timeout=0.3,
            poll_interval=0.05,
            max_nudges=50,
            log=log,
        )
        assert rc == 0

        # The task should still be claimed by alice (nudged, not released).
        tasks = Team("nudge1", root=tmp_path).list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["status"] == "claimed"
        assert tasks[0]["claimed_by"] == "alice"

        # Alice's mailbox should have at least one nudge from the runner.
        msgs = TeamMailbox(Team("nudge1", root=tmp_path), "alice").recv()
        nudges = [m for m in msgs if m["from"] == "chimera-team-run"]
        assert len(nudges) >= 1
        # The nudge mentions the stuck task id and what to do about it.
        nudge_body = nudges[0]["content"]
        assert tasks[0]["id"] in nudge_body
        assert "team_complete_task" in nudge_body
        assert "team_release_task" in nudge_body
        # Log should also record the nudge.
        assert "nudged alice about stuck task" in log.getvalue()
        # Should NOT have force-released (max_nudges=50 is unreachable
        # within the 0.3s idle window).
        assert "force-released" not in log.getvalue()

    def test_repeated_stuck_claim_gets_released(self, tmp_path: Path) -> None:
        # With max_nudges=1, the first round (agent claims) nudges, the
        # second round (agent re-spawned to handle the stuck claim, still
        # does nothing) force-releases the task back to the pool.
        team = Team("nudge2", root=tmp_path)
        team.init()
        task_id = team.add_task("claim-and-bail-twice", created_by="lead")

        log = io.StringIO()
        rc = run_loop(
            team_name="nudge2",
            agent_id="alice",
            cmd_template=_cmd_for(MOCK_AGENT_CLAIM_ONLY),
            teams_root=tmp_path,
            idle_timeout=0.5,
            poll_interval=0.05,
            max_nudges=1,
            log=log,
        )
        assert rc == 0

        log_text = log.getvalue()
        # We should see exactly one nudge and one force-release for this task.
        assert "nudged alice about stuck task" in log_text
        assert "force-released" in log_text
        assert task_id in log_text

        # The released task should be open and unclaimed.
        tasks = Team("nudge2", root=tmp_path).list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["status"] == "open"
        assert tasks[0]["claimed_by"] is None

    def test_progress_resets_nudge_count(self, tmp_path: Path) -> None:
        # When a task transitions out of "claimed" (here: agent releases it
        # itself, mid-loop), the per-task nudge counter resets. So the
        # second time the agent claims-and-bails on the same task, the
        # runner starts from "nudge 1/N" again instead of carrying forward.
        team = Team("nudge3", root=tmp_path)
        team.init()
        team.add_task("watch-me-bounce", created_by="lead")

        log = io.StringIO()
        rc = run_loop(
            team_name="nudge3",
            agent_id="alice",
            cmd_template=_cmd_for(MOCK_AGENT_CLAIM_THEN_RELEASE),
            teams_root=tmp_path,
            idle_timeout=0.4,
            poll_interval=0.05,
            max_nudges=5,
            log=log,
        )
        assert rc == 0

        log_text = log.getvalue()
        # The expected log prefix (max_nudges=5 keeps the runner from
        # auto-releasing within the idle window):
        #   round 0: agent claims      -> "nudge 1/5"
        #   round 1: agent releases    -> counter cleared, no nudge
        #   round 2: agent claims again -> "nudge 1/5" again, NOT "nudge 2/5"
        # Two "nudge 1/5" occurrences prove the counter reset. (Later
        # rounds may climb to "nudge N/5" because the mock agent runs out
        # of scripted behaviors and stalls; that's irrelevant here.)
        first_nudge_idx = log_text.find("nudge 1/5")
        second_nudge_idx = log_text.find("nudge 1/5", first_nudge_idx + 1)
        assert first_nudge_idx != -1, "first nudge missing"
        assert second_nudge_idx != -1, "counter did not reset for re-claim"
        # The gap between the two "nudge 1/5" entries must contain the
        # mock agent's release (proving the reset trigger).
        between = log_text[first_nudge_idx:second_nudge_idx]
        assert "team state changed" in between
        # And the runner must not have force-released anything before the
        # second "nudge 1/5" — only the agent's voluntary release should
        # have cleared the counter in that window.
        assert "force-released" not in between

    def test_task_timeout_kills_subprocess(self, tmp_path: Path) -> None:
        team = Team("hang", root=tmp_path)
        team.init()
        team.add_task("hangs forever", created_by="lead")

        log = io.StringIO()
        start = time.time()
        rc = run_loop(
            team_name="hang",
            agent_id="alice",
            cmd_template=_cmd_for(MOCK_AGENT_HANG),
            teams_root=tmp_path,
            idle_timeout=0.3,
            task_timeout=0.5,
            poll_interval=0.05,
            log=log,
        )
        elapsed = time.time() - start
        assert rc == 0
        assert "exceeded task_timeout" in log.getvalue()
        # Should have killed the hang and idle-timed out within a few seconds.
        assert elapsed < 5.0


class TestCommandTemplate:
    def test_prompt_file_substitution(self, tmp_path: Path) -> None:
        # An agent that reads its prompt from a file we pass via {prompt_file}.
        agent = textwrap.dedent("""
            import os, sys
            prompt = open(sys.argv[1]).read()
            assert 'teammate' in prompt.lower()
            from chimera.cli.agent_teams import Team
            team = Team(os.environ['CHIMERA_TEAM'])
            for rec in team.list_tasks():
                if rec.get('status') == 'open':
                    if team.claim_task(rec['id'], os.environ['CHIMERA_AGENT']):
                        team.complete_task(rec['id'], os.environ['CHIMERA_AGENT'], result='ok')
                        break
        """).strip()
        cmd = f"{shlex.quote(sys.executable)} -c {shlex.quote(agent)} {{prompt_file}}"

        team = Team("ftest", root=tmp_path)
        team.init()
        team.add_task("first", created_by="lead")

        rc = run_loop(
            team_name="ftest",
            agent_id="alice",
            cmd_template=cmd,
            teams_root=tmp_path,
            idle_timeout=0.3,
            poll_interval=0.05,
            log=io.StringIO(),
        )
        assert rc == 0
        statuses = [t["status"] for t in Team("ftest", root=tmp_path).list_tasks()]
        assert statuses == ["completed"]

    def test_main_rejects_template_without_placeholders(self, tmp_path: Path) -> None:
        rc = main([
            "--team", "x", "--agent", "y",
            "--cmd", "echo no-placeholders-here",
            "--teams-home", str(tmp_path),
            "--idle-timeout", "0.1",
        ])
        assert rc == 2


class TestPromptContents:
    def test_prompt_includes_team_and_agent(self) -> None:
        rendered = TEAMMATE_PROMPT.format(agent_id="codex-1", team="review-pr")
        assert "codex-1" in rendered
        assert "review-pr" in rendered
        # Critical workflow steps the agent must perform:
        assert "team_recv_messages" in rendered
        assert "team_claim_task" in rendered
        assert "team_complete_task" in rendered
        # Stop-after-one-task instruction is the load-bearing one for
        # the runner's invariant (one invocation = one task).
        assert "work exactly ONE task" in rendered


# ---------------------------------------------------------------------------
# Session reuse (issue #148)
# ---------------------------------------------------------------------------


class _FakeACPClient:
    """In-process stand-in for ``chimera.acp.client.ACPClient``.

    Mutates the on-disk team state on every ``send_message`` call —
    claim + complete the next open task — exactly like a real ACP agent
    using the ``team_*`` MCP tools would. The bookkeeping counters
    (``start_calls`` / ``stop_calls`` / ``send_calls``) let assertions
    prove the runner actually reuses one subprocess across N tasks.

    Construct with ``crash_on_send`` to simulate an agent that dies
    mid-session — the next iteration of the runner should re-create the
    client.
    """

    def __init__(
        self,
        cfg: ACPSessionConfig,
        teams_root: Path,
        team_name: str,
        agent_id: str,
        crash_on_send: bool = False,
    ) -> None:
        self.cfg = cfg
        self.teams_root = teams_root
        self.team_name = team_name
        self.agent_id = agent_id
        self.crash_on_send = crash_on_send
        self.start_calls = 0
        self.stop_calls = 0
        self.send_calls = 0
        self._started = False

    def start(self) -> None:
        self.start_calls += 1
        self._started = True

    def stop(self) -> None:
        self.stop_calls += 1
        self._started = False

    def send_message(
        self, text: str, on_chunk: Callable[[str], None] | None = None,
    ) -> ACPResponse:
        self.send_calls += 1
        if self.crash_on_send:
            raise RuntimeError("ACP process closed stdout")
        # Simulate the agent calling team_claim_task + team_complete_task
        # via its MCP client. The on-disk team state is shared with the
        # runner, so this is equivalent to a real agent's behavior from
        # the runner's point of view.
        team = Team(self.team_name, root=self.teams_root)
        for rec in team.list_tasks():
            if rec.get("status") == "open":
                if team.claim_task(rec["id"], self.agent_id):
                    team.complete_task(
                        rec["id"], self.agent_id, result="acp mock done",
                    )
                    break
        return ACPResponse(
            text="", thoughts=[], tool_calls=[],
            cost=0.0, input_tokens=0, output_tokens=0,
        )


def _factory_for(
    teams_root: Path,
    team_name: str,
    agent_id: str,
    *,
    crash_first: bool = False,
    track: list[Any] | None = None,
) -> Callable[[ACPSessionConfig], _FakeACPClient]:
    """Build an ``acp_client_factory`` that records each constructed client.

    When ``crash_first`` is True the first client raises on
    ``send_message`` and subsequent ones behave normally — drives the
    "crash + respawn" assertion.
    """
    sink: list[Any] = track if track is not None else []

    def _factory(cfg: ACPSessionConfig) -> _FakeACPClient:
        crash = crash_first and len(sink) == 0
        client = _FakeACPClient(
            cfg, teams_root=teams_root,
            team_name=team_name, agent_id=agent_id,
            crash_on_send=crash,
        )
        sink.append(client)
        return client

    _factory.clients = sink  # type: ignore[attr-defined]
    return _factory


class TestSessionReuse:
    def test_acp_reuse_keeps_one_subprocess_across_tasks(
        self, tmp_path: Path,
    ) -> None:
        # Three open tasks. With --reuse-session --runtime acp, the
        # runner must create exactly ONE ACPClient and call .start()
        # exactly once — not three times. send_message gets called once
        # per task (3 total).
        team = Team("reuse1", root=tmp_path)
        team.init()
        for i in range(3):
            team.add_task(f"reuse task {i}", created_by="lead")

        factory = _factory_for(tmp_path, "reuse1", "alice")

        log = io.StringIO()
        rc = run_loop(
            team_name="reuse1",
            agent_id="alice",
            cmd_template="opencode acp",  # no placeholders required
            teams_root=tmp_path,
            idle_timeout=0.3,
            poll_interval=0.05,
            log=log,
            reuse_session=True,
            runtime="acp",
            acp_client_factory=factory,
        )
        assert rc == 0

        clients = factory.clients  # type: ignore[attr-defined]
        # Reuse means one ACPClient instance across all tasks.
        assert len(clients) == 1, (
            f"expected 1 client (reuse), got {len(clients)}"
        )
        # And exactly one start() — the whole point of the flag.
        assert clients[0].start_calls == 1
        # Three send_message calls (one per task).
        assert clients[0].send_calls == 3
        # All tasks completed.
        statuses = [
            t["status"] for t in Team("reuse1", root=tmp_path).list_tasks()
        ]
        assert statuses.count("completed") == 3
        # Log shows the persistent session was started.
        assert "started persistent ACP session" in log.getvalue()
        assert "stopped persistent ACP session" in log.getvalue()

    def test_acp_reuse_respawns_after_crash(self, tmp_path: Path) -> None:
        # The first ACPClient crashes on send_message (RuntimeError);
        # the runner must tear it down and create a second client on
        # the next iteration. That second client claims+completes the
        # task. We assert two clients were created and at least one
        # task completed.
        team = Team("reuse2", root=tmp_path)
        team.init()
        for i in range(2):
            team.add_task(f"reuse task {i}", created_by="lead")

        factory = _factory_for(
            tmp_path, "reuse2", "alice", crash_first=True,
        )

        log = io.StringIO()
        rc = run_loop(
            team_name="reuse2",
            agent_id="alice",
            cmd_template="opencode acp",
            teams_root=tmp_path,
            idle_timeout=0.5,
            poll_interval=0.05,
            log=log,
            reuse_session=True,
            runtime="acp",
            acp_client_factory=factory,
        )
        assert rc == 0

        clients = factory.clients  # type: ignore[attr-defined]
        # At least two clients — first crashed, second (and maybe more)
        # took over. We don't pin the exact count because the loop may
        # respawn more than once during idle_timeout, but two is the
        # minimum that proves respawn happened.
        assert len(clients) >= 2, (
            f"expected respawn after crash, got {len(clients)} client(s)"
        )
        # The crashed client got stopped (cleanup of dead session).
        assert clients[0].stop_calls >= 1
        # The crashed client logged its failure.
        assert "ACP session crashed" in log.getvalue()
        # At least one task completed via the surviving client.
        completed = [
            t for t in Team("reuse2", root=tmp_path).list_tasks()
            if t.get("status") == "completed"
        ]
        assert len(completed) >= 1

    def test_acp_reuse_stops_client_on_idle_exit(
        self, tmp_path: Path,
    ) -> None:
        # When the loop exits (here: via idle_timeout because no tasks
        # remain), the persistent ACP client must be stopped. This
        # mirrors the "session cleanup on shutdown" acceptance criterion.
        team = Team("reuse3", root=tmp_path)
        team.init()
        team.add_task("only task", created_by="lead")

        factory = _factory_for(tmp_path, "reuse3", "alice")

        log = io.StringIO()
        rc = run_loop(
            team_name="reuse3",
            agent_id="alice",
            cmd_template="opencode acp",
            teams_root=tmp_path,
            idle_timeout=0.3,
            poll_interval=0.05,
            log=log,
            reuse_session=True,
            runtime="acp",
            acp_client_factory=factory,
        )
        assert rc == 0

        clients = factory.clients  # type: ignore[attr-defined]
        assert len(clients) == 1
        # Critical assertion: stop() called on shutdown.
        assert clients[0].stop_calls == 1
        assert "stopped persistent ACP session" in log.getvalue()

    def test_reuse_session_without_acp_runtime_falls_back_to_spawn(
        self, tmp_path: Path,
    ) -> None:
        # If a user passes --reuse-session without --runtime acp the
        # runner downgrades with a warning and uses the legacy
        # spawn-per-task path. The task still completes — we don't
        # block progress just because the flag is misconfigured.
        team = Team("fallback", root=tmp_path)
        team.init()
        team.add_task("first", created_by="lead")

        log = io.StringIO()
        rc = run_loop(
            team_name="fallback",
            agent_id="alice",
            cmd_template=_cmd_for(MOCK_AGENT),
            teams_root=tmp_path,
            idle_timeout=0.3,
            poll_interval=0.05,
            log=log,
            reuse_session=True,
            runtime="spawn",
        )
        assert rc == 0
        # Warning shown to the operator.
        assert "falling back to spawn-per-task" in log.getvalue()
        # The task still completed via the spawn path.
        statuses = [
            t["status"] for t in Team("fallback", root=tmp_path).list_tasks()
        ]
        assert statuses == ["completed"]
        # No ACP session was started.
        assert "started persistent ACP session" not in log.getvalue()

    def test_acp_send_failure_at_start_returns_to_loop(
        self, tmp_path: Path,
    ) -> None:
        # Hostile factory: every ACPClient raises in .start(). The loop
        # logs the failure but does not crash — it idles out cleanly
        # so the operator can fix the config.
        team = Team("reuse4", root=tmp_path)
        team.init()
        team.add_task("first", created_by="lead")

        class _BrokenStartClient:
            def __init__(self, cfg: ACPSessionConfig) -> None:
                self.cfg = cfg
                self.stop_calls = 0

            def start(self) -> None:
                raise RuntimeError("no such binary")

            def stop(self) -> None:
                self.stop_calls += 1

            def send_message(
                self, text: str,
                on_chunk: Callable[[str], None] | None = None,
            ) -> ACPResponse:  # pragma: no cover — start() raises first
                raise AssertionError("send_message should not be reached")

        log = io.StringIO()
        rc = run_loop(
            team_name="reuse4",
            agent_id="alice",
            cmd_template="opencode acp",
            teams_root=tmp_path,
            idle_timeout=0.3,
            poll_interval=0.05,
            log=log,
            reuse_session=True,
            runtime="acp",
            acp_client_factory=_BrokenStartClient,  # type: ignore[arg-type]
        )
        assert rc == 0
        assert "failed to start ACP session" in log.getvalue()
        # Task still open — nothing got done.
        tasks = Team("reuse4", root=tmp_path).list_tasks()
        assert all(t["status"] == "open" for t in tasks)


class TestSessionReuseCLI:
    def test_main_rejects_placeholders_only_for_spawn(
        self, tmp_path: Path,
    ) -> None:
        # In the default (spawn) runtime the placeholder check still applies.
        rc = main([
            "--team", "x", "--agent", "y",
            "--cmd", "echo no-placeholders-here",
            "--teams-home", str(tmp_path),
            "--idle-timeout", "0.05",
        ])
        assert rc == 2

    def test_main_accepts_placeholder_free_cmd_with_reuse_acp(
        self, tmp_path: Path,
    ) -> None:
        # With --reuse-session --runtime acp the placeholders are not
        # required (prompts arrive via session/sendMessage). Use a
        # quick-failing command so we don't actually spawn anything
        # interesting; what matters is rc != 2 (placeholder validation
        # did not fire).
        rc = main([
            "--team", "x", "--agent", "y",
            "--cmd", "true",  # placeholder-free; would fail validation pre-flag
            "--teams-home", str(tmp_path),
            "--idle-timeout", "0.05",
            "--runtime", "acp",
            "--reuse-session",
        ])
        # rc could be 0 (idle-out with no tasks) — what matters is that
        # we got past the placeholder validation that would have returned 2.
        assert rc != 2

    def test_main_rejects_unknown_runtime(self, tmp_path: Path) -> None:
        # argparse should reject an invalid --runtime value before any
        # of our own validation runs.
        import pytest
        with pytest.raises(SystemExit):
            main([
                "--team", "x", "--agent", "y",
                "--cmd", "echo {prompt}",
                "--teams-home", str(tmp_path),
                "--idle-timeout", "0.05",
                "--runtime", "nonsense",
            ])
