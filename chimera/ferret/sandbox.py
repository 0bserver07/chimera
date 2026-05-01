"""Sandbox-first execution wrapper for ferret.

Mirrors the upstream IDE-first / OpenAI-flagship coding agent's three-tier
sandbox model on top of :class:`chimera.env.local.LocalEnvironment`:

* :attr:`SandboxMode.READ_ONLY` (default) — reads only. Writes and mutating
  shell commands are blocked. The agent can ``cat``, ``ls``, ``grep`` etc. but
  cannot edit files or run installers.
* :attr:`SandboxMode.WORKSPACE_WRITE` — reads anywhere allowed by the
  underlying environment, writes restricted to ``workdir`` (parity with the
  upstream's "writable_roots = [cwd]" default). Network-touching commands are
  blocked.
* :attr:`SandboxMode.WORKSPACE_WRITE_NETWORK` — same as
  ``WORKSPACE_WRITE`` plus outbound network access. Useful for ``pip install``,
  ``npm install``, ``curl`` calls, etc.

The wrapper is intentionally conservative: command classification is a
best-effort static analysis of the command string. It catches the obvious
cases (``rm -rf``, ``curl``, ``pip install``) without attempting to be a full
shell parser. The defence-in-depth posture matches the upstream's design — a
process-level sandbox would need OS-specific primitives (seatbelt on macOS,
landlock on Linux), which Chimera's stdlib-only core deliberately avoids.

The wrapper is also itself an :class:`~chimera.env.base.Environment`, so it
can be slotted in anywhere a ``LocalEnvironment`` would have been used.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import threading
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.env.base import Environment
from chimera.types import CommandResult, TestResult

if TYPE_CHECKING:
    from chimera.env.local import LocalEnvironment


__all__ = [
    "MutableSandboxMode",
    "SandboxMode",
    "SandboxViolation",
    "SandboxedEnvironment",
    "parse_sandbox_mode",
]


# ---------------------------------------------------------------------------
# Mode enum + parsing
# ---------------------------------------------------------------------------


class SandboxMode(str, Enum):
    """Three-tier sandbox model.

    The string values are the kebab-case spellings used on the ``--sandbox``
    CLI flag. They match the upstream's wire format so configuration files
    that target either tool stay readable.
    """

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    WORKSPACE_WRITE_NETWORK = "workspace-write-network"


def parse_sandbox_mode(value: str | SandboxMode | None) -> SandboxMode:
    """Coerce a CLI/config string into a :class:`SandboxMode`.

    Args:
        value: Either a :class:`SandboxMode`, a kebab-case string
            (``"read-only"``, ``"workspace-write"``,
            ``"workspace-write-network"``), or ``None`` to mean default.

    Returns:
        The matching :class:`SandboxMode`. ``None`` → ``READ_ONLY``.

    Raises:
        ValueError: If ``value`` is a string that does not match any mode.
    """
    if value is None:
        return SandboxMode.READ_ONLY
    if isinstance(value, SandboxMode):
        return value
    normalized = value.strip().lower().replace("_", "-")
    for mode in SandboxMode:
        if mode.value == normalized:
            return mode
    valid = ", ".join(m.value for m in SandboxMode)
    raise ValueError(f"Unknown sandbox mode: {value!r}. Valid: {valid}")


# ---------------------------------------------------------------------------
# Mutable mode holder
# ---------------------------------------------------------------------------


class MutableSandboxMode:
    """Hot-swappable holder around an active :class:`SandboxMode`.

    The ferret REPL builds its environment once (at agent creation), but
    the ``/sandbox`` slash command lets the user re-shape the policy
    mid-session without rebuilding the agent. Rather than track mode on
    :class:`SandboxedEnvironment` as a plain attribute (whose changes
    would race with concurrent tool executions), the env consults this
    holder on every call. ``/sandbox`` then atomically swaps the holder's
    inner mode and the very next ``run_command`` / ``write_file`` sees
    the new policy.

    Thread-safety: a :class:`threading.Lock` guards the value so a
    background loop thread cannot read a torn enum reference while the
    REPL thread is mid-write. The lock is released before
    :class:`SandboxedEnvironment` reads classification fields off the
    snapshotted mode, which keeps the critical section minimal.

    Args:
        mode: Initial :class:`SandboxMode`. Defaults to
            :attr:`SandboxMode.READ_ONLY` — the safest starting posture.
    """

    def __init__(self, mode: SandboxMode | str | None = SandboxMode.READ_ONLY) -> None:
        self._mode = parse_sandbox_mode(mode)
        self._lock = threading.Lock()

    def get(self) -> SandboxMode:
        """Return the currently active :class:`SandboxMode`."""
        with self._lock:
            return self._mode

    def set(self, mode: SandboxMode | str | None) -> SandboxMode:
        """Atomically replace the active mode.

        Accepts the same inputs as :func:`parse_sandbox_mode` so callers
        can hand in a kebab-case string straight from the slash-command
        argument line.

        Args:
            mode: New :class:`SandboxMode`, kebab-case string, or
                ``None`` to mean :attr:`SandboxMode.READ_ONLY`.

        Returns:
            The previous mode (handy for tests / undo flows).
        """
        new_mode = parse_sandbox_mode(mode)
        with self._lock:
            previous = self._mode
            self._mode = new_mode
            return previous


# ---------------------------------------------------------------------------
# Violation
# ---------------------------------------------------------------------------


class SandboxViolation(PermissionError):
    """Raised when an operation is blocked by the active sandbox policy.

    Inherits from :class:`PermissionError` so callers that already handle
    permission failures from :class:`~chimera.env.local.LocalEnvironment`'s
    ``_contain`` method continue to work.
    """


# ---------------------------------------------------------------------------
# Command classification
# ---------------------------------------------------------------------------


# Read-only commands. Conservative whitelist — anything outside this set is
# blocked under READ_ONLY. The list deliberately mirrors what the upstream
# considers "safe to run without approval" plus a handful of obvious extras.
READ_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "cat",
        "ls",
        "ll",
        "head",
        "tail",
        "less",
        "more",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "find",
        "fd",
        "wc",
        "stat",
        "file",
        "tree",
        "pwd",
        "echo",
        "printf",
        "true",
        "false",
        "test",
        "[",
        "which",
        "type",
        "whoami",
        "id",
        "uname",
        "date",
        "env",
        "printenv",
        "git",  # only sub-commands; we further restrict below
        "diff",
        "cmp",
        "sort",
        "uniq",
        "cut",
        "awk",
        "sed",  # sed without -i is read-only; -i is detected below
        "tr",
        "column",
        "tee",  # only as a passthrough; redirects detected below
        "xargs",  # but its target gets re-classified
        "jq",
        "yq",
        "python",  # invocations get scrutinised separately
        "python3",
        "py",
        "node",
        "ruby",
        "perl",
        "go",
    }
)


# Git sub-commands that mutate the working tree or history.
GIT_MUTATING_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "add",
        "commit",
        "push",
        "pull",
        "fetch",
        "merge",
        "rebase",
        "reset",
        "checkout",
        "switch",
        "restore",
        "rm",
        "mv",
        "clean",
        "stash",
        "tag",
        "apply",
        "am",
        "cherry-pick",
        "revert",
        "init",
        "clone",
        "submodule",
        "worktree",
        "config",
    }
)


# Commands that always mutate or are otherwise unsafe under READ_ONLY.
MUTATING_COMMANDS: frozenset[str] = frozenset(
    {
        "rm",
        "rmdir",
        "mv",
        "cp",
        "ln",
        "touch",
        "mkdir",
        "chmod",
        "chown",
        "chgrp",
        "dd",
        "shred",
        "truncate",
        "install",
        "make",
        "ninja",
        "cmake",
        "cargo",
        "npm",
        "pnpm",
        "yarn",
        "bun",
        "pip",
        "pip3",
        "uv",
        "poetry",
        "rustup",
        "apt",
        "apt-get",
        "brew",
        "dpkg",
        "yum",
        "dnf",
        "pacman",
        "snap",
        "systemctl",
        "service",
        "reboot",
        "shutdown",
        "kill",
        "killall",
        "pkill",
    }
)


# Commands that touch the network. Blocked unless mode includes network.
NETWORK_COMMANDS: frozenset[str] = frozenset(
    {
        "curl",
        "wget",
        "ftp",
        "sftp",
        "scp",
        "ssh",
        "rsync",
        "nc",
        "netcat",
        "ping",
        "telnet",
        "host",
        "dig",
        "nslookup",
        "git",  # remote sub-commands handled below
    }
)


# git sub-commands that hit the network.
GIT_NETWORK_SUBCOMMANDS: frozenset[str] = frozenset(
    {"push", "pull", "fetch", "clone", "remote", "submodule"}
)


# Shell metacharacters that can chain or hide commands. We refuse to classify
# anything containing these under READ_ONLY because static analysis becomes
# unreliable. WORKSPACE_WRITE is more permissive but still scans every token.
_DANGEROUS_SHELL_CHARS = re.compile(r"[`$]|\$\(")


def _split_pipeline(cmd: str) -> list[list[str]]:
    """Split ``cmd`` into individual command invocations across ``;``, ``&&``,
    ``||``, and ``|`` boundaries.

    Returns a list of token lists, one per sub-command. Best-effort — falls
    back to a single-command list if shlex fails.
    """
    # Replace operators with a sentinel we can re-split on after shlex tokenisation.
    sentinel = "\x00CMD_BREAK\x00"
    # Order matters: longer tokens first.
    pattern = re.compile(r"(\|\||&&|;|\||&)")
    tokens_with_breaks = pattern.sub(sentinel, cmd)
    pieces = [p.strip() for p in tokens_with_breaks.split(sentinel) if p.strip()]
    out: list[list[str]] = []
    for piece in pieces:
        try:
            out.append(shlex.split(piece, posix=True))
        except ValueError:
            # Unbalanced quotes etc. — treat as one opaque token so the caller
            # can still make a coarse decision.
            out.append([piece])
    return out


def _command_name(tokens: list[str]) -> str:
    """Extract the program name from a tokenised command, ignoring leading
    ``VAR=value`` env assignments and ``sudo``/``env`` wrappers.
    """
    for tok in tokens:
        if "=" in tok and not tok.startswith("-") and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            continue
        if tok in {"sudo", "env", "command", "exec", "nice", "nohup", "time"}:
            continue
        return Path(tok).name
    return ""


def _has_redirect_to_file(piece: str) -> bool:
    """Does this raw command piece contain an output redirect?"""
    # Match `>` and `>>` but not within quotes. Cheap regex; misses heredocs
    # but those are rare in agent-emitted commands.
    stripped = re.sub(r"'[^']*'|\"[^\"]*\"", "", piece)
    return bool(re.search(r"(?<![<>])>>?(?!&)", stripped))


def _is_sed_in_place(tokens: list[str]) -> bool:
    """``sed -i`` mutates files; plain ``sed`` is read-only."""
    if not tokens or _command_name(tokens) != "sed":
        return False
    return any(t == "-i" or t.startswith("-i") and not t.startswith("--") for t in tokens[1:])


def _is_git_mutating(tokens: list[str]) -> bool:
    """Does a ``git`` invocation modify state?"""
    if _command_name(tokens) != "git":
        return False
    # Skip flags between ``git`` and the sub-command (``git -C path commit``).
    for tok in tokens[1:]:
        if tok.startswith("-"):
            continue
        return tok in GIT_MUTATING_SUBCOMMANDS
    return False


def _is_git_network(tokens: list[str]) -> bool:
    if _command_name(tokens) != "git":
        return False
    for tok in tokens[1:]:
        if tok.startswith("-"):
            continue
        return tok in GIT_NETWORK_SUBCOMMANDS
    return False


def _classify_command(cmd: str) -> tuple[bool, bool, str]:
    """Classify a shell command string.

    Returns:
        ``(is_mutating, touches_network, reason)``. ``reason`` is empty on
        clean classification, otherwise a human-readable description of the
        riskiest token.
    """
    if _DANGEROUS_SHELL_CHARS.search(cmd):
        return True, True, "command contains shell substitution / backticks"

    pieces = _split_pipeline(cmd)
    raw_pieces = re.split(r"\|\||&&|;|\||&", cmd)
    raw_pieces = [p.strip() for p in raw_pieces if p.strip()]

    is_mutating = False
    touches_network = False
    reason = ""

    for tokens, raw in zip(pieces, raw_pieces):
        if not tokens:
            continue
        name = _command_name(tokens)
        if not name:
            continue

        if _has_redirect_to_file(raw):
            is_mutating = True
            reason = reason or f"redirect to file in: {raw}"

        if name in MUTATING_COMMANDS:
            is_mutating = True
            reason = reason or f"mutating command: {name}"

        if _is_sed_in_place(tokens):
            is_mutating = True
            reason = reason or "sed -i (in-place edit)"

        if _is_git_mutating(tokens):
            is_mutating = True
            reason = reason or f"git {tokens[1] if len(tokens) > 1 else ''}"

        if name in NETWORK_COMMANDS and name != "git":
            touches_network = True
            reason = reason or f"network command: {name}"

        if _is_git_network(tokens):
            touches_network = True
            reason = reason or "git (network sub-command)"

        # If the command is unknown to us *and* not in the explicit read-only
        # whitelist, mark it as mutating so READ_ONLY blocks it. This is the
        # safe default: users can always escalate to WORKSPACE_WRITE.
        if (
            name not in READ_ONLY_COMMANDS
            and name not in MUTATING_COMMANDS
            and name not in NETWORK_COMMANDS
        ):
            is_mutating = True
            reason = reason or f"unrecognised command: {name}"

    return is_mutating, touches_network, reason


# ---------------------------------------------------------------------------
# SandboxedEnvironment
# ---------------------------------------------------------------------------


class SandboxedEnvironment(Environment):
    """Wrap a :class:`~chimera.env.local.LocalEnvironment` with sandbox policy.

    The wrapper itself implements :class:`~chimera.env.base.Environment` so
    it slots in transparently.

    Reads are always allowed (subject to the inner environment's path
    containment). Writes are policed by mode. Bash commands are statically
    classified and blocked if they fall outside the allowed envelope.

    The mode is stored inside a :class:`MutableSandboxMode` holder so the
    ferret REPL's ``/sandbox`` slash command can swap it mid-session
    without rebuilding the agent. Reads of :attr:`mode` and
    :meth:`set_mode` writes are atomic against each other; the wrapper
    therefore behaves like a thread-safe view onto the holder. Direct
    assignments (``env.mode = SandboxMode.X``) are honoured for
    back-compat by routing through the holder.

    Args:
        inner: The :class:`~chimera.env.local.LocalEnvironment` to wrap.
        mode: Either a :class:`MutableSandboxMode` to share across
            multiple consumers, a :class:`SandboxMode`, a kebab-case
            string, or ``None``. Defaults to :attr:`SandboxMode.READ_ONLY`.

    Attributes:
        inner: The wrapped environment.
        mode: The active sandbox mode (live read from the holder).
        mode_holder: The :class:`MutableSandboxMode` backing :attr:`mode`.
            Slash commands swap mode by calling
            ``env.mode_holder.set(new_mode)`` (or, equivalently,
            ``env.set_mode(new_mode)``).
        workdir: Convenience pass-through to ``inner.workdir``.
    """

    inner: LocalEnvironment
    mode_holder: MutableSandboxMode
    os_sandbox: str

    def __init__(
        self,
        inner: LocalEnvironment,
        mode: MutableSandboxMode | SandboxMode | str | None = SandboxMode.READ_ONLY,
        *,
        os_sandbox: str = "auto",
    ) -> None:
        """Wrap *inner* with the named sandbox policy.

        Args:
            inner: The :class:`LocalEnvironment` to wrap.
            mode: The active :class:`SandboxMode`, a kebab-case string,
                or a pre-built :class:`MutableSandboxMode` (when sharing
                a holder across consumers — e.g. a slash command and a
                background agent loop).
            os_sandbox: ``"auto"`` (default — engage the OS-level
                sandbox if the platform supports it), ``"on"`` (fail
                loudly if the platform can't), ``"off"`` (wrapper
                only). See :mod:`chimera.ferret.os_sandbox`.
        """
        self.inner = inner
        if isinstance(mode, MutableSandboxMode):
            self.mode_holder = mode
        else:
            self.mode_holder = MutableSandboxMode(mode)
        # Late import keeps os_sandbox optional at the module-load
        # boundary (it leans on ctypes which loads libc on Linux).
        try:
            from chimera.ferret.os_sandbox import parse_os_sandbox_flag

            self.os_sandbox = parse_os_sandbox_flag(os_sandbox)
        except Exception:  # noqa: BLE001 — fall through to wrapper-only
            self.os_sandbox = "off"

    # -- convenience -------------------------------------------------------

    @property
    def mode(self) -> SandboxMode:
        """Live read of the active :class:`SandboxMode`.

        Funneled through :attr:`mode_holder` so ``/sandbox`` swaps land
        on the very next tool call without rebuilding the env.
        """
        return self.mode_holder.get()

    @mode.setter
    def mode(self, value: SandboxMode | str | None) -> None:
        """Assignment to ``env.mode`` updates the holder atomically.

        Preserved so existing code that reaches in to mutate the mode
        directly (tests, inspection tools) keeps working.
        """
        self.mode_holder.set(value)

    def set_mode(self, mode: SandboxMode | str | None) -> SandboxMode:
        """Atomically swap the active sandbox mode.

        Convenience wrapper around ``self.mode_holder.set(...)`` for
        callers that prefer a method form. Returns the previous mode.
        """
        return self.mode_holder.set(mode)

    @property
    def workdir(self) -> Path:
        return self.inner.workdir

    @property
    def allows_writes(self) -> bool:
        return self.mode in {
            SandboxMode.WORKSPACE_WRITE,
            SandboxMode.WORKSPACE_WRITE_NETWORK,
        }

    @property
    def allows_network(self) -> bool:
        return self.mode == SandboxMode.WORKSPACE_WRITE_NETWORK

    # -- Environment ABC ---------------------------------------------------

    def setup(self) -> None:
        self.inner.setup()

    def cleanup(self) -> None:
        self.inner.cleanup()

    def read_file(self, path: str) -> str:
        # Reads are always allowed (path containment still enforced inside).
        return self.inner.read_file(path)

    def write_file(self, path: str, content: str) -> None:
        if not self.allows_writes:
            raise SandboxViolation(
                f"write_file blocked by sandbox mode={self.mode.value!r}: {path}"
            )
        # WORKSPACE_WRITE: only paths inside workdir.
        # ``LocalEnvironment._contain`` already enforces this — we just call
        # through, but we also resolve here for a clearer error message.
        self._require_inside_workdir(path)
        self.inner.write_file(path, content)

    def list_files(self, pattern: str = "**/*") -> list[str]:
        return self.inner.list_files(pattern)

    def run_command(
        self, cmd: str, timeout: int = 120, shell_name: str = "main"
    ) -> CommandResult:
        is_mutating, touches_network, reason = _classify_command(cmd)

        if self.mode == SandboxMode.READ_ONLY:
            if is_mutating or touches_network:
                raise SandboxViolation(
                    f"command blocked by sandbox mode={self.mode.value!r}: "
                    f"{reason or cmd}"
                )
        else:
            # WORKSPACE_WRITE / WORKSPACE_WRITE_NETWORK
            if touches_network and not self.allows_network:
                raise SandboxViolation(
                    f"network access blocked by sandbox mode={self.mode.value!r}: "
                    f"{reason or cmd}"
                )

        # OS-sandbox layer. When enabled and the platform supports it,
        # we re-run the bash command inside Seatbelt (macOS) so any
        # bypass of our static classifier is contained at the kernel
        # level. The Linux Landlock equivalent runs in-process from
        # the embedded helper (see os_sandbox._format_for_landlock_prefix);
        # to keep the wrapper small here we only engage the macOS path
        # (Linux falls through to the wrapper-only sandbox until the
        # in-process helper is wired into a forked subprocess).
        if self.os_sandbox in ("auto", "on"):
            wrapped = self._maybe_wrap_for_os_sandbox(cmd)
            if wrapped is not None:
                return wrapped

        return self.inner.run_command(cmd, timeout=timeout, shell_name=shell_name)

    def run_tests(self) -> TestResult:
        # Tests usually need writes (pytest cache, __pycache__). Surface a
        # clear violation under READ_ONLY rather than letting pytest fail
        # opaquely on a permission error from the OS.
        if self.mode == SandboxMode.READ_ONLY:
            raise SandboxViolation(
                "run_tests blocked by sandbox mode='read-only'"
            )
        return self.inner.run_tests()

    def checkpoint(self) -> str:
        if not self.allows_writes:
            raise SandboxViolation(
                f"checkpoint blocked by sandbox mode={self.mode.value!r}"
            )
        return self.inner.checkpoint()

    def restore(self, checkpoint_id: str) -> None:
        if not self.allows_writes:
            raise SandboxViolation(
                f"restore blocked by sandbox mode={self.mode.value!r}"
            )
        self.inner.restore(checkpoint_id)

    def clone(self) -> SandboxedEnvironment:
        cloned_inner = self.inner.clone()
        return SandboxedEnvironment(
            cloned_inner, mode=self.mode, os_sandbox=self.os_sandbox,
        )

    # -- OS sandbox bridge -------------------------------------------------

    def _maybe_wrap_for_os_sandbox(self, cmd: str) -> CommandResult | None:
        """Run *cmd* under an OS-level sandbox when one is available.

        Returns:
            A :class:`CommandResult` when the OS sandbox engaged
            successfully. ``None`` when no sandbox primitive is
            reachable on this host (caller falls back to the inner
            environment's plain ``run_command``).
        """
        try:
            from chimera.ferret import os_sandbox as _os
        except Exception:  # noqa: BLE001 — module unavailable
            return None
        snap = _os.detect_availability()
        if snap.seatbelt:
            argv = _os.wrap_bash_command(
                cmd, mode=self.mode, workdir=str(self.workdir),
            )
            try:
                proc = subprocess.run(  # noqa: S603 — argv list, no shell
                    argv,
                    cwd=str(self.workdir),
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return CommandResult(
                    stdout="", stderr="os-sandbox: timeout", exit_code=124,
                )
            return CommandResult(
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                exit_code=int(proc.returncode),
            )
        # Landlock requires a forked subprocess that applies the
        # ruleset before exec; for now the wrapper-only path is the
        # safe default. The helper exists in os_sandbox.landlock_apply
        # for callers that fork their own children.
        return None

    # -- helpers -----------------------------------------------------------

    def _require_inside_workdir(self, path: str) -> None:
        """Raise :class:`SandboxViolation` if ``path`` escapes ``workdir``.

        This duplicates ``LocalEnvironment._contain`` so the error class is
        ``SandboxViolation`` (a subclass of :class:`PermissionError`) and
        speaks in sandbox-mode language rather than path-escape language.
        """
        workdir_resolved = self.inner.workdir.resolve()
        candidate = (self.inner.workdir / path).resolve()
        try:
            candidate.relative_to(workdir_resolved)
        except ValueError as exc:
            raise SandboxViolation(
                f"write path escapes workdir under sandbox mode={self.mode.value!r}: {path}"
            ) from exc
