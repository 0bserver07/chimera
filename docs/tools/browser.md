---
title: "browser — drive a real browser session"
description: "Control a Playwright-backed browser. Navigate, click, type, screenshot, evaluate JavaScript, manage tabs."
---

`browser` is a single tool with 16 actions for end-to-end browser automation. It wraps Playwright; install with `pip install chimera-run[browser]`.

## Schema

| Arg | Type | Description |
|---|---|---|
| `action` | string | One of: `navigate`, `click`, `type`, `screenshot`, `content`, `html`, `evaluate`, `wait`, `select`, `scroll`, `back`, `forward`, `tabs`, `new_tab`, `close_tab`, `switch_tab`. |
| `url` | string | For `navigate` / `new_tab`. |
| `selector` | string | CSS selector for `click` / `type` / `wait` / `select`. |
| `text` | string | Text to `type`. |
| `js` | string | JavaScript expression for `evaluate`. |
| `path` | string | Output path for `screenshot`. |
| `full_page` | boolean | Capture the whole page (default `false`). |
| `direction` | string | `up` / `down` for `scroll`. |
| `amount` | number | Scroll pixels. |
| `timeout` | number | Action timeout in ms. |
| `value` | string | Value for `select`. |

`action` is required; the other fields are action-specific.

## Example invocation

```json
{"action": "navigate", "url": "https://example.com"}
{"action": "screenshot", "path": "/tmp/snap.png", "full_page": true}
{"action": "evaluate", "js": "document.title"}
```

```python
from chimera.tools.browser import BrowserTool

browser = BrowserTool()
browser.execute({"action": "navigate", "url": "https://example.com"}, env=None)
browser.execute({"action": "click", "selector": "a.login"}, env=None)
browser.execute({"action": "type", "selector": "#user", "text": "alice"}, env=None)
```

## Output sample

```
Navigated to https://example.com (200, 1.4s).
```

## Lifecycle

The tool keeps a single browser context across calls in the same agent run. Closed automatically when the agent terminates. To force a fresh session, drop the tool from the agent's tool list and re-instantiate.

## See also

- [`read_image`](./image-read.md) — for analyzing the screenshot output.
- [`web_fetch`](./web-fetch.md) — text-only HTTP fetch.
