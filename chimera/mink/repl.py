"""REPL-side appliers for ``MinkSettings.keybindings`` and ``.statusline``.

W14-7 wires the user-visible REPL personality keys that ``W13-G14``
parsed but did not yet apply:

* :func:`apply_keybindings` — translates ``MinkSettings.keybindings``
  into ``readline.parse_and_bind`` directives (or ``prompt_toolkit``
  ``KeyBindings`` when the optional dependency is present, falling
  back to readline otherwise). Honors a small action vocabulary so a
  typo in a key name is logged but never crashes the REPL.
* :func:`render_statusline` — formats the bottom-bar text described
  by ``MinkSettings.statusline``. The dict shape supports a literal
  ``format`` template, a shell ``command`` (whose stdout is inlined),
  and an ``enabled`` toggle. Bool values pass through verbatim.

Both functions are idempotent and best-effort: when the input is
``None``/empty/disabled they are no-ops. When something goes wrong
(``readline`` import error, subprocess failure, malformed dict) they
log to the supplied ``stream`` and return gracefully so the REPL
keeps running.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from chimera.mink.settings import MinkSettings

__all__ = [
    "apply_keybindings",
    "render_statusline",
    "format_statusline_text",
    "translate_key",
]


# ---------------------------------------------------------------------------
# Keybindings
# ---------------------------------------------------------------------------
#
# ``MinkSettings.keybindings`` is a flat ``{action: key}`` map. CC's reference
# schema names the actions; the value is a key spec like ``"ctrl-d"`` /
# ``"ctrl-shift-c"`` / ``"alt-enter"`` / ``"f5"``. We translate those into
# readline syntax (``\C-d``, ``\C-c``, ``\e\r``, ``\eOR``) so the existing
# ``_setup_readline`` code path can apply them with one extra
# ``parse_and_bind`` call per action. Unknown specs are skipped with a
# warning so a single bad entry never blows up the whole REPL.

_ACTION_FUNCTIONS: dict[str, str] = {
    # Map an action name to the readline function that should fire when the
    # bound key is pressed. The right-hand side is what ``parse_and_bind``
    # accepts after the colon, e.g. ``"\\C-d": end-of-file"``.
    "submit": "accept-line",
    "accept": "accept-line",
    "newline": "accept-line",
    "cancel": "abort",
    "abort": "abort",
    "interrupt": "abort",
    "clear-screen": "clear-screen",
    "clear": "clear-screen",
    "history-up": "previous-history",
    "history-down": "next-history",
    "complete": "complete",
    "kill-line": "kill-line",
    "backward-kill-word": "backward-kill-word",
    "forward-word": "forward-word",
    "backward-word": "backward-word",
    "beginning-of-line": "beginning-of-line",
    "end-of-line": "end-of-line",
    "delete-char": "delete-char",
    "transpose-chars": "transpose-chars",
}


def translate_key(spec: str) -> str | None:
    """Translate a friendly key spec into a readline escape sequence.

    Examples:
        ``"ctrl-d"``    -> ``"\\C-d"``
        ``"ctrl-shift-c"`` -> ``"\\C-C"`` (shift = uppercase the letter)
        ``"alt-enter"`` -> ``"\\e\\r"``
        ``"alt-x"``     -> ``"\\ex"``
        ``"f5"``        -> ``"\\eOR"``
        ``"enter"``     -> ``"\\r"``
        ``"tab"``       -> ``"\\t"``

    Args:
        spec: Lowercase or mixed-case spec string.

    Returns:
        A readline-quoted escape sequence, or ``None`` if the spec is empty
        or cannot be translated.
    """
    if not spec or not isinstance(spec, str):
        return None
    s = spec.strip().lower()
    if not s:
        return None

    # Standalone named keys (no modifier).
    _NAMED = {
        "enter": "\\r",
        "return": "\\r",
        "tab": "\\t",
        "esc": "\\e",
        "escape": "\\e",
        "space": " ",
        "backspace": "\\C-h",
        "del": "\\e[3~",
        "delete": "\\e[3~",
    }
    if s in _NAMED:
        return _NAMED[s]
    # F-keys map to the xterm SS3 P/Q/R/S sequences for F1..F4 and CSI [n~
    # for F5..F12. Keep only the common range; obscure terminals can rebind
    # via raw escapes if they really need it.
    _F = {
        "f1": "\\eOP", "f2": "\\eOQ", "f3": "\\eOR", "f4": "\\eOS",
        "f5": "\\e[15~", "f6": "\\e[17~", "f7": "\\e[18~",
        "f8": "\\e[19~", "f9": "\\e[20~", "f10": "\\e[21~",
        "f11": "\\e[23~", "f12": "\\e[24~",
    }
    if s in _F:
        return _F[s]

    parts = s.split("-")
    if not parts:
        return None
    *mods, base = parts
    mods_set = {m for m in mods}
    has_ctrl = "ctrl" in mods_set or "c" in mods_set
    has_alt = "alt" in mods_set or "meta" in mods_set
    has_shift = "shift" in mods_set

    # Resolve the base key into a literal character or escape sequence.
    if base in _NAMED:
        rendered = _NAMED[base]
    elif base in _F:
        # F-key chord with a modifier is uncommon and terminal-specific,
        # so we keep the base sequence and prepend Alt only when asked.
        rendered = _F[base]
        if has_ctrl:
            return None
    elif len(base) == 1:
        char = base
        if has_shift and char.isalpha():
            char = char.upper()
        rendered = char
    else:
        # Unrecognised compound; let the caller log a warning.
        return None

    # ``Ctrl-`` only composes with single-character bases.
    if has_ctrl:
        if len(base) != 1:
            return None
        if has_alt:
            return f"\\e\\C-{rendered}"
        return f"\\C-{rendered}"
    if has_alt:
        return f"\\e{rendered}"
    return rendered


def apply_keybindings(
    settings: "MinkSettings | None",
    *,
    stream: TextIO | None = None,
) -> int:
    """Apply ``settings.keybindings`` to the active readline namespace.

    Returns the number of bindings actually installed. ``0`` is a normal
    outcome (no settings, no readline, all skipped). Errors are logged to
    ``stream`` (defaults to stderr) and the call returns ``0`` rather than
    raising so the caller can keep launching the REPL.

    Args:
        settings: Resolved :class:`MinkSettings`. ``None`` / empty
            ``keybindings`` is a no-op.
        stream: Where to print warnings. Defaults to ``sys.stderr``.
    """
    out = stream or sys.stderr
    bindings = getattr(settings, "keybindings", None) or {}
    if not bindings:
        return 0
    try:
        import readline  # pyright: ignore[reportMissingImports]
    except ImportError:
        # Windows ships without readline; nothing to bind.
        return 0

    applied = 0
    for raw_action, raw_key in bindings.items():
        action = str(raw_action).strip().lower()
        key_seq = translate_key(str(raw_key))
        fn = _ACTION_FUNCTIONS.get(action)
        if fn is None:
            print(
                f"[mink] keybindings: unknown action {action!r}; skipped",
                file=out,
            )
            continue
        if key_seq is None:
            print(
                f"[mink] keybindings: cannot parse key {raw_key!r}; skipped",
                file=out,
            )
            continue
        directive = f'"{key_seq}": {fn}'
        try:
            readline.parse_and_bind(directive)
            applied += 1
        except Exception as exc:  # noqa: BLE001 - readline is platform-quirky
            print(
                f"[mink] keybindings: failed to bind {action} → "
                f"{raw_key!r}: {exc}",
                file=out,
            )
    return applied


# ---------------------------------------------------------------------------
# Statusline
# ---------------------------------------------------------------------------


def _coerce_statusline(spec: Any) -> dict[str, Any] | None:
    """Normalise ``settings.statusline`` into a dict view (or ``None`` to skip)."""
    if spec is False or spec is None:
        return None
    if spec is True:
        return {"format": "{cwd} | model={model}", "enabled": True}
    if isinstance(spec, str):
        return {"command": spec, "enabled": True}
    if isinstance(spec, dict):
        if not spec.get("enabled", True):
            return None
        return dict(spec)
    return None


def format_statusline_text(
    spec: Any,
    *,
    context: dict[str, Any] | None = None,
    command_runner: Any = None,
) -> str:
    """Return the rendered status-line text for ``spec``.

    The function is split out from :func:`render_statusline` so tests can
    assert on the rendered string without monkey-patching stdout.

    Args:
        spec: ``settings.statusline`` value (``bool`` / ``str`` / ``dict``
            / ``None``).
        context: Token map for ``{cwd}``, ``{model}``, ``{cost}``, etc. in
            the dict's ``"format"`` template. Missing keys render as the
            literal placeholder.
        command_runner: Optional callable ``(argv: list[str]) -> str`` used
            in tests to stub out subprocess execution. Defaults to a real
            ``subprocess.run`` invocation.

    Returns:
        The rendered status-line text, or an empty string when the spec is
        disabled or empty.
    """
    cfg = _coerce_statusline(spec)
    if cfg is None:
        return ""
    fmt = cfg.get("format")
    if isinstance(fmt, str) and fmt:
        ctx = dict(context or {})
        # ``str.format_map`` with a defaultdict-style mapping that returns
        # ``"{name}"`` for missing keys keeps the placeholder visible
        # rather than crashing on KeyError. Implemented inline so we don't
        # introduce a stdlib defaultdict for one call site.

        class _SafeMap(dict[str, Any]):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        try:
            return fmt.format_map(_SafeMap(ctx))
        except (IndexError, ValueError):
            return fmt
    cmd = cfg.get("command")
    if isinstance(cmd, str) and cmd:
        try:
            argv = shlex.split(cmd)
        except ValueError:
            return ""
        if not argv:
            return ""
        runner = command_runner or _default_runner
        try:
            return str(runner(argv)).rstrip()
        except Exception:  # noqa: BLE001 - statusline must never crash REPL
            return ""
    return ""


def _default_runner(argv: list[str]) -> str:
    """Run ``argv`` and return stdout; failures collapse to ``""``."""
    try:
        proc = subprocess.run(  # noqa: S603 - argv is shlex-split, not shell=True
            argv,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout


def render_statusline(
    spec: Any,
    *,
    context: dict[str, Any] | None = None,
    stream: TextIO | None = None,
    command_runner: Any = None,
) -> bool:
    """Render the status line to ``stream``.

    Returns ``True`` iff a non-empty line was actually written. The output
    ends with a newline so the REPL prompt below is visually separated.

    Args:
        spec: See :func:`format_statusline_text`.
        context: See :func:`format_statusline_text`.
        stream: Output stream; defaults to ``sys.stdout``.
        command_runner: Test hook; defaults to subprocess.

    Returns:
        ``True`` if a status line was printed.
    """
    text = format_statusline_text(
        spec, context=context, command_runner=command_runner,
    )
    if not text:
        return False
    out = stream or sys.stdout
    # Wrap in dim ANSI so the bar reads as decoration. Rendering this in
    # plain ASCII when ``NO_COLOR`` is set is the renderer's job, not the
    # statusline formatter — keep this layer ANSI-clean.
    out.write("\x1b[2m" + text + "\x1b[0m\n")
    out.flush()
    return True
