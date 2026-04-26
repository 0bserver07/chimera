"""TUI rendering primitives for the `chimera mink` REPL (M1).

Three classes: MarkdownStream, Spinner, ToolBlockRenderer. The optional
``mink`` extra (``pip install chimera-run[mink]``) brings in ``rich`` and
``pygments`` for the polished view; when either dependency is missing
this module still imports cleanly and every renderer falls back to plain
text so callers never need to guard imports.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TextIO

from chimera.streaming.base import StreamHandler
from chimera.streaming.handlers import ConsoleStreamHandler

if TYPE_CHECKING:
    # WHY (pyright/mypy): expose the real ``Console`` symbol to the type
    # checker so annotations like ``_RichConsole | None`` resolve to the
    # actual class regardless of whether ``rich`` is installed at runtime.
    # The ``try``/``except`` block below owns runtime binding (and may set
    # ``Console`` to ``None``); this import never executes.
    from rich.console import Console as _RichConsole

try:
    from rich.console import Console
    from rich.markdown import Markdown

    _RICH_AVAILABLE = True
    # WHY: a separately-typed reference to the ``Console`` *class* (or None
    # when rich is missing). Pyright can narrow ``_ConsoleClass is not None``
    # at the call site, which the original ternary on the runtime ``Console``
    # name could not satisfy (reportOptionalCall on line 112).
    _ConsoleClass: type[_RichConsole] | None = Console
except ImportError:  # pragma: no cover
    # WHY (pyright): bind names to ``None`` in the failure branch so static
    # analysis can see the symbols are always defined at module scope.
    # Runtime guards on ``_RICH_AVAILABLE`` keep the None values from ever
    # being called.
    Console = None  # type: ignore[assignment,misc]
    Markdown = None  # type: ignore[assignment,misc]
    _RICH_AVAILABLE = False
    _ConsoleClass = None

try:
    from pygments import highlight  # type: ignore[import-untyped]
    from pygments.formatters import Terminal256Formatter  # type: ignore[import-untyped]
    from pygments.lexers import get_lexer_by_name  # type: ignore[import-untyped]
    from pygments.util import ClassNotFound  # type: ignore[import-untyped]

    _PYGMENTS_AVAILABLE = True
except ImportError:  # pragma: no cover
    # WHY (pyright): bind names to ``None`` in the failure branch so static
    # analysis can see the symbols are always defined at module scope.
    # Runtime guards on ``_PYGMENTS_AVAILABLE`` keep the None values from
    # ever being called.
    highlight = None  # type: ignore[assignment]
    Terminal256Formatter = None  # type: ignore[assignment,misc]
    get_lexer_by_name = None  # type: ignore[assignment]
    ClassNotFound = Exception  # type: ignore[assignment,misc]
    _PYGMENTS_AVAILABLE = False

__all__ = [
    "MarkdownStream",
    "Spinner",
    "ToolBlockRenderer",
    "ThinkingBlockRenderer",
    "DiffRenderer",
    "RichStreamHandler",
    "MinkStreamHandler",
    "build_stream_handler",
]


# Open-fence regex: counts un-closed ``` blocks to know if we're "inside" code.
_FENCE_RE = re.compile(r"^(?:```|~~~)", re.MULTILINE)
# A safe boundary is a blank line that is NOT inside an open fence.
_PARA_BREAK = "\n\n"


@dataclass
class _MarkdownStreamState:
    """Mirror of CC's MarkdownStreamState (research/mink/09-cc-tui.md)."""

    buffer: str = ""
    rendered_chars: int = 0  # WHY: lets callers diff what's been emitted.

    def open_fence(self) -> bool:  # True iff buffer ends inside an open ``` fence.
        return len(_FENCE_RE.findall(self.buffer)) % 2 == 1


