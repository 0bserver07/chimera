"""Tests for chimera.tools.browser.BrowserTool.

All Playwright interactions are mocked so the tests run without a real browser.
"""
from __future__ import annotations

import base64
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers to build a convincing mock Playwright stack
# ---------------------------------------------------------------------------

def _make_mock_page(url: str = "about:blank") -> MagicMock:
    page = MagicMock()
    page.url = url
    page.inner_text.return_value = "Hello world"
    page.content.return_value = "<html><body>Hello world</body></html>"
    page.evaluate.return_value = 42
    page.screenshot.return_value = b"\x89PNG fake screenshot bytes"
    page.mouse = MagicMock()
    return page


def _make_mock_context(page: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.new_page.return_value = page
    ctx.pages = [page]
    return ctx


def _make_mock_browser(context: MagicMock) -> MagicMock:
    browser = MagicMock()
    browser.new_context.return_value = context
    return browser


def _make_mock_playwright(browser: MagicMock) -> MagicMock:
    pw = MagicMock()
    pw.chromium.launch.return_value = browser
    return pw


def _make_sync_playwright(pw_mock: MagicMock) -> MagicMock:
    """Return a callable that, when called, returns a context manager / .start()."""
    starter = MagicMock()
    starter.start.return_value = pw_mock
    factory = MagicMock(return_value=starter)
    return factory


@pytest.fixture()
def tool_with_mocks():
    """Yield a (BrowserTool, mocks_dict) with Playwright fully mocked."""
    from chimera.tools.browser import BrowserTool

    page = _make_mock_page("https://example.com")
    context = _make_mock_context(page)
    browser = _make_mock_browser(context)
    pw = _make_mock_playwright(browser)
    sync_pw = _make_sync_playwright(pw)

    bt = BrowserTool()
    # Patch the module-level sync_playwright so setup() uses our mocks
    with patch("chimera.tools.browser.sync_playwright", sync_pw):
        bt.setup()
        yield bt, {
            "page": page,
            "context": context,
            "browser": browser,
            "playwright": pw,
            "sync_playwright": sync_pw,
        }
    bt.cleanup()


# ---------------------------------------------------------------------------
# Constructor defaults
# ---------------------------------------------------------------------------

def test_constructor_defaults():
    from chimera.tools.browser import BrowserTool
    bt = BrowserTool()
    assert bt.headless is True
    assert bt.timeout == 30000
    assert bt.viewport == (1280, 720)
    assert bt.allowed_domains is None
    assert bt._browser is None
    assert bt._playwright is None


def test_constructor_custom():
    from chimera.tools.browser import BrowserTool
    bt = BrowserTool(headless=False, timeout=5000, viewport=(800, 600), allowed_domains=["a.com"])
    assert bt.headless is False
    assert bt.timeout == 5000
    assert bt.viewport == (800, 600)
    assert bt.allowed_domains == ["a.com"]


# ---------------------------------------------------------------------------
# Lazy setup
# ---------------------------------------------------------------------------

def test_lazy_setup():
    """Browser is not started until first execute()."""
    from chimera.tools.browser import BrowserTool

    page = _make_mock_page()
    context = _make_mock_context(page)
    browser = _make_mock_browser(context)
    pw = _make_mock_playwright(browser)
    sync_pw = _make_sync_playwright(pw)

    bt = BrowserTool()
    assert bt._browser is None

    with patch("chimera.tools.browser.sync_playwright", sync_pw):
        result = bt.execute({"action": "content"}, None)

    assert bt._browser is not None
    assert result.success


# ---------------------------------------------------------------------------
# ImportError path
# ---------------------------------------------------------------------------

def test_import_error_message():
    from chimera.tools.browser import BrowserTool
    bt = BrowserTool()

    with patch("chimera.tools.browser.sync_playwright", None):
        result = bt.execute({"action": "navigate", "url": "https://example.com"}, None)

    assert result.error is not None
    assert "playwright not installed" in result.error


# ---------------------------------------------------------------------------
# Action: navigate
# ---------------------------------------------------------------------------

def test_action_navigate(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "navigate", "url": "https://example.com"}, None)
    assert result.success
    assert "Navigated to" in result.output
    mocks["page"].goto.assert_called_once()


# ---------------------------------------------------------------------------
# Action: click
# ---------------------------------------------------------------------------

