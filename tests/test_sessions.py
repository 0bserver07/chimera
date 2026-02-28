from __future__ import annotations

import os

import pytest

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.prompt import Prompt
from chimera.core.tool import BaseTool
from chimera.providers.base import Provider, Response
from chimera.sessions import (
    FileStorage,
    InMemoryStorage,
    SQLiteStorage,
    Session,
    SessionData,
    Storage,
)
from chimera.types import Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeProvider(Provider):
    """Provider that returns a canned sequence of responses."""

    def __init__(self, responses: list[Response] | None = None) -> None:
        self._responses = list(responses or [])
        self._call_count = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None) -> Response:
        if self._call_count >= len(self._responses):
            return Response(content="(exhausted)", tool_calls=[], usage={})
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "fake"


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo a message back"
    parameters = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    def execute(self, args, env):
        return ToolResult(output=f"Echo: {args['message']}")


def _make_agent(responses: list[Response] | None = None) -> Agent:
    """Build a minimal Agent backed by FakeProvider."""
    return Agent(
        provider=FakeProvider(responses),
        prompt=Prompt.from_string("You are a test agent."),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_messages() -> list[Message]:
    return [
        Message.user("Hello"),
        Message.assistant("Hi there!"),
        Message.user("Do something"),
        Message.assistant(
            "Using tool",
            tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "hi"})],
        ),
        Message.tool("tc1", "Echo: hi"),
    ]


def _sample_data(session_id: str = "sess-1") -> SessionData:
    return SessionData(
        session_id=session_id,
        messages=_sample_messages(),
        system="You are a test agent.",
        parent_id=None,
        created_at=1000.0,
        updated_at=2000.0,
        metadata={"key": "value"},
    )


# ===================================================================
# InMemoryStorage tests
# ===================================================================


class TestInMemoryStorage:
    def test_save_and_load(self) -> None:
        store = InMemoryStorage()
        data = _sample_data()
        store.save("sess-1", data)

        loaded = store.load("sess-1")
        assert loaded is not None
        assert loaded.session_id == "sess-1"
        assert len(loaded.messages) == 5
        assert loaded.system == "You are a test agent."
        assert loaded.metadata == {"key": "value"}

    def test_load_missing(self) -> None:
        store = InMemoryStorage()
        assert store.load("nonexistent") is None

    def test_list_sessions(self) -> None:
        store = InMemoryStorage()
        store.save("a", _sample_data("a"))
        store.save("b", _sample_data("b"))
        ids = store.list_sessions()
        assert set(ids) == {"a", "b"}

    def test_delete(self) -> None:
        store = InMemoryStorage()
        store.save("sess-1", _sample_data())
        assert store.load("sess-1") is not None
        store.delete("sess-1")
        assert store.load("sess-1") is None

    def test_delete_missing_is_noop(self) -> None:
        store = InMemoryStorage()
        store.delete("nonexistent")  # Should not raise

    def test_overwrite(self) -> None:
        store = InMemoryStorage()
        data1 = _sample_data()
        data1.metadata = {"v": 1}
        store.save("sess-1", data1)

        data2 = _sample_data()
        data2.metadata = {"v": 2}
        store.save("sess-1", data2)

        loaded = store.load("sess-1")
        assert loaded is not None
        assert loaded.metadata == {"v": 2}

    def test_deep_copy_isolation(self) -> None:
        """Mutations after save/load do not affect stored data."""
        store = InMemoryStorage()
        data = _sample_data()
        store.save("sess-1", data)

        # Mutate the original -- should not affect storage
        data.metadata["extra"] = True

        loaded = store.load("sess-1")
        assert loaded is not None
        assert "extra" not in loaded.metadata


# ===================================================================
# FileStorage tests
# ===================================================================


