"""Tests for every example — uses real provider when credentials are set,
falls back to mock provider when they're not.

    # Run with mocks (no API key):
    python -m pytest tests/test_examples.py -v

    # Run with real GLM-5:
    source .env
    python -m pytest tests/test_examples.py -v
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock


import chimera
from chimera.types import ToolCall


# -- Provider setup: real or mock --

_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
_LIVE = _TOKEN is not None


def _real_provider():
    return chimera.create_provider(model=os.environ.get("ANTHROPIC_MODEL", "glm-5"))


def _mock_provider(*responses):
    provider = MagicMock()
    provider.model_name = "test-model"
    mocks = []
    for r in responses:
        m = MagicMock()
        if isinstance(r, str):
            m.content = r
            m.tool_calls = []
            m.has_tool_calls = False
        else:
            text, tcs = r
            m.content = text
            m.tool_calls = tcs
            m.has_tool_calls = len(tcs) > 0
        m.usage = {"input_tokens": 10, "output_tokens": 5}
        mocks.append(m)
    provider.complete.side_effect = mocks
    return provider


# ---------------------------------------------------------------
# 1. Provider basics (quickstart_provider.py)
# ---------------------------------------------------------------

def test_provider_text_completion():
    if _LIVE:
        provider = _real_provider()
        resp = provider.complete([chimera.Message.user("What is 7 * 8? Just the number.")])
        assert "56" in resp.content
    else:
        provider = _mock_provider("56")
        resp = provider.complete([chimera.Message.user("What is 7 * 8?")])
        assert resp.content == "56"


def test_provider_tool_call():
    tool_schema = {
        "name": "calculator",
        "description": "Evaluate a math expression",
        "input_schema": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    }
    if _LIVE:
        provider = _real_provider()
        resp = provider.complete(
            [chimera.Message.user("What is 123 * 456? Use the calculator.")],
            tools=[tool_schema],
        )
        assert resp.has_tool_calls
        assert resp.tool_calls[0].name == "calculator"
    else:
        tc = ToolCall(id="tc1", name="calculator", arguments={"expression": "123*456"})
        provider = _mock_provider(("", [tc]))
        resp = provider.complete([chimera.Message.user("calc")], tools=[tool_schema])
        assert resp.has_tool_calls


def test_provider_multi_turn():
    if _LIVE:
        provider = _real_provider()
        r1 = provider.complete([chimera.Message.user("My favorite color is cerulean.")])
        r2 = provider.complete([
            chimera.Message.user("My favorite color is cerulean."),
            chimera.Message.assistant(r1.content),
            chimera.Message.user("What is my favorite color?"),
        ])
        assert "cerulean" in r2.content.lower()
    else:
        provider = _mock_provider("Got it", "cerulean")
        r1 = provider.complete([chimera.Message.user("My color is cerulean")])
        r2 = provider.complete([chimera.Message.user("What is my color?")])
        assert "cerulean" in r2.content


# ---------------------------------------------------------------
# 2. Agent with tools (agent_with_tools.py)
# ---------------------------------------------------------------

def test_agent_with_tools_creates_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = chimera.LocalEnvironment(workdir=tmpdir)
        env.setup()

        if _LIVE:
            provider = _real_provider()
        else:
            write_call = ToolCall(id="tc1", name="write_file", arguments={
                "path": "fibonacci.py", "content": "print([0,1,1,2,3,5,8,13,21,34])"
            })
            provider = _mock_provider(
                ("Creating file", [write_call]),
                "The Fibonacci numbers are: 0, 1, 1, 2, 3, 5, 8, 13, 21, 34",
            )

        agent = chimera.Agent(
            provider=provider,
            tools=list(chimera.AGENT_TOOLS),
            loop=chimera.ReAct(max_steps=8),
        )
        result = agent.run(
            "Create a file called fibonacci.py that prints the first 10 Fibonacci numbers.",
            env=env,
        )
        assert result.success

        if _LIVE:
            assert os.path.exists(os.path.join(tmpdir, "fibonacci.py"))

        env.cleanup()


# ---------------------------------------------------------------
# 3. Pipeline composition (composition_pipeline.py)
# ---------------------------------------------------------------

def test_pipeline_two_agents():
    if _LIVE:
        provider = _real_provider()
        coder = chimera.Agent(provider=provider, loop=chimera.ReAct(max_steps=3), name="coder")
        reviewer = chimera.Agent(provider=provider, loop=chimera.ReAct(max_steps=3), name="reviewer")
    else:
        coder = chimera.Agent(
            provider=_mock_provider("def is_palindrome(s): return s == s[::-1]"),
            loop=chimera.ReAct(max_steps=2), name="coder",
        )
        reviewer = chimera.Agent(
            provider=_mock_provider("Code looks good."),
            loop=chimera.ReAct(max_steps=2), name="reviewer",
        )

    pipe = chimera.Pipeline([coder, reviewer])
    result = pipe.run("Write a palindrome checker and review it.", env=None)
    assert result.success


# ---------------------------------------------------------------
# 4. Think + AskUser (think_and_ask.py)
# ---------------------------------------------------------------

def test_think_and_ask_user():
    callback_called = []

    def cb(q, choices=None):
        callback_called.append(q)
        return "Python"

    if _LIVE:
        provider = _real_provider()
    else:
        think_call = ToolCall(id="tc1", name="think", arguments={
            "thought": "I need to know what language"
        })
        ask_call = ToolCall(id="tc2", name="ask_user", arguments={
            "question": "What language?"
        })
        provider = _mock_provider(
            ("Let me think", [think_call, ask_call]),
            "Great, I'll help you with Python!",
        )

    agent = chimera.Agent(
        provider=provider,
        tools=[chimera.ThinkTool(), chimera.AskUserTool(callback=cb)],
        loop=chimera.ReAct(max_steps=8),
        prompt=chimera.Prompt.from_string(
            "You are a helpful tutor. Use think to reason, then ask_user "
            "to ask ONE question. After getting the answer, respond with advice. "
            "Do NOT ask more than one question."
        ),
    )
    result = agent.run("Help me learn to code. Ask me what language I want.", env=None)
    assert result.success
    assert len(callback_called) >= 1


# ---------------------------------------------------------------
# 5. Wire monitoring (wire_monitoring.py)
# ---------------------------------------------------------------

def test_wire_monitoring():

    wire = chimera.Wire()
    received = []
    wire.on_message(lambda msg: received.append(type(msg).__name__))

    config = chimera.LoopConfig(wire=wire)

    if _LIVE:
        provider = _real_provider()
    else:
        provider = _mock_provider("Hello from Chimera!")

    agent = chimera.Agent(
        provider=provider,
        tools=[],
        loop=chimera.ReAct(max_steps=5, config=config),
    )
    result = agent.run("Say hello.", env=None)
    assert result.success
    assert "StepBegin" in received
    assert "StepEnd" in received


# ---------------------------------------------------------------
# 6. D-Mail context rewind (dmail_context_rewind.py)
# ---------------------------------------------------------------

def test_dmail_context_rewind():
    dmail = chimera.DMailTool()

    if _LIVE:
        provider = _real_provider()
        tools = [dmail, chimera.ThinkTool()]
    else:
        cp_call = ToolCall(id="tc1", name="dmail", arguments={"action": "checkpoint"})
        send_call = ToolCall(id="tc3", name="dmail", arguments={
            "action": "send", "checkpoint_id": 0,
            "message": "This is a Flask+PostgreSQL web app."
        })
        provider = _mock_provider(
            ("Checkpoint", [cp_call]),
            ("Sending d-mail", [send_call]),
            "Based on my D-Mail: this is a Flask app.",
        )
        tools = [dmail]

    agent = chimera.Agent(
        provider=provider,
        tools=tools,
        loop=chimera.ReAct(max_steps=8),
        prompt=chimera.Prompt.from_string(
            "You have a dmail tool. First create a checkpoint (action='checkpoint'), "
            "then send a d-mail (action='send') to rewind with a summary. "
            "After the rewind, respond with what you know."
        ),
    )
    result = agent.run("Create a checkpoint, then send a d-mail back to it.", env=None)
    assert result.success
    assert dmail._context is not None
    assert dmail.checkpoint_count >= 1


# ---------------------------------------------------------------
# 7. Flow skills (flow_skills.py)
# ---------------------------------------------------------------

def test_flow_skills_parse_and_advance():
    flow = chimera.Flow.from_mermaid("""\
