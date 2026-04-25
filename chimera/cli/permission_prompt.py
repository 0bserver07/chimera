"""Interactive single-keystroke permission prompt for the Chimera REPL.

Surfaced when the permission checker returns ``ASK``. Stdlib-only so the
REPL core stays lean. Keys: ``a`` approve once, ``A`` always allow,
``d`` deny once, ``D`` always deny, ``c`` cancel turn, ``?`` help.
``A``/``D`` persist a rule into ``~/.claude/settings.local.json``. When
stdin is not a TTY we degrade to ``readline()`` so tests and pipes work.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO

from chimera.permissions.base import PermissionPolicy
from chimera.permissions.decisions import DecisionReason, PermissionDecision
from chimera.permissions.risk import RiskLevel, classify_risk
from chimera.permissions.rules import PermissionBehavior

__all__ = [
    "InteractivePermissionPrompt",
    "PermissionCancelled",
    "PermissionRequest",
]


class PermissionCancelled(Exception):
    """Raised when the user presses ``c`` at the permission prompt.

    The REPL loop is expected to catch this exception and abort the
    current turn cleanly without retrying.
    """


@dataclass
class PermissionRequest:
    """Inputs surfaced to the interactive prompt.

    Attributes:
        tool_name:       Tool being gated.
        input_args:      Argument dict the agent proposed.
        risk:            Pre-computed risk; falls back to classify_risk.
        reason:          Why the prompt fired (e.g. "matches Bash(rm:*)").
        rule_suggestion: Rule-string for A/D persistence; auto-derived if None.
    """

    tool_name: str
    input_args: dict[str, Any] = field(default_factory=dict)
    risk: RiskLevel | None = None
    reason: str = ""
    rule_suggestion: str | None = None


_HELP_LINES: tuple[str, ...] = (
    "[a] Approve once   [A] Always allow this command",
    "[d] Deny once      [D] Always deny this command",
    "[c] Cancel turn    [?] Help",
)

_VALID_KEYS = frozenset({"a", "A", "d", "D", "c", "?"})


def _default_settings_path() -> Path:
    """Resolve the user-scope settings file.

    Honors ``CHIMERA_MINK_SETTINGS_PATH`` (and the deprecated
    ``CHIMERA_CC_SETTINGS_PATH`` alias) so tests can redirect writes.
    """
    override = os.environ.get("CHIMERA_MINK_SETTINGS_PATH")
    if override:
        return Path(override)
    legacy = os.environ.get("CHIMERA_CC_SETTINGS_PATH")
    if legacy:
        # Quiet fallback so existing tests using the old var keep working.
        return Path(legacy)
    return Path.home() / ".claude" / "settings.local.json"


def _persist_rule(rule: str, *, behavior: PermissionBehavior, path: Path | None = None) -> Path:
    """Append ``rule`` to the ``allow`` or ``deny`` list in settings.

    Atomic write via ``os.replace``; missing/corrupt files become ``{}``.

    Args:
        rule:     CC rule string such as ``"Bash(rm:*)"``.
        behavior: ``ALLOW`` or ``DENY``.
        path:     Override settings location.

    Returns:
        Path written to.
    """
    target = path or _default_settings_path()
    data: dict[str, Any] = {}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    perms = data.setdefault("permissions", {})
    bucket_key = "allow" if behavior is PermissionBehavior.ALLOW else "deny"
    bucket = perms.setdefault(bucket_key, [])
    if not isinstance(bucket, list):
        bucket = []
        perms[bucket_key] = bucket
    if rule not in bucket:
        bucket.append(rule)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def _suggest_rule(req: PermissionRequest) -> str:
    """Build a CC-style rule string when caller did not supply one."""
    if req.rule_suggestion:
        return req.rule_suggestion
    for key in ("command", "file_path", "path", "url", "pattern"):
        val = req.input_args.get(key)
        if isinstance(val, str) and val:
            head = val.strip().split()[0] if key == "command" else val
            return f"{req.tool_name}({key}:{head}*)"
    return req.tool_name


def _truncate(value: Any, limit: int = 200) -> str:
    """Render ``value`` with an ellipsis if longer than ``limit`` chars."""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


# --- single-keystroke reader ------------------------------------------------


def _read_keystroke(stream: TextIO) -> str:
    """Read one keystroke; degrade to ``readline()`` for non-TTY streams.

    POSIX TTYs use ``termios`` cbreak; Windows uses ``msvcrt.getwch``.
    Returns "" on EOF.
    """
    try:
        is_tty = stream.isatty()
    except (AttributeError, ValueError):
        is_tty = False
    if not is_tty:
        try:
            line = stream.readline()
        except (EOFError, KeyboardInterrupt):
            return ""
        return line[:1] if line else ""
    if sys.platform == "win32":  # pragma: no cover - platform branch
        import msvcrt

        return msvcrt.getwch()
    import termios
    import tty

    fd = stream.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return stream.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


# --- main policy ------------------------------------------------------------


class InteractivePermissionPrompt(PermissionPolicy):
    """Render a permission panel and read one keystroke.

    Subclasses :class:`PermissionPolicy` so it can sit on a ``LoopConfig``
    directly. ``evaluate`` delegates to :meth:`prompt` and projects the
    resulting :class:`PermissionDecision` back to a ``PermissionAction``.

    Args:
        input_stream:     File for keystroke input (defaults to stdin).
        output_stream:    File for panel output (defaults to stderr).
        settings_path:    Override path for persisted A/D rules.
        keystroke_reader: Test injection point.
    """

    def __init__(
        self,
        *,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        settings_path: Path | None = None,
        keystroke_reader: Callable[[TextIO], str] | None = None,
    ) -> None:
        self._in = input_stream or sys.stdin
        self._out = output_stream or sys.stderr
        self._settings_path = settings_path
        self._reader = keystroke_reader or _read_keystroke

    # -- PermissionPolicy plumbing ------------------------------------------

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Bridge the legacy :class:`PermissionPolicy` ABC."""
        from chimera.permissions.base import PermissionAction

        decision = self.prompt(PermissionRequest(tool_name=tool_name, input_args=args))
        if decision.behavior is PermissionBehavior.ALLOW:
            return PermissionAction.ALLOW
        if decision.behavior is PermissionBehavior.DENY:
            return PermissionAction.DENY
        return PermissionAction.ASK

    # -- public API ---------------------------------------------------------

    def prompt(self, request: PermissionRequest) -> PermissionDecision:
        """Display the panel and block on one keystroke.

        Args:
            request: The permission request being adjudicated.

        Returns:
            A :class:`PermissionDecision` whose ``behavior`` is ALLOW or
            DENY. ``reason.detail`` carries the persisted rule for A/D.

        Raises:
            PermissionCancelled: When the user presses ``c``.
        """
        risk = request.risk or classify_risk(request.tool_name, request.input_args)[0]
        self._render_panel(request, risk)
        while True:
            key = self._reader(self._in)
            if not key:
                # EOF / piped input exhausted -> fail closed.
                self._out.write("\n[no input] denying once\n")
                self._out.flush()
                return PermissionDecision.deny("No input available")
            if key == "\x03":  # Ctrl-C
                raise PermissionCancelled("Ctrl-C at permission prompt")
            if key not in _VALID_KEYS:
                self._out.write(f"\nUnknown key {key!r}; press one of a/A/d/D/c or ? for help\n")
                self._out.flush()
                continue
            if key == "?":
                self._render_help()
                continue
            return self._handle_key(key, request)

    def _render_panel(self, req: PermissionRequest, risk: RiskLevel) -> None:
        title = " Permission required "
        bar = "-" * 15
        self._out.write(f"\n{bar}{title}{bar}\n")
        self._out.write(f"Tool:   {req.tool_name}\n")
        for key in ("command", "file_path", "path", "url", "pattern"):
            if key in req.input_args:
                self._out.write(f"Input:  {_truncate(req.input_args[key])}\n")
                break
        else:
            if req.input_args:
                self._out.write(f"Input:  {_truncate(req.input_args)}\n")
        self._out.write(f"Risk:   {risk.value}\n")
        if req.reason:
            self._out.write(f"Reason: {req.reason}\n")
        for line in _HELP_LINES:
            self._out.write(line + "\n")
        self._out.write("-" * (len(title) + len(bar) * 2) + "\n> ")
        self._out.flush()

    def _render_help(self) -> None:
        self._out.write("\n")
        for line in _HELP_LINES:
            self._out.write(line + "\n")
        self._out.write("> ")
        self._out.flush()

    def _handle_key(self, key: str, req: PermissionRequest) -> PermissionDecision:
        if key == "a":
            self._out.write("approved (once)\n")
            self._out.flush()
            return PermissionDecision.allow("User approved once")
        if key == "d":
            self._out.write("denied (once)\n")
            self._out.flush()
            return PermissionDecision.deny("User denied once")
        if key == "c":
            self._out.write("cancelled\n")
            self._out.flush()
            raise PermissionCancelled("User pressed cancel at permission prompt")
        if key in ("A", "D"):
            behavior = PermissionBehavior.ALLOW if key == "A" else PermissionBehavior.DENY
            rule = _suggest_rule(req)
            try:
                target = _persist_rule(rule, behavior=behavior, path=self._settings_path)
                self._out.write(f"persisted {behavior.value}: {rule} -> {target}\n")
                self._out.flush()
            except OSError as exc:
                self._out.write(f"failed to persist rule ({exc}); applying for this turn only\n")
                self._out.flush()
            if behavior is PermissionBehavior.ALLOW:
                return PermissionDecision.allow(f"Always allow: {rule}", reason=DecisionReason.rule(rule))
            return PermissionDecision.deny(f"Always deny: {rule}", reason=DecisionReason.rule(rule))
        # Defensive: _VALID_KEYS already filtered, so this is unreachable.
        return PermissionDecision.deny("Unhandled key")  # pragma: no cover