class MarkdownStream:
    """Incremental markdown renderer; flushes only at safe block boundaries.

    Mirrors CC's MarkdownStreamState: buffer chunks until a paragraph break
    appears OUTSIDE any open fence, then render via ``rich.markdown.Markdown``.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        width: int | None = None,
        highlight_code: bool = True,
    ) -> None:
        """Initialize.

        Args:
            stream: Output stream; defaults to stdout.
            width: Console width; auto-detects when None.
            highlight_code: When True and pygments is installed, fenced code
                blocks are colourised via ``Terminal256Formatter``. Falls
                back to plain text when pygments is missing or the lexer
                name is unknown.
        """
        self._stream = stream or sys.stdout
        # WHY: when the ``mink`` extra (rich) is not installed we still need
        # an importable, instantiable ``MarkdownStream`` so the rest of the
        # module loads. ``_console`` stays ``None`` and ``_render`` falls
        # back to writing plain text via ``self._stream.write``.
        # WHY (pyright): use the typed ``_ConsoleClass`` alias rather than
        # the runtime ``Console`` name. ``_ConsoleClass`` is declared as
        # ``type[Console] | None`` so the ``is not None`` narrowing here
        # eliminates ``reportOptionalCall`` on the constructor call below.
        self._console: _RichConsole | None
        if _ConsoleClass is not None:
            self._console = _ConsoleClass(file=self._stream, force_terminal=True, width=width)
        else:
            self._console = None
        self._state = _MarkdownStreamState()
        self._do_highlight = highlight_code and _PYGMENTS_AVAILABLE

    @staticmethod
    def _split_fenced(text: str) -> list[tuple[str, str]]:
        """Split ``text`` into ``(kind, body)`` chunks.

        ``kind`` is ``"text"`` for prose or the lexer name for fenced blocks
        (empty string when no language tag is provided).
        """
        chunks: list[tuple[str, str]] = []
        lines = text.splitlines(keepends=True)
        i = 0
        buf: list[str] = []
        while i < len(lines):
            line = lines[i]
            stripped = line.lstrip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                if buf:
                    chunks.append(("text", "".join(buf)))
                    buf = []
                lang = stripped[3:].strip()
                fence = stripped[:3]
                i += 1
                code: list[str] = []
                while i < len(lines) and not lines[i].lstrip().startswith(fence):
                    code.append(lines[i])
                    i += 1
                chunks.append((lang, "".join(code)))
                if i < len(lines):
                    i += 1  # skip closing fence
                continue
            buf.append(line)
            i += 1
        if buf:
            chunks.append(("text", "".join(buf)))
        return chunks

    @staticmethod
    def _highlight_code(code: str, lang: str) -> str:
        """Return ANSI-coloured ``code`` or the plain string on fallback."""
        if not _PYGMENTS_AVAILABLE or not lang:
            return code
        # WHY (pyright): the imports above bind these symbols to ``None``
        # in the ImportError branch so module load doesn't crash; the
        # ``_PYGMENTS_AVAILABLE`` guard above proves they're real here,
        # but pyright can't follow that flag, so assert + assignment to
        # local names re-narrows for the call sites below.
        assert get_lexer_by_name is not None
        assert Terminal256Formatter is not None
        assert highlight is not None
        try:
            lexer = get_lexer_by_name(lang)
        except ClassNotFound:
            return code
        formatter = Terminal256Formatter()
        return str(highlight(code, lexer, formatter))

    def push(self, chunk: str) -> None:
        """Append ``chunk`` and flush every complete block in the buffer."""
        self._state.buffer += chunk
        # WHY: a blank line inside a fence is content, not a divider.
        while not self._state.open_fence() and _PARA_BREAK in self._state.buffer:
            head, _, tail = self._state.buffer.partition(_PARA_BREAK)
            self._render(head + "\n")
            self._state.buffer = tail

    def flush(self) -> None:
        """Render and clear any remaining buffered text."""
        if self._state.buffer:
            self._render(self._state.buffer)
            self._state.buffer = ""

    def reset(self) -> None:
        """Drop buffered content without rendering it."""
        self._state = _MarkdownStreamState()

    def _render(self, text: str) -> None:
        if not text.strip():
            self._stream.write(text)
            self._stream.flush()
            return
        # WHY: rich missing -> degrade to plain text + (optional) pygments
        # highlighting. We still split fences so syntax highlighting kicks
        # in for code blocks, but prose lines emit verbatim.
        if self._console is None:
            if self._do_highlight and ("```" in text or "~~~" in text):
                for kind, body in self._split_fenced(text):
                    if kind == "text":
                        self._stream.write(body)
                    else:
                        self._stream.write(self._highlight_code(body, kind))
            else:
                self._stream.write(text)
            self._stream.flush()
            self._state.rendered_chars += len(text)
            return
        # WHY (pyright): the ``_RICH_AVAILABLE`` flag is what kept ``Markdown``
        # from being ``None`` here, but we just confirmed ``self._console``
        # is non-None which proves rich loaded. Assert to re-narrow.
        assert Markdown is not None
        if self._do_highlight and ("```" in text or "~~~" in text):
            for kind, body in self._split_fenced(text):
                if kind == "text":
                    if body.strip():
                        self._console.print(Markdown(body), end="")
                    else:
                        self._stream.write(body)
                else:
                    self._stream.write(self._highlight_code(body, kind))
            self._stream.flush()
            self._state.rendered_chars += len(text)
            return
        self._console.print(Markdown(text), end="")
        self._state.rendered_chars += len(text)


# 10-frame braille spinner -- same glyph set as CC's rusty-claude-cli.
_FRAMES: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class Spinner:
    """Caller-driven braille spinner (no background threads)."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout
        self._frame = 0
        self._message = ""
        self._active = False

    def start(self, message: str) -> None:
        """Begin spinning with ``message`` to the right of the glyph."""
        self._message = message
        self._frame = 0
        self._active = True
        self._stream.write(f"\r{_FRAMES[0]} {message}")
        self._stream.flush()

    def tick(self) -> None:
        """Advance one frame, overwriting the previous line in place."""
        if not self._active:
            return
        self._frame = (self._frame + 1) % len(_FRAMES)
        self._stream.write(f"\r{_FRAMES[self._frame]} {self._message}")
        self._stream.flush()

    def stop(self, success: bool = True) -> None:
        """Finalize the line with a ``+`` or ``x`` marker."""
        if not self._active:
            return
        marker = "+" if success else "x"
        # WHY: \x1b[2K clears the line so a long spinner doesn't leak chars.
        self._stream.write(f"\r\x1b[2K{marker} {self._message}\n")
        self._stream.flush()
        self._active = False


