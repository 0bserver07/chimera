"""Tests for HTML session export."""
from __future__ import annotations

import os


from chimera.core.html_export import export_session_html
from chimera.types import Message


def test_export_creates_file(tmp_path):
    messages = [Message.user("hello")]
    output = tmp_path / "session.html"
    result = export_session_html(messages, output)
    assert os.path.exists(result)
    assert result == str(output)


def test_export_contains_messages(tmp_path):
    messages = [
        Message.user("What is 2+2?"),
        Message.assistant("The answer is 4."),
    ]
    output = tmp_path / "session.html"
    export_session_html(messages, output)
    html = output.read_text()
    assert "What is 2+2?" in html
    assert "The answer is 4." in html
    assert "user" in html
    assert "assistant" in html


def test_export_escapes_html(tmp_path):
    messages = [Message.user("<script>alert('xss')</script>")]
    output = tmp_path / "session.html"
    export_session_html(messages, output)
    html = output.read_text()
    # The script tag should be escaped, not raw
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
