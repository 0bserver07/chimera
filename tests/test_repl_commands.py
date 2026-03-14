# tests/test_repl_commands.py
"""Tests for /init and /yolo REPL commands."""
import pytest
from unittest.mock import MagicMock, patch

from chimera.cli.code import cmd_init, cmd_yolo, _COMMANDS


def test_init_registered():
    assert "init" in _COMMANDS

def test_yolo_registered():
    assert "yolo" in _COMMANDS

def test_yolo_toggle_on():
    session = MagicMock()
    session._yolo_mode = False  # not set initially
    del session._yolo_mode  # simulate AttributeError on getattr
    env = MagicMock()
    messages = []
    cmd_yolo(session, env, "", messages.append)
    assert any("ON" in m for m in messages)

def test_yolo_toggle_off():
    session = MagicMock()
    session._yolo_mode = True
    env = MagicMock()
    messages = []
    cmd_yolo(session, env, "", messages.append)
    assert any("OFF" in m for m in messages)

def test_init_calls_iter_chat():
    session = MagicMock()
    # Mock iter_chat to return an iterator that drain_steps can consume
    mock_result = MagicMock()
    mock_result.cost = 0.001
    mock_result.steps = 1

    env = MagicMock()
    env.workdir = "/tmp/test"
    messages = []

    with patch("chimera.cli.code.drain_steps") as mock_drain:
        mock_drain.return_value = mock_result
        cmd_init(session, env, "", messages.append)

    assert any("Analyzing" in m for m in messages)
    session.iter_chat.assert_called_once()
