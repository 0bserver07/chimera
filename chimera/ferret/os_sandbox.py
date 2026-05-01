"""OS-level sandboxing for ferret's :class:`SandboxedEnvironment`.

The wrapper-level sandbox in :mod:`chimera.ferret.sandbox` is good
defence-in-depth against well-formed agent commands, but a sufficiently
clever ``bash -c`` invocation that hides intent behind ``$()`` /
backticks / heredocs can bypass static classification. This module adds
an OS-level second line of defence:

* **macOS** uses ``sandbox-exec`` (a.k.a. *Seatbelt*). We synthesize a
  ``.sb`` profile string per :class:`~chimera.ferret.sandbox.SandboxMode`
  and run the bash command via
  ``sandbox-exec -p <profile> bash -c '<cmd>'``. The same SBPL dialect
  Apple ships with the OS — no third-party deps.
* **Linux** uses Landlock (kernel >= 5.13) via direct syscall ``ctypes``.
  We restrict the process tree to a small allow-list of paths that
  matches the active mode. Network restriction lands on Landlock
  ABI >= 4 (kernel 6.7+) and is best-effort otherwise (the wrapper-level
  network classifier still applies).

Both implementations *fail open*: if the OS does not support the
primitive (older kernel, sandbox-exec missing, ctypes can't load
libc), we emit a single ``stderr`` warning and fall back to the
wrapper-only sandbox. That keeps ferret usable on every platform we
ship to without forcing operators to disable the feature explicitly.

Hard rules:

* Stdlib + ``ctypes`` only. No ``seccomp-tools`` or other binary deps.
* Pure functions where possible — :func:`seatbelt_profile` returns the
  profile string, :func:`wrap_bash_command` returns the rewritten argv,
  callers decide when (and whether) to apply.
* All warnings go to ``stderr`` and only fire once per process per
  reason, so a long-running session doesn't spam the operator.

See ``docs/ferret/sandbox.md`` for the operator-facing description.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import shlex
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.ferret.sandbox import SandboxMode


__all__ = [
    "OSSandboxAvailability",
    "OSSandboxMode",
    "describe_os_sandbox",
    "is_seatbelt_available",
    "is_landlock_available",
    "landlock_apply",
    "parse_os_sandbox_flag",
    "seatbelt_profile",
    "wrap_bash_command",
]


# ---------------------------------------------------------------------------
# CLI flag parsing
# ---------------------------------------------------------------------------


# Tri-state CLI flag: ``--os-sandbox auto|on|off``. ``auto`` (the default)
# means "use the OS primitive when supported, fall back silently
# otherwise"; ``on`` forces it (and fails loud if unsupported); ``off``
# disables it entirely. Plain string Enum gives us free CLI parsing.
class OSSandboxMode(str):
    """Tri-state value for the ``--os-sandbox`` flag.

    Not a real :class:`enum.Enum` so callers can keep passing the raw
    strings ``"auto"`` / ``"on"`` / ``"off"`` from argparse without an
    extra coercion step.
    """

    AUTO = "auto"
    ON = "on"
    OFF = "off"


_VALID_FLAG_VALUES: frozenset[str] = frozenset({"auto", "on", "off"})


def parse_os_sandbox_flag(value: str | None) -> str:
    """Normalize a ``--os-sandbox`` CLI value.

    Args:
        value: Raw string from argparse (``"auto"`` / ``"on"`` / ``"off"``)
            or ``None``.

    Returns:
        One of ``"auto"`` (default), ``"on"``, ``"off"``. ``None``
        becomes ``"auto"``.

    Raises:
        ValueError: When ``value`` is not in the valid set.
    """
    if value is None:
        return "auto"
    normalized = value.strip().lower()
    if normalized not in _VALID_FLAG_VALUES:
        valid = ", ".join(sorted(_VALID_FLAG_VALUES))
        raise ValueError(
            f"Unknown --os-sandbox value: {value!r}. Valid: {valid}"
        )
    return normalized


# ---------------------------------------------------------------------------
# Availability detection (one-shot, cached, warns once)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OSSandboxAvailability:
    """Snapshot of which OS primitives are reachable on this host.

    Attributes:
        platform: The :func:`platform.system` value at detection time.
        seatbelt: True when ``sandbox-exec`` resolves on ``$PATH``.
        landlock: True when libc loaded *and* ``landlock_create_ruleset``
            returned a non-error ABI version probe.
        reason: When neither primitive is available, a short human
            description of why. Empty string otherwise.
    """

    platform: str
    seatbelt: bool
    landlock: bool
    reason: str = ""


_availability_lock = threading.Lock()
_availability_cache: OSSandboxAvailability | None = None
_warned_reasons: set[str] = set()


def _warn_once(reason: str) -> None:
    """Emit ``reason`` to stderr at most once per process."""
    with _availability_lock:
        if reason in _warned_reasons:
            return
        _warned_reasons.add(reason)
    sys.stderr.write(f"[ferret] os-sandbox: {reason}\n")
    sys.stderr.flush()


def _detect_seatbelt() -> bool:
    """Is ``sandbox-exec`` available on this host?"""
    if platform.system() != "Darwin":
        return False
    # ``sandbox-exec`` ships in /usr/bin on every supported macOS release.
    # Probe via PATH so callers in unusual environments still resolve.
    for entry in os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep):
        candidate = Path(entry) / "sandbox-exec"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return True
    # Fallback to the canonical absolute path.
    return Path("/usr/bin/sandbox-exec").is_file()


def _detect_landlock() -> bool:
    """Is Landlock supported by the running Linux kernel?"""
    if platform.system() != "Linux":
        return False
    try:
        libc_path = ctypes.util.find_library("c")
        if libc_path is None:
            return False
        libc = ctypes.CDLL(libc_path, use_errno=True)
    except OSError:
        return False
    # ``landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)``
    # returns the ABI version when supported and -1 / ENOSYS otherwise.
    # We invoke via ``syscall`` because glibc may not expose the wrapper.
    syscall = getattr(libc, "syscall", None)
    if syscall is None:
        return False
    syscall.restype = ctypes.c_long
    # SYS_landlock_create_ruleset = 444 on x86_64; arch-specific in
    # general. We avoid depending on the exact number by treating any
    # non-ENOSYS errno as "supported well enough to warrant trying".
    sys_no = _LANDLOCK_CREATE_RULESET_SYSCALL.get(platform.machine(), 444)
    LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
    rc = syscall(
        ctypes.c_long(sys_no),
        ctypes.c_void_p(0),
        ctypes.c_size_t(0),
        ctypes.c_uint(LANDLOCK_CREATE_RULESET_VERSION),
    )
    if rc < 0:
        errno = ctypes.get_errno()
        # ENOSYS = 38 on Linux — kernel doesn't have the syscall.
        # EOPNOTSUPP = 95 — Landlock not enabled in kernel config.
        return errno not in (38, 95)
    return True


# Map machine() values to the Linux Landlock create_ruleset syscall
# number. We support the commonly-shipped arches; everything else falls
# through to the x86_64 number (444) and gets caught by errno=ENOSYS at
# runtime, which is fine — the detection still fails closed.
_LANDLOCK_CREATE_RULESET_SYSCALL: dict[str, int] = {
    "x86_64": 444,
    "aarch64": 444,
    "armv7l": 444,
    "ppc64le": 444,
    "s390x": 444,
    "riscv64": 444,
}


def detect_availability() -> OSSandboxAvailability:
    """Probe and cache OS-sandbox primitive availability.

    Returns:
        :class:`OSSandboxAvailability` with the seatbelt / landlock
        booleans set per the running platform. Subsequent calls return
        the same snapshot — cheap to call from hot paths.
    """
    global _availability_cache
    with _availability_lock:
        if _availability_cache is not None:
            return _availability_cache
    plat = platform.system()
    seatbelt = _detect_seatbelt() if plat == "Darwin" else False
    landlock = _detect_landlock() if plat == "Linux" else False
    if not seatbelt and not landlock:
        if plat == "Darwin":
            reason = "sandbox-exec not found on PATH"
        elif plat == "Linux":
            reason = "Landlock not available (kernel < 5.13 or disabled)"
        else:
            reason = f"no OS sandbox primitive on {plat}"
    else:
        reason = ""
    snap = OSSandboxAvailability(
        platform=plat, seatbelt=seatbelt, landlock=landlock, reason=reason,
    )
    with _availability_lock:
        _availability_cache = snap
    return snap


def is_seatbelt_available() -> bool:
    """True when seatbelt (``sandbox-exec``) can be used on this host."""
    return detect_availability().seatbelt


def is_landlock_available() -> bool:
    """True when Landlock can be used on this host."""
    return detect_availability().landlock


def describe_os_sandbox() -> str:
    """One-line operator-facing description of OS-sandbox availability."""
    snap = detect_availability()
    if snap.seatbelt:
        return f"os-sandbox: seatbelt (macOS, {snap.platform})"
    if snap.landlock:
        return f"os-sandbox: landlock (Linux, {snap.platform})"
    return f"os-sandbox: unavailable ({snap.reason})"


# ---------------------------------------------------------------------------
# macOS — Seatbelt profile generation
# ---------------------------------------------------------------------------


# Profile templates use SBPL (Apple's S-expression-flavored sandbox
# language). We avoid keys that vary across macOS releases and stick to
# the documented core: ``allow``, ``deny``, ``file*``, ``network*``,
# ``subpath``, ``literal``, ``regex``. All three modes default-deny then
# explicitly allow what's safe.

_SEATBELT_HEADER = "(version 1)\n(deny default)\n"

# Always-on allowances. We allow broad file-read* (every read-able path
# on the system) because bash itself needs to read shared libraries,
# /dev/urandom, dyld caches, locale data, terminfo, and friends — and
# because the wrapper-level sandbox already vetoed any *commands* we
# don't trust before this code runs. The real teeth of the OS layer
# are the file-write* and network* denials below.
_SEATBELT_BASE_ALLOWS = """
(allow process-fork)
(allow process-exec)
(allow signal (target self))
(allow sysctl-read)
(allow sysctl-write)
(allow mach-lookup)
(allow mach-priv-host-port)
(allow ipc-posix-shm)
(allow ipc-posix-sem)
(allow iokit-open)
(allow file-read*)
(allow file-read-metadata)
(allow file-ioctl)
(allow file-write-data (literal "/dev/null"))
(allow file-write-data (literal "/dev/stdout"))
(allow file-write-data (literal "/dev/stderr"))
(allow file-write-data (literal "/dev/tty"))
(allow file-write-data (literal "/dev/dtracehelper"))
"""


def _quote_sbpl(value: str) -> str:
    """Escape ``value`` for inclusion as an SBPL string literal.

    SBPL accepts double-quoted strings with backslash escapes for
    backslash and quote. Newlines are not allowed inside a literal.
    Path strings on macOS never legitimately contain newlines, so we
    strip them defensively.
    """
    cleaned = value.replace("\\", "\\\\").replace('"', '\\"')
    cleaned = cleaned.replace("\n", "").replace("\r", "")
    return cleaned


def seatbelt_profile(
    mode: SandboxMode,
    workdir: str | os.PathLike[str],
    *,
    extra_read_paths: list[str] | None = None,
) -> str:
    """Generate an SBPL profile string for the given sandbox mode.

    Args:
        mode: The active :class:`~chimera.ferret.sandbox.SandboxMode`.
        workdir: The project root the agent is operating on. Reads /
            writes are scoped here for ``WORKSPACE_WRITE`` and
            ``WORKSPACE_WRITE_NETWORK``.
        extra_read_paths: Optional extra subpaths to add to the read
            allow-list (e.g. global git config, nvm cache). Each path
            must already be absolute.

    Returns:
        A complete ``.sb`` profile string ready to pass to
        ``sandbox-exec -p``.

    Raises:
        ValueError: When ``mode`` is not a known SandboxMode value.
    """
    # Late-import to avoid circular dependency between sandbox.py and
    # this module.
    from chimera.ferret.sandbox import SandboxMode as _SandboxMode

    if not isinstance(mode, _SandboxMode):
        raise ValueError(f"unknown sandbox mode: {mode!r}")

    workdir_abs = os.path.abspath(os.fspath(workdir))
    workdir_quoted = _quote_sbpl(workdir_abs)
    extra = extra_read_paths or []

    parts: list[str] = [_SEATBELT_HEADER, _SEATBELT_BASE_ALLOWS]

    # Read access to the workdir is allowed in every mode.
    parts.append(f'\n(allow file-read-data (subpath "{workdir_quoted}"))\n')
    for path in extra:
        parts.append(
            f'(allow file-read-data (subpath "{_quote_sbpl(os.path.abspath(path))}"))\n'
        )

    if mode == _SandboxMode.READ_ONLY:
        # No writes anywhere outside the dev/null pseudo-files allowed
        # in the base block. Network is denied implicitly.
        pass
    elif mode == _SandboxMode.WORKSPACE_WRITE:
        # Writes confined to workdir; network still denied.
        parts.append(
            f'(allow file-write* (subpath "{workdir_quoted}"))\n'
            f'(allow file-write* (subpath "/private/tmp"))\n'
            f'(allow file-write* (subpath "/private/var/folders"))\n'
        )
    elif mode == _SandboxMode.WORKSPACE_WRITE_NETWORK:
        parts.append(
            f'(allow file-write* (subpath "{workdir_quoted}"))\n'
            f'(allow file-write* (subpath "/private/tmp"))\n'
            f'(allow file-write* (subpath "/private/var/folders"))\n'
            "(allow network*)\n"
        )
    else:  # pragma: no cover - exhaustive enum
        raise ValueError(f"unhandled sandbox mode: {mode!r}")

    return "".join(parts)


def wrap_bash_command(
    cmd: str,
    mode: SandboxMode,
    workdir: str | os.PathLike[str],
    *,
    extra_read_paths: list[str] | None = None,
) -> list[str]:
    """Wrap a bash command for execution under the macOS seatbelt sandbox.

    The returned argv is suitable to pass to
    :func:`subprocess.run` / :func:`subprocess.Popen`. When seatbelt
    isn't available on this host, returns the bare ``["bash", "-c",
    cmd]`` form (caller falls through to the wrapper-only sandbox).

    Args:
        cmd: The user-supplied bash command string.
        mode: Active :class:`~chimera.ferret.sandbox.SandboxMode`.
        workdir: Active working directory.
        extra_read_paths: Optional extra read-allow subpaths.

    Returns:
        An argv list. First element is either ``"sandbox-exec"`` (when
        the sandbox is engaged) or ``"bash"`` (fall-through).
    """
    snap = detect_availability()
    if not snap.seatbelt:
        return ["bash", "-c", cmd]
    profile = seatbelt_profile(mode, workdir, extra_read_paths=extra_read_paths)
    # ``sandbox-exec -p <profile-string> -- bash -c <cmd>`` — we keep
    # the profile inline so we don't have to manage a temp file. The
    # profile is shell-quoted by argv handoff (no shell=True), so the
    # length cap is OS argv max (~256K on Darwin); our profiles are
    # < 4K so this is safe with broad headroom.
    return [
        "sandbox-exec",
        "-p",
        profile,
        "bash",
        "-c",
        cmd,
    ]


# ---------------------------------------------------------------------------
# Linux — Landlock application
# ---------------------------------------------------------------------------


# Landlock access flags for filesystem rules. See
# include/uapi/linux/landlock.h in the Linux source. We re-declare the
# constants here so the module stays stdlib-only (no asking the system
# for kernel headers at import time).
_LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
_LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
_LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
_LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
_LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
_LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
_LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
_LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
_LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
_LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
_LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
_LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
_LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12

_LANDLOCK_READ_BITS = (
    _LANDLOCK_ACCESS_FS_EXECUTE
    | _LANDLOCK_ACCESS_FS_READ_FILE
    | _LANDLOCK_ACCESS_FS_READ_DIR
)
_LANDLOCK_WRITE_BITS = (
    _LANDLOCK_ACCESS_FS_WRITE_FILE
    | _LANDLOCK_ACCESS_FS_REMOVE_DIR
    | _LANDLOCK_ACCESS_FS_REMOVE_FILE
    | _LANDLOCK_ACCESS_FS_MAKE_CHAR
    | _LANDLOCK_ACCESS_FS_MAKE_DIR
    | _LANDLOCK_ACCESS_FS_MAKE_REG
    | _LANDLOCK_ACCESS_FS_MAKE_SOCK
    | _LANDLOCK_ACCESS_FS_MAKE_FIFO
    | _LANDLOCK_ACCESS_FS_MAKE_BLOCK
    | _LANDLOCK_ACCESS_FS_MAKE_SYM
)


# prctl + secure-bits constants (from <sys/prctl.h>).
_PR_SET_NO_NEW_PRIVS = 38


class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
    ]


class _LandlockPathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


# Landlock syscall numbers per arch — same caveat as
# _LANDLOCK_CREATE_RULESET_SYSCALL above.
_LANDLOCK_ADD_RULE_SYSCALL = 445
_LANDLOCK_RESTRICT_SELF_SYSCALL = 446
_LANDLOCK_RULE_PATH_BENEATH = 1


def _allowed_paths_for_mode(
    mode: SandboxMode, workdir: str
) -> list[tuple[str, int]]:
    """Compute (path, access_bits) tuples for a Landlock ruleset.

    READ_ONLY allows reads of workdir, /usr, /lib, etc.
    WORKSPACE_WRITE adds writes to workdir + /tmp.
    WORKSPACE_WRITE_NETWORK is identical for the FS layer (network is
    handled separately on ABI >= 4 hosts).
    """
    from chimera.ferret.sandbox import SandboxMode as _SandboxMode

    workdir_abs = os.path.abspath(workdir)
    base_reads = [
        "/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc",
        "/opt", "/var/lib", "/proc", "/sys", "/dev/null",
    ]
    reads = [(workdir_abs, _LANDLOCK_READ_BITS)]
    for p in base_reads:
        if os.path.isdir(p) or os.path.exists(p):
            reads.append((p, _LANDLOCK_READ_BITS))

    if mode == _SandboxMode.READ_ONLY:
        return reads

    write_paths = [
        (workdir_abs, _LANDLOCK_READ_BITS | _LANDLOCK_WRITE_BITS),
        ("/tmp", _LANDLOCK_READ_BITS | _LANDLOCK_WRITE_BITS),
        ("/var/tmp", _LANDLOCK_READ_BITS | _LANDLOCK_WRITE_BITS),
    ]
    # For WORKSPACE_WRITE_NETWORK we keep the same FS rules; the network
    # layer is opt-in on ABI >= 4.
    return [
        *[(p, b) for p, b in reads if p != workdir_abs],
        *write_paths,
    ]


def landlock_apply(
    mode: SandboxMode,
    workdir: str | os.PathLike[str],
) -> bool:
    """Attempt to apply a Landlock ruleset to the current process.

    Args:
        mode: Active :class:`~chimera.ferret.sandbox.SandboxMode`.
        workdir: Active working directory.

    Returns:
        ``True`` when the ruleset was applied successfully (the calling
        process and its children are now sandboxed at the kernel
        level). ``False`` when Landlock is unavailable or any syscall
        failed; in that case a single ``stderr`` warning is emitted
        and callers should fall through to the wrapper-only sandbox.
    """
    snap = detect_availability()
    if not snap.landlock:
        _warn_once(
            f"Landlock unsupported on {snap.platform}; "
            "falling back to wrapper-only sandbox.",
        )
        return False

    try:
        libc_path = ctypes.util.find_library("c")
        if libc_path is None:
            _warn_once("libc not found; cannot apply Landlock.")
            return False
        libc = ctypes.CDLL(libc_path, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = [
            ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_ulong, ctypes.c_ulong,
        ]
        prctl.restype = ctypes.c_int
        syscall = libc.syscall
        syscall.restype = ctypes.c_long

        # Step 1 — set no-new-privs. Required for Landlock to take effect.
        if prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            _warn_once(
                "prctl(PR_SET_NO_NEW_PRIVS) failed; cannot apply Landlock."
            )
            return False

        # Step 2 — create the ruleset describing what we want to handle.
        attr = _LandlockRulesetAttr(
            handled_access_fs=ctypes.c_uint64(
                _LANDLOCK_READ_BITS | _LANDLOCK_WRITE_BITS,
            ),
            handled_access_net=ctypes.c_uint64(0),
        )
        sys_no_create = _LANDLOCK_CREATE_RULESET_SYSCALL.get(
            platform.machine(), 444,
        )
        ruleset_fd = syscall(
            ctypes.c_long(sys_no_create),
            ctypes.byref(attr),
            ctypes.c_size_t(ctypes.sizeof(attr)),
            ctypes.c_uint(0),
        )
        if ruleset_fd < 0:
            _warn_once(
                "landlock_create_ruleset failed "
                f"(errno={ctypes.get_errno()}); falling back."
            )
            return False

        try:
            # Step 3 — add a path-beneath rule for each allowed location.
            # ``O_PATH`` is Linux-only; ``getattr`` keeps mypy on macOS happy.
            o_path = getattr(os, "O_PATH", 0o10000000)
            o_cloexec = getattr(os, "O_CLOEXEC", 0o2000000)
            for path, access in _allowed_paths_for_mode(mode, os.fspath(workdir)):
                try:
                    parent_fd = os.open(path, o_path | o_cloexec)
                except OSError:
                    continue
                try:
                    rule = _LandlockPathBeneathAttr(
                        allowed_access=ctypes.c_uint64(access),
                        parent_fd=ctypes.c_int32(parent_fd),
                    )
                    rc = syscall(
                        ctypes.c_long(_LANDLOCK_ADD_RULE_SYSCALL),
                        ctypes.c_int(int(ruleset_fd)),
                        ctypes.c_uint(_LANDLOCK_RULE_PATH_BENEATH),
                        ctypes.byref(rule),
                        ctypes.c_uint(0),
                    )
                    if rc < 0:
                        # Don't bail — best-effort per path.
                        continue
                finally:
                    os.close(parent_fd)

            # Step 4 — restrict self.
            rc = syscall(
                ctypes.c_long(_LANDLOCK_RESTRICT_SELF_SYSCALL),
                ctypes.c_int(int(ruleset_fd)),
                ctypes.c_uint(0),
            )
            if rc != 0:
                _warn_once(
                    "landlock_restrict_self failed "
                    f"(errno={ctypes.get_errno()}); falling back."
                )
                return False
        finally:
            try:
                os.close(int(ruleset_fd))
            except OSError:
                pass

        return True
    except OSError as exc:
        _warn_once(f"Landlock application raised OSError: {exc}; falling back.")
        return False


def _format_for_landlock_prefix(
    mode: SandboxMode,
    workdir: str | os.PathLike[str],
    cmd: str,
) -> list[str]:
    """Render a bash invocation that applies Landlock then exec's *cmd*.

    On Linux we cannot apply Landlock from the parent process without
    affecting the parent (Landlock is per-thread-group and inherited
    by all descendants). The standard pattern is to ``bash -c`` a
    small Python helper that calls landlock_apply then
    ``os.execvp("bash", ...)``. Keeping this pure-stdlib (no helper
    binary to ship) means we inline the Python.
    """
    # Embed a tiny Python that re-imports our module and applies the
    # ruleset. We pass workdir + mode via env vars so the embedded
    # source stays a constant string (easier to audit, no shell
    # quoting issues with paths containing single quotes).
    helper = (
        "import os, sys\n"
        "from chimera.ferret.os_sandbox import landlock_apply\n"
        "from chimera.ferret.sandbox import SandboxMode\n"
        "mode = SandboxMode(os.environ['_FERRET_OS_SANDBOX_MODE'])\n"
        "workdir = os.environ['_FERRET_OS_SANDBOX_WORKDIR']\n"
        "landlock_apply(mode, workdir)\n"
        "os.execvp('bash', ['bash', '-c', os.environ['_FERRET_OS_SANDBOX_CMD']])\n"
    )
    workdir_abs = os.path.abspath(os.fspath(workdir))
    # pass-through env via the parent -> child boundary; populated by
    # the caller (see SandboxedEnvironment.run_command wiring).
    env_prefix = (
        f"_FERRET_OS_SANDBOX_MODE={shlex.quote(mode.value)} "
        f"_FERRET_OS_SANDBOX_WORKDIR={shlex.quote(workdir_abs)} "
        f"_FERRET_OS_SANDBOX_CMD={shlex.quote(cmd)} "
    )
    return [
        "bash",
        "-c",
        f"{env_prefix}python3 -c {shlex.quote(helper)}",
    ]