@dataclass
class _Style:
    """ANSI escape helpers, one place for every code used by the tool block."""

    reset: str = "\x1b[0m"
    dim: str = "\x1b[2m"
    bold: str = "\x1b[1m"
    fg_red_203: str = "\x1b[38;5;203m"
    bg_236: str = "\x1b[48;5;236m"
    fg_green: str = "\x1b[38;5;42m"

    def wrap(self, text: str, *codes: str) -> str: return "".join(codes) + text + self.reset  # noqa: E704


_STYLE = _Style()
_TRUNC_LIMIT = 2000


class ToolBlockRenderer:
    """Render a tool call as a collapsed line plus a styled result block."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout

    def render_call(self, name: str, args: dict[str, Any] | str) -> None:
        """Write the collapsed header line: ``> name(short_args)``."""
        short = self._short_args(args)
        line = f"▶ {_STYLE.wrap(name, _STYLE.bold)}({_STYLE.wrap(short, _STYLE.dim)})\n"
        self._stream.write(line)
        self._stream.flush()

    def render_result(
        self,
        name: str,
        output: str | dict[str, Any],
        is_error: bool = False,
        exit_code: int | None = None,
    ) -> None:
        """Write the indented result block.

        Args:
            name: Tool name; ``edit`` gets a unified diff, ``bash`` a bg tint.
            output: Raw text or a structured dict (Edit returns a dict).
            is_error: Body lines render in color 203 (red) when True.
            exit_code: Appended as a trailing ``exit N`` line when set.
        """
        if name.lower() == "edit" and isinstance(output, dict):
            body = self._edit_diff(output)
        elif isinstance(output, dict):
            body = json.dumps(output, indent=2)
        else:
            body = output

        body, footer = self._truncate(body)
        is_bash = name.lower() == "bash"

        for raw_line in body.splitlines() or [""]:
            styled = raw_line
            if is_error:
                styled = _STYLE.wrap(styled, _STYLE.fg_red_203)
            if is_bash:
                # WHY: wrap bg AFTER fg so the tint covers the leading indent.
                styled = _STYLE.wrap("  " + styled, _STYLE.bg_236)
            else:
                styled = "  " + styled
            self._stream.write(styled + "\n")

        if footer:
            self._stream.write(f"  {_STYLE.wrap(footer, _STYLE.dim)}\n")
        if exit_code is not None:
            tag = f"exit {exit_code}"
            color = _STYLE.fg_red_203 if exit_code != 0 else _STYLE.fg_green
            self._stream.write(f"  {_STYLE.wrap(tag, color)}\n")
        self._stream.flush()

    @staticmethod
    def _short_args(args: dict[str, Any] | str) -> str:
        if isinstance(args, str):
            s = args
        else:
            # WHY: prefer the most readable single field over raw json blob.
            for key in ("command", "file_path", "path", "query", "url"):
                if key in args:
                    s = str(args[key])
                    break
            else:
                s = json.dumps(args, separators=(",", ":"))
        s = s.replace("\n", " ")
        return s if len(s) <= 60 else s[:57] + "..."

    @staticmethod
    def _truncate(body: str) -> tuple[str, str]:
        if len(body) <= _TRUNC_LIMIT:
            return body, ""
        return body[:_TRUNC_LIMIT], f"({len(body) - _TRUNC_LIMIT} more bytes)"

    @staticmethod
    def _edit_diff(result: dict[str, Any]) -> str:
        old = result.get("old_string") or result.get("old") or ""
        new = result.get("new_string") or result.get("new") or ""
        if not old and not new:
            return json.dumps(result, indent=2)
        path = result.get("file_path") or result.get("path") or "edit"
        # WHY: delegate to DiffRenderer so Edit results render with the same
        # ANSI palette as standalone diffs printed via DiffRenderer.print().
        return DiffRenderer().format(old, new, path)


_THINK_PATTERNS: tuple[str, ...] = (
    r"<thinking>(.*?)</thinking>",
    r"<think>(.*?)</think>",
)


class ThinkingBlockRenderer:
    """Strip ``<thinking>``/``<think>`` blocks from a stream and stash them.

    ``feed(text)`` returns the text with thinking blocks removed and emits a
    collapsed marker line per block. ``expand(index=-1)`` re-renders the
    captured body for inspection. ``captured`` exposes the full list.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        patterns: tuple[str, ...] = _THINK_PATTERNS,
    ) -> None:
        self._stream = stream or sys.stdout
        self._regexes = [re.compile(p, re.DOTALL | re.IGNORECASE) for p in patterns]
        self._captured: list[str] = []

    @property
    def captured(self) -> list[str]:
        """All thinking blocks fed so far, in insertion order."""
        return list(self._captured)

    def feed(self, text: str) -> str:
        """Strip thinking blocks from ``text`` and print a collapsed marker.

        Returns the cleaned text (safe to forward to MarkdownStream).
        """
        cleaned = text
        for rx in self._regexes:
            def _swap(match: re.Match[str]) -> str:
                body = match.group(1)
                self._captured.append(body)
                marker = f"▶ Thinking ({len(body)} chars hidden)\n"
                self._stream.write(_STYLE.wrap(marker, _STYLE.dim))
                self._stream.flush()
                return ""
            cleaned = rx.sub(_swap, cleaned)
        return cleaned

    def expand(self, index: int = -1) -> str:
        """Print and return the captured block at ``index`` (default: last)."""
        if not self._captured:
            return ""
        body = self._captured[index]
        header = "▼ Thinking (expanded)\n"
        self._stream.write(_STYLE.wrap(header, _STYLE.dim))
        for line in body.splitlines() or [""]:
            self._stream.write(f"  {line}\n")
        self._stream.flush()
        return body


