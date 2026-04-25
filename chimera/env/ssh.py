"""SSH-backed execution environment.

A minimal :class:`SSHEnvironment` implementation that proxies the
:class:`~chimera.env.base.Environment` surface (``run_bash`` /
``read_file`` / ``write_file``) over an OpenSSH client subprocess.

This is the *scaffolding* implementation for issue #127. It uses only
the Python standard library: every operation shells out to the system
``ssh`` binary via :func:`subprocess.run` and authentication relies on
your existing SSH config (``~/.ssh/config``, agent, key files). A richer
async + SFTP-backed implementation (``asyncssh``) is planned as a
follow-up and will live behind an optional ``ssh`` extra; this module
intentionally keeps the dependency surface at zero so it works in
any deployment.

Typical use:

    env = SSHEnvironment(host="user@example.com", workdir="/srv/chimera")
    env.setup()
    result = env.run_bash("ls -la")
    env.cleanup()

Limitations (deferred to follow-up issues):
  * No SFTP — file I/O is implemented via ``ssh cat`` and ``ssh tee``.
    Acceptable for small text files, not for binaries.
  * No ProxyJump / bastion host support beyond what's already in your
    ``~/.ssh/config``.
  * No password / passphrase prompting — assumes key auth via agent.
  * No persistent session multiplexing (each call spawns a fresh ssh).
  * No checkpoint/restore (raises NotImplementedError).
"""

from __future__ import annotations

import shlex
import subprocess
from typing import TYPE_CHECKING

from chimera.env.base import Environment
from chimera.types import CommandResult, TestResult

if TYPE_CHECKING:
    from collections.abc import Sequence


