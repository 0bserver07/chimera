"""SSH-backed execution environments.

This module ships **two** SSH-backed :class:`Environment` implementations:

1. :class:`SSHEnvironment` — the zero-dependency default. Every operation
   shells out to the system ``ssh`` / ``scp`` binaries via
   :mod:`subprocess`. Authentication relies on your existing SSH config
   (``~/.ssh/config``, agent, key files). Works in any deployment.
2. :class:`AsyncSSHEnvironment` — an opt-in async backend built on
   :mod:`asyncssh`. Adds SFTP-based file transfer, native ProxyJump /
   bastion chains, password / passphrase prompting, persistent
   connections, and a concurrent command pool. Requires the ``ssh``
   extra (``pip install 'chimera-run[ssh]'``).

Both classes share a small set of helpers (retry/backoff, ProxyJump
construction, control-master multiplexing for the subprocess path) and
implement the full :class:`~chimera.env.base.Environment` ABC including
:meth:`checkpoint` / :meth:`restore` (tar-archive snapshot of the
remote workdir streamed back over SFTP / ``ssh tar``).

Typical use (subprocess, no extra deps)::

    env = SSHEnvironment(host="user@example.com", workdir="/srv/chimera")
    env.setup()
    result = env.run_bash("ls -la")
    env.cleanup()

Typical use (asyncssh, native SFTP, persistent connection)::

    # Requires `pip install 'chimera-run[ssh]'`
    env = AsyncSSHEnvironment(
        host="example.com",
        username="alice",
        workdir="/srv/chimera",
        proxy_jump="bastion.example.com",
        password=None,  # or a string / callable for prompting
        client_keys=["/home/u/.ssh/id_ed25519"],
    )
    env.setup()
    env.run_bash("pytest -q")
    env.cleanup()
"""

from __future__ import annotations

import io
import os
import random
import shlex
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Callable

from chimera.env.base import Environment
from chimera.types import CommandResult, TestResult

if TYPE_CHECKING:
    from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Optional asyncssh import (graceful — keeps subprocess path zero-dep)
# ---------------------------------------------------------------------------

# asyncio is stdlib and can never fail to import, so it lives OUTSIDE the
# try: when it shared the block with asyncssh, a missing extra blanked it to
# None too. That made the async backend untestable against a fake transport
# (and would have surfaced as a confusing AttributeError had any code path
# reached it without asyncssh).
import asyncio

try:
    import asyncssh  # type: ignore[import-not-found]

    _ASYNCSSH_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised in env without the extra
    asyncssh = None  # type: ignore[assignment]
    _ASYNCSSH_AVAILABLE = False


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _dirname(path: str) -> str:
    """Pure-Python POSIX ``dirname`` (avoids platform-specific imports).

    The remote may be Linux or macOS, but never Windows in practice for
    these classes, so the simple ``rsplit("/")`` is safe.

    Args:
        path: A POSIX-style path.

    Returns:
        The parent directory, or ``"."`` when ``path`` has no slash.
    """
    if "/" not in path:
        return "."
    head = path.rsplit("/", 1)[0]
    return head or "/"


def _retry_with_backoff(
    fn: Callable[[], Any],
    *,
    retries: int,
    initial_delay: float,
    max_delay: float,
    exceptions: tuple[type[BaseException], ...],
    jitter: bool = True,
) -> Any:
    """Run ``fn`` with exponential backoff on the listed exceptions.

    Args:
        fn: Zero-arg callable to invoke.
        retries: Maximum number of *additional* attempts after the first.
            ``retries=0`` means "try once, no retry".
        initial_delay: Sleep before the second attempt (seconds).
        max_delay: Cap on the sleep before any single attempt.
        exceptions: Exception classes that trigger a retry.
        jitter: When ``True``, multiplies the delay by ``random.uniform(0.5, 1.5)``
            to avoid thundering-herd retry storms.

    Returns:
        Whatever ``fn`` returns on the first successful attempt.

    Raises:
        Whatever ``fn`` raises on the *final* attempt (re-raised after the
        retry budget is exhausted).
    """
    delay = initial_delay
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except exceptions as exc:
            last_exc = exc
            if attempt >= retries:
                break
            sleep_for = min(delay, max_delay)
            if jitter:
                sleep_for *= random.uniform(0.5, 1.5)
            time.sleep(sleep_for)
            delay = min(delay * 2, max_delay)
    # Type narrowing: we only reach this after a raised exception.
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# SSHEnvironment — subprocess-based default (zero deps)
# ---------------------------------------------------------------------------


