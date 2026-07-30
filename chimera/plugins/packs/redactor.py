"""Redactor policy pack: scrub a secret pattern from the wire and transcript.

A worked policy on the two data-shaping seams:

- ``provider_request`` (fail-open): rewrites the request envelope before
  it leaves the process — message contents, text content blocks, string
  values inside assistant tool-call arguments, and request headers. The
  rewrite is per call and ephemeral; the durable conversation keeps its
  originals.
- ``tool_result`` (fail-open): scrubs each executed tool's output (and
  error text) before it enters the conversation — this one is durable,
  since the scrubbed result is what the transcript records.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from chimera.core.interception import InterceptDecision, Interceptors, ProviderRequest
from chimera.plugins.base import BasePlugin
from chimera.plugins.registry import PluginExtensionRegistry
from chimera.types import ContentBlock, Message, TextContent, ToolCall, ToolResult

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from chimera.plugins.base import ComponentRegistry

__all__ = ["DEFAULT_SECRET_PATTERN", "RedactorPlugin"]

#: Default secret shape: ``sk- / key- / token- / secret-`` prefixed values.
#: A starting point, not a vault — configure *pattern* for your real shapes.
DEFAULT_SECRET_PATTERN: str = r"\b(?:sk|key|token|secret)-[A-Za-z0-9_\-]{8,}\b"


class RedactorPlugin(BasePlugin):
    """Scrub a configurable secret pattern from payloads, headers, results.

    Every match of *pattern* is replaced with *replacement* in outgoing
    message contents, in ``TextContent`` blocks (multimodal messages
    duplicate their text into one), in string values inside tool-call
    arguments riding those messages, in request-header values, and in
    tool outputs; headers named in *headers* are replaced wholesale
    (their value is the secret). Header-name comparison is
    case-insensitive.

    Honest limits:

    - Header redaction applies only when the provider transport exposes a
      header surface (a ``request_headers`` property); otherwise the
      envelope carries ``headers=None`` and header redaction is a no-op —
      the payload scrub still applies.
    - Text only. Exactly what passes through unscrubbed:
      ``ImageContent`` blocks (base64 bytes and media type), content
      blocks of types other than ``TextContent``, and
      ``ToolResult.metadata``.
    - Both seams are fail-open by the seam contract: a crash inside the
      redactor degrades to no redaction for that value, reported
      observationally. Pair this pack with the events-side
      :class:`~chimera.secrets.redactor.RedactionMiddleware` (which
      covers logs and telemetry) for defense in depth.
    - The ``provider_request`` scrub is ephemeral: the durable
      conversation still holds the original text; the ``tool_result``
      scrub is durable.

    Args:
        pattern: Regular expression for secret values. Defaults to
            :data:`DEFAULT_SECRET_PATTERN`.
        replacement: Replacement text for matches and named headers.
        headers: Header names (case-insensitive) replaced wholesale.

    Raises:
        re.error: If *pattern* is not a valid regular expression — an
            unusable redactor must fail at construction, not silently
            redact nothing.

    Example:
        ```python
        from chimera.plugins import PluginManager
        from chimera.plugins.packs import RedactorPlugin

        PluginManager().load_plugin(
            RedactorPlugin(pattern=r"acme-[0-9a-f]{32}")
        )
        ```
    """

    version = "1.0.0"
    description = "Scrub a secret pattern from provider payloads, headers, and tool results."
    author = "Chimera Contributors"

    def __init__(
        self,
        *,
        pattern: str = DEFAULT_SECRET_PATTERN,
        replacement: str = "[redacted]",
        headers: Iterable[str] = ("Authorization",),
    ) -> None:
        self._regex = re.compile(pattern)
        self._replacement = replacement
        self._header_names = frozenset(h.lower() for h in headers)
        self._registered: list[tuple[str, Callable[..., InterceptDecision | None]]] = []

    @property
    def name(self) -> str:
        """Unique plugin name."""
        return "redactor"

    # -- interceptors ----------------------------------------------------

    def interceptors(self) -> Interceptors:
        """This pack's chains as one bundle, for host-side use.

        Returns:
            An :class:`~chimera.core.interception.Interceptors` carrying
            the envelope scrub (``provider_request``) and the result
            scrub (``tool_result``).
        """
        return Interceptors(
            provider_request=[self._scrub_request],
            tool_result=[self._scrub_result],
        )

    def register_interceptors(self, registry: ComponentRegistry) -> None:
        """Register the envelope and result scrubs on their seams."""
        self._registered = [
            ("provider_request", self._scrub_request),
            ("tool_result", self._scrub_result),
        ]
        for seam, fn in self._registered:
            PluginExtensionRegistry.register_interceptor(seam, fn)

    def deactivate(self) -> None:
        """Withdraw the pack's chains."""
        for seam, fn in self._registered:
            PluginExtensionRegistry.unregister_interceptor(seam, fn)
        self._registered = []

    # -- scrub helpers ---------------------------------------------------

    def _scrub_text(self, text: str) -> tuple[str, bool]:
        """Return (scrubbed text, whether anything changed)."""
        scrubbed = self._regex.sub(self._replacement, text)
        return scrubbed, scrubbed != text

    def _scrub_value(self, value: Any) -> tuple[Any, bool]:
        """Scrub string values recursively through dicts and lists."""
        if isinstance(value, str):
            return self._scrub_text(value)
        if isinstance(value, dict):
            changed = False
            out: dict[Any, Any] = {}
            for key, item in value.items():
                new_item, item_changed = self._scrub_value(item)
                out[key] = new_item
                changed = changed or item_changed
            return (out, True) if changed else (value, False)
        if isinstance(value, list):
            changed = False
            items: list[Any] = []
            for item in value:
                new_item, item_changed = self._scrub_value(item)
                items.append(new_item)
                changed = changed or item_changed
            return (items, True) if changed else (value, False)
        return value, False

    def _scrub_blocks(
        self, blocks: list[ContentBlock],
    ) -> tuple[list[ContentBlock], bool]:
        """Scrub the text inside ``TextContent`` blocks; leave the rest alone.

        Image blocks (and any other non-text block type) pass through as
        the same objects — the class docstring's "text only" limit.
        """
        changed = False
        out: list[ContentBlock] = []
        for block in blocks:
            if isinstance(block, TextContent):
                text, text_changed = self._scrub_text(block.text)
                if text_changed:
                    out.append(TextContent(type=block.type, text=text))
                    changed = True
                    continue
            out.append(block)
        return (out, True) if changed else (blocks, False)

    def _scrub_message(self, message: Message) -> tuple[Message, bool]:
        """Scrub one message's content, content blocks, and tool-call arguments."""
        content, content_changed = self._scrub_text(message.content or "")
        calls_changed = False
        tool_calls = message.tool_calls
        if message.tool_calls:
            new_calls: list[ToolCall] = []
            for tc in message.tool_calls:
                args, args_changed = self._scrub_value(tc.arguments)
                calls_changed = calls_changed or args_changed
                new_calls.append(
                    ToolCall(id=tc.id, name=tc.name, arguments=args)
                    if args_changed else tc
                )
            if calls_changed:
                tool_calls = new_calls
        blocks, blocks_changed = self._scrub_blocks(message.content_blocks)
        if not (content_changed or calls_changed or blocks_changed):
            return message, False
        return Message(
            role=message.role,
            content=content,
            tool_calls=tool_calls,
            call_id=message.call_id,
            content_blocks=blocks,
        ), True

    # -- seam callables --------------------------------------------------

    def _scrub_request(self, request: ProviderRequest) -> InterceptDecision | None:
        """``provider_request`` seam: scrub payload and headers per call."""
        changed = False
        messages: list[Message] = []
        for message in request.messages:
            scrubbed, message_changed = self._scrub_message(message)
            messages.append(scrubbed)
            changed = changed or message_changed

        headers = request.headers
        if request.headers is not None:
            new_headers: dict[str, str] = {}
            headers_changed = False
            for key, value in request.headers.items():
                if key.lower() in self._header_names:
                    new_headers[key] = self._replacement
                    headers_changed = headers_changed or value != self._replacement
                else:
                    new_value, value_changed = self._scrub_text(value)
                    new_headers[key] = new_value
                    headers_changed = headers_changed or value_changed
            if headers_changed:
                headers = new_headers
                changed = True

        if not changed:
            return None
        return InterceptDecision.replace(ProviderRequest(
            model=request.model,
            messages=messages,
            tools=request.tools,
            kwargs=request.kwargs,
            headers=headers,
        ))

    def _scrub_result(
        self, call: ToolCall, result: ToolResult,
    ) -> InterceptDecision | None:
        """``tool_result`` seam: scrub output and error before the transcript."""
        del call  # policy applies to every tool uniformly
        output, output_changed = self._scrub_text(result.output or "")
        error = result.error
        error_changed = False
        if result.error:
            error, error_changed = self._scrub_text(result.error)
        if not (output_changed or error_changed):
            return None
        return InterceptDecision.replace(ToolResult(
            output=output,
            error=error,
            metadata=result.metadata,
        ))