def test_action_click(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "click", "selector": "#btn"}, None)
    assert result.success
    assert "Clicked" in result.output
    mocks["page"].click.assert_called_once_with("#btn", timeout=30000)


# ---------------------------------------------------------------------------
# Action: type
# ---------------------------------------------------------------------------

def test_action_type(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "type", "selector": "input", "text": "hi"}, None)
    assert result.success
    assert "Typed" in result.output
    mocks["page"].fill.assert_called_once_with("input", "hi", timeout=30000)


# ---------------------------------------------------------------------------
# Action: screenshot
# ---------------------------------------------------------------------------

def test_action_screenshot(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "screenshot"}, None)
    assert result.success
    assert result.output == "Screenshot captured"
    assert "screenshot" in result.metadata
    decoded = base64.b64decode(result.metadata["screenshot"])
    assert decoded == b"\x89PNG fake screenshot bytes"


def test_action_screenshot_full_page(tool_with_mocks):
    bt, mocks = tool_with_mocks
    bt.execute({"action": "screenshot", "full_page": True, "path": "/tmp/s.png"}, None)
    mocks["page"].screenshot.assert_called_with(path="/tmp/s.png", full_page=True)


# ---------------------------------------------------------------------------
# Action: content
# ---------------------------------------------------------------------------

def test_action_content(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "content"}, None)
    assert result.success
    assert result.output == "Hello world"


def test_action_content_truncation(tool_with_mocks):
    bt, mocks = tool_with_mocks
    mocks["page"].inner_text.return_value = "x" * 20000
    result = bt.execute({"action": "content"}, None)
    assert len(result.output) < 20000
    assert result.output.endswith("... (truncated)")


# ---------------------------------------------------------------------------
# Action: html
# ---------------------------------------------------------------------------

def test_action_html(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "html"}, None)
    assert result.success
    assert "<html>" in result.output


def test_action_html_truncation(tool_with_mocks):
    bt, mocks = tool_with_mocks
    mocks["page"].content.return_value = "y" * 20000
    result = bt.execute({"action": "html"}, None)
    assert len(result.output) < 20000
    assert result.output.endswith("... (truncated)")


# ---------------------------------------------------------------------------
# Action: evaluate
# ---------------------------------------------------------------------------

def test_action_evaluate(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "evaluate", "js": "1+1"}, None)
    assert result.success
    assert result.output == "42"
    mocks["page"].evaluate.assert_called_once_with("1+1")


# ---------------------------------------------------------------------------
# Action: wait
# ---------------------------------------------------------------------------

def test_action_wait(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "wait", "selector": ".loaded"}, None)
    assert result.success
    assert "found" in result.output
    mocks["page"].wait_for_selector.assert_called_once_with(".loaded", timeout=30000)


# ---------------------------------------------------------------------------
# Action: select
# ---------------------------------------------------------------------------

def test_action_select(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "select", "selector": "select#foo", "value": "bar"}, None)
    assert result.success
    assert "Selected" in result.output
    mocks["page"].select_option.assert_called_once_with("select#foo", "bar", timeout=30000)


# ---------------------------------------------------------------------------
# Action: scroll
# ---------------------------------------------------------------------------

def test_action_scroll_down(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "scroll", "direction": "down", "amount": 300}, None)
    assert result.success
    mocks["page"].mouse.wheel.assert_called_once_with(0, 300)


def test_action_scroll_up(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "scroll", "direction": "up", "amount": 200}, None)
    assert result.success
    mocks["page"].mouse.wheel.assert_called_once_with(0, -200)


def test_action_scroll_defaults(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "scroll"}, None)
    assert result.success
    mocks["page"].mouse.wheel.assert_called_once_with(0, 500)


# ---------------------------------------------------------------------------
# Action: back / forward
# ---------------------------------------------------------------------------

def test_action_back(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "back"}, None)
    assert result.success
    mocks["page"].go_back.assert_called_once()


def test_action_forward(tool_with_mocks):
    bt, mocks = tool_with_mocks
    result = bt.execute({"action": "forward"}, None)
    assert result.success
    mocks["page"].go_forward.assert_called_once()


# ---------------------------------------------------------------------------
# Tab management
# ---------------------------------------------------------------------------

