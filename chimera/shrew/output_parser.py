"""Repair text-mode tool calls leaked by small models.

Small local models (qwen-coder, llama.cpp, gguf-quantised variants) often
break out of the structured ``tool_use`` channel and emit tool invocations
as **plain text** in the assistant message. The four most common shapes
this module handles:

1. **Triple-fenced ``tool`` block.** Some quantised qwen builds emit::

       ```tool
       {"name": "bash", "arguments": {"cmd": "ls -la"}}
       ```

   even though the request specified native tool-calling.

2. **``<tool_call>`` XML-ish wrapper.** The format encouraged by qwen's
   chat template when ``enable_thinking`` is set::

       <tool_call>
       {"name": "bash", "arguments": {"cmd": "ls"}}
       </tool_call>

3. **Bare JSON object.** Llama.cpp's grammar-constrained mode sometimes
   returns the JSON unwrapped::

       {"name": "bash", "arguments": {"cmd": "ls"}}

4. **Python function-call shorthand.** Tiny models routinely fall back
   to natural-language style::

       bash(cmd="ls -la")
       read(path="/tmp/foo", offset=0)

   We accept ``=`` and ``:`` as the key-value separator and quoted /
   bare numeric / bare boolean / single-quoted values.

The parser is pure: text in, ``list[ParsedToolCall]`` out (zero or more).
The shrew loop calls :func:`parse_tool_calls` on every assistant message
that arrived without structured ``tool_use`` blocks, and converts hits
to real :class:`chimera.types.ToolCall` instances via
:meth:`ParsedToolCall.to_tool_call`. Untouched text (the parts that
weren't a tool call) is returned via :func:`strip_tool_calls` so the
caller can still display the model's prose.

Stdlib-only. No regex backtracking traps — every pattern is anchored
either to a fence delimiter or to an identifier-shaped prefix so the
worst case is linear in input length.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Final

from chimera.types import ToolCall

__all__ = [
    "MAX_PARSE_LENGTH",
    "ParsedToolCall",
    "ParseSource",
    "TOOL_CALL_FENCE_PATTERN",
    "TOOL_CALL_XML_PATTERN",
    "FUNCTION_CALL_PATTERN",
    "BARE_JSON_PATTERN",
    "parse_tool_calls",
    "strip_tool_calls",
    "looks_like_tool_call",
    "to_tool_calls",
]


MAX_PARSE_LENGTH: Final[int] = 32_000
"""Cap on input characters scanned per call.

Bounds worst-case latency on pasted logs / runaway model output. The
cap is generous (~8k tokens) but not unbounded; callers feeding huge
contexts should slice the message themselves.
"""


ParseSource = str
"""Tag identifying which pattern produced a :class:`ParsedToolCall`.

Values: ``"fence"``, ``"xml"``, ``"json"``, ``"function"``. Useful for
metrics / debugging — the shrew quality monitor records the source so
we can tune which formats need the strongest nudge back to native
tool-calling.
"""


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------


TOOL_CALL_FENCE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"```(?:tool|tool_call|tool_use|json)\s*\n(.*?)\n```",
    re.DOTALL,
)
"""Match triple-backtick fenced blocks tagged ``tool`` / ``tool_call`` / ``json``.

Captures the inner body (group 1) for downstream JSON parsing. Tolerates
the four common fence tags small models emit; we deliberately accept
``json`` because llama.cpp's stop-token logic sometimes tags pure JSON
with the language hint.
"""


TOOL_CALL_XML_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>",
    re.DOTALL,
)
"""Match qwen-style ``<tool_call>...</tool_call>`` wrappers.

Captures the inner JSON (group 1). The wrapper itself is dropped when
the call is converted into a real :class:`ToolCall`.
"""


# Match a bare top-level JSON object whose first two keys are ``"name"``
# and ``"arguments"``. We avoid running a generic JSON balancer because
# the model often nests other JSON blobs in the prose; only the
# ``{"name":...,"arguments":...}`` shape is unambiguous.
BARE_JSON_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\{\s*\"name\"\s*:\s*\"(?P<name>[A-Za-z_][A-Za-z0-9_-]*)\"\s*,"
    r"\s*\"arguments\"\s*:\s*\{",
    re.DOTALL,
)
"""Match the *prefix* of a bare ``{"name":..., "arguments": {`` object.

The pattern only locates the start; the body is closed by a manual
brace-balancer (:func:`_balance_braces`) so nested argument dicts
parse correctly. A pure regex would either need recursion (not
supported in :mod:`re`) or a non-greedy ``.*?`` that breaks on the
first inner ``}}``.
"""


# Match ``tool_name(arg=val, arg2=val2)`` invocations. The argument list
# capture is greedy *up to* the first balancing ``)`` — we re-validate
# parenthesis balance in :func:`_parse_function_args` to handle nested
# parens / quoted strings / commas-in-values robustly.
FUNCTION_CALL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\n|\r|\s|>)(?P<name>[a-z][a-z0-9_]{1,63})\((?P<args>[^)]{0,2048})\)",
    re.IGNORECASE,
)
"""Match Python-style ``tool_name(arg=val, ...)`` shorthand.