flowchart TD
    A([BEGIN]) --> B[Read the code]
    B --> C{Has tests?}
    C -->|yes| D[Run tests]
    C -->|no| E[Write tests]
    D --> F([END])
    E --> D
""")
    assert len(flow.nodes) == 6
    assert flow.nodes[flow.begin_id].kind == "begin"

    current = flow.begin_id
    current = flow.advance(current)
    assert flow.nodes[current].label == "Read the code"

    current = flow.advance(current)
    assert flow.nodes[current].kind == "decision"

    current = flow.advance(current, choice="yes")
    assert flow.nodes[current].label == "Run tests"

    current = flow.advance(current)
    assert current == flow.end_id


def test_flow_to_prompt():
    flow = chimera.Flow.from_mermaid("""\
flowchart TD
    A([BEGIN]) --> B[Do work]
    B --> C([END])
""")
    prompt = flow.to_prompt(current_node_id="B")
    assert "You are currently at" in prompt
    assert "Do work" in prompt


def test_flow_with_real_agent():
    """Agent follows a flow decision using real or mock provider."""
    flow = chimera.Flow.from_mermaid("""\
flowchart TD
    A([BEGIN]) --> B{Is it raining?}
    B -->|yes| C[Take umbrella]
    B -->|no| D[Wear sunglasses]
    C --> E([END])
    D --> E
