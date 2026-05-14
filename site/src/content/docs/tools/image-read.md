---
title: "read_image — load an image as base64"
description: "Read an image from a file path or HTTP(S) URL and return its base64-encoded content for multimodal models."
---

`read_image` returns an image as base64 plus its media type, ready to feed into a multimodal Anthropic / OpenAI / Google request.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Local file path **or** `http://` / `https://` URL. |

## Example invocation

```json
{"path": "screenshots/dashboard.png"}
```

```python
from chimera.tools.image_read import ReadImageTool

tool = ReadImageTool()
result = tool.execute(
    {"path": "https://example.com/diagram.png"},
    env=local_env,
)

print(result.metadata["media_type"])  # "image/png"
print(len(result.metadata["image_data"]))  # base64 string length
```

## Output sample

```
Read 87.2 KB image: screenshots/dashboard.png (image/png)
```

`result.metadata` carries:

| Key | Value |
|---|---|
| `image_data` | base64 string |
| `media_type` | e.g. `image/png`, `image/jpeg` |
| `path` | echo of the input path |

## Supported formats

PNG, JPEG, GIF, and WebP — the formats accepted by every major multimodal provider.

## See also

- [`browser`](./browser.md) — screenshot capture from a live browser.
- [`web_fetch`](./web-fetch.md) — text fetch.