The leading anchor (``^|\\n|\\r|\\s|>``) keeps us from matching mid-word
function names embedded in prose like "we ran the bash(...)" — the
space / newline before the name is required. The argument capture caps
at 2 KB to bound regex cost; longer argument lists are rejected.
"""


# Reserved Python keywords / common control-flow words we never want
# to parse as tool names. The whitelist is small on purpose; the real
# defence against hallucinated tool names lives in
# :mod:`chimera.shrew.quality_monitor`, which checks against the live
# tool registry. This blacklist just prevents trivial false positives
# during natural-language emission.
_FUNCTION_BLACKLIST: Final[frozenset[str]] = frozenset({
    "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from",
    "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
    "or", "pass", "raise", "return", "try", "while", "with", "yield",
    "true", "false", "none", "null",
    "print", "input", "len", "range", "list", "dict", "set", "str",
    "int", "float", "bool", "tuple", "type",
    # Markdown/prose heads that look identifier-shaped.
    "note", "warning", "error", "info", "tip",
})


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedToolCall:
    """One repaired tool call extracted from text.

    Attributes:
        name: The tool name (e.g. ``"bash"``).
        arguments: JSON-shaped argument dict.
        source: Which parser produced this hit. See :data:`ParseSource`.
        span: ``(start, end)`` half-open character range in the input
            text. The caller can use this to remove / replace the
            text-mode call when emitting prose to the user.
        raw: The exact substring that was parsed. Useful for logging.
    """

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    source: ParseSource = "json"
    span: tuple[int, int] = (0, 0)
    raw: str = ""

    def to_tool_call(self, *, call_id: str | None = None) -> ToolCall:
        """Convert into a real :class:`chimera.types.ToolCall`.

        Args:
            call_id: Optional override for the synthesised tool-call id.
                When ``None`` we mint a UUID4-flavoured ``"shrew-<hex>"``
                so the call is distinguishable from native tool_use ids.

        Returns:
            A :class:`ToolCall` with matching ``name`` and
            ``arguments``.
        """
        cid = call_id or f"shrew-{uuid.uuid4().hex[:12]}"
        return ToolCall(id=cid, name=self.name, arguments=dict(self.arguments))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_tool_calls(text: str) -> list[ParsedToolCall]:
    """Extract every text-mode tool call from ``text`` in source order.

    The four formats listed at the top of this module are tried in
    descending precedence: fenced > XML > JSON > function shorthand.
    Once a span is consumed by an earlier format, later patterns skip
    it (so a fenced block containing JSON is parsed *as a fence*, not
    twice). Empty input returns ``[]``.

    Args:
        text: Free-form assistant message body. May contain prose
            interspersed with tool-call snippets. Up to
            :data:`MAX_PARSE_LENGTH` characters are scanned.

    Returns:
        Hits in input order (ascending ``span[0]``). Each hit's
        ``arguments`` dict is validated to be JSON-shaped (string keys,
        JSON-serialisable values).
    """
    if not text:
        return []
    body = text[:MAX_PARSE_LENGTH]
    consumed: list[tuple[int, int]] = []
    hits: list[ParsedToolCall] = []

    # 1. Fenced ```tool blocks
    for m in TOOL_CALL_FENCE_PATTERN.finditer(body):
        inner = m.group(1).strip()
        parsed = _parse_json_call(inner)
        if parsed is None:
            continue
        name, args = parsed
        hits.append(
            ParsedToolCall(
                name=name,
                arguments=args,
                source="fence",
                span=(m.start(), m.end()),
                raw=m.group(0),
            )
        )
        consumed.append((m.start(), m.end()))

    # 2. <tool_call> XML wrappers
    for m in TOOL_CALL_XML_PATTERN.finditer(body):
        if _overlaps(m.start(), m.end(), consumed):
            continue
        inner = m.group(1).strip()
        parsed = _parse_json_call(inner)
        if parsed is None:
            continue
        name, args = parsed
        hits.append(
            ParsedToolCall(
                name=name,
                arguments=args,
                source="xml",
                span=(m.start(), m.end()),
                raw=m.group(0),
            )
        )
        consumed.append((m.start(), m.end()))

    # 3. Bare {"name":..., "arguments":...}
    for m in BARE_JSON_PATTERN.finditer(body):
        if _overlaps(m.start(), m.end(), consumed):
            continue
        # Prefix matched up through the opening ``{`` of the args
        # dict. Find the balancing ``}`` for the args dict, then the
        # balancing ``}`` for the outer wrapper.
        args_open = m.end() - 1  # index of the inner ``{``
        args_close = _balance_braces(body, args_open)
        if args_close < 0:
            continue
        # Skip whitespace between args ``}`` and outer ``}``.
        i = args_close + 1
        while i < len(body) and body[i] in " \t\r\n":
            i += 1
        if i >= len(body) or body[i] != "}":
            continue
        outer_close = i
        try:
            args_raw = body[args_open: args_close + 1]
            args = json.loads(args_raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(args, dict):
            continue
        if not all(isinstance(k, str) for k in args.keys()):
            continue
        end_pos = outer_close + 1
        hits.append(
            ParsedToolCall(
                name=m.group("name"),
                arguments=args,
                source="json",
                span=(m.start(), end_pos),
                raw=body[m.start(): end_pos],
            )
        )
        consumed.append((m.start(), end_pos))

    # 4. Python-shorthand function calls
    for m in FUNCTION_CALL_PATTERN.finditer(body):
        if _overlaps(m.start("name"), m.end(), consumed):
            continue
        name = m.group("name")
        if name.lower() in _FUNCTION_BLACKLIST:
            continue
        args_text = m.group("args")
        fn_args = _parse_function_args(args_text)
        if fn_args is None:
            continue
        hits.append(
            ParsedToolCall(
                name=name,
                arguments=fn_args,
                source="function",
                span=(m.start("name"), m.end()),
                raw=body[m.start("name"): m.end()],
            )
        )
        consumed.append((m.start("name"), m.end()))

    hits.sort(key=lambda p: p.span[0])
    return hits


def strip_tool_calls(text: str, calls: Iterable[ParsedToolCall]) -> str:
    """Return ``text`` with every parsed tool-call span removed.

    Spans are removed in reverse order (so earlier offsets stay
    accurate) and adjacent whitespace is collapsed to a single newline.
    Empty calls iterator returns the input unchanged.

    Used by the shrew REPL when displaying the model's prose: the
    repaired tool calls become real ``ToolCall`` objects and shouldn't
    *also* appear inline in the rendered transcript.
    """
    if not text:
        return text
    spans = sorted({c.span for c in calls}, reverse=True)
    if not spans:
        return text
    out = text
    for start, end in spans:
        out = out[:start] + out[end:]
    # Collapse runs of blank lines introduced by the cuts.
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def looks_like_tool_call(text: str) -> bool:
    """Cheap pre-flight check: does ``text`` plausibly contain a tool call?

    Returns ``True`` if any of the four patterns has a quick prefix
    match. The shrew loop calls this before :func:`parse_tool_calls`
    on every assistant message; the full parser only runs when this
    returns ``True``, saving regex work on plain prose.
    """
    if not text:
        return False
    body = text[:MAX_PARSE_LENGTH]
    if "```tool" in body or "```json" in body:
        return True
    if "<tool_call>" in body:
        return True
    if "\"name\"" in body and "\"arguments\"" in body:
        return True
    if FUNCTION_CALL_PATTERN.search(body):
        return True
    return False


def to_tool_calls(parsed: Iterable[ParsedToolCall]) -> list[ToolCall]:
    """Convenience: convert every :class:`ParsedToolCall` to a :class:`ToolCall`.

    Each call gets a fresh ``shrew-<hex>`` id so they can be threaded
    through the agent loop alongside any native tool-use blocks without
    id collision.
    """
    return [p.to_tool_call() for p in parsed]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _parse_json_call(raw: str) -> tuple[str, dict[str, Any]] | None:
    """Parse a JSON tool-call body (``{"name":...,"arguments":...}``).

    Tolerates surrounding whitespace and an optional leading newline
    inside the fenced block. Returns ``None`` if the JSON is invalid,
    the structure is wrong, or the tool name doesn't look identifier-
    shaped.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name") or data.get("tool") or data.get("function")
    if not isinstance(name, str) or not name:
        return None
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", name):
        return None
    args = data.get("arguments") or data.get("args") or data.get("parameters")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    if not all(isinstance(k, str) for k in args.keys()):
        return None
    return name, dict(args)