""")

    if _LIVE:
        provider = _real_provider()
    else:
        provider = _mock_provider("It's sunny today. <choice>no</choice>")

    agent = chimera.Agent(
        provider=provider,
        loop=chimera.ReAct(max_steps=3),
    )

    prompt = flow.to_prompt(current_node_id="B")
    result = agent.run(
        "The weather is sunny and clear. " + prompt,
        env=None,
    )
    assert result.success

    choice = chimera.parse_choice(result.output)
    if choice:
        next_id = flow.advance("B", choice)
        assert next_id in flow.nodes


# ---------------------------------------------------------------
# 8. Synthesis (quickstart_synthesize.py)
# ---------------------------------------------------------------

def test_synthesize_imports():
    assert callable(chimera.synthesize)


# ---------------------------------------------------------------
# 9. Minimal coding agent (coding_agent_minimal.py)
# ---------------------------------------------------------------

def test_minimal_coding_agent_imports():
    """Verify the minimal coding agent imports and its main() is callable."""
    import importlib.util
    examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples")
    path = os.path.join(examples_dir, "agent", "coding_agent_minimal.py")
    spec = importlib.util.spec_from_file_location("coding_agent_minimal", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")
    assert callable(mod.main)


# ---------------------------------------------------------------
# 10. Supervisor delegation (supervisor_delegation.py)
# ---------------------------------------------------------------

def test_supervisor_delegation():
    from chimera.composition import Supervisor

    if _LIVE:
        provider = _real_provider()
        researcher = chimera.Agent(
            provider=provider, loop=chimera.ReAct(max_steps=3), name="researcher",
        )
        coder = chimera.Agent(
            provider=provider, loop=chimera.ReAct(max_steps=3), name="coder",
        )
        coordinator = chimera.Agent(
            provider=provider, loop=chimera.ReAct(max_steps=6), name="coordinator",
        )
    else:
        # The coordinator calls researcher, then coder, then responds
        research_call = ToolCall(id="tc1", name="researcher", arguments={
            "task": "Research LRU cache approaches"
        })
        code_call = ToolCall(id="tc2", name="coder", arguments={
            "task": "Implement an LRU cache"
        })
        coordinator = chimera.Agent(
            provider=_mock_provider(
                ("Delegating research", [research_call]),
                ("Delegating coding", [code_call]),
                "Done. LRU cache implemented with OrderedDict.",
            ),
            loop=chimera.ReAct(max_steps=6), name="coordinator",
        )
        researcher = chimera.Agent(
            provider=_mock_provider("Use collections.OrderedDict for O(1) LRU."),
            loop=chimera.ReAct(max_steps=2), name="researcher",
        )
        coder = chimera.Agent(
            provider=_mock_provider("class LRUCache:\n    pass"),
            loop=chimera.ReAct(max_steps=2), name="coder",
        )

    sup = Supervisor(
        coordinator=coordinator,
        workers={"researcher": researcher, "coder": coder},
    )
    result = sup.run("Research and implement an LRU cache.", env=None)
    assert result.success


# ---------------------------------------------------------------
# 11. CI fix workflow (ci_fix.py)
# ---------------------------------------------------------------

def test_ci_fix_workflow():
    from chimera.ci import CIFixWorkflow

    ci_log = (
        "FAILED test_calculator.py::test_add - AssertionError: Expected 5\n"
        "test_calculator.py:4: AssertionError\n"
    )

    workflow = CIFixWorkflow(max_attempts=2)

    # Test diagnosis
    failures = workflow.diagnose(ci_log)
    assert len(failures) >= 1
    assert failures[0].file_path == "test_calculator.py"

    # Test prompt building
    prompt = workflow.build_prompt(failures)
    assert "Fix the following CI failures" in prompt
    assert "test_calculator.py" in prompt

    # Test the run method with mock agent
    if _LIVE:
        provider = _real_provider()
    else:
        edit_call = ToolCall(id="tc1", name="edit_file", arguments={
            "path": "calculator.py",
            "old_string": "return a - b",
            "new_string": "return a + b",
        })
        provider = _mock_provider(
            ("Fixing the bug", [edit_call]),
            "Fixed: changed subtraction to addition.",
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write buggy file
        with open(os.path.join(tmpdir, "calculator.py"), "w") as f:
            f.write("def add(a, b):\n    return a - b\n")
        with open(os.path.join(tmpdir, "test_calculator.py"), "w") as f:
            f.write("from calculator import add\ndef test_add():\n    assert add(2,3)==5\n")

        env = chimera.LocalEnvironment(workdir=tmpdir)
        env.setup()
        agent = chimera.Agent(
            provider=provider,
            tools=list(chimera.AGENT_TOOLS),
            loop=chimera.ReAct(max_steps=8),
        )
        workflow2 = CIFixWorkflow(max_attempts=1)
        workflow2.run(ci_log, agent=agent, env=env)
        assert len(workflow2.attempts) >= 1
        env.cleanup()


# ---------------------------------------------------------------
# 12. Session persistence (session_persistence.py)
# ---------------------------------------------------------------

def test_session_persistence():
    from chimera.sessions.storage.file import FileStorage

    if _LIVE:
        provider = _real_provider()
    else:
        provider = _mock_provider(
            "Nice to meet you, Bob! Rust is great.",
            "Your name is Bob and your favorite language is Rust.",
        )

    agent = chimera.Agent(
        provider=provider,
        loop=chimera.ReAct(max_steps=5),
        prompt=chimera.Prompt.from_string("You are a helpful assistant."),
    )

    with tempfile.TemporaryDirectory() as session_dir:
        storage = FileStorage(session_dir)

        # Turn 1
        session = chimera.Session(agent=agent, env=None, storage=storage)
        sid = session.session_id
        result1 = chimera.drain_steps(session.iter_chat(
            "My name is Bob and I like Rust."
        ))
        assert result1.success
        session.save()

        # Verify save
        assert sid in storage.list_sessions()

        # Resume
        session2 = chimera.Session.resume(sid, agent=agent, storage=storage)
        assert len(session2.messages) == len(session.messages)

        # Turn 2
        if not _LIVE:
            # Reset provider mock for second turn
            agent._provider = _mock_provider(
                "Your name is Bob and your favorite language is Rust.",
            )

        result2 = chimera.drain_steps(session2.iter_chat(
            "What is my name?"
        ))
        assert result2.success
        session2.save()


# ---------------------------------------------------------------
# 13. Streaming agent (streaming_agent.py)
# ---------------------------------------------------------------

def test_streaming_agent():
    from chimera.streaming.handlers import ConsoleStreamHandler

    config = chimera.LoopConfig(handler=ConsoleStreamHandler())

    if _LIVE:
        provider = _real_provider()
    else:
        think_call = ToolCall(id="tc1", name="think", arguments={
            "thought": "Fibonacci is 0,1,1,2,3,5,8..."
        })
        provider = _mock_provider(
            ("Let me think", [think_call]),
            "Three facts about Fibonacci: ratios approach golden ratio, "
            "appears in nature, used in algorithms.",
        )

    agent = chimera.Agent(
        provider=provider,
        tools=[chimera.ThinkTool()],
        loop=chimera.ReAct(max_steps=10, config=config),
    )
    result = agent.run("Tell me about Fibonacci.", env=None)
    assert result.success


# ---------------------------------------------------------------
# Meta: all examples exist and are importable
# ---------------------------------------------------------------

_SUBDIR_EXAMPLES = [
    "provider/quickstart_provider.py",
    "agent/agent_with_tools.py",
    "composition/composition_pipeline.py",
    "synthesis/quickstart_synthesize.py",
    "agent/coding_agent_minimal.py",
    "composition/supervisor_delegation.py",
    "real_world/ci_fix.py",
    "real_world/session_persistence.py",
    "provider/streaming_agent.py",
]


def test_all_example_files_exist():
    examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples")
    for rel in _SUBDIR_EXAMPLES:
        path = os.path.join(examples_dir, rel)
        assert os.path.isfile(path), f"Missing example: {rel}"


def test_all_examples_have_main():
    import importlib.util
    examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples")
    for rel in _SUBDIR_EXAMPLES:
        path = os.path.join(examples_dir, rel)
        mod_name = os.path.splitext(os.path.basename(rel))[0]
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "main"), f"{rel} missing main()"