class TestFileStorage:
    def test_save_and_load(self, tmp_path) -> None:
        store = FileStorage(directory=str(tmp_path))
        data = _sample_data()
        store.save("sess-1", data)

        loaded = store.load("sess-1")
        assert loaded is not None
        assert loaded.session_id == "sess-1"
        assert len(loaded.messages) == 5
        assert loaded.messages[3].tool_calls[0].name == "echo"
        assert loaded.messages[4].call_id == "tc1"
        assert loaded.metadata == {"key": "value"}

    def test_load_missing(self, tmp_path) -> None:
        store = FileStorage(directory=str(tmp_path))
        assert store.load("nonexistent") is None

    def test_list_sessions(self, tmp_path) -> None:
        store = FileStorage(directory=str(tmp_path))
        store.save("alpha", _sample_data("alpha"))
        store.save("beta", _sample_data("beta"))
        ids = store.list_sessions()
        assert set(ids) == {"alpha", "beta"}

    def test_delete(self, tmp_path) -> None:
        store = FileStorage(directory=str(tmp_path))
        store.save("sess-1", _sample_data())
        store.delete("sess-1")
        assert store.load("sess-1") is None
        assert not (tmp_path / "sess-1.json").exists()

    def test_delete_missing_is_noop(self, tmp_path) -> None:
        store = FileStorage(directory=str(tmp_path))
        store.delete("nonexistent")  # Should not raise

    def test_creates_directory(self, tmp_path) -> None:
        nested = tmp_path / "deep" / "nested"
        store = FileStorage(directory=str(nested))
        store.save("sess-1", _sample_data())
        assert (nested / "sess-1.json").exists()

    def test_list_empty_directory(self, tmp_path) -> None:
        store = FileStorage(directory=str(tmp_path))
        assert store.list_sessions() == []

    def test_list_nonexistent_directory(self, tmp_path) -> None:
        store = FileStorage(directory=str(tmp_path / "nope"))
        assert store.list_sessions() == []


# ===================================================================
# SQLiteStorage tests
# ===================================================================