def _parse_function_args(raw: str) -> dict[str, Any] | None:
    """Parse a Python-shorthand argument list into a dict.

    Accepts:
        ``key=value, key2=value2`` and ``key: value, key2: value2``.

    Values may be:
        * Double- or single-quoted strings (escapes preserved by
          :func:`json.loads`-fallback for double-quoted, and a small
          custom unescaper for single-quoted).
        * Bare integers and floats.
        * Bare booleans (``true``/``false``, case-insensitive).
        * Bare ``null``/``None``.
        * Bare bareword tokens (treated as strings).

    Returns:
        The parsed dict, or ``None`` when the syntax is malformed
        (unbalanced quotes, no separator, etc.). Empty input returns an
        empty dict.
    """
    s = raw.strip()
    if not s:
        return {}
    out: dict[str, Any] = {}
    pairs = list(_split_top_level(s, sep=","))
    for pair in pairs:
        pair = pair.strip()
        if not pair:
            continue
        # Find the first '=' or ':' that isn't inside quotes.
        sep_pos = _find_top_level_sep(pair)
        if sep_pos < 0:
            return None
        key = pair[:sep_pos].strip()
        value = pair[sep_pos + 1:].strip()
        if not key or not _is_identifier(key):
            return None
        out[key] = _coerce_value(value)
    return out


