---
title: "web_fetch — fetch a URL as plain text"
description: "Fetch a URL via HTTP(S) and return the response body as text. HTML tags are stripped; output is truncated to 50 KB."
---

`web_fetch` issues a single `GET` request with redirects followed and a 30-second timeout. If the response is HTML, `<script>` / `<style>` blocks are removed, tags are stripped, and whitespace is collapsed. Output is truncated to 50 KB so a large page doesn't blow the context window.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `url` | string | yes | Absolute URL (`http://` or `https://`). |

## Prerequisites

```bash
pip install chimera-run[web]   # pulls httpx
```

Without `httpx`, the tool returns `error="httpx not installed. pip install httpx"`.

## Example invocation

```json
{"url": "https://example.com"}
```

```python
from chimera.tools.web_fetch import WebFetchTool

tool = WebFetchTool()
result = tool.execute({"url": "https://example.com"}, env=None)
print(result.output[:200])
```

## Output sample

```
Example Domain This domain is for use in illustrative examples in documents. You may use this domain in literature without prior coordination or asking for permission. More information...
```

## Notes

- No JavaScript execution — use [`browser`](./browser.md) for SPA pages.
- No persistent cookies; each call is independent.
- Truncation is hard-coded at 50 000 chars.

## See also

- [`browser`](./browser.md) — full Playwright session.
- [`grounded_search`](https://github.com/0bserver07/chimera/blob/master/chimera/tools/grounded_search.py) — RAG-style answer with citations.
