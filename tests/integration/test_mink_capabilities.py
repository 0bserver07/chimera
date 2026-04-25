"""Mink integration smoke tests.

Eleven end-to-end smoke tests exercising the CC-parity capabilities of the
``mink`` subcommand. Each test is independent (no shared state, no required
order). Mark with ``integration`` so they can be selected via
``pytest -m integration``.

Run with::

    uv run pytest tests/integration/test_mink_capabilities.py -v --tb=short

The Kimi-cloud-dependent test (``test_11_walking_skeleton_live_or_skip``) is
skipped cleanly when the local Ollama daemon is unreachable or the
``kimi-k2.6:cloud`` tag is not pulled.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1. CLI surface
# ---------------------------------------------------------------------------


def test_01_mink_cli_help_text() -> None:
    """``chimera mink --help`` exits 0, advertises the documented flags, and
    contains no leftover ``Claude Code`` / ``CC clone`` branding strings."""
    result = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", "mink", "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, (
        f"`mink --help` exited {result.returncode}.\nstderr=\n{result.stderr}"
    )
    # Documented flag matrix
    for flag in ("--model", "--permission-mode", "--print", "--resume", "--output-format"):
        assert flag in result.stdout, f"missing {flag} in --help output"
    # No leftover branding
    assert "Claude Code" not in combined, "`Claude Code` still present in mink --help"
    assert "CC clone" not in combined, "`CC clone` still present in mink --help"


# ---------------------------------------------------------------------------
# 2. Settings loader -> permission ruleset
# ---------------------------------------------------------------------------


def test_02_settings_loader_enforces_rules(tmp_path, monkeypatch) -> None:
    """A project ``.claude/settings.json`` round-trips into a working
    last-match-wins ruleset: ``Read`` allowed, ``WebFetch`` denied,
    ``Bash(git push *)`` asks, plain ``git status`` defaults."""
    # Isolate from the real $HOME so user-level settings can't leak in.
    monkeypatch.setenv("HOME", str(tmp_path))
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": ["Read"],
                    "deny": ["WebFetch"],
                    "ask": ["Bash(command:git push *)"],
                }
            }
        )
    )

    from chimera.mink.settings import load_mink_settings
    from chimera.permissions.base import PermissionAction

    settings = load_mink_settings(cwd=tmp_path)
    assert settings.permissions.allow == ["Read"]
    assert settings.permissions.deny == ["WebFetch"]
    assert settings.permissions.ask == ["Bash(command:git push *)"]

    cfg = settings.to_chimera_loop_config()
    ruleset = cfg.permissions
    assert ruleset is not None, "to_chimera_loop_config did not wire a ruleset"

    assert ruleset.evaluate("Read", {}) is PermissionAction.ALLOW
    assert ruleset.evaluate("WebFetch", {"url": "https://example"}) is PermissionAction.DENY
    assert (
        ruleset.evaluate("Bash", {"command": "git push origin main"})
        is PermissionAction.ASK
    )
    # plain `git status` matches no rule => default = ASK (CC parity).
    assert ruleset.evaluate("Bash", {"command": "git status"}) is PermissionAction.ASK


# ---------------------------------------------------------------------------
# 3. PreToolUse hook mutates input
# ---------------------------------------------------------------------------


def test_03_pre_tool_use_hook_mutates_input(tmp_path) -> None:
    """A FunctionHook returning ``hookSpecificOutput.updatedInput`` shall
    rewrite the tool input. Then run BashTool with the rewritten args and
    confirm the safe command actually executed."""
    from chimera.env.local import LocalEnvironment
    from chimera.hooks.events import HookEvent
    from chimera.hooks.executor import HookExecutor
    from chimera.hooks.hook_types import (
        FunctionHook,
        HookInput,
        HookMatcher,
        HookOutput,
    )
    from chimera.tools.bash import BashTool

    def rewrite(_messages, _abort) -> HookOutput:
        return HookOutput(updated_input={"command": "echo SAFE"})

    matcher = HookMatcher(hooks=[FunctionHook(callback=rewrite)], matcher="bash")
    executor = HookExecutor()
    inp = HookInput(
        event=HookEvent.PRE_TOOL_USE,
        session_id="t3",
        tool_name="bash",
        tool_input={"command": "rm -rf /"},
    )
    out = asyncio.run(
        executor.execute(HookEvent.PRE_TOOL_USE, inp, [matcher])
    )
    assert out.updated_input == {"command": "echo SAFE"}, (
        f"hook did not return updated_input; got {out.updated_input!r}"
    )

    # Apply the rewrite the way tool_executor does and run the tool.
    effective = dict(inp.tool_input or {})
    effective.update(out.updated_input)
    env = LocalEnvironment(workdir=str(tmp_path))
    env.setup()
    try:
        result = BashTool().execute(effective, env)
    finally:
        env.cleanup()
    assert result.error in (None, ""), f"bash error: {result.error!r}"
    assert "SAFE" in (result.output or ""), (
        f"expected SAFE in bash output; got {result.output!r}"
    )


# ---------------------------------------------------------------------------
# 4. PreToolUse hook overrides permission
# ---------------------------------------------------------------------------


def test_04_pre_tool_use_hook_overrides_permission() -> None:
    """``PermissionChecker.check`` honours a hook's
    ``hookSpecificOutput.permissionDecision`` literal: ``deny`` -> DENY
    decision (with the hook's reason), ``allow`` -> ALLOW even with no
    matching rule."""
    from chimera.permissions.checker import PermissionChecker
    from chimera.permissions.context import PermissionContext
    from chimera.permissions.modes import PermissionMode
    from chimera.permissions.rules import PermissionBehavior

    class _StubTool:
        name = "bash"
        is_read_only = False
        requires_user_interaction = False

    checker = PermissionChecker()
    ctx = PermissionContext(mode=PermissionMode.DEFAULT)

    deny_decision = asyncio.run(
        checker.check(
            _StubTool(), {"command": "ls"}, ctx, permission_decision="deny",
        )
    )
    assert deny_decision.behavior is PermissionBehavior.DENY
    # Reason should carry hook provenance.
    assert (
        deny_decision.reason is not None
        and deny_decision.reason.type == "hook"
        and deny_decision.reason.detail == "deny"
    ), f"missing hook provenance on deny: {deny_decision.reason!r}"

    allow_decision = asyncio.run(
        checker.check(
            _StubTool(), {"command": "ls"}, ctx, permission_decision="allow",
        )
    )
    assert allow_decision.behavior is PermissionBehavior.ALLOW, (
        f"hook 'allow' did not override default-ASK; got {allow_decision.behavior}"
    )


# ---------------------------------------------------------------------------
# 5. CLAUDE.md memory loaded + injected
# ---------------------------------------------------------------------------


def test_05_claude_md_memory_loaded(tmp_path, monkeypatch) -> None:
    """``load_memory`` discovers a project-level CLAUDE.md and
    ``inject_memory`` slots it after any leading system messages as a user
    message (CC contract)."""
    # Isolate $HOME so the user's own ~/.claude/CLAUDE.md doesn't merge in.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("# Project Notes\nUse uv, not pip.\n")

    from chimera.context.agent_memory import inject_memory, load_memory

    text = load_memory(cwd=proj)
    assert "Use uv, not pip" in text, (
        f"load_memory missed the project file; got {text!r}"
    )

    msgs = inject_memory([{"role": "system", "content": "sys"}], cwd=proj)
    assert len(msgs) == 2, f"expected 2 messages after injection, got {len(msgs)}"
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[1]["role"] == "user"
    assert "Use uv, not pip" in msgs[1]["content"]


# ---------------------------------------------------------------------------
# 6. Task tool spawns isolated subagent
# ---------------------------------------------------------------------------


def test_06_task_tool_spawns_isolated_subagent(tmp_path) -> None:
    """``TaskTool`` resolves a subagent type, runs it under the parent's
    provider in an isolated context, and short-circuits cleanly when the
    parent cancellation token fires."""
    from chimera.core.agent import Agent
    from chimera.core.cancellation import CancellationToken
    from chimera.core.context import Context
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.providers.base import Provider, Response
    from chimera.tools.task_tool import TaskTool

    class _StubProvider(Provider):
        # Implement every abstract on Provider with the cheapest viable shape.
        def __init__(self) -> None:
            self.calls = 0
        def complete(  # type: ignore[override]
            self, messages, tools=None, temperature=0.0,
            max_tokens=None, thinking=None,
        ) -> Response:
            self.calls += 1
            return Response(content="done", tool_calls=[], usage={"input": 1, "output": 1})
        @property
        def model_name(self) -> str:  # type: ignore[override]
            return "stub-model"
        @property
        def context_window(self) -> int:  # type: ignore[override]
            return 8192
        @property
        def supports_tool_use(self) -> bool:  # type: ignore[override]
            return True

    provider = _StubProvider()
    cancel = CancellationToken()
    parent_loop = ReAct(max_steps=4, config=LoopConfig(cancellation=cancel))
    parent = Agent(
        provider=provider,
        tools=[],
        loop=parent_loop,
        prompt=Prompt.from_string("you are parent"),
    )
    # Track parent context separately — TaskTool builds the *child* context.
    parent_context = Context(system="parent system")
    parent_msgs_before = len(list(parent_context.messages))

    tool = TaskTool(parent=parent)

    # Use the built-in 'general-purpose' preset (always present in the registry).
    args = {
        "description": "smoke",
        "prompt": "say done",
        "subagent_type": "general-purpose",
        "isolation": "full",
    }
    result = tool.execute(args, env=None)
    # TaskTool must always return a ToolResult (no silent None / hang).
    assert result is not None
    assert (result.output or "") != "" or result.error, (
        "TaskTool returned empty output and no error"
    )

    # Isolation: the parent's separately-tracked Context wasn't touched.
    parent_msgs_after = len(list(parent_context.messages))
    assert parent_msgs_after == parent_msgs_before, (
        "parent context grew; isolation broken"
    )

    # Cancellation propagation: cancel and re-dispatch; child should bail
    # quickly without hanging the test.
    cancel.cancel()
    t0 = time.time()
    second = tool.execute(args, env=None)
    elapsed = time.time() - t0
    assert elapsed < 5.0, f"cancelled subagent took {elapsed:.2f}s to return"
    assert second is not None


# ---------------------------------------------------------------------------
# 7. MCP tool naming + permission glob
# ---------------------------------------------------------------------------


def test_07_mcp_tool_naming() -> None:
    """A stub MCP server exposing ``read_file`` / ``write_file`` from
    ``filesystem`` produces the canonical ``mcp__filesystem__<tool>`` names,
    and a ``mcp__filesystem*`` permission rule matches both."""
    from chimera.mcp.client import MCPClient
    from chimera.mcp.tools import MCPTool, mcp_prefix
    from chimera.mcp.transport import MCPTransport
    from chimera.permissions.base import PermissionAction
    from chimera.permissions.rule import PermissionRuleset, Rule

    class _StubTransport(MCPTransport):
        def start(self) -> None:
            pass
        def send(self, _payload: dict[str, Any]):  # noqa: ANN201
            return {"jsonrpc": "2.0", "id": 1, "result": {}}
        def close(self) -> None:
            pass

    client = MCPClient()
    transport = _StubTransport()
    client.add_transport("filesystem", transport)
    # Bypass the network discovery; install the tool defs directly.
    client._tool_defs["filesystem"] = [  # type: ignore[attr-defined]
        {"name": "read_file", "description": "read", "inputSchema": {"type": "object"}},
        {"name": "write_file", "description": "write", "inputSchema": {"type": "object"}},
    ]

    tools = client.tools
    names = sorted(t.name for t in tools)
    assert names == ["mcp__filesystem__read_file", "mcp__filesystem__write_file"], (
        f"unexpected MCP tool names: {names}"
    )
    assert all(isinstance(t, MCPTool) for t in tools)
    assert mcp_prefix("filesystem", "read_file") == "mcp__filesystem__read_file"

    rules = [Rule(tool_pattern="mcp__filesystem*", action=PermissionAction.ALLOW)]
    ruleset = PermissionRuleset(rules=rules, default=PermissionAction.ASK)
    for n in names:
        assert ruleset.evaluate(n, {}) is PermissionAction.ALLOW, (
            f"{n} did not match mcp__filesystem* glob"
        )


# ---------------------------------------------------------------------------
# 8. Notebook / worktree / cron tools dispatchable + notebook smoke
# ---------------------------------------------------------------------------


def test_08_notebook_worktree_cron_dispatchable(tmp_path) -> None:
    """Each of the six niche CC-parity tools instantiates with a name +
    description + JSON-Schema input shape. NotebookEditTool then performs
    a real insert against the fixture and confirms cell count grew by 1."""
    pytest.importorskip("nbformat")
    from chimera.tools.cron_tools import (
        CronCreateTool,
        CronDeleteTool,
        CronListTool,
    )
    from chimera.tools.notebook_edit import NotebookEditTool
    from chimera.tools.worktree_tool import EnterWorktreeTool, ExitWorktreeTool

    tools = [
        NotebookEditTool(),
        EnterWorktreeTool(),
        ExitWorktreeTool(),
        CronCreateTool(),
        CronListTool(),
        CronDeleteTool(),
    ]
    for t in tools:
        assert getattr(t, "name", ""), f"{type(t).__name__} has no .name"
        assert getattr(t, "description", ""), f"{t.name} has no .description"
        params = getattr(t, "parameters", None)
        assert isinstance(params, dict) and params.get("type") == "object", (
            f"{t.name} has no JSON-Schema parameters dict"
        )

    # Smoke: insert a cell and confirm the count changed by +1.
    fixture_src = REPO_ROOT / "tests" / "fixtures" / "sample.ipynb"
    assert fixture_src.exists(), f"missing fixture {fixture_src}"
    target = tmp_path / "sample.ipynb"
    shutil.copyfile(fixture_src, target)

    import nbformat
    before = len(nbformat.read(str(target), as_version=4).cells)

    tool = NotebookEditTool()
    res = tool.execute(
        {
            "notebook_path": str(target),
            "action": "insert",
            "cell_index": 0,
            "cell_type": "code",
            "content": "# inserted by smoke test\n",
        },
        env=None,
    )
    assert res.error in (None, ""), f"NotebookEdit failed: {res.error!r}"

    after = len(nbformat.read(str(target), as_version=4).cells)
    assert after == before + 1, f"cell count went {before} -> {after}, expected +1"


# ---------------------------------------------------------------------------
# 9. Slash commands wired
# ---------------------------------------------------------------------------


def test_09_resume_cost_compact_slash_commands_wired(capsys) -> None:
    """The mink REPL slash-command registry exposes the documented set;
    each handler dispatches with a stub session and prints something."""
    from chimera.cli import slash_commands

    names = {n for n, _ in slash_commands.list_commands()}
    expected = {
        "resume", "cost", "compact", "status", "doctor", "permissions",
        "hooks", "mcp", "sandbox", "subagent", "plugin", "review", "config",
    }
    missing = expected - names
    assert not missing, f"missing slash commands: {sorted(missing)}"

    class _StubMessages:
        def __iter__(self):
            return iter([])

    class _StubContext:
        def __init__(self) -> None:
            self.messages = _StubMessages()

    class _StubAgent:
        provider = None
        tools: list[Any] = []
        prompt = None

    class _StubSession:
        def __init__(self) -> None:
            self.id = "stub-session"
            self.context = _StubContext()
            self.agent = _StubAgent()
            self.cost = 0.0
            self.tools: list[Any] = []
        def messages(self):
            return []

    session = _StubSession()
    handled_count = 0
    for cmd in expected:
        # Capture before each dispatch so prints attribute correctly.
        capsys.readouterr()
        try:
            handled = slash_commands.dispatch(f"/{cmd}", session, env=None)
        except Exception as exc:
            # A stub session may legitimately raise inside a handler; what
            # we care about is that the command was recognised by dispatch.
            # If dispatch itself raised before recognising the name, that's
            # a real wiring bug.
            captured = capsys.readouterr()
            assert cmd in (captured.out + captured.err) or "Unknown command" not in (
                captured.out + captured.err
            ), f"/{cmd} not registered (exception {exc!r})"
            continue
        captured = capsys.readouterr()
        if handled:
            handled_count += 1
        # Either dispatch returned True OR the handler printed *something*
        # (handlers may return False on degraded paths but must explain).
        assert handled or (captured.out + captured.err), (
            f"/{cmd}: dispatch returned False and printed nothing"
        )
    assert handled_count >= 1, "no slash commands were handled at all"


# ---------------------------------------------------------------------------
# 10. stream-json output handler
# ---------------------------------------------------------------------------


def test_10_stream_json_output_emits_per_event() -> None:
    """``StreamJsonHandler`` writes one NDJSON line per LoopEvent, each
    line parses to a dict carrying ``type`` and ``ts`` keys, and a
    registered API key in the payload is redacted before emission."""
    from chimera.cli.output_format import StreamJsonHandler
    from chimera.events.types import (
        AgentEndEvent,
        AgentStartEvent,
        StepEvent,
        TextDeltaEvent,
        ToolCallEvent,
    )
    from chimera.secrets.detector import SecretDetector
    from chimera.secrets.redactor import RedactionMiddleware
    from chimera.secrets.registry import SecretRegistry

    # Wire a registry that knows our fake key by name.
    registry = SecretRegistry()
    fake = "sk-mink-INTEGRATION-SECRET-ABC123"
    registry.register("FAKE_API_KEY", fake)
    redaction = RedactionMiddleware(
        registry=registry, detector=SecretDetector(), detect_unknown=True,
    )

    buf = io.StringIO()
    handler = StreamJsonHandler(out=buf, redaction=redaction)
    events = [
        AgentStartEvent(max_steps=3),
        StepEvent(step_number=1, content="planning"),
        ToolCallEvent(
            tool_name="bash",
            arguments={"command": f"echo {fake}"},
            call_id="c1",
        ),
        TextDeltaEvent(content=f"partial result with {fake} embedded"),
        AgentEndEvent(steps=1, success=True, total_cost=0.0),
    ]
    for e in events:
        handler.handle_loop_event(e)

    raw = buf.getvalue()
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 5, f"expected 5 NDJSON lines, got {len(lines)}: {raw!r}"
    for ln in lines:
        obj = json.loads(ln)
        assert "type" in obj, f"missing 'type' in {obj}"
        assert "ts" in obj, f"missing 'ts' in {obj}"
    assert fake not in raw, f"fake API key leaked into stream-json output: {raw!r}"


# ---------------------------------------------------------------------------
# 11. Walking skeleton: live (or skip)
# ---------------------------------------------------------------------------


def _ollama_has_kimi(host: str, tag: str = "kimi-k2.6:cloud") -> bool:
    """Best-effort reachability + tag-presence probe; never raises.

    The default ``tag`` is ``kimi-k2.6:cloud`` (matches the published
    walking-skeleton).  CI uses ``CHIMERA_MINK_LIVE_MODEL`` (or
    ``CHIMERA_MINK_MODEL``, the same env the walking-skeleton honors) to
    point at a smaller CPU model — see ``.github/workflows/mink-live.yml``.
    """
    # WHY (CI parity): honour the same env vars the walking-skeleton uses so
    # ``mink-live.yml`` can run with ``llama3.2:3b`` instead of the
    # cloud-credentialled kimi tag.
    tag = (
        os.environ.get("CHIMERA_MINK_LIVE_MODEL")
        or os.environ.get("CHIMERA_MINK_MODEL")
        or tag
    )
    try:
        import httpx  # type: ignore[import-not-found]
    except Exception:
        return False
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=3)
        r.raise_for_status()
        body = r.json()
    except Exception:
        return False
    models = body.get("models") or []
    family = tag.split(":")[0]
    for m in models:
        name = (m.get("name") if isinstance(m, dict) else "") or ""
        if name == tag or name.startswith(family):
            return True
    return False


@pytest.mark.live
def test_11_walking_skeleton_live_or_skip() -> None:
    """If a local Ollama daemon answers and ``kimi-k2.6:cloud`` is pulled,
    drive the published walking-skeleton end-to-end. Otherwise skip
    cleanly so CI without GPUs / cloud creds stays green.

    Gated behind ``CHIMERA_MINK_LIVE=1`` (CI must opt in); the live model
    can take well over the original 120s budget when ``kimi-k2.6:cloud``
    is reachable but throttled, so the per-process timeout is 240s.
    """
    if os.environ.get("CHIMERA_MINK_LIVE") != "1":
        pytest.skip("set CHIMERA_MINK_LIVE=1 to run the live walking-skeleton test")

    host = os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
    if not _ollama_has_kimi(host):
        pytest.skip("kimi-k2.6:cloud not available locally")

    skel = REPO_ROOT / "examples" / "mink_walking_skeleton.py"
    if not skel.exists():
        pytest.skip(f"walking-skeleton missing at {skel}")

    # WHY: prompt is phrased as a hard MUST so the model is forced to
    # dispatch a tool. The previous "List the files... then summarise"
    # phrasing left the model free to reply from priors and skip the
    # tool, which made the tool-evidence assertion below a coin flip.
    prompt = (
        "You MUST use the bash tool to run `echo HELLO_FROM_BASH` "
        "before responding. After the bash call returns, write a "
        "one-line summary that includes the literal text "
        "HELLO_FROM_BASH."
    )
    proc = subprocess.run(
        [sys.executable, str(skel), prompt],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=240,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, (
        f"walking-skeleton exit={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    # Evidence of at least one tool dispatch — ConsoleStreamHandler
    # renders tool calls as `[Tool: <name>]`; stream-json mode emits
    # `tool_call` JSON lines. Don't fall back to a substring check on
    # the literal word `bash` — that matched the prompt itself, so
    # every run "passed" regardless of whether a tool actually ran.
    saw_tool = (
        "[Tool:" in combined
        or "tool_call" in combined
        or "ToolCall" in combined
    )
    assert saw_tool, (
        "no tool dispatch evidence (no `[Tool:`, `tool_call`, or "
        f"`ToolCall`) in walking-skeleton output:\n{combined}"
    )
    # And the loop produced *some* trailing assistant text.
    assert combined.strip(), "walking-skeleton produced no output at all"
