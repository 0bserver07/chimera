"""Export a chimera session to a standalone HTML file."""
from __future__ import annotations

from pathlib import Path

from chimera.types import Message

HTML_TEMPLATE = '''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Chimera Session Export</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #1a1a2e; color: #e0e0e0; }}
.message {{ margin: 12px 0; padding: 12px 16px; border-radius: 8px; }}
.user {{ background: #16213e; border-left: 3px solid #0f3460; }}
.assistant {{ background: #1a1a2e; border-left: 3px solid #533483; }}
.tool {{ background: #0f0f23; border-left: 3px solid #e94560; font-family: monospace; font-size: 0.9em; white-space: pre-wrap; }}
.role {{ font-size: 0.75em; text-transform: uppercase; color: #888; margin-bottom: 4px; }}
pre {{ background: #0d0d1a; padding: 10px; border-radius: 4px; overflow-x: auto; }}
h1 {{ color: #533483; }}
.meta {{ color: #666; font-size: 0.8em; margin-top: 20px; border-top: 1px solid #333; padding-top: 10px; }}
</style>
</head>
<body>
<h1>Chimera Session</h1>
{messages}
<div class="meta">{meta}</div>
</body>
</html>'''


def export_session_html(
    messages: list[Message],
    output_path: str | Path,
    meta: str = "",
) -> str:
    """Export messages to a standalone HTML file."""
    html_parts = []
    for msg in messages:
        role = getattr(msg, "role", "unknown")
        content = getattr(msg, "content", str(msg))
        # Escape HTML
        content = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Wrap code blocks
        content = content.replace("```", "</pre><pre>")  # Simple code block handling
        css_class = role if role in ("user", "assistant", "tool") else "assistant"
        html_parts.append(
            f'<div class="message {css_class}">'
            f'<div class="role">{role}</div>{content}</div>'
        )

    html = HTML_TEMPLATE.format(messages="\n".join(html_parts), meta=meta)
    Path(output_path).write_text(html)
    return str(output_path)
