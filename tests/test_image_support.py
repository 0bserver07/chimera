"""Tests for image/vision support."""

from __future__ import annotations

import base64
import os
import tempfile


from chimera.types import ImageContent, Message, TextContent
from chimera.tools.image_read import ImageReadTool


class TestContentBlocks:
    def test_text_content(self):
        block = TextContent(text="hello")
        assert block.type == "text"
        assert block.text == "hello"

    def test_image_content(self):
        block = ImageContent(data="abc123", media_type="image/png")
        assert block.type == "image"
        assert block.data == "abc123"

    def test_message_with_image(self):
        msg = Message.user_with_image("describe this", "base64data", "image/png")
        assert msg.role == "user"
        assert msg.content == "describe this"
        assert msg.has_images
        assert len(msg.content_blocks) == 2

    def test_message_without_image(self):
        msg = Message.user("hello")
        assert not msg.has_images

    def test_backward_compatible(self):
        msg = Message(role="user", content="hello")
        assert msg.content_blocks == []
        assert not msg.has_images


class TestImageReadTool:
    def test_read_png(self):
        tool = ImageReadTool()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            path = f.name
        try:
            result = tool.execute({"path": path}, env=None)
            assert result.success
            assert "image/png" in result.metadata.get("media_type", "")
        finally:
            os.unlink(path)

    def test_read_jpeg(self):
        tool = ImageReadTool()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
            path = f.name
        try:
            result = tool.execute({"path": path}, env=None)
            assert result.success
            assert result.metadata["media_type"] == "image/jpeg"
        finally:
            os.unlink(path)

    def test_unsupported_type(self):
        tool = ImageReadTool()
        result = tool.execute({"path": "test.bmp"}, env=None)
        assert result.error
        assert "Unsupported" in result.error

    def test_missing_file(self):
        tool = ImageReadTool()
        result = tool.execute({"path": "/nonexistent/img.png"}, env=None)
        assert result.error

    def test_missing_path(self):
        tool = ImageReadTool()
        result = tool.execute({}, env=None)
        assert result.error

    def test_metadata_has_base64(self):
        tool = ImageReadTool()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            content = b"\x89PNG" + b"\x00" * 50
            f.write(content)
            path = f.name
        try:
            result = tool.execute({"path": path}, env=None)
            b64 = result.metadata.get("image_data", "")
            decoded = base64.b64decode(b64)
            assert decoded == content
        finally:
            os.unlink(path)