def _split_top_level(s: str, *, sep: str) -> Iterable[str]:
    """Split ``s`` on ``sep`` outside of quoted strings / nested parens.

    Yields successive top-level chunks. Used to break the function
    argument list into individual ``key=value`` pieces without tripping
    on commas embedded in string literals.
    """
    depth = 0
    quote: str | None = None
    start = 0
    for i, ch in enumerate(s):
        if quote is not None:
            if ch == "\\":
                continue
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            continue
        if ch in "([{":
            depth += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            continue
        if ch == sep and depth == 0:
            yield s[start:i]
            start = i + 1
    yield s[start:]


def _find_top_level_sep(s: str) -> int:
    """Find the first ``=`` or ``:`` outside quoted strings.

    Returns the character index, or ``-1`` if no top-level separator
    is found. Skips ``==`` and ``:=`` two-char operators so equality
    comparisons embedded in values don't fool the parser.
    """
    quote: str | None = None
    i = 0
    while i < len(s):
        ch = s[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            continue
        if ch == "=" and (i + 1 >= len(s) or s[i + 1] != "="):
            return i
        if ch == ":" and (i + 1 >= len(s) or s[i + 1] != "="):
            return i
        i += 1
    return -1


def _is_identifier(s: str) -> bool:
    """Return ``True`` if ``s`` is a Python-style identifier."""
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", s))


def _coerce_value(raw: str) -> Any:
    """Best-effort coerce a bare value string to a Python type.

    Order: JSON literal → quoted string (double or single) → int → float
    → bool → null → bareword (returned as-is). Falls back to the raw
    string when everything else fails so the caller still gets a usable
    dict for almost any input.
    """
    if not raw:
        return ""
    # Try JSON literal first — handles numbers, true/false/null, lists,
    # nested dicts, and double-quoted strings in one shot.
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    # Single-quoted strings: hand-unescape then return as plain str.
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        inner = raw[1:-1]
        out_chars: list[str] = []
        i = 0
        escape_map = {
            "\\": "\\", "\'": "'", '\"': '"',
            "n": "\n", "t": "\t", "r": "\r",
        }
        while i < len(inner):
            ch = inner[i]
            if ch == "\\" and i + 1 < len(inner):
                nxt = inner[i + 1]
                out_chars.append(escape_map.get(nxt, nxt))
                i += 2
                continue
            out_chars.append(ch)
            i += 1
        return "".join(out_chars)
    low = raw.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("none", "null"):
        return None
    # Bareword integer / float
    try:
        if "." in raw or "e" in low:
            return float(raw)
        return int(raw)
    except ValueError:
        pass
    return raw


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    """Return ``True`` if ``[start, end)`` overlaps any range in ``ranges``."""
    for rs, re_ in ranges:
        if start < re_ and end > rs:
            return True
    return False


def _balance_braces(body: str, open_pos: int) -> int:
    """Find the index of the ``}`` that closes the ``{`` at ``open_pos``.

    Walks ``body`` honouring quoted strings (single + double) and
    standard ``\\``-escapes inside them. Returns the index of the
    matching close brace, or ``-1`` when the string ends before
    balance is achieved.
    """
    if open_pos >= len(body) or body[open_pos] != "{":
        return -1
    depth = 0
    i = open_pos
    quote: str | None = None
    while i < len(body):
        ch = body[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1