class SSHEnvironment(Environment):
    """Execute commands and move files on a remote host over OpenSSH.

    Default operations are stateless — each call shells out to
    ``subprocess.run(["ssh", ...])``. When ``persistent=True`` the class
    enables OpenSSH's *control-master* multiplexing so subsequent calls
    reuse a single TCP/auth handshake (huge latency win for
    chatty agent loops).

    Args:
        host: SSH destination as accepted by ``ssh(1)``. May be a bare
            ``hostname``, ``user@hostname``, or any alias defined in
            ``~/.ssh/config``.
        workdir: Remote working directory. Every shell command runs as
            ``cd <workdir> && <cmd>`` so relative paths in tools land
            in the project tree, not in the user's home directory.
        port: TCP port the remote sshd listens on. Defaults to 22.
        identity_file: Optional path to a private key file
            (``-i <path>``). When ``None``, ``ssh`` falls back to
            ``ssh-agent`` and the keys named in your config.
        ssh_options: Extra ``-o key=value`` overrides applied verbatim.
            Useful for ``StrictHostKeyChecking=no`` in CI,
            ``ServerAliveInterval``, etc.
        proxy_jump: One or more bastion hops as a comma-separated string
            (``"jump1.example.com,jump2.example.com"``). Translated into
            ``-o ProxyJump=…``; takes precedence over a ``ProxyJump``
            entry in ``ssh_options``. ``None`` (default) means direct
            connection or whatever ``~/.ssh/config`` already specifies.
        timeout: Default per-command wall-clock timeout in seconds.
        test_cmd: Command run by :meth:`run_tests`. Defaults to
            ``python -m pytest`` to match :class:`LocalEnvironment`.
        retries: Number of additional attempts on transient SSH failures
            (network blip, sshd restart). Default ``0`` keeps behavior
            identical to direct ``subprocess.run``.
        retry_initial_delay: Seconds to sleep before the first retry.
        retry_max_delay: Cap on the per-attempt sleep.
        persistent: Enable OpenSSH control-master multiplexing. When
            ``True``, :meth:`setup` opens a master connection and every
            subsequent call shares it via a per-instance Unix socket
            under :func:`tempfile.gettempdir`. Significantly reduces
            tail-latency for chatty workloads.
        max_concurrency: Worker count for :meth:`run_bash_many` and
            :meth:`upload_files`. Defaults to ``5`` — a sensible upper
            bound for the OpenSSH MaxSessions default of 10.
        password: Optional password / passphrase. When set, an askpass
            wrapper script is generated at :meth:`setup` time so OpenSSH
            can read the secret without an interactive TTY. The wrapper
            is removed on :meth:`cleanup`. NEVER commit this; pass it
            from a secret manager.
    """

    def __init__(
        self,
        host: str,
        *,
        workdir: str = ".",
        port: int = 22,
        identity_file: str | None = None,
        ssh_options: dict[str, str] | None = None,
        proxy_jump: str | None = None,
        timeout: int = 300,
        test_cmd: str = "python -m pytest",
        retries: int = 0,
        retry_initial_delay: float = 1.0,
        retry_max_delay: float = 30.0,
        persistent: bool = False,
        max_concurrency: int = 5,
        password: str | None = None,
    ) -> None:
        if not host:
            raise ValueError("SSHEnvironment requires a non-empty host")
        self.host = host
        self.workdir = workdir
        self.port = port
        self.identity_file = identity_file
        self.ssh_options = dict(ssh_options or {})
        if proxy_jump:
            # Explicit kwarg takes precedence over an earlier ssh_options
            # entry — keeps the constructor surface unambiguous.
            self.ssh_options["ProxyJump"] = proxy_jump
        self.proxy_jump = proxy_jump
        self.timeout = timeout
        self.test_cmd = test_cmd
        self.retries = retries
        self.retry_initial_delay = retry_initial_delay
        self.retry_max_delay = retry_max_delay
        self.persistent = persistent
        self.max_concurrency = max_concurrency
        self.password = password

        # Allocated lazily during setup() so unrelated tests can construct
        # the env without touching the filesystem.
        self._control_path: str | None = None
        self._askpass_path: str | None = None
        self._executor: ThreadPoolExecutor | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ssh_prefix(self) -> list[str]:
        """Build the leading ``["ssh", ...]`` argv shared by every call.

        Includes control-master flags when :attr:`persistent` is on so
        subsequent calls latch onto the master socket.
        """
        cmd: list[str] = ["ssh"]
        if self.port and self.port != 22:
            cmd.extend(["-p", str(self.port)])
        if self.identity_file:
            cmd.extend(["-i", self.identity_file])
        for key, value in self.ssh_options.items():
            cmd.extend(["-o", f"{key}={value}"])
        if self.persistent and self._control_path:
            # ControlMaster=auto on every call lets the first one open
            # the master and the rest attach. ControlPersist keeps the
            # socket alive for 60s after cleanup() in case a stray
            # background task was still using it.
            cmd.extend(
                [
                    "-o",
                    "ControlMaster=auto",
                    "-o",
                    f"ControlPath={self._control_path}",
                    "-o",
                    "ControlPersist=60s",
                ]
            )
        cmd.append(self.host)
        return cmd

    def _scp_prefix(self) -> list[str]:
        """Build the ``["scp", ...]`` argv used by file-transfer helpers.

        ``scp(1)`` shares ssh_options + identity_file but uses ``-P`` for
        the port (note the capital P) instead of ``-p``.
        """
        cmd: list[str] = ["scp"]
        if self.port and self.port != 22:
            cmd.extend(["-P", str(self.port)])
        if self.identity_file:
            cmd.extend(["-i", self.identity_file])
        for key, value in self.ssh_options.items():
            cmd.extend(["-o", f"{key}={value}"])
        if self.persistent and self._control_path:
            cmd.extend(["-o", f"ControlPath={self._control_path}"])
        return cmd

    def _wrap_remote(self, remote_cmd: str) -> str:
        """Prepend ``cd <workdir> &&`` so commands land in the project tree.

        Args:
            remote_cmd: The user-supplied command string.

        Returns:
            ``cd <quoted_workdir> && <remote_cmd>`` (or just
            ``remote_cmd`` when ``workdir`` is the default ``.``).
        """
        if not self.workdir or self.workdir == ".":
            return remote_cmd
        return f"cd {shlex.quote(self.workdir)} && {remote_cmd}"

    def _invoke(
        self,
        argv: Sequence[str],
        *,
        timeout: int | None = None,
        input_text: str | None = None,
        binary_input: bytes | None = None,
        capture_binary: bool = False,
    ) -> subprocess.CompletedProcess[Any]:
        """Run ``argv`` via :func:`subprocess.run` with retry support.

        Centralizing the call lets the test suite patch a single symbol
        (``chimera.env.ssh.subprocess.run``) and inspect the constructed
        argv without having to mock anywhere else.

        Args:
            argv: The full command argv.
            timeout: Per-call wall-clock limit. ``None`` uses :attr:`timeout`.
            input_text: Optional stdin payload (text mode).
            binary_input: Optional stdin payload (bytes mode). Mutually
                exclusive with ``input_text``.
            capture_binary: When ``True``, return ``stdout`` / ``stderr``
                as :class:`bytes`; otherwise as :class:`str`.

        Returns:
            The completed process. Caller inspects ``returncode`` and
            ``stdout`` / ``stderr``.
        """
        if input_text is not None and binary_input is not None:
            raise ValueError("input_text and binary_input are mutually exclusive")

        env = os.environ.copy()
        if self._askpass_path is not None:
            # SSH_ASKPASS_REQUIRE=force makes ssh use the helper even
            # when stdin is a TTY; SETSID detaches us from the
            # controlling terminal so the helper is invoked.
            env["SSH_ASKPASS"] = self._askpass_path
            env["SSH_ASKPASS_REQUIRE"] = "force"
            env["DISPLAY"] = env.get("DISPLAY", ":0")

        def _attempt() -> subprocess.CompletedProcess[Any]:
            return subprocess.run(
                list(argv),
                capture_output=True,
                text=not capture_binary,
                timeout=timeout if timeout is not None else self.timeout,
                input=binary_input if binary_input is not None else input_text,
                check=False,
                env=env,
            )

        if self.retries <= 0:
            return _attempt()
        # Treat TimeoutExpired and any subprocess-level OSError as transient.
        return _retry_with_backoff(  # type: ignore[no-any-return]
            _attempt,
            retries=self.retries,
            initial_delay=self.retry_initial_delay,
            max_delay=self.retry_max_delay,
            exceptions=(subprocess.TimeoutExpired, OSError),
        )

    def _write_askpass(self, password: str) -> str:
        """Write a one-shot askpass helper for non-interactive auth.

        OpenSSH only consults ``$SSH_ASKPASS`` when no controlling TTY
        is available (or when ``SSH_ASKPASS_REQUIRE=force``). The helper
        simply ``printf``-s the password — that is enough for keyboard-
        interactive auth and key passphrases.

        Returns:
            Absolute path to the helper script (mode ``0o700``).
        """
        fd, path = tempfile.mkstemp(prefix="chimera_askpass_", suffix=".sh")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write("#!/bin/sh\n")
                # shlex.quote handles shell-meta inside the password.
                fh.write(f"printf %s {shlex.quote(password)}\n")
            os.chmod(path, 0o700)
        except Exception:
            # Best-effort cleanup if write/chmod fails.
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        return path

    # ------------------------------------------------------------------
    # Environment ABC implementation
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Probe the remote host and (optionally) open a control-master.

        When :attr:`password` is set, an askpass helper is materialized
        first so the probe itself can authenticate non-interactively.
        When :attr:`persistent` is set, the probe is run with
        ``ControlMaster=auto`` so it doubles as the master-connection
        opener.

        Raises:
            ConnectionError: When the probe exits non-zero or times out.
        """
        if self.password is not None and self._askpass_path is None:
            self._askpass_path = self._write_askpass(self.password)
        if self.persistent and self._control_path is None:
            # %h%p%r is the canonical ssh ControlPath template (host/port/
            # remote-user). We add a UUID to disambiguate repeated
            # constructions of the same host pair within one process.
            self._control_path = os.path.join(
                tempfile.gettempdir(),
                f"chimera-ssh-{uuid.uuid4().hex}.sock",
            )
        if self._executor is None and self.max_concurrency > 1:
            self._executor = ThreadPoolExecutor(
                max_workers=self.max_concurrency,
                thread_name_prefix="chimera-ssh",
            )

        argv = [*self._ssh_prefix(), "true"]
        try:
            result = self._invoke(argv, timeout=min(self.timeout, 30))
        except subprocess.TimeoutExpired as exc:
            raise ConnectionError(
                f"SSH probe to {self.host} timed out after {exc.timeout}s"
            ) from exc
        if result.returncode != 0:
            raise ConnectionError(
                f"SSH probe to {self.host} failed (exit {result.returncode}): "
                f"{result.stderr.strip() or '(no stderr)'}"
            )

    def cleanup(self) -> None:
        """Tear down control-master + askpass + executor (idempotent)."""
        if self.persistent and self._control_path:
            # Best-effort: don't blow up on a stale socket. If the master
            # already died, ``ssh -O exit`` returns non-zero but we don't
            # care.
            try:
                subprocess.run(
                    [*self._ssh_prefix()[:-1], "-O", "exit", self.host],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                pass
            self._control_path = None
        if self._askpass_path:
            try:
                os.unlink(self._askpass_path)
            except OSError:
                pass
            self._askpass_path = None
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

    def run_bash(self, cmd: str, timeout: int | None = None) -> CommandResult:
        """Execute ``cmd`` on the remote host inside :attr:`workdir`.

        Args:
            cmd: Shell command to run.
            timeout: Optional override for :attr:`timeout`.

        Returns:
            A :class:`~chimera.types.CommandResult`. Timeouts surface as
            exit code ``124`` (matching GNU ``timeout(1)``).
        """
        argv = [*self._ssh_prefix(), self._wrap_remote(cmd)]
        try:
            result = self._invoke(argv, timeout=timeout)
        except subprocess.TimeoutExpired:
            return CommandResult(stdout="", stderr="Command timed out", exit_code=124)
        return CommandResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )

    def run_command(
        self, cmd: str, timeout: int = 120, shell_name: str = "main"
    ) -> CommandResult:
        """Alias for :meth:`run_bash` (Environment ABC parity).

        ``shell_name`` is accepted for signature parity with
        :class:`LocalEnvironment` but ignored — every SSH call is its
        own short-lived shell unless :attr:`persistent` reuses the master.
        """
        del shell_name
        return self.run_bash(cmd, timeout=timeout)

    def run_bash_many(
        self,
        cmds: Sequence[str],
        *,
        timeout: int | None = None,
    ) -> list[CommandResult]:
        """Run multiple commands concurrently against the same host.

        Backed by :class:`concurrent.futures.ThreadPoolExecutor` with
        :attr:`max_concurrency` workers. When ``max_concurrency=1`` (or
        the executor wasn't allocated yet) the calls fall back to
        sequential execution so the behavior is well-defined even before
        :meth:`setup` has been called.

        Args:
            cmds: Iterable of command strings.
            timeout: Per-command timeout (not the total).

        Returns:
            A list of :class:`CommandResult` aligned positionally with ``cmds``.
        """
        if self._executor is None or self.max_concurrency <= 1:
            return [self.run_bash(c, timeout=timeout) for c in cmds]
        futures = [self._executor.submit(self.run_bash, c, timeout) for c in cmds]
        return [f.result() for f in futures]

    def read_file(self, path: str) -> str:
        """Fetch a remote text file via ``ssh <host> cat``.

        For binaries, prefer :meth:`download_file` which uses ``scp`` and
        preserves byte boundaries.

        Args:
            path: Workspace-relative or absolute remote path.

        Returns:
            The text contents.

        Raises:
            FileNotFoundError: When the remote ``cat`` exits non-zero.
        """
        remote = f"cat {shlex.quote(path)}"
        argv = [*self._ssh_prefix(), self._wrap_remote(remote)]
        result = self._invoke(argv)
        if result.returncode != 0:
            raise FileNotFoundError(
                f"Remote read failed for {path!r}: "
                f"{result.stderr.strip() or '(no stderr)'}"
            )
        # ``capture_binary=False`` (default) means stdout is str.
        return result.stdout  # type: ignore[no-any-return]

    def write_file(self, path: str, content: str) -> None:
        """Upload ``content`` to ``path`` via ``ssh <host> tee``.

        Args:
            path: Remote path (workspace-relative or absolute).
            content: Text body to write.

        Raises:
            OSError: When the remote command exits non-zero.
        """
        remote = (
            f"mkdir -p {shlex.quote(_dirname(path))} && "
            f"tee {shlex.quote(path)} > /dev/null"
        )
        argv = [*self._ssh_prefix(), self._wrap_remote(remote)]
        result = self._invoke(argv, input_text=content)
        if result.returncode != 0:
            raise OSError(
                f"Remote write failed for {path!r}: "
                f"{result.stderr.strip() or '(no stderr)'}"
            )

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """Upload a local file to the remote host via ``scp``.

        Uses ``scp`` (which is SFTP-backed in modern OpenSSH) instead of
        piping through ``tee`` so binaries stay byte-exact and large
        files don't bloat the argv.

        Args:
            local_path: Path on the local filesystem.
            remote_path: Destination path on the remote host. The
                workdir is *not* prepended — pass an absolute remote
                path or include the workdir explicitly.
        """
        # Ensure the destination directory exists; scp itself won't mkdir.
        self.run_bash(f"mkdir -p {shlex.quote(_dirname(remote_path))}")
        argv = [
            *self._scp_prefix(),
            local_path,
            f"{self.host}:{remote_path}",
        ]
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=self.timeout, check=False
        )
        if result.returncode != 0:
            raise OSError(
                f"scp upload failed for {local_path!r} -> {remote_path!r}: "
                f"{result.stderr.strip() or '(no stderr)'}"
            )

    def download_file(self, remote_path: str, local_path: str) -> None:
        """Download a remote file via ``scp`` into ``local_path``.

        Args:
            remote_path: Absolute remote path (workdir is *not* prepended).
            local_path: Destination on the local filesystem.

        Raises:
            FileNotFoundError: When the remote file is missing or unreadable.
        """
        argv = [
            *self._scp_prefix(),
            f"{self.host}:{remote_path}",
            local_path,
        ]
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=self.timeout, check=False
        )
        if result.returncode != 0:
            raise FileNotFoundError(
                f"scp download failed for {remote_path!r} -> {local_path!r}: "
                f"{result.stderr.strip() or '(no stderr)'}"
            )

    def upload_files(
        self,
        pairs: Sequence[tuple[str, str]],
    ) -> None:
        """Upload multiple ``(local, remote)`` pairs concurrently.

        Mirrors :meth:`run_bash_many`'s thread-pool semantics. Useful for
        seeding a workspace from a local checkout in one round-trip
        instead of N sequential scp's.
        """
        if self._executor is None or self.max_concurrency <= 1:
            for local, remote in pairs:
                self.upload_file(local, remote)
            return
        futures = [self._executor.submit(self.upload_file, l_, r_) for l_, r_ in pairs]
        for f in futures:
            f.result()

    def list_files(self, pattern: str = "**/*") -> list[str]:
        """Enumerate files matching ``pattern`` under :attr:`workdir`.

        Implemented as ``find <workdir> -type f`` followed by client-side
        glob filtering. ``pattern`` semantics match :mod:`fnmatch`.

        Args:
            pattern: Glob pattern relative to :attr:`workdir`.

        Returns:
            Sorted list of workspace-relative paths.
        """
        import fnmatch

        remote = "find . -type f -print"
        argv = [*self._ssh_prefix(), self._wrap_remote(remote)]
        result = self._invoke(argv)
        if result.returncode != 0:
            return []
        paths = [line.lstrip("./") for line in result.stdout.splitlines() if line]
        if pattern in ("**/*", "*", ""):
            return sorted(paths)
        return sorted(p for p in paths if fnmatch.fnmatch(p, pattern))

    def run_tests(self) -> TestResult:
        """Run :attr:`test_cmd` remotely and return a stub :class:`TestResult`.

        Parsing pytest output is deferred — the scaffold returns the raw
        remote output with zero counts so the field types stay honest.
        """
        result = self.run_bash(self.test_cmd)
        return TestResult(
            passed=0,
            failed=0,
            errors=0,
            output=result.stdout + result.stderr,
        )

    # ------------------------------------------------------------------
    # Checkpoint / restore — tar-archive snapshot of the remote workdir
    # ------------------------------------------------------------------

    def checkpoint(self) -> str:
        """Snapshot :attr:`workdir` into a remote tarball and return its id.

        The snapshot lives under ``~/.chimera/ssh-checkpoints/`` on the
        remote host. The checkpoint id is a short opaque token; pass it
        verbatim to :meth:`restore`. Falls back to raising
        :class:`NotImplementedError` only when :attr:`workdir` is the
        default ``.`` (no defined snapshot scope).

        Returns:
            Opaque checkpoint id (UUID hex).

        Raises:
            NotImplementedError: When :attr:`workdir` is unset / ``.``.
            OSError: When the remote ``tar`` exits non-zero.
        """
        if not self.workdir or self.workdir == ".":
            raise NotImplementedError(
                "SSHEnvironment.checkpoint() requires an explicit workdir; "
                "pass workdir=… so the snapshot has a well-defined scope."
            )
        cid = uuid.uuid4().hex[:16]
        # ``tar`` runs in a parent dir to keep relative paths inside the
        # archive. Use ``mkdir -p`` so the checkpoint store is created on
        # first use without a separate setup step.
        #
        # NOTE: ``$HOME`` is left UNQUOTED on purpose. Wrapping the path
        # in ``shlex.quote`` would single-quote ``$HOME`` and prevent
        # POSIX shell expansion, leading ``tar`` to write to a literal
        # ``$HOME/.chimera/...`` directory that doesn't exist (surfaced
        # by the live Docker-sshd test in M9). The components we
        # interpolate into the path (``cid`` is uuid4 hex, the rest is
        # constant) contain no shell metacharacters, so leaving them
        # unquoted is safe.
        store = "$HOME/.chimera/ssh-checkpoints"
        snapshot = f"{store}/{cid}.tar.gz"
        # We deliberately don't ``cd`` into the workdir here — we tar
        # *from* its parent so restore can rebuild the workdir wholesale.
        parent = _dirname(self.workdir.rstrip("/")) or "/"
        leaf = self.workdir.rstrip("/").rsplit("/", 1)[-1]
        cmd = (
            f"mkdir -p {store} && "
            f"tar -czf {snapshot} "
            f"-C {shlex.quote(parent)} {shlex.quote(leaf)}"
        )
        argv = [*self._ssh_prefix(), cmd]
        result = self._invoke(argv)
        if result.returncode != 0:
            raise OSError(
                f"checkpoint tar failed: {result.stderr.strip() or '(no stderr)'}"
            )
        return cid

    def restore(self, checkpoint_id: str) -> None:
        """Restore :attr:`workdir` from a previously-saved checkpoint.

        Args:
            checkpoint_id: Token returned by a prior :meth:`checkpoint` call.

        Raises:
            FileNotFoundError: When the checkpoint id is unknown.
            OSError: When the remote ``tar`` extraction fails.
        """
        if not checkpoint_id or "/" in checkpoint_id or ".." in checkpoint_id:
            # Defensive — stop a malicious id from escaping the store.
            raise ValueError(f"invalid checkpoint id: {checkpoint_id!r}")
        # Same ``$HOME`` quoting caveat as :meth:`checkpoint` — leave the
        # snapshot path unquoted so the remote shell expands ``$HOME``.
        # ``checkpoint_id`` is validated above to be slash/dot-free.
        store = "$HOME/.chimera/ssh-checkpoints"
        snapshot = f"{store}/{checkpoint_id}.tar.gz"
        parent = _dirname(self.workdir.rstrip("/")) or "/"
        leaf = self.workdir.rstrip("/").rsplit("/", 1)[-1]
        # ``rm -rf`` then untar gives a clean restore even if files were
        # added since the checkpoint. The ``test -f`` guard surfaces a
        # missing-checkpoint error before we delete anything.
        cmd = (
            f"test -f {snapshot} && "
            f"rm -rf {shlex.quote(self.workdir)} && "
            f"tar -xzf {snapshot} -C {shlex.quote(parent)}"
        )
        del leaf  # only used for symmetry in checkpoint(); not needed here.
        argv = [*self._ssh_prefix(), cmd]
        result = self._invoke(argv)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if "test -f" in cmd and result.returncode == 1:
                raise FileNotFoundError(
                    f"checkpoint {checkpoint_id!r} not found on {self.host}"
                )
            raise OSError(
                f"restore tar failed: {stderr or '(no stderr)'}"
            )


# ---------------------------------------------------------------------------
# AsyncSSHEnvironment — asyncssh-based, native SFTP, persistent connection
# ---------------------------------------------------------------------------


class AsyncSSHEnvironment(Environment):
    """asyncssh-backed SSH environment with native SFTP + persistent conn.

    This is the higher-fidelity sibling of :class:`SSHEnvironment`. It
    requires the optional ``asyncssh`` dependency and exposes the
    synchronous :class:`Environment` ABC by driving asyncssh on a
    private :mod:`asyncio` event loop.

    Why a private loop? The :class:`Environment` ABC is sync. Spinning
    up our own loop keeps the public surface unchanged while letting us
    use asyncssh's first-class APIs. If you need the async API directly,
    use :meth:`run_bash_async` / :meth:`read_file_async` / etc.

    Args:
        host: Hostname or IP of the remote.
        port: TCP port (defaults to 22).
        username: Remote username. Defaults to the local user.
        workdir: Remote working directory. Same semantics as
            :class:`SSHEnvironment`.
        client_keys: Iterable of private-key paths. ``None`` means use
            the ssh-agent / default identity discovery.
        password: Optional password. Sent during auth handshake.
        passphrase: Optional passphrase for an encrypted private key.
        known_hosts: Path to a known_hosts file. ``None`` (default)
            uses ``~/.ssh/known_hosts``. Pass an empty tuple to disable
            host-key checking (NOT recommended outside ephemeral CI).
        proxy_jump: One or more bastion hops as a comma-separated string
            (``"jump1,jump2"``) or a list of host strings. Translated
            into an asyncssh ``connect()`` ``tunnel=`` chain.
        ssh_options: Extra kwargs forwarded to :func:`asyncssh.connect`.
            Shadows any kwarg this class manages explicitly.
        timeout: Per-command wall-clock timeout in seconds.
        retries: Additional reconnection attempts on connection-level
            errors. ``0`` (default) keeps behavior identical to a single
            ``asyncssh.connect()``.
        retry_initial_delay: Seconds before the first retry.
        retry_max_delay: Cap on the per-attempt sleep.
        max_concurrency: Channel-level concurrency for batched ops
            (:meth:`run_bash_many`, :meth:`upload_files`). Backed by an
            asyncio Semaphore.
        test_cmd: Command run by :meth:`run_tests`.

    Raises:
        ImportError: When :mod:`asyncssh` is not installed. Install via
            ``pip install 'chimera-run[ssh]'``.
        ValueError: When ``host`` is empty.
    """

    def __init__(
        self,
        host: str,
        *,
        port: int = 22,
        username: str | None = None,
        workdir: str = ".",
        client_keys: Sequence[str] | None = None,
        password: str | None = None,
        passphrase: str | None = None,
        known_hosts: str | tuple[Any, ...] | None = None,
        proxy_jump: str | Sequence[str] | None = None,
        ssh_options: dict[str, Any] | None = None,
        timeout: int = 300,
        retries: int = 0,
        retry_initial_delay: float = 1.0,
        retry_max_delay: float = 30.0,
        max_concurrency: int = 5,
        test_cmd: str = "python -m pytest",
    ) -> None:
        if not _ASYNCSSH_AVAILABLE:
            raise ImportError(
                "AsyncSSHEnvironment requires the 'asyncssh' package. "
                "Install with: pip install 'chimera-run[ssh]'"
            )
        if not host:
            raise ValueError("AsyncSSHEnvironment requires a non-empty host")
        self.host = host
        self.port = port
        self.username = username
        self.workdir = workdir
        self.client_keys = list(client_keys) if client_keys else None
        self.password = password
        self.passphrase = passphrase
        self.known_hosts = known_hosts
        if isinstance(proxy_jump, str):
            self.proxy_jump: list[str] | None = [
                h.strip() for h in proxy_jump.split(",") if h.strip()
            ]
        elif proxy_jump:
            self.proxy_jump = list(proxy_jump)
        else:
            self.proxy_jump = None
        self.ssh_options = dict(ssh_options or {})
        self.timeout = timeout
        self.retries = retries
        self.retry_initial_delay = retry_initial_delay
        self.retry_max_delay = retry_max_delay
        self.max_concurrency = max_concurrency
        self.test_cmd = test_cmd

        # asyncio internals — built lazily during setup().
        self._loop: Any | None = None
        self._conn: Any | None = None  # asyncssh.SSHClientConnection
        self._sem: Any | None = None  # asyncio.Semaphore

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect_kwargs(self) -> dict[str, Any]:
        """Build the kwargs dict passed to :func:`asyncssh.connect`."""
        kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
        }
        if self.username:
            kwargs["username"] = self.username
        if self.client_keys:
            kwargs["client_keys"] = self.client_keys
        if self.password is not None:
            kwargs["password"] = self.password
        if self.passphrase is not None:
            kwargs["passphrase"] = self.passphrase
        if self.known_hosts is not None:
            kwargs["known_hosts"] = self.known_hosts
        # Caller-supplied options override the defaults above.
        kwargs.update(self.ssh_options)
        return kwargs

    async def _open_connection(self) -> Any:
        """Open the asyncssh connection (with optional ProxyJump tunnel).

        For multi-hop ProxyJump we open one connection per hop and stack
        them via the ``tunnel=`` kwarg, which is exactly how asyncssh's
        examples wire bastions.
        """
        if not self.proxy_jump:
            return await asyncssh.connect(**self._connect_kwargs())

        # Open the first jump host with default-ish kwargs (only host
        # specified — credentials come from the user's agent / config).
        # Each subsequent connection tunnels through the prior one.
        tunnel: Any | None = None
        for hop in self.proxy_jump:
            hop_kwargs: dict[str, Any] = {"host": hop}
            if tunnel is not None:
                hop_kwargs["tunnel"] = tunnel
            tunnel = await asyncssh.connect(**hop_kwargs)
        # Final hop is the destination; reuse our credentials.
        final_kwargs = self._connect_kwargs()
        final_kwargs["tunnel"] = tunnel
        return await asyncssh.connect(**final_kwargs)

    async def _connect_with_retry(self) -> Any:
        """Open the connection with optional exponential-backoff retries."""
        delay = self.retry_initial_delay
        last_exc: BaseException | None = None
        for attempt in range(self.retries + 1):
            try:
                return await self._open_connection()
            except (OSError, asyncssh.Error) as exc:
                last_exc = exc
                if attempt >= self.retries:
                    break
                sleep_for = min(delay, self.retry_max_delay) * random.uniform(0.5, 1.5)
                await asyncio.sleep(sleep_for)
                delay = min(delay * 2, self.retry_max_delay)
        assert last_exc is not None
        raise last_exc

    def _ensure_loop(self) -> Any:
        """Return (or create) the per-instance event loop."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run(self, coro: Any) -> Any:
        """Drive a coroutine on the private loop (sync glue)."""
        loop = self._ensure_loop()
        return loop.run_until_complete(coro)

    def _wrap_remote(self, cmd: str) -> str:
        """Same workdir prefix as :class:`SSHEnvironment`."""
        if not self.workdir or self.workdir == ".":
            return cmd
        return f"cd {shlex.quote(self.workdir)} && {cmd}"

    def _require_conn(self) -> Any:
        """Return the live connection or raise if setup() was skipped."""
        if self._conn is None:
            raise RuntimeError(
                "AsyncSSHEnvironment.setup() must be called before use."
            )
        return self._conn

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Open the persistent asyncssh connection (with retries).

        Raises:
            ConnectionError: When all reconnection attempts fail.
        """
        try:
            self._conn = self._run(self._connect_with_retry())
        except (OSError, asyncssh.Error) as exc:
            raise ConnectionError(
                f"AsyncSSH connect to {self.host} failed: {exc}"
            ) from exc
        self._sem = asyncio.Semaphore(self.max_concurrency)

    def cleanup(self) -> None:
        """Close the connection and tear down the loop."""
        if self._conn is not None:
            try:
                self._conn.close()
                self._run(self._conn.wait_closed())
            except Exception:
                pass
            self._conn = None
        if self._loop is not None:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None
        self._sem = None

    # ------------------------------------------------------------------
    # Async primitives (callable directly when caller has its own loop)
    # ------------------------------------------------------------------

    async def run_bash_async(
        self, cmd: str, timeout: int | None = None
    ) -> CommandResult:
        """Async sibling of :meth:`run_bash`."""
        conn = self._require_conn()
        wrapped = self._wrap_remote(cmd)
        try:
            result = await asyncio.wait_for(
                conn.run(wrapped, check=False),
                timeout=timeout if timeout is not None else self.timeout,
            )
        except asyncio.TimeoutError:
            return CommandResult(stdout="", stderr="Command timed out", exit_code=124)
        return CommandResult(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            exit_code=result.exit_status if result.exit_status is not None else -1,
        )

    async def read_file_async(self, path: str) -> str:
        """Async SFTP-backed read."""
        conn = self._require_conn()
        try:
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(self._resolve_path(path), "r") as fh:
                    data = await fh.read()
                    if isinstance(data, bytes):
                        return data.decode("utf-8")
                    return data  # type: ignore[no-any-return]
        except (asyncssh.SFTPError, OSError) as exc:
            raise FileNotFoundError(f"SFTP read failed for {path!r}: {exc}") from exc

    async def write_file_async(self, path: str, content: str) -> None:
        """Async SFTP-backed write (creates parent dirs)."""
        conn = self._require_conn()
        full = self._resolve_path(path)
        async with conn.start_sftp_client() as sftp:
            parent = _dirname(full)
            if parent and parent != ".":
                # makedirs is idempotent in asyncssh's SFTPClient.
                try:
                    await sftp.makedirs(parent, exist_ok=True)
                except (asyncssh.SFTPError, OSError):
                    # Older asyncssh versions raise on existing dirs even
                    # with exist_ok=True; ignore — the next open() will
                    # surface a real error.
                    pass
            async with sftp.open(full, "w") as fh:
                await fh.write(content)

    def _resolve_path(self, path: str) -> str:
        """Resolve a workspace-relative path against :attr:`workdir`.

        SFTP doesn't honor a remote ``cd``, so we have to materialize
        the absolute path ourselves.
        """
        if path.startswith("/"):
            return path
        if not self.workdir or self.workdir == ".":
            return path
        return f"{self.workdir.rstrip('/')}/{path}"

    # ------------------------------------------------------------------
    # Environment ABC — sync facade over the async primitives
    # ------------------------------------------------------------------

    def run_bash(self, cmd: str, timeout: int | None = None) -> CommandResult:
        """Sync wrapper around :meth:`run_bash_async`."""
        return self._run(self.run_bash_async(cmd, timeout=timeout))  # type: ignore[no-any-return]

    def run_command(
        self, cmd: str, timeout: int = 120, shell_name: str = "main"
    ) -> CommandResult:
        """Alias for :meth:`run_bash` (Environment ABC parity)."""
        del shell_name
        return self.run_bash(cmd, timeout=timeout)

    def run_bash_many(
        self,
        cmds: Sequence[str],
        *,
        timeout: int | None = None,
    ) -> list[CommandResult]:
        """Run multiple commands concurrently against the same connection.

        Concurrency is gated by an :class:`asyncio.Semaphore` sized to
        :attr:`max_concurrency` so we don't exceed the remote's
        ``MaxSessions`` limit.
        """

        async def _gather() -> list[CommandResult]:
            sem = self._sem
            assert sem is not None

            async def _one(c: str) -> CommandResult:
                async with sem:
                    return await self.run_bash_async(c, timeout=timeout)

            return await asyncio.gather(*(_one(c) for c in cmds))

        return self._run(_gather())  # type: ignore[no-any-return]

    def read_file(self, path: str) -> str:
        return self._run(self.read_file_async(path))  # type: ignore[no-any-return]

    def write_file(self, path: str, content: str) -> None:
        self._run(self.write_file_async(path, content))

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """Upload a local file via SFTP (preserves byte boundaries)."""
        async def _do() -> None:
            conn = self._require_conn()
            async with conn.start_sftp_client() as sftp:
                parent = _dirname(remote_path)
                if parent and parent != ".":
                    try:
                        await sftp.makedirs(parent, exist_ok=True)
                    except (asyncssh.SFTPError, OSError):
                        pass
                await sftp.put(local_path, remote_path)

        self._run(_do())

    def download_file(self, remote_path: str, local_path: str) -> None:
        """Download a remote file via SFTP."""
        async def _do() -> None:
            conn = self._require_conn()
            async with conn.start_sftp_client() as sftp:
                await sftp.get(remote_path, local_path)

        self._run(_do())

    def upload_files(self, pairs: Sequence[tuple[str, str]]) -> None:
        """Concurrent SFTP uploads gated by the connection semaphore."""

        async def _gather() -> None:
            conn = self._require_conn()
            sem = self._sem
            assert sem is not None
            async with conn.start_sftp_client() as sftp:

                async def _one(local: str, remote: str) -> None:
                    async with sem:
                        parent = _dirname(remote)
                        if parent and parent != ".":
                            try:
                                await sftp.makedirs(parent, exist_ok=True)
                            except (asyncssh.SFTPError, OSError):
                                pass
                        await sftp.put(local, remote)

                await asyncio.gather(*(_one(l_, r_) for l_, r_ in pairs))

        self._run(_gather())

    def list_files(self, pattern: str = "**/*") -> list[str]:
        """SFTP walk + client-side fnmatch.

        We do an SFTP walk rather than ``find`` because it preserves the
        connection (no extra channel) and gives us proper UTF-8 paths.
        """
        import fnmatch

        async def _walk() -> list[str]:
            conn = self._require_conn()
            base = self.workdir if self.workdir and self.workdir != "." else "."
            collected: list[str] = []
            async with conn.start_sftp_client() as sftp:
                # asyncssh.SFTPClient.scandir is async; recurse manually.
                async def _recurse(d: str, prefix: str) -> None:
                    try:
                        entries = await sftp.readdir(d)
                    except (asyncssh.SFTPError, OSError):
                        return
                    for entry in entries:
                        name = (
                            entry.filename
                            if isinstance(entry.filename, str)
                            else entry.filename.decode("utf-8")
                        )
                        if name in (".", ".."):
                            continue
                        full = f"{d.rstrip('/')}/{name}"
                        rel = f"{prefix}{name}" if not prefix else f"{prefix}/{name}"
                        # Stat-based dir detection — entry.attrs may be None
                        # for some servers; fall back to readdir failure.
                        is_dir = bool(
                            entry.attrs and entry.attrs.permissions
                            and (entry.attrs.permissions & 0o040000)
                        )
                        if is_dir:
                            await _recurse(full, rel)
                        else:
                            collected.append(rel)

                await _recurse(base, "")
            return collected

        paths = self._run(_walk())
        if pattern in ("**/*", "*", ""):
            return sorted(paths)
        return sorted(p for p in paths if fnmatch.fnmatch(p, pattern))

    def run_tests(self) -> TestResult:
        """Run :attr:`test_cmd` remotely and return a stub :class:`TestResult`."""
        result = self.run_bash(self.test_cmd)
        return TestResult(
            passed=0,
            failed=0,
            errors=0,
            output=result.stdout + result.stderr,
        )

    def checkpoint(self) -> str:
        """Snapshot :attr:`workdir` into a remote tarball (same as subprocess)."""
        if not self.workdir or self.workdir == ".":
            raise NotImplementedError(
                "AsyncSSHEnvironment.checkpoint() requires an explicit workdir."
            )
        cid = uuid.uuid4().hex[:16]
        # See SSHEnvironment.checkpoint for why ``$HOME`` stays unquoted.
        store = "$HOME/.chimera/ssh-checkpoints"
        snapshot = f"{store}/{cid}.tar.gz"
        parent = _dirname(self.workdir.rstrip("/")) or "/"
        leaf = self.workdir.rstrip("/").rsplit("/", 1)[-1]
        cmd = (
            f"mkdir -p {store} && "
            f"tar -czf {snapshot} "
            f"-C {shlex.quote(parent)} {shlex.quote(leaf)}"
        )
        result = self.run_bash(cmd)
        if result.exit_code != 0:
            raise OSError(
                f"checkpoint tar failed: {result.stderr.strip() or '(no stderr)'}"
            )
        return cid

    def restore(self, checkpoint_id: str) -> None:
        """Restore from a previously-saved checkpoint."""
        if not checkpoint_id or "/" in checkpoint_id or ".." in checkpoint_id:
            raise ValueError(f"invalid checkpoint id: {checkpoint_id!r}")
        # See SSHEnvironment.restore for why ``$HOME`` stays unquoted.
        store = "$HOME/.chimera/ssh-checkpoints"
        snapshot = f"{store}/{checkpoint_id}.tar.gz"
        parent = _dirname(self.workdir.rstrip("/")) or "/"
        cmd = (
            f"test -f {snapshot} && "
            f"rm -rf {shlex.quote(self.workdir)} && "
            f"tar -xzf {snapshot} -C {shlex.quote(parent)}"
        )
        result = self.run_bash(cmd)
        if result.exit_code != 0:
            stderr = (result.stderr or "").strip()
            if result.exit_code == 1:
                raise FileNotFoundError(
                    f"checkpoint {checkpoint_id!r} not found on {self.host}"
                )
            raise OSError(f"restore tar failed: {stderr or '(no stderr)'}")


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "SSHEnvironment",
    "AsyncSSHEnvironment",
]


# Silence "imported but unused" when asyncssh is missing — io is used by
# tests for binary-readback assertions and we don't want a second import
# in the test path.
_ = io  # noqa: F841