class DiffRenderer:
    """Render unified diffs with an ANSI palette compatible with CC.

    Colour map:
        ``+`` lines  -> green (color 42)
        ``-`` lines  -> red   (color 203)
        ``@@`` hunks -> cyan  (color 51)
        ``+++/---``  -> bold

    ``format(old, new, path)`` returns the styled string; ``print(...)``
    writes it to the configured stream.
    """

    _FG_GREEN_42 = "\x1b[38;5;42m"
    _FG_RED_203 = "\x1b[38;5;203m"
    _FG_CYAN_51 = "\x1b[38;5;51m"
    _BOLD = "\x1b[1m"
    _RESET = "\x1b[0m"

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout

    def format(self, old: str, new: str, path: str = "file") -> str:
        """Return a coloured unified diff between ``old`` and ``new``."""
        diff = difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=2,
        )
        out: list[str] = []
        for line in diff:
            stripped = line.rstrip("\n")
            if stripped.startswith("+++") or stripped.startswith("---"):
                out.append(f"{self._BOLD}{stripped}{self._RESET}")
            elif stripped.startswith("@@"):
                out.append(f"{self._FG_CYAN_51}{stripped}{self._RESET}")
            elif stripped.startswith("+"):
                out.append(f"{self._FG_GREEN_42}{stripped}{self._RESET}")
            elif stripped.startswith("-"):
                out.append(f"{self._FG_RED_203}{stripped}{self._RESET}")
            else:
                out.append(stripped)
        return "\n".join(out)

    def print(self, old: str, new: str, path: str = "file") -> None:
        """Format and write the diff to the renderer's stream."""
        text = self.format(old, new, path)
        if text:
            self._stream.write(text + "\n")
            self._stream.flush()


