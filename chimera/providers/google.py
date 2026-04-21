# chimera/providers/google.py
from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message, ToolCall

if TYPE_CHECKING:
    from chimera.auth.manager import AuthManager

try:
    import google.generativeai as genai  # type: ignore[import-not-found]
except ImportError:
    genai = None  # type: ignore[assignment]


class GoogleProvider(Provider):
    """Google Gemini provider."""

    CONTEXT_WINDOWS = {
        "gemini-2.0": 1_048_576,
        "gemini-1.5": 1_048_576,
        "gemini-1.0": 32_768,
    }

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        auth_manager: AuthManager | None = None,
    ) -> None:
        if genai is None:
            raise ImportError("pip install chimera-run[google]")

        resolved_key = api_key
        if resolved_key is None and auth_manager is not None:
            try:
                resolved_key = auth_manager.get_token("google")
            except Exception:
                pass
        if resolved_key is None:
            resolved_key = os.environ.get("GOOGLE_API_KEY")

        genai.configure(api_key=resolved_key)
        self._model_name = model
        self._model = genai.GenerativeModel(model)

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
    ) -> Response:
        contents = self._convert_messages(messages)
        kwargs: dict[str, Any] = {}

        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        generation_config: dict[str, Any] = {"temperature": temperature}
        if max_tokens:
            generation_config["max_output_tokens"] = max_tokens
        kwargs["generation_config"] = generation_config

        response = self._model.generate_content(contents, **kwargs)

        # Parse response
        text_parts = []
        tool_calls = []
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.text:
                    text_parts.append(part.text)
                elif part.function_call:
                    fc = part.function_call
                    tool_calls.append(ToolCall(
                        id=f"call_{uuid.uuid4().hex[:12]}",
                        name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                    ))

        return Response(
            content="".join(text_parts),
            tool_calls=tool_calls,
            usage={
                "input_tokens": getattr(response.usage_metadata, "prompt_token_count", 0),
                "output_tokens": getattr(response.usage_metadata, "candidates_token_count", 0),
            },
        )

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert Chimera messages to Gemini contents format."""
        contents: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                # Gemini handles system prompt separately; prepend as user context
                contents.append({"role": "user", "parts": [{"text": f"[System] {msg.content}"}]})
            elif msg.role == "user":
                contents.append({"role": "user", "parts": [{"text": msg.content}]})
            elif msg.role == "assistant":
                parts: list[dict[str, Any]] = []
                if msg.content:
                    parts.append({"text": msg.content})
                for tc in msg.tool_calls:
                    parts.append({"functionCall": {"name": tc.name, "args": tc.arguments}})
                if parts:
                    contents.append({"role": "model", "parts": parts})
            elif msg.role == "tool":
                contents.append({
                    "role": "user",
                    "parts": [{"functionResponse": {"name": "tool", "response": {"result": msg.content}}}],
                })
        return contents

    def _convert_tools(self, tools: list[ToolSchema]) -> list[dict[str, Any]]:
        """Convert tool schemas to Gemini function declarations."""
        declarations = []
        for tool in tools:
            schema = tool.get("input_schema", tool.get("parameters", {})) or {}
            declarations.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": self._clean_schema(schema),
            })
        return [{"function_declarations": declarations}]

    @staticmethod
    def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
        """Strip JSON Schema keys unsupported by Gemini."""
        unsupported = {"$schema", "$id", "$ref", "$comment", "$defs",
                       "additionalProperties", "patternProperties",
                       "anyOf", "oneOf", "allOf", "minLength", "maxLength", "pattern"}
        cleaned = {}
        for k, v in schema.items():
            if k in unsupported:
                continue
            if isinstance(v, dict):
                cleaned[k] = GoogleProvider._clean_schema(v)
            else:
                cleaned[k] = v
        return cleaned

    @property
    def context_window(self) -> int:
        for prefix, size in self.CONTEXT_WINDOWS.items():
            if self._model_name.startswith(prefix):
                return size
        return 1_048_576

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model_name


from chimera.providers.registry import register_provider as _register  # noqa: E402
_register("google", lambda model="", api_key=None, base_url=None, **kw: GoogleProvider(model=model, api_key=api_key, **kw))
