# chimera/tools/browser.py
"""Browser automation tool using Playwright.

Provides a :class:`BrowserTool` that wraps Playwright for headless (or headed)
browser interaction.  Playwright is an optional dependency -- if it is not
installed the tool returns a helpful error message on first use.
"""
from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from chimera.core.tool import BaseTool
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.env.base import Environment

try:
    from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright
except ImportError:
    sync_playwright = None  # type: ignore[assignment,misc]
    Browser = None  # type: ignore[assignment,misc]
    BrowserContext = None  # type: ignore[assignment,misc]
    Page = None  # type: ignore[assignment,misc]
    Playwright = None  # type: ignore[assignment,misc]

_CONTENT_LIMIT = 10_000


class BrowserTool(BaseTool):
    """Headless browser automation via Playwright.

    Actions:
        navigate, click, type, screenshot, content, html, evaluate, wait,
        select, scroll, back, forward, tabs, new_tab, close_tab, switch_tab

    Args:
        headless: Run the browser in headless mode (default ``True``).
        timeout: Default timeout in milliseconds (default ``30000``).
        viewport: ``(width, height)`` tuple for the browser viewport.
        allowed_domains: Optional list of allowed domains.  When set,
            ``navigate`` and ``new_tab`` will refuse to visit URLs whose
            hostname is not in the list.
    """

    name = "browser"
    description = (
        "Control a browser. Actions: navigate, click, type, screenshot, content, "
        "html, evaluate, wait, select, scroll, back, forward, tabs, new_tab, "
        "close_tab, switch_tab."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": (
                    "The browser action to perform. One of: navigate, click, type, "
                    "screenshot, content, html, evaluate, wait, select, scroll, back, "
                    "forward, tabs, new_tab, close_tab, switch_tab."
                ),
            },
            "url": {"type": "string", "description": "URL for navigate / new_tab."},
            "selector": {"type": "string", "description": "CSS selector for click/type/wait/select."},
            "text": {"type": "string", "description": "Text to type."},
            "js": {"type": "string", "description": "JavaScript expression for evaluate."},
            "path": {"type": "string", "description": "File path for screenshot."},
            "full_page": {"type": "boolean", "description": "Full-page screenshot."},
            "direction": {"type": "string", "description": "Scroll direction: up or down."},
            "amount": {"type": "number", "description": "Scroll amount in pixels."},
            "timeout": {"type": "number", "description": "Action-specific timeout (ms)."},
            "value": {"type": "string", "description": "Value for select."},
            "index": {"type": "integer", "description": "Tab index for switch_tab."},
        },
        "required": ["action"],
    }

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
        viewport: tuple[int, int] = (1280, 720),
        allowed_domains: list[str] | None = None,
    ) -> None:
        self.headless = headless
        self.timeout = timeout
        self.viewport = viewport
        self.allowed_domains = allowed_domains

        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Lazily start Playwright and open a browser."""
        if self._browser is not None:
            return
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            viewport={"width": self.viewport[0], "height": self.viewport[1]},
        )
        self._context.set_default_timeout(self.timeout)
        self._page = self._context.new_page()

    def cleanup(self) -> None:
        """Close browser and stop Playwright."""
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._context = None
        self._page = None

    # ------------------------------------------------------------------
    # Domain restriction
    # ------------------------------------------------------------------

    def _check_domain(self, url: str) -> str | None:
        """Return an error string if *url* is outside allowed domains, else ``None``."""
        if self.allowed_domains is None:
            return None
        hostname = urlparse(url).hostname or ""
        if hostname not in self.allowed_domains:
            return f"Domain '{hostname}' is not in the allowed list: {self.allowed_domains}"
        return None

    # ------------------------------------------------------------------
    # Execute dispatch
    # ------------------------------------------------------------------

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Dispatch to the appropriate ``_action_*`` method."""
        if sync_playwright is None:
            return ToolResult(
                output="",
                error="playwright not installed. pip install playwright && playwright install",
            )
        try:
            self.setup()
            action = args.get("action", "")
            handler = getattr(self, f"_action_{action}", None)
            if handler is None:
                return ToolResult(output="", error=f"Unknown browser action: {action}")
            return handler(args)
        except Exception as exc:
            return ToolResult(output="", error=str(exc))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _action_navigate(self, args: dict[str, Any]) -> ToolResult:
        url = args["url"]
        err = self._check_domain(url)
        if err:
            return ToolResult(output="", error=err)
        timeout = args.get("timeout", self.timeout)
        self._page.goto(url, timeout=timeout)
        return ToolResult(output=f"Navigated to {url}")

    def _action_click(self, args: dict[str, Any]) -> ToolResult:
        selector = args["selector"]
        timeout = args.get("timeout", self.timeout)
        self._page.click(selector, timeout=timeout)
        return ToolResult(output=f"Clicked {selector}")

    def _action_type(self, args: dict[str, Any]) -> ToolResult:
        selector = args["selector"]
        text = args["text"]
        timeout = args.get("timeout", self.timeout)
        self._page.fill(selector, text, timeout=timeout)
        return ToolResult(output=f"Typed into {selector}")

    def _action_screenshot(self, args: dict[str, Any]) -> ToolResult:
        path = args.get("path")
        full_page = args.get("full_page", False)
        raw = self._page.screenshot(path=path, full_page=full_page)
        b64 = base64.b64encode(raw).decode()
        return ToolResult(
            output="Screenshot captured",
            metadata={"screenshot": b64},
        )

    def _action_content(self, args: dict[str, Any]) -> ToolResult:
        text = self._page.inner_text("body")
        if len(text) > _CONTENT_LIMIT:
            text = text[:_CONTENT_LIMIT] + "... (truncated)"
        return ToolResult(output=text)

    def _action_html(self, args: dict[str, Any]) -> ToolResult:
        html = self._page.content()
        if len(html) > _CONTENT_LIMIT:
            html = html[:_CONTENT_LIMIT] + "... (truncated)"
        return ToolResult(output=html)

    def _action_evaluate(self, args: dict[str, Any]) -> ToolResult:
        js = args["js"]
        result = self._page.evaluate(js)
        return ToolResult(output=str(result))

    def _action_wait(self, args: dict[str, Any]) -> ToolResult:
        selector = args["selector"]
        timeout = args.get("timeout", self.timeout)
        self._page.wait_for_selector(selector, timeout=timeout)
        return ToolResult(output=f"Element {selector} found")

    def _action_select(self, args: dict[str, Any]) -> ToolResult:
        selector = args["selector"]
        value = args["value"]
        timeout = args.get("timeout", self.timeout)
        self._page.select_option(selector, value, timeout=timeout)
        return ToolResult(output=f"Selected {value} in {selector}")

    def _action_scroll(self, args: dict[str, Any]) -> ToolResult:
        direction = args.get("direction", "down")
        amount = args.get("amount", 500)
        delta = -amount if direction == "up" else amount
        self._page.mouse.wheel(0, delta)
        return ToolResult(output=f"Scrolled {direction} by {amount}px")

    def _action_back(self, args: dict[str, Any]) -> ToolResult:
        self._page.go_back()
        return ToolResult(output="Navigated back")

    def _action_forward(self, args: dict[str, Any]) -> ToolResult:
        self._page.go_forward()
        return ToolResult(output="Navigated forward")

    def _action_tabs(self, args: dict[str, Any]) -> ToolResult:
        pages = self._context.pages
        info = [f"[{i}] {p.url}" for i, p in enumerate(pages)]
        return ToolResult(output="\n".join(info))

    def _action_new_tab(self, args: dict[str, Any]) -> ToolResult:
        url = args.get("url", "about:blank")
        err = self._check_domain(url)
        if err:
            return ToolResult(output="", error=err)
        self._page = self._context.new_page()
        if url != "about:blank":
            self._page.goto(url)
        return ToolResult(output=f"Opened new tab: {url}")

    def _action_close_tab(self, args: dict[str, Any]) -> ToolResult:
        self._page.close()
        pages = self._context.pages
        if pages:
            self._page = pages[-1]
        return ToolResult(output="Closed current tab")

    def _action_switch_tab(self, args: dict[str, Any]) -> ToolResult:
        index = args["index"]
        pages = self._context.pages
        if index < 0 or index >= len(pages):
            return ToolResult(output="", error=f"Tab index {index} out of range (0-{len(pages) - 1})")
        self._page = pages[index]
        return ToolResult(output=f"Switched to tab {index}: {self._page.url}")
