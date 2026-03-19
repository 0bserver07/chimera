"""Integration test: all 7 pi-mono features wired together."""
from chimera.core.agent import Agent
from chimera.core.cancellation import CancellationToken, OperationCancelled
from chimera.core.file_tracker import FileTracker
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.message_queue import MessageQueues
from chimera.core.operations import LocalReadOps, LocalBashOps
from chimera.providers.base import Provider, Response
from chimera.providers.registry import list_providers, _ensure_builtins_registered
from chimera.rpc.server import RpcServer
from chimera.rpc.handler import RpcHandler
from chimera.rpc.types import GetStateCommand
from chimera.sessions.session import Session
from chimera.sessions.tree import SessionTree
from chimera.tools.read import ReadFileTool
from chimera.tools.bash import BashTool
from chimera.types import Message

import io
import json


class MockProvider(Provider):
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return Response(content="done", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5})

    @property
    def context_window(self):
        return 1000

    @property
    def supports_tool_use(self):
        return True

    @property
    def model_name(self):
        return "mock"


def test_all_features_wire_together():
    """All 7 features instantiated and wired into an Agent."""
    _ensure_builtins_registered()
    assert "anthropic" in list_providers()

    queues = MessageQueues()
    tracker = FileTracker()
    cancel = CancellationToken()
    config = LoopConfig(
        message_queues=queues,
        file_tracker=tracker,
        cancellation=cancel,
    )
    loop = ReAct(max_steps=5, config=config)

    read_ops = LocalReadOps(cwd="/tmp")
    bash_ops = LocalBashOps(cwd="/tmp")
    tools = [
        ReadFileTool(ops=read_ops),
        BashTool(ops=bash_ops),
    ]

    agent = Agent(
        provider=MockProvider(),
        tools=tools,
        loop=loop,
    )
    result = agent.run("test", env=None)
    assert result.success


def test_session_tree_with_session(tmp_path):
    """SessionTree wired into Session."""
    tree = SessionTree(tmp_path / "session.jsonl")
    agent = Agent(provider=MockProvider(), loop=ReAct(max_steps=5))
    session = Session(agent=agent, tree=tree)
    result = session.chat("hello")
    assert result.output == "done"
    assert tree.entry_count >= 2


def test_cancellation_stops_agent():
    """CancellationToken stops the agent loop."""
    cancel = CancellationToken()
    cancel.cancel()  # Pre-cancel
    config = LoopConfig(cancellation=cancel)
    loop = ReAct(max_steps=10, config=config)
    agent = Agent(provider=MockProvider(), loop=loop)
    result = agent.run("do stuff", env=None)
    assert not result.success
    assert "cancel" in (result.error or "").lower()


def test_message_queues_steering():
    """Steering messages are injected mid-turn."""
    queues = MessageQueues()
    config = LoopConfig(message_queues=queues)
    loop = ReAct(max_steps=5, config=config)
    agent = Agent(provider=MockProvider(), loop=loop)
    # Queue a steering message (will be drained after first step)
    queues.steer(Message.user("actually do X instead"))
    result = agent.run("do Y", env=None)
    assert result.success


def test_file_tracker_records(tmp_path):
    """FileTracker records files from tool execution."""
    tracker = FileTracker()
    tracker.record_read("src/main.py")
    tracker.record_modified("src/app.py")
    section = tracker.to_prompt_section()
    assert "src/main.py" in section
    assert "src/app.py" in section
    meta = tracker.to_metadata()
    assert meta.read_files == ["src/main.py"]
    assert meta.modified_files == ["src/app.py"]


def test_rpc_end_to_end():
    """RPC server processes commands."""
    agent = Agent(provider=MockProvider(), loop=ReAct(max_steps=5))
    session = Session(agent=agent)

    stdin = io.StringIO('{"type": "get_state", "id": "1"}\n')
    stdout = io.StringIO()

    server = RpcServer(session, stdin=stdin, stdout=stdout)
    handler = RpcHandler(server)
    server.set_handlers(handler.handlers)
    server.run()

    output = stdout.getvalue()
    lines = [json.loads(l) for l in output.strip().split("\n") if l.strip()]
    assert any(l.get("command") == "get_state" for l in lines)


def test_operations_with_tools(tmp_path):
    """Tools work with ops backends."""
    (tmp_path / "test.txt").write_text("hello")
    read_ops = LocalReadOps(cwd=str(tmp_path))
    tool = ReadFileTool(ops=read_ops)
    result = tool.execute({"path": "test.txt"}, env=None)
    assert result.success
    assert result.output == "hello"
