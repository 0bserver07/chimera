# tests/test_streaming.py
from chimera.core.streaming import StreamHandler, PrintStreamHandler, CollectStreamHandler
from chimera.providers.base import StreamEvent


class TestStreamHandler:
    def test_collect_handler(self):
        handler = CollectStreamHandler()
        handler.on_text("Hello ")
        handler.on_text("world")
        handler.on_tool_start("read_file", "call_1")
        handler.on_tool_end("call_1", "file contents")
        assert handler.text == "Hello world"
        assert len(handler.events) == 4

    def test_print_handler_no_crash(self, capsys):
        handler = PrintStreamHandler()
        handler.on_text("Hello")
        handler.on_tool_start("bash", "call_1")
        handler.on_tool_end("call_1", "output")
        handler.on_done()
        captured = capsys.readouterr()
        assert "Hello" in captured.out

    def test_handler_from_stream_events(self):
        handler = CollectStreamHandler()
        events = [
            StreamEvent(type="text_delta", content="Hi"),
            StreamEvent(type="text_delta", content=" there"),
            StreamEvent(type="done"),
        ]
        for event in events:
            handler.handle_event(event)
        assert handler.text == "Hi there"

    def test_custom_handler(self):
        """Verify the base class can be subclassed."""
        class MyHandler(StreamHandler):
            def __init__(self):
                self.chunks = []
            def on_text(self, text): self.chunks.append(text)
            def on_tool_start(self, name, call_id): pass
            def on_tool_end(self, call_id, output): pass
            def on_done(self): pass

        h = MyHandler()
        h.on_text("hello")
        assert h.chunks == ["hello"]
