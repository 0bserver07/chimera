"""Pure text-shaping helpers for the shared TUI transcript renderer.

The Phase-1 presentation-discipline mechanisms of
``docs/specs/tui-ux-refinements.md``, kept terminal-free and stdlib-only so
they are exhaustively unit-testable (spec §13 "pure functions first"):

- :func:`split_complete_blocks` — R-REN-6 stream-safe commitment: split a
  streaming buffer into completed top-level markdown blocks and a live tail.
- :func:`live_tail_view` — R-REN-6: trim a partial *closing* fence from the
  live tail so a rendered tail never shrinks when the real close arrives.
- :func:`normalize_nested_fences` — R-REN-7: upgrade an outer code fence whose
  body itself contains fence markers, so inner markers render as content.
- :func:`elide_middle` / :func:`caps_for_tool` — R-FOLD-2 head+tail display
  elision with dual caps (lines *and* chars) and a per-tool-class cap table.
  Display-only by contract: the session record keeps full output (R-FOLD-3).

Block-boundary rules implemented by :func:`split_complete_blocks`
(conservative by design — when in doubt the text stays live, because committed
text goes to an append-only sink and must never re-render differently):

- A fenced code block completes at its closing fence. Fences track char *and*
  length: a close needs the same char with a run at least as long as the
  opener, and a backtick opener whose info string contains a backtick is not
  a fence at all.
- A paragraph-like run (prose, headings joined to prose, quotes) completes at
  the blank line that terminates it; the blank commits with the block.
- A pipe table is held back until a non-table line arrives — a new row
  reshapes every column, so rows must never commit one at a time.
- List and indented-code runs hold across blank lines; they complete when a
  blank line is followed by a line that cannot continue them.
- A top-level fence opener also completes whatever run precedes it.
- The buffer's final line is incomplete until its newline arrives, so chunk
  boundaries landing mid-marker (e.g. a fence split across chunks) can never
  mis-classify a line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "DEFAULT_CAPS",
    "ElisionCaps",
    "QUIET_CAPS",
    "SHELL_CAPS",
    "caps_for_tool",
    "elide_middle",
    "live_tail_view",
    "normalize_nested_fences",
    "split_complete_blocks",
]

# A fence marker line: up to 3 leading spaces, then a run of >=3 backticks or
# tildes, then the info string.
_FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_TABLE_RE = re.compile(r"^ {0,3}\|")
_LIST_RE = re.compile(r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]|$)")


def _fence_open(content: str) -> tuple[str, int] | None:
    """Classify *content* as a fence opener, returning ``(char, length)``."""
    m = _FENCE_RE.match(content)
    if m is None:
        return None
    run = m.group(2)
    if run[0] == "`" and "`" in m.group(3):
        return None  # backtick fences cannot carry backticks in the info string
    return run[0], len(run)


def _fence_close(content: str, char: str, length: int) -> bool:
    """True if *content* closes a fence of *char* with opening run *length*."""
    m = _FENCE_RE.match(content)
    if m is None:
        return False
    run = m.group(2)
    return run[0] == char and len(run) >= length and not m.group(3).strip()


def _is_indent_line(content: str) -> bool:
    return content.startswith(("    ", "\t"))


def _classify(content: str) -> str:
    """Kind of run a non-blank line starts: table, list, indent, or para."""
    if _TABLE_RE.match(content):
        return "table"
    if _LIST_RE.match(content):
        return "list"
    if _is_indent_line(content):
        return "indent"
    return "para"


def _held_run_continues(kind: str | None, content: str) -> bool:
    """Whether a non-blank line continues a list/indent run held across blanks."""
    if kind == "list":
        return bool(_LIST_RE.match(content)) or content[:1] in (" ", "\t")
    if kind == "indent":
        return _is_indent_line(content)
    return False


def split_complete_blocks(buffer: str) -> tuple[list[str], str]:
    """Split a streaming buffer into completed top-level blocks and a live tail.

    Args:
        buffer: Accumulated streamed markdown source.

    Returns:
        ``(blocks, tail)``: each block is the source text of one completed
        top-level markdown block (cut at line boundaries per the module rules),
        ``tail`` the still-live remainder. ``"".join(blocks) + tail == buffer``
        always holds, and the same text fed in any chunking commits the same
        blocks (prefix-stable), so an append-only sink can render each block
        the moment it completes (R-REN-6).
    """
    lines = buffer.splitlines(keepends=True)
    frag = ""
    if lines and not lines[-1].endswith("\n"):
        frag = lines.pop()

    blocks: list[str] = []
    run: list[str] = []
    kind: str | None = None
    fence: tuple[str, int] | None = None
    fence_in_run = False  # fence nested inside a list run (indented opener)
    holding = False  # list/indent run saw a blank; awaiting a decisive line

    def commit_run() -> None:
        nonlocal kind, holding
        if run:
            blocks.append("".join(run))
            run.clear()
        kind = None
        holding = False

    for raw in lines:
        content = raw.rstrip("\r\n")
        if fence is not None:  # verbatim until a valid close (char + length)
            run.append(raw)
            if _fence_close(content, *fence):
                fence = None
                if not fence_in_run:
                    commit_run()  # a closed fence can never be extended
            continue
        blank = not content.strip()
        opener = _fence_open(content)
        if holding and not blank:
            holding = False
            if opener is None and not _held_run_continues(kind, content):
                commit_run()  # fence openers are resolved by the branch below
        if opener is not None:
            if kind == "list" and content[:1] in (" ", "\t"):
                fence = opener  # indented fence is list-item content
                fence_in_run = True
                run.append(raw)
                continue
            if kind is not None:
                commit_run()  # a top-level fence completes the pending run
            fence = opener
            fence_in_run = False
            run.append(raw)
            continue
        if blank:
            run.append(raw)
            if kind in ("para", "table"):
                commit_run()  # blank-line-terminated run, blank included
            elif kind in ("list", "indent"):
                holding = True  # lists/indent code may continue past blanks
            continue
        if kind is None:
            kind = _classify(content)
        elif kind == "table" and not _TABLE_RE.match(content):
            commit_run()  # tables finish when a non-table line arrives
            kind = _classify(content)
        run.append(raw)

    return blocks, "".join(run) + frag


def live_tail_view(tail: str) -> str:
    """Display form of the live tail: drop a trailing partial closing fence.

    While a fence is open in the tail, a trailing fragment made only of the
    fence char (e.g. ``\\x60\\x60`` while the close streams in) would briefly
    render as code content and then vanish — the block would shrink when the
    full close arrives. Trimming it keeps the live render growth-only
    (R-REN-6). Everything else passes through unchanged; the tail itself stays
    intact in the buffer.

    Args:
        tail: The uncommitted live tail from :func:`split_complete_blocks`.

    Returns:
        The tail, minus a trailing partial closing-fence fragment when one is
        being streamed inside an open fence.
    """
    if not tail:
        return tail
    lines = tail.split("\n")
    fence: tuple[str, int] | None = None
    for line in lines[:-1]:
        content = line.rstrip("\r")
        if fence is None:
            fence = _fence_open(content)
        elif _fence_close(content, *fence):
            fence = None
    if fence is None:
        return tail
    partial = lines[-1]
    if re.fullmatch(rf" {{0,3}}{re.escape(fence[0])}+[ \t]*", partial):
        return tail[: len(tail) - len(partial)]
    return tail


@dataclass
class _TopFence:
    """A top-level fence found by the intent parse of :func:`normalize_nested_fences`."""

    open_idx: int
    char: str
    length: int
    close_idx: int | None = None
    max_inner: int = 0  # longest same-char marker run on lines inside the body


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    """Classify *line* as any fence marker, returning ``(char, length, info)``."""
    m = _FENCE_RE.match(line)
    if m is None:
        return None
    run = m.group(2)
    info = m.group(3).strip()
    if run[0] == "`" and "`" in info:
        return None
    return run[0], len(run), info


def _rewrite_fence_line(line: str, length: int) -> str:
    m = _FENCE_RE.match(line)
    assert m is not None  # callers pass known marker lines
    return m.group(1) + m.group(2)[0] * length + m.group(3)


def normalize_nested_fences(text: str) -> str:
    """Upgrade outer fences whose bodies contain fence markers (R-REN-7).

    Markdown parsers read fences flat: when model output nests a fence inside
    a fence of the same char, the inner close ends the *outer* block early and
    the intended outer close reopens a dangling fence. This reconstructs the
    intended nesting (a marker with an info string opens; a bare marker closes
    the innermost same-char fence at least as short) and lengthens each outer
    fence's delimiters to one more than the longest same-char marker run in
    its body, so inner markers become content.

    Args:
        text: Assistant markdown source about to be rendered.

    Returns:
        *text* itself when no nesting is detected (the common case), otherwise
        a copy with only the outer fence delimiter lines rewritten.
    """
    lines = text.split("\n")
    stack: list[tuple[str, int, _TopFence]] = []
    tops: list[_TopFence] = []
    nested = False
    for idx, line in enumerate(lines):
        marker = _fence_marker(line)
        if marker is None:
            continue
        char, length, info = marker
        close_depth: int | None = None
        if not info:
            for depth in range(len(stack) - 1, -1, -1):
                s_char, s_len, _ = stack[depth]
                if s_char == char and length >= s_len:
                    close_depth = depth
                    break
        if close_depth is not None:
            top = stack[0][2]
            if close_depth == 0:
                top.close_idx = idx
            elif char == top.char:
                top.max_inner = max(top.max_inner, length)
            del stack[close_depth:]
            continue
        if stack:
            nested = True
            top = stack[0][2]
            if char == top.char:
                top.max_inner = max(top.max_inner, length)
            stack.append((char, length, top))
        else:
            rec = _TopFence(open_idx=idx, char=char, length=length)
            tops.append(rec)
            stack.append((char, length, rec))
    if not nested:
        return text
    changed = False
    for rec in tops:
        if rec.max_inner < 3:
            continue
        new_length = rec.max_inner + 1
        if new_length <= rec.length:
            continue
        for line_idx in (rec.open_idx, rec.close_idx):
            if line_idx is not None:
                lines[line_idx] = _rewrite_fence_line(lines[line_idx], new_length)
        changed = True
    return "\n".join(lines) if changed else text


@dataclass(frozen=True)
class ElisionCaps:
    """Display caps for one tool class (R-FOLD-2 dual caps).

    Args:
        head_lines: Lines kept from the start of the output.
        tail_lines: Lines kept from the end of the output.
        max_chars: Character budget for the displayed head + tail combined;
            binds when it is hit before the line caps do.
    """

    head_lines: int
    tail_lines: int
    max_chars: int


# Per-tool-class caps (spec §11 posture: shell-class output earns more room
# than bulk read/search/list output; unknown tools get a modest default).
SHELL_CAPS = ElisionCaps(head_lines=10, tail_lines=5, max_chars=4000)
QUIET_CAPS = ElisionCaps(head_lines=3, tail_lines=2, max_chars=800)
DEFAULT_CAPS = ElisionCaps(head_lines=6, tail_lines=3, max_chars=1600)

_TOOL_CAPS: dict[str, ElisionCaps] = {
    "bash": SHELL_CAPS,
    "powershell": SHELL_CAPS,
    "ipython": SHELL_CAPS,
    "test": SHELL_CAPS,
    "git": SHELL_CAPS,
    "verify": SHELL_CAPS,
    "read": QUIET_CAPS,
    "read_file": QUIET_CAPS,
    "search": QUIET_CAPS,
    "grep": QUIET_CAPS,
    "list_files": QUIET_CAPS,
    "repo_map": QUIET_CAPS,
    "import_graph": QUIET_CAPS,
    "web_fetch": QUIET_CAPS,
}


def caps_for_tool(name: str) -> ElisionCaps:
    """Display caps for a tool name; unknown tools get :data:`DEFAULT_CAPS`."""
    return _TOOL_CAPS.get(name, DEFAULT_CAPS)


def elide_middle(text: str, caps: ElisionCaps) -> tuple[str, str, str]:
    """Head+tail elision with dual caps, for display only (R-FOLD-2/3).

    Line and char caps both apply — whichever binds first. The head is always
    a prefix and the tail always a suffix of *text*; the marker names exactly
    what was hidden (``… +37 lines …``, or ``… +1204 chars …`` when the char
    cap did the cutting, e.g. for one giant line).

    Args:
        text: Full tool output (the caller keeps this intact for the record).
        caps: The cap set to apply, usually from :func:`caps_for_tool`.

    Returns:
        ``(head, marker, tail)``. When *text* fits, ``(text, "", "")``.
    """
    lines = text.split("\n")
    over_lines = len(lines) > caps.head_lines + caps.tail_lines + 1
    over_chars = len(text) > caps.max_chars
    if not over_lines and not over_chars:
        return text, "", ""
    head_budget = caps.max_chars * 2 // 3
    tail_budget = caps.max_chars - head_budget
    char_cut = False
    if over_lines:
        head_ls = lines[: caps.head_lines]
        tail_ls = lines[-caps.tail_lines :]
        if over_chars:  # shed whole lines toward the char budget first
            while len(head_ls) > 1 and len("\n".join(head_ls)) > head_budget:
                head_ls.pop()
            while len(tail_ls) > 1 and len("\n".join(tail_ls)) > tail_budget:
                tail_ls.pop(0)
    else:  # few but enormous lines: the char cap is the binding constraint
        head_ls = [text[:head_budget]]
        tail_ls = [text[-tail_budget:]]
        char_cut = True
    head = "\n".join(head_ls)
    tail = "\n".join(tail_ls)
    if over_chars:
        if len(head) > head_budget:
            head = head[:head_budget]
            char_cut = True
        if len(tail) > tail_budget:
            tail = tail[-tail_budget:]
            char_cut = True
    if char_cut:
        marker = f"… +{len(text) - len(head) - len(tail)} chars …"
    else:
        marker = f"… +{len(lines) - len(head_ls) - len(tail_ls)} lines …"
    return head, marker, tail