# ---------------------------------------------------------------------------
# StreamHandler adapter (audit B-2, B-7, B-8)
# ---------------------------------------------------------------------------
#
# WHY: every primitive above is import-clean and unit-tested but no production
# code path constructs it. The adapter below glues MarkdownStream / Spinner /
# ToolBlockRenderer / ThinkingBlockRenderer to the existing
# ``chimera.streaming.base.StreamHandler`` ABC so the live REPL and one-shot
# `--print` mode can swap a `ConsoleStreamHandler()` for a
# `RichStreamHandler()` with no other call-site changes.


class RichStreamHandler(StreamHandler):
    """StreamHandler adapter that routes events through ``render.py`` polish.

    Subclasses ``chimera.streaming.base.StreamHandler`` so it drops into the
    same ``LoopConfig.handler`` slot the legacy ``ConsoleStreamHandler`` uses.

    Behavior per hook:

    * ``on_text``:  push delta into :class:`MarkdownStream` for incremental
      markdown rendering. Thinking blocks are stripped first so they don't
      leak into the rendered prose.
    * ``on_tool_start``:  stop any active spinner, render the collapsed
      ``▶ name(short_args)`` header via :class:`ToolBlockRenderer`.
    * ``on_tool_end``:  render the indented result body.
    * ``on_step_start``:  start the spinner with ``"thinking"``.
    * ``on_step_end``:  stop the spinner with success.
    * ``on_done``:  flush any buffered markdown so the user sees it before
      the next prompt.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        """Initialize all child renderers against the same stream."""
        self._stream = stream or sys.stdout
        self._md = MarkdownStream(stream=self._stream)
        self._spinner = Spinner(stream=self._stream)
        self._tools = ToolBlockRenderer(stream=self._stream)
        self._thinking = ThinkingBlockRenderer(stream=self._stream)
        # Map ``call_id`` -> tool_name so on_tool_end can pick the right
        # render path (bash gets a tinted block, edit gets a diff, etc.).
        self._call_names: dict[str, str] = {}
        # Cache the most recent tool args so on_tool_end can render an Edit
        # diff even when the StreamHandler ABC delivers only the output.
        self._call_args: dict[str, dict[str, Any] | str] = {}

    # -- StreamHandler hooks --------------------------------------------------

    def on_text(self, text: str) -> None:
        """Filter ``<thinking>`` blocks then push markdown into MarkdownStream."""
        cleaned = self._thinking.feed(text)
        if cleaned:
            self._md.push(cleaned)

    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        """Stop the spinner, then print the collapsed tool header."""
        self._md.flush()
        self._spinner.stop(success=True)
        self._call_names[call_id] = tool_name
        # WHY: the StreamHandler ABC doesn't deliver tool args to on_tool_start;
        # render with an empty arg view so the header still prints. Callers
        # that want richer headers can subclass and override.
        self._tools.render_call(tool_name, "")

    def on_tool_end(self, call_id: str, output: str) -> None:
        """Render the result block under the matching tool header."""
        name = self._call_names.pop(call_id, "tool")
        args = self._call_args.pop(call_id, None)
        # WHY: Edit results carry old/new strings via the call args, not the
        # output string. When we have them cached we synthesize the dict
        # ToolBlockRenderer expects so the diff palette engages.
        if name.lower() == "edit" and isinstance(args, dict):
            payload: dict[str, Any] = dict(args)
            payload.setdefault("file_path", args.get("file_path") or "edit")
            self._tools.render_result(name, payload)
            return
        self._tools.render_result(name, output)

    def on_step_start(self, step: int) -> None:
        """Start the spinner with a generic ``thinking`` label."""
        # WHY: ``step`` is required by the StreamHandler interface but the
        # spinner only needs a generic label here; ``del`` documents the
        # intentional discard and silences ARG002 / unused-parameter warnings.
        del step
        self._spinner.start("thinking")

    def on_step_end(self, step: int) -> None:
        """Stop the spinner; the next on_text/on_tool_start handles the rest."""
        del step
        self._spinner.stop(success=True)

    def on_done(self) -> None:
        """Flush MarkdownStream so trailing content doesn't get stuck."""
        self._md.flush()
        self._stream.write("\n")
        self._stream.flush()

    # -- Extension hooks honored by AgentLoop when present --------------------

    def on_thinking_delta(self, text: str) -> None:
        """Optional hook: route explicit thinking deltas through the collapser."""
        self._thinking.feed(text)