class TestSQLiteStorage:
    def test_save_and_load(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = SQLiteStorage(db_path=db)
        data = _sample_data()
        store.save("sess-1", data)

        loaded = store.load("sess-1")
        assert loaded is not None
        assert loaded.session_id == "sess-1"
        assert len(loaded.messages) == 5
        assert loaded.messages[3].tool_calls[0].name == "echo"
        assert loaded.metadata == {"key": "value"}

    def test_load_missing(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = SQLiteStorage(db_path=db)
        assert store.load("nonexistent") is None

    def test_list_sessions(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = SQLiteStorage(db_path=db)
        store.save("alpha", _sample_data("alpha"))
        store.save("beta", _sample_data("beta"))
        ids = store.list_sessions()
        assert set(ids) == {"alpha", "beta"}

    def test_delete(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = SQLiteStorage(db_path=db)
        store.save("sess-1", _sample_data())
        store.delete("sess-1")
        assert store.load("sess-1") is None

    def test_delete_missing_is_noop(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = SQLiteStorage(db_path=db)
        store.delete("nonexistent")  # Should not raise

    def test_upsert(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = SQLiteStorage(db_path=db)

        data1 = _sample_data()
        data1.metadata = {"v": 1}
        store.save("sess-1", data1)

        data2 = _sample_data()
        data2.metadata = {"v": 2}
        store.save("sess-1", data2)

        loaded = store.load("sess-1")
        assert loaded is not None
        assert loaded.metadata == {"v": 2}

    def test_preserves_system_and_parent(self, tmp_path) -> None:
        db = str(tmp_path / "test.db")
        store = SQLiteStorage(db_path=db)
        data = _sample_data()
        data.system = "custom system"
        data.parent_id = "parent-abc"
        store.save("sess-1", data)

        loaded = store.load("sess-1")
        assert loaded is not None
        assert loaded.system == "custom system"
        assert loaded.parent_id == "parent-abc"


# ===================================================================
# Session tests
# ===================================================================


class TestSession:
    def test_chat_runs_loop(self) -> None:
        """Session.chat() invokes the agent loop and returns a result."""
        agent = _make_agent([
            Response(content="Hello back!", tool_calls=[], usage={}),
        ])
        session = Session(agent=agent)

        result = session.chat("Hello")
        assert result.success is True
        assert result.output == "Hello back!"

    def test_chat_multi_turn(self) -> None:
        """Messages accumulate across multiple chat() calls."""
        agent = _make_agent([
            Response(content="First reply.", tool_calls=[], usage={}),
            Response(content="Second reply.", tool_calls=[], usage={}),
        ])
        session = Session(agent=agent)

        session.chat("Turn 1")
        assert len(session.messages) == 2  # user + assistant

        session.chat("Turn 2")
        assert len(session.messages) == 4  # 2 users + 2 assistants

        roles = [m.role for m in session.messages]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_default_storage_is_in_memory(self) -> None:
        """When no storage is provided, InMemoryStorage is used."""
        agent = _make_agent()
        session = Session(agent=agent)
        assert isinstance(session._storage, InMemoryStorage)

    def test_session_id_generated(self) -> None:
        """A UUID-style session_id is auto-generated."""
        agent = _make_agent()
        session = Session(agent=agent)
        assert len(session.session_id) == 36  # UUID format
        assert session.session_id.count("-") == 4

    def test_explicit_session_id(self) -> None:
        agent = _make_agent()
        session = Session(agent=agent, session_id="my-custom-id")
        assert session.session_id == "my-custom-id"

    def test_save_and_resume(self) -> None:
        """Session can be saved and resumed with full context."""
        agent = _make_agent([
            Response(content="Hello!", tool_calls=[], usage={}),
            Response(content="Resumed reply.", tool_calls=[], usage={}),
        ])
        storage = InMemoryStorage()
        session = Session(agent=agent, storage=storage, session_id="s1")

        session.chat("Hi")
        session.save()

        # Resume into a new session object
        agent2 = _make_agent([
            Response(content="Continued.", tool_calls=[], usage={}),
        ])
        resumed = Session.resume("s1", agent=agent2, storage=storage)

        assert resumed.session_id == "s1"
        assert len(resumed.messages) == 2  # user + assistant from first chat
        assert resumed.context.system is not None

    def test_resume_not_found(self) -> None:
        agent = _make_agent()
        storage = InMemoryStorage()
        with pytest.raises(ValueError, match="not found"):
            Session.resume("nonexistent", agent=agent, storage=storage)

    def test_fork_creates_independent_branch(self) -> None:
        """Forked session is independent -- mutations do not leak."""
        agent = _make_agent([
            Response(content="Reply A", tool_calls=[], usage={}),
            Response(content="Reply B", tool_calls=[], usage={}),
        ])
        session = Session(agent=agent, session_id="original")

        session.chat("First message")
        forked = session.fork()

        # Forked session has a different ID
        assert forked.session_id != session.session_id

        # Forked session starts with the same messages
        assert len(forked.messages) == len(session.messages)

        # Adding to forked does not affect original
        forked.context.add(Message.user("Forked message"))
        assert len(forked.messages) == len(session.messages) + 1

    def test_fork_records_parent_id(self) -> None:
        agent = _make_agent([
            Response(content="ok", tool_calls=[], usage={}),
        ])
        session = Session(agent=agent, session_id="parent-id")
        session.chat("hello")

        forked = session.fork()
        assert forked._parent_id == "parent-id"

    def test_chat_with_tools(self) -> None:
        """Session handles tool-using agents correctly."""
        agent = Agent(
            provider=FakeProvider([
                Response(
                    content="Let me echo that.",
                    tool_calls=[
                        ToolCall(id="tc1", name="echo", arguments={"message": "world"}),
                    ],
                    usage={},
                ),
                Response(content="Done echoing.", tool_calls=[], usage={}),
            ]),
            tools=[EchoTool()],
            prompt=Prompt.from_string("You are a test agent."),
        )
        session = Session(agent=agent)

        result = session.chat("Echo world")
        assert result.success is True
        assert result.output == "Done echoing."
        # user + assistant(tool_call) + tool_result + assistant(final)
        assert len(session.messages) == 4

    def test_context_property(self) -> None:
        agent = _make_agent()
        session = Session(agent=agent)
        assert session.context.system is not None
        assert "test agent" in session.context.system

    def test_save_with_file_storage(self, tmp_path) -> None:
        """End-to-end save/resume with FileStorage."""
        agent = _make_agent([
            Response(content="Saved!", tool_calls=[], usage={}),
        ])
        storage = FileStorage(directory=str(tmp_path))
        session = Session(agent=agent, storage=storage, session_id="file-s1")

        session.chat("Persist me")
        session.save()

        # Resume
        agent2 = _make_agent([
            Response(content="Continued.", tool_calls=[], usage={}),
        ])
        resumed = Session.resume("file-s1", agent=agent2, storage=storage)
        assert resumed.session_id == "file-s1"
        assert len(resumed.messages) == 2

    def test_save_with_sqlite_storage(self, tmp_path) -> None:
        """End-to-end save/resume with SQLiteStorage."""
        db = str(tmp_path / "session.db")
        agent = _make_agent([
            Response(content="Saved!", tool_calls=[], usage={}),
        ])
        storage = SQLiteStorage(db_path=db)
        session = Session(agent=agent, storage=storage, session_id="sqlite-s1")

        session.chat("Persist me")
        session.save()

        # Resume
        agent2 = _make_agent([
            Response(content="Continued.", tool_calls=[], usage={}),
        ])
        resumed = Session.resume("sqlite-s1", agent=agent2, storage=storage)
        assert resumed.session_id == "sqlite-s1"
        assert len(resumed.messages) == 2