class SSHEnvironment(Environment):
    """Execute commands and move files on a remote host over SSH.

    All operations are stateless — every call shells out to
    ``subprocess.run(["ssh", ...])`` so there is no long-lived
    connection to manage. ``setup()`` runs a one-shot reachability
    probe (``ssh <host> true``) so callers fail fast on misconfiguration
    rather than on the first real tool call.

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
            Useful for ``StrictHostKeyChecking=no`` in CI, ``ProxyJump``,
            ``ServerAliveInterval``, etc.
        timeout: Default per-command wall-clock timeout in seconds.
        test_cmd: Command run by :meth:`run_tests`. Defaults to
            ``python -m pytest`` to match :class:`LocalEnvironment`.
    """

    def __init__(
        self,
        host: str,
        *,
        workdir: str = ".",
        port: int = 22,
        identity_file: str | None = None,
        ssh_options: dict[str, str] | None = None,
        timeout: int = 300,
        test_cmd: str = "python -m pytest",
    ) -> None:
        if not host:
            raise ValueError("SSHEnvironment requires a non-empty host")
        self.host = host
        self.workdir = workdir
        self.port = port
        self.identity_file = identity_file
        self.ssh_options = dict(ssh_options or {})
        self.timeout = timeout
        self.test_cmd = test_cmd

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ssh_prefix(self) -> list[str]:
        """Build the leading ``["ssh", ...]`` argv shared by every call.

        Returns:
            The flag-only prefix; callers append the remote command.
        """
        cmd: list[str] = ["ssh"]
        if self.port and self.port != 22:
            cmd.extend(["-p", str(self.port)])
        if self.identity_file:
            cmd.extend(["-i", self.identity_file])
        for key, value in self.ssh_options.items():
            cmd.extend(["-o", f"{key}={value}"])
        cmd.append(self.host)
        return cmd

    def _wrap_remote(self, remote_cmd: str) -> str:
        """Prepend a ``cd <workdir> &&`` so commands land in the project tree.

        ``ssh`` runs the remote command in the user's login directory by
        default; we explicitly ``cd`` so workspace-relative tool paths
        resolve correctly. ``shlex.quote`` escapes the workdir to keep
        paths with spaces or shell metacharacters from breaking out.

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
    ) -> subprocess.CompletedProcess[str]:
        """Thin wrapper around :func:`subprocess.run` for testability.

        Centralizing the call lets the test suite patch a single symbol
        (``chimera.env.ssh.subprocess.run``) and inspect the constructed
        argv without having to mock anywhere else.

        Args:
            argv: The full command argv (including the leading ``ssh``).
            timeout: Per-call wall-clock limit. ``None`` uses
                ``self.timeout``.
            input_text: Optional stdin payload (used by :meth:`write_file`
                to pipe the file contents into ``tee``).

        Returns:
            The completed process. Caller is responsible for inspecting
            ``returncode`` and ``stdout`` / ``stderr``.
        """
        return subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else self.timeout,
            input=input_text,
            check=False,
        )

    # ------------------------------------------------------------------
    # Environment ABC implementation
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Probe the remote host with ``ssh <host> true``.

        Raises:
            ConnectionError: When the probe exits non-zero (network
                unreachable, auth failure, host key mismatch). The remote
                ``stderr`` is included in the error message so the user
                can debug without re-running by hand.
        """
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
        """No persistent state — kept for ABC compliance."""
        return None

    def run_bash(self, cmd: str, timeout: int | None = None) -> CommandResult:
        """Execute ``cmd`` on the remote host inside ``workdir``.

        Args:
            cmd: Shell command to run.
            timeout: Optional override for the default per-call timeout.

        Returns:
            A :class:`~chimera.types.CommandResult` capturing remote
            stdout / stderr / exit code. Timeouts are surfaced as exit
            code ``124`` (matching the GNU ``timeout(1)`` convention) so
            downstream tools can branch on it.
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

    # Environment ABC names alias the more SSH-idiomatic ``run_bash``.
    def run_command(
        self, cmd: str, timeout: int = 120, shell_name: str = "main"
    ) -> CommandResult:
        """Alias for :meth:`run_bash` (Environment ABC parity).

        ``shell_name`` is accepted for signature parity with
        :class:`LocalEnvironment` but ignored — every SSH call is its
        own short-lived shell.
        """
        del shell_name
        return self.run_bash(cmd, timeout=timeout)

    def read_file(self, path: str) -> str:
        """Fetch a remote file via ``ssh <host> cat <quoted-path>``.

        Args:
            path: Workspace-relative or absolute remote path.

        Returns:
            The text contents of the file.

        Raises:
            FileNotFoundError: When the remote ``cat`` exits non-zero
                (missing file, permission denied). The remote stderr is
                included in the error message.
        """
        remote = f"cat {shlex.quote(path)}"
        argv = [*self._ssh_prefix(), self._wrap_remote(remote)]
        result = self._invoke(argv)
        if result.returncode != 0:
            raise FileNotFoundError(
                f"Remote read failed for {path!r}: "
                f"{result.stderr.strip() or '(no stderr)'}"
            )
        return result.stdout

    def write_file(self, path: str, content: str) -> None:
        """Upload ``content`` to ``path`` via ``ssh <host> tee``.

        Pipes the file body to a remote ``tee`` (output redirected to
        ``/dev/null`` so the data isn't echoed back into ``stdout``).
        Parent directories are created with ``mkdir -p`` to mirror
        :class:`LocalEnvironment` behavior.

        Args:
            path: Remote path (workspace-relative or absolute).
            content: Text body to write.

        Raises:
            OSError: When the remote command exits non-zero (full disk,
                permission denied). Remote stderr is included.
        """
        # ``mkdir -p $(dirname …)`` ensures the destination is writable
        # before ``tee`` opens the file, matching the local-FS contract.
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

    def list_files(self, pattern: str = "**/*") -> list[str]:
        """Enumerate files matching ``pattern`` under ``workdir``.

        Implemented as ``find <workdir> -type f`` followed by client-side
        glob filtering. ``pattern`` semantics match :mod:`fnmatch`, not
        full bash extglob, but the common cases (``*.py``, ``**/*.md``)
        work as expected.

        Args:
            pattern: Glob pattern relative to ``workdir``.

        Returns:
            Sorted list of workspace-relative paths.
        """
        import fnmatch

        # ``-print`` is the portable spelling; ``-printf`` isn't on BSD/macOS.
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

        Parsing pytest output mirrors :class:`LocalEnvironment` but is
        deferred to a follow-up — the scaffold returns the raw remote
        output with zero counts so the field types stay honest.
        """
        result = self.run_bash(self.test_cmd)
        return TestResult(
            passed=0,
            failed=0,
            errors=0,
            output=result.stdout + result.stderr,
        )

    def checkpoint(self) -> str:
        """Not implemented in the scaffold (deferred to follow-up)."""
        raise NotImplementedError(
            "SSHEnvironment.checkpoint() is not implemented; "
            "use git-based checkpointing on the remote host instead."
        )

    def restore(self, checkpoint_id: str) -> None:
        """Not implemented in the scaffold (deferred to follow-up)."""
        del checkpoint_id
        raise NotImplementedError(
            "SSHEnvironment.restore() is not implemented; "
            "use git-based checkpointing on the remote host instead."
        )


def _dirname(path: str) -> str:
    """Pure-Python ``dirname`` so we don't depend on ``posixpath`` semantics.

    The remote may be Linux or macOS, but never Windows in practice for
    this scaffold, so the simple ``rsplit("/")`` is safe and avoids the
    ``os.path.dirname`` import-time platform check.

    Args:
        path: A POSIX-style path.

    Returns:
        The parent directory, or ``"."`` when ``path`` has no slash.
    """
    if "/" not in path:
        return "."
    head = path.rsplit("/", 1)[0]
    return head or "/"


__all__ = ["SSHEnvironment"]