# Public alias retained as ``MinkStreamHandler`` for callers that prefer the
# product-named import. Both names refer to the exact same adapter so a
# single class is shipped (and tested) but two imports are valid.
MinkStreamHandler = RichStreamHandler


def _is_tty(stream: TextIO | None) -> bool:
    """Return True iff ``stream`` looks like a real terminal.

    Falls back to checking ``sys.stdout``. Wrapped streams that lack
    ``isatty`` (e.g. ``io.StringIO``) report False.
    """
    target = stream if stream is not None else sys.stdout
    isatty = getattr(target, "isatty", None)
    if isatty is None:
        return False
    try:
        return bool(isatty())
    except (ValueError, OSError):
        return False


def _no_color_env() -> bool:
    """Honor the ``NO_COLOR`` env-var convention (https://no-color.org)."""
    val = os.environ.get("NO_COLOR")
    return val is not None and val != ""


def build_stream_handler(
    *,
    stream: TextIO | None = None,
    no_color: bool = False,
    force_rich: bool = False,
) -> StreamHandler:
    """Pick the right :class:`StreamHandler` for the current environment.

    Plain-text :class:`ConsoleStreamHandler` is returned when:

    * the optional ``mink`` extra (``rich``) is not installed, OR
    * ``no_color=True`` (the caller passed ``--no-color``), OR
    * the standard ``NO_COLOR`` env var is set, OR
    * ``stream`` (or ``sys.stdout`` when ``stream`` is None) is not a TTY.

    Otherwise a :class:`MinkStreamHandler` is returned for the rich
    Markdown / Spinner / ToolBlock / Diff polished view.

    Args:
        stream: Output stream candidate; defaults to ``sys.stdout``.
        no_color: Force the plain-text handler regardless of TTY/env.
        force_rich: Skip TTY/env checks and always return a
            :class:`MinkStreamHandler`. Useful for tests and when the
            caller knows it is writing to a terminal-like sink. Still
            falls back to :class:`ConsoleStreamHandler` when ``rich``
            is not importable.

    Returns:
        A ready-to-use :class:`StreamHandler` instance.
    """
    # WHY: the polished MinkStreamHandler requires ``rich``; without it the
    # adapter still constructs but can't print Markdown, so route to the
    # plain handler unconditionally.
    if not _RICH_AVAILABLE:
        return ConsoleStreamHandler()
    if force_rich:
        return MinkStreamHandler(stream=stream)
    if no_color or _no_color_env() or not _is_tty(stream):
        return ConsoleStreamHandler()
    return MinkStreamHandler(stream=stream)


def _demo() -> None:
    """Visual smoke test for `python chimera/cli/render.py`."""
    out = sys.stdout
    md = MarkdownStream(stream=out)
    md.push("Hello **world**\n\n")
    md.flush()

    sp = Spinner(stream=out)
    sp.start("Thinking")
    for _ in range(5):
        sp.tick()
    sp.stop(success=True)

    tb = ToolBlockRenderer(stream=out)
    tb.render_call("bash", {"command": "echo hi && ls /tmp | head -2"})
    tb.render_result("bash", "hi\nfile_a.txt\nfile_b.txt", exit_code=0)

    # Thinking block: collapsed first, then expanded.
    out.write("\n-- thinking --\n")
    th = ThinkingBlockRenderer(stream=out)
    cleaned = th.feed("Prefix <thinking>weighing two refactors</thinking> suffix.\n")
    out.write(f"cleaned: {cleaned}")
    th.expand()

    # Pygments-highlighted python fence.
    out.write("\n-- code fence --\n")
    md2 = MarkdownStream(stream=out)
    md2.push("```python\ndef greet(name: str) -> str:\n    return f'hi {name}'\n```\n\n")
    md2.flush()

    # Standalone 5-line diff.
    out.write("\n-- diff --\n")
    dr = DiffRenderer(stream=out)
    old = "alpha\nbeta\ngamma\ndelta\nepsilon\n"
    new = "alpha\nBETA\ngamma\nDELTA\nepsilon\n"
    dr.print(old, new, path="greek.txt")

    # Edit tool block: body should auto-render as a coloured diff.
    out.write("\n-- edit tool block --\n")
    tb.render_call("edit", {"file_path": "greek.txt"})
    tb.render_result(
        "edit",
        {"file_path": "greek.txt", "old_string": old, "new_string": new},
    )


if __name__ == "__main__":
    _demo()  # pragma: no cover
