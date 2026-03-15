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

import pytest

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
    from chimera.wire.types import StepBegin, StepEnd

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
# Meta: all examples exist and are importable
# ---------------------------------------------------------------

def test_all_example_files_exist():
    examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples")
    expected = [
        "quickstart_provider.py",
        "agent_with_tools.py",
        "composition_pipeline.py",
        "think_and_ask.py",
        "wire_monitoring.py",
        "dmail_context_rewind.py",
        "flow_skills.py",
        "quickstart_synthesize.py",
        "run_all.py",
    ]
    for name in expected:
        path = os.path.join(examples_dir, name)
        assert os.path.isfile(path), f"Missing example: {name}"


def test_all_examples_have_main():
    import importlib.util
    examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples")
    scripts = [
        "quickstart_provider.py",
        "agent_with_tools.py",
        "composition_pipeline.py",
        "think_and_ask.py",
        "wire_monitoring.py",
        "dmail_context_rewind.py",
        "flow_skills.py",
        "quickstart_synthesize.py",
    ]
    for name in scripts:
        path = os.path.join(examples_dir, name)
        spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "main"), f"{name} missing main()"