def test_action_tabs(tool_with_mocks):
    bt, mocks = tool_with_mocks
    page1 = _make_mock_page("https://one.com")
    page2 = _make_mock_page("https://two.com")
    mocks["context"].pages = [page1, page2]
    result = bt.execute({"action": "tabs"}, None)
    assert result.success
    assert "https://one.com" in result.output
    assert "https://two.com" in result.output


def test_action_new_tab(tool_with_mocks):
    bt, mocks = tool_with_mocks
    new_page = _make_mock_page()
    mocks["context"].new_page.return_value = new_page
    result = bt.execute({"action": "new_tab", "url": "https://new.com"}, None)
    assert result.success
    assert "new tab" in result.output.lower()
    new_page.goto.assert_called_once_with("https://new.com")


def test_action_new_tab_blank(tool_with_mocks):
    bt, mocks = tool_with_mocks
    new_page = _make_mock_page()
    mocks["context"].new_page.return_value = new_page
    result = bt.execute({"action": "new_tab"}, None)
    assert result.success
    new_page.goto.assert_not_called()


def test_action_close_tab(tool_with_mocks):
    bt, mocks = tool_with_mocks
    page2 = _make_mock_page("https://remaining.com")
    mocks["context"].pages = [page2]
    result = bt.execute({"action": "close_tab"}, None)
    assert result.success
    assert "Closed" in result.output
    assert bt._page is page2


def test_action_switch_tab(tool_with_mocks):
    bt, mocks = tool_with_mocks
    page0 = _make_mock_page("https://zero.com")
    page1 = _make_mock_page("https://one.com")
    mocks["context"].pages = [page0, page1]
    result = bt.execute({"action": "switch_tab", "index": 1}, None)
    assert result.success
    assert bt._page is page1


def test_action_switch_tab_out_of_range(tool_with_mocks):
    bt, mocks = tool_with_mocks
    mocks["context"].pages = [_make_mock_page()]
    result = bt.execute({"action": "switch_tab", "index": 5}, None)
    assert not result.success
    assert "out of range" in result.error


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------

def test_unknown_action(tool_with_mocks):
    bt, _ = tool_with_mocks
    result = bt.execute({"action": "nonexistent"}, None)
    assert not result.success
    assert "Unknown browser action" in result.error


# ---------------------------------------------------------------------------
# Domain restriction
# ---------------------------------------------------------------------------

def test_domain_restriction_blocks(tool_with_mocks):
    bt, mocks = tool_with_mocks
    bt.allowed_domains = ["safe.com"]
    result = bt.execute({"action": "navigate", "url": "https://evil.com/page"}, None)
    assert not result.success
    assert "not in the allowed list" in result.error
    mocks["page"].goto.assert_not_called()


def test_domain_restriction_allows(tool_with_mocks):
    bt, mocks = tool_with_mocks
    bt.allowed_domains = ["safe.com"]
    result = bt.execute({"action": "navigate", "url": "https://safe.com/page"}, None)
    assert result.success
    mocks["page"].goto.assert_called_once()


def test_domain_restriction_new_tab_blocks(tool_with_mocks):
    bt, mocks = tool_with_mocks
    bt.allowed_domains = ["ok.com"]
    result = bt.execute({"action": "new_tab", "url": "https://bad.com"}, None)
    assert not result.success
    assert "not in the allowed list" in result.error


def test_domain_restriction_new_tab_allows(tool_with_mocks):
    bt, mocks = tool_with_mocks
    bt.allowed_domains = ["ok.com"]
    new_page = _make_mock_page()
    mocks["context"].new_page.return_value = new_page
    result = bt.execute({"action": "new_tab", "url": "https://ok.com/x"}, None)
    assert result.success


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_exception_wrapped_in_tool_result(tool_with_mocks):
    bt, mocks = tool_with_mocks
    mocks["page"].goto.side_effect = RuntimeError("Timeout!")
    result = bt.execute({"action": "navigate", "url": "https://example.com"}, None)
    assert not result.success
    assert "Timeout!" in result.error


# ---------------------------------------------------------------------------
# cleanup()
# ---------------------------------------------------------------------------

def test_cleanup(tool_with_mocks):
    bt, mocks = tool_with_mocks
    assert bt._browser is not None
    bt.cleanup()
    mocks["browser"].close.assert_called_once()
    mocks["playwright"].stop.assert_called_once()
    assert bt._browser is None
    assert bt._playwright is None
    assert bt._page is None
    assert bt._context is None
