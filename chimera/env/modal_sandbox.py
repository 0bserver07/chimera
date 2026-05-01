"""Modal cloud sandbox environment.

Provisions an ephemeral Modal container on :meth:`setup` and runs every
agent tool call through the Modal Python client instead of the local
machine. Lets ferret/otter agents execute shell + filesystem operations
inside an isolated Modal sandbox without touching the host filesystem.

The optional ``modal`` dependency gates the live path. When the ``modal``
package is not installed the environment still constructs cleanly but
:meth:`setup` raises a clear ``ImportError`` pointing operators at the
``[modal-sandbox]`` extra.

Test posture: tests mock ``modal.Stub`` (or whatever the runtime client
exposes) and verify wiring. Live tests are gated by
``pytest.importorskip("modal")`` and never run in CI by default.

Reference:
    * :class:`chimera.env.cloud.CloudEnvironment` — generic HTTP
      provisioning sibling.
    * :class:`chimera.env.docker.DockerEnvironment` — containerised
      sibling that mirrors the in-memory fallback pattern this module
      uses when ``modal`` is absent.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from chimera.env.base import Environment
from chimera.types import CommandResult, TestResult

try:
    import modal  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised via tests with monkeypatch
    modal = None  # type: ignore[assignment]


_MODAL_EXTRA_HINT = (
    "modal is required for ModalSandboxEnvironment. Install it with: "
    "pip install 'chimera-run[modal-sandbox]'"
)


def _require_modal() -> Any:
    """Return the imported ``modal`` module or raise a friendly ImportError.

    Centralises the optional-dep gate so :class:`ModalSandboxEnvironment`
    can probe the package from a single place. Tests monkeypatch
    ``chimera.env.modal_sandbox.modal`` to inject a stub.

    Raises:
        ImportError: When the ``modal`` package is not importable.
    """
    if modal is None:
        raise ImportError(_MODAL_EXTRA_HINT)
    return modal


class ModalSandboxEnvironment(Environment):
    """Run agent tool calls inside an ephemeral Modal sandbox container.

    The lifecycle is:

    1. :meth:`setup` builds a Modal :class:`Stub` (or :class:`App` on
       newer SDKs) backed by *image* and opens a sandbox via
       ``stub.app.spawn_sandbox`` (the modern API). When the SDK exposes
       a ``modal.Sandbox.create`` constructor we prefer that.
    2. :meth:`run_command` dispatches through ``sandbox.exec`` and
       collects stdout/stderr/exit-code.
    3. :meth:`read_file` / :meth:`write_file` / :meth:`list_files` round
       trip through ``sandbox.exec`` (``cat``, base64-decoded ``echo``,
       ``find``) so they don't depend on file-mount features that vary
       across Modal SDK versions.
    4. :meth:`cleanup` terminates the sandbox.

    When ``modal`` isn't installed (or the operator passes a stub), the
    environment falls back to an in-memory store sufficient for unit
    tests. This mirrors :class:`chimera.env.docker.DockerEnvironment`'s
    no-container fallback.

    Args:
        image: Container image identifier passed to
            ``modal.Image.from_registry``. Defaults to
            ``python:3.11-slim``.
        workdir: Working directory inside the sandbox. Defaults to
            ``/workspace``.
        test_cmd: Test command for :meth:`run_tests`.
        cpu: Optional CPU request (cores) forwarded to Modal.
        memory: Optional memory request (MiB) forwarded to Modal.
        timeout: Default per-command timeout (seconds).
        app_name: Logical Modal app name. Modal groups sandboxes under
            named apps; the default uses a ``chimera-`` prefix so usage
            is easy to grep in the Modal dashboard.
        keep_alive: When ``True`` :meth:`cleanup` does NOT terminate the
            sandbox so a follow-up run can attach to the same container.

    Notes:
        * Checkpoint / restore are not implemented for live sandboxes —
          modal containers are ephemeral and snapshotting requires app
          deployment, which is out of scope here. The in-memory fallback
          supports checkpoint/restore so tests stay deterministic.
        * Network access inside the sandbox follows the Modal account
          default; tighten it with the ``network_file_systems`` /
          ``allow_internet=False`` knobs when you instantiate your own
          stub via the *modal_app* constructor argument.
    """

    def __init__(
        self,
        image: str = "python:3.11-slim",
        workdir: str = "/workspace",
        test_cmd: str = "python -m pytest",
        cpu: float | None = None,
        memory: int | None = None,
        timeout: int = 300,
        app_name: str | None = None,
        keep_alive: bool = False,
        modal_app: Any = None,
    ) -> None:
        self._image = image
        self._workdir = workdir
        self._test_cmd = test_cmd
        self._cpu = cpu
        self._memory = memory
        self._timeout = timeout
        self._app_name = app_name or f"chimera-{uuid.uuid4().hex[:8]}"
        self._keep_alive = keep_alive

        # Live sandbox handles. Populated by :meth:`setup` when modal is
        # available and an app/stub has been wired.
        self._app: Any = modal_app
        self._sandbox: Any = None

        # In-memory fallback used when ``modal`` is not installed and the
        # caller didn't inject a ``modal_app``. Mirrors
        # DockerEnvironment's no-container path so unit tests remain
        # independent of the cloud SDK.
        self._files: dict[str, str] = {}
        self._checkpoints: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def app_name(self) -> str:
        """Return the logical Modal app name used for this environment."""
        return self._app_name

    @property
    def is_live(self) -> bool:
        """``True`` when a real Modal sandbox handle is attached.

        Useful in tests + tooling: ``env.is_live`` distinguishes the
        in-memory unit-test mode from a fully-provisioned sandbox.
        """
        return self._sandbox is not None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Spawn a Modal sandbox.

        When ``modal`` is unavailable AND no ``modal_app`` was injected
        at construction time, :meth:`setup` falls back to in-memory
        operation: subsequent file ops touch ``self._files`` and
        :meth:`run_command` returns a stub error result. This keeps
        unit tests fast without crashing on missing optional deps.

        When ``modal`` is unavailable but the caller asked for live
        operation (no ``modal_app`` injected), :meth:`setup` raises
        :class:`ImportError`. The ferret CLI catches the error and
        falls back to :class:`~chimera.env.local.LocalEnvironment` so
        end-users don't see a traceback.

        Raises:
            ImportError: When neither ``modal`` is importable nor a
                ``modal_app`` was injected.
        """
        if self._app is None:
            if modal is None:
                raise ImportError(_MODAL_EXTRA_HINT)
            # Defer to the active modal SDK; both modern and legacy
            # surfaces expose a ``Stub`` / ``App`` constructor.
            app_cls = getattr(modal, "App", None) or getattr(modal, "Stub", None)
            if app_cls is None:  # pragma: no cover - defensive
                raise RuntimeError(
                    "modal package is installed but exposes neither "
                    "modal.App nor modal.Stub; please upgrade modal."
                )
            self._app = app_cls(self._app_name)

        # Prefer the modern ``modal.Sandbox.create`` constructor when
        # exposed by the installed SDK; otherwise fall back to the
        # historic ``app.spawn_sandbox`` shape.
        sandbox_cls = getattr(modal, "Sandbox", None) if modal is not None else None
        spawn = getattr(sandbox_cls, "create", None) if sandbox_cls else None
        if spawn is not None:
            kwargs: dict[str, Any] = {
                "image": self._build_image(),
                "workdir": self._workdir,
                "timeout": self._timeout,
                "app": self._app,
            }
            if self._cpu is not None:
                kwargs["cpu"] = self._cpu
            if self._memory is not None:
                kwargs["memory"] = self._memory
            self._sandbox = spawn(**kwargs)
        else:
            # Legacy SDK path. ``app.spawn_sandbox`` takes the image as
            # the leading positional and resource kwargs second.
            spawn_legacy = getattr(self._app, "spawn_sandbox", None)
            if spawn_legacy is None:
                # No sandbox API available — leave ``_sandbox`` None so
                # the in-memory fallback engages. Tests can still wire
                # a fake ``modal_app`` whose ``spawn_sandbox`` returns
                # a stub.
                return
            self._sandbox = spawn_legacy(
                self._build_image(),
                workdir=self._workdir,
                timeout=self._timeout,
            )

    def cleanup(self) -> None:
        """Terminate the sandbox unless *keep_alive* was set."""
        if self._sandbox is None:
            return
        if self._keep_alive:
            return
        terminate = getattr(self._sandbox, "terminate", None)
        if terminate is not None:
            try:
                terminate()
            except Exception:  # noqa: BLE001 — cleanup must never raise
                pass
        self._sandbox = None

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def read_file(self, path: str) -> str:
        if self._sandbox is None:
            if path in self._files:
                return self._files[path]
            raise FileNotFoundError(path)
        result = self._exec(f"cat {self._workdir}/{path}")
        if result.exit_code != 0:
            raise FileNotFoundError(path)
        return result.stdout

    def write_file(self, path: str, content: str) -> None:
        if self._sandbox is None:
            self._files[path] = content
            return
        if "/" in path:
            parent = "/".join(path.split("/")[:-1])
            self._exec(f"mkdir -p {self._workdir}/{parent}")
        # Base64 round-trip avoids quoting headaches with arbitrary content.
        import base64

        encoded = base64.b64encode(content.encode()).decode()
        self._exec(
            f"echo {encoded} | base64 -d > {self._workdir}/{path}",
        )

    def list_files(self, pattern: str = "**/*") -> list[str]:
        if self._sandbox is None:
            return sorted(self._files.keys())
        result = self._exec(f"find {self._workdir} -type f")
        if result.exit_code != 0:
            return []
        prefix = f"{self._workdir}/"
        return [
            line[len(prefix):] if line.startswith(prefix) else line
            for line in result.stdout.splitlines()
            if line.strip()
        ]

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def run_command(
        self, cmd: str, timeout: int = 120, shell_name: str = "main"
    ) -> CommandResult:
        if self._sandbox is None:
            return CommandResult(
                stdout="",
                stderr="ModalSandboxEnvironment: no live sandbox attached",
                exit_code=1,
            )
        return self._exec(cmd, timeout=timeout)

    def run_tests(self) -> TestResult:
        result = self.run_command(self._test_cmd)
        output = (result.stdout or "") + (result.stderr or "")
        passed = failed = errors = 0
        match = re.search(r"(\d+) passed", output)
        if match:
            passed = int(match.group(1))
        match = re.search(r"(\d+) failed", output)
        if match:
            failed = int(match.group(1))
        match = re.search(r"(\d+) error", output)
        if match:
            errors = int(match.group(1))
        return TestResult(
            passed=passed,
            failed=failed,
            errors=errors,
            output=output,
        )

    # ------------------------------------------------------------------
    # Checkpointing (in-memory fallback only)
    # ------------------------------------------------------------------
    #
    # Snapshotting a live Modal sandbox requires app deployment + a
    # bind-mount volume per snapshot, which is out of scope. Live
    # callers should layer ``GitEnvironment`` over the sandbox if
    # they need rollback semantics.

    def checkpoint(self) -> str:
        if self._sandbox is not None:
            raise NotImplementedError(
                "ModalSandboxEnvironment.checkpoint() is not implemented "
                "for live sandboxes. Layer GitEnvironment for rollback "
                "or destroy + recreate the sandbox."
            )
        cp_id = uuid.uuid4().hex[:8]
        self._checkpoints[cp_id] = dict(self._files)
        return cp_id

    def restore(self, checkpoint_id: str) -> None:
        if self._sandbox is not None:
            raise NotImplementedError(
                "ModalSandboxEnvironment.restore() is not implemented "
                "for live sandboxes. Layer GitEnvironment for rollback "
                "or destroy + recreate the sandbox."
            )
        if checkpoint_id not in self._checkpoints:
            raise ValueError(f"Checkpoint {checkpoint_id} not found")
        self._files = dict(self._checkpoints[checkpoint_id])

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_image(self) -> Any:
        """Construct a Modal image from ``self._image``.

        Returns the image object directly when modal is unavailable —
        callers that injected a stub *modal_app* in the constructor
        manage their own image plumbing. The live path uses
        ``modal.Image.from_registry`` so any public Docker image works.
        """
        if modal is None:
            return self._image
        image_factory = getattr(modal, "Image", None)
        if image_factory is None:  # pragma: no cover - defensive
            return self._image
        from_registry = getattr(image_factory, "from_registry", None)
        if from_registry is None:  # pragma: no cover - defensive
            return self._image
        return from_registry(self._image)

    def _exec(self, cmd: str, timeout: int | None = None) -> CommandResult:
        """Run *cmd* inside the sandbox and collect output.

        Modal's :class:`Sandbox` exposes :meth:`exec` returning an object
        with ``stdout`` / ``stderr`` / ``returncode`` attributes (or a
        ``wait()`` -returning equivalent). We normalise both shapes so
        callers always get a :class:`CommandResult`.

        Args:
            cmd: Shell command to run via ``sh -c``.
            timeout: Optional override for the per-command timeout.

        Returns:
            A :class:`CommandResult` with stdout/stderr/exit_code.
        """
        assert self._sandbox is not None
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        proc = self._sandbox.exec("sh", "-c", cmd, **kwargs)

        # Collect output. The two known SDK shapes:
        # 1. ``proc.stdout`` / ``proc.stderr`` are bytes-like buffers and
        #    ``proc.wait()`` returns the integer exit code.
        # 2. ``proc.stdout`` is an iterable of byte chunks; ``proc.wait()``
        #    is required to flush.
        wait = getattr(proc, "wait", None)
        exit_code: int = 0
        if wait is not None:
            try:
                exit_code = int(wait())
            except TypeError:
                # ``wait()`` may return None; fall back to ``returncode``.
                exit_code = int(getattr(proc, "returncode", 0) or 0)

        stdout = _read_stream(getattr(proc, "stdout", ""))
        stderr = _read_stream(getattr(proc, "stderr", ""))
        return CommandResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )


def _read_stream(stream: Any) -> str:
    """Best-effort decode of a Modal sandbox output stream.

    Accepts:
        * ``str``: returned unchanged.
        * ``bytes`` / ``bytearray``: decoded as UTF-8 (errors replaced).
        * Iterable of bytes/str chunks: joined and decoded.
        * Object with ``read()``: read once and decoded.

    Anything else is coerced via ``str()``. Never raises — output
    formatting failures must not crash an agent loop.
    """
    if stream is None:
        return ""
    if isinstance(stream, str):
        return stream
    if isinstance(stream, (bytes, bytearray)):
        return bytes(stream).decode("utf-8", errors="replace")
    read = getattr(stream, "read", None)
    if callable(read):
        try:
            data = read()
        except Exception:  # noqa: BLE001
            data = ""
        if isinstance(data, (bytes, bytearray)):
            return bytes(data).decode("utf-8", errors="replace")
        return str(data or "")
    try:
        chunks = list(stream)
    except TypeError:
        return str(stream)
    parts: list[str] = []
    for chunk in chunks:
        if isinstance(chunk, (bytes, bytearray)):
            parts.append(bytes(chunk).decode("utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts)


__all__ = ["ModalSandboxEnvironment"]
