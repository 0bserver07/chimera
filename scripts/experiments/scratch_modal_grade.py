"""SCRATCH: Modal-backed grading for ProgramBench — run the upstream Evaluator
on native amd64 Modal sandboxes (no QEMU, parallelizable).

Strategy: a ModalContainerEnvironment that matches the upstream
`programbench.container.ContainerEnvironment` interface, injected by subclassing
the upstream `Evaluator` and overriding `_new_env`. Proven against figlet here
before productionizing into chimera.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import modal


class ModalContainerEnvironment:
    """Drop-in for programbench's docker ``ContainerEnvironment``, backed by a
    ``modal.Sandbox`` (native amd64). Implements the methods the upstream
    ``Evaluator`` actually calls: ``execute``, ``copy_in``, ``copy_in_tar``,
    ``commit`` (→ filesystem snapshot), ``cleanup``.
    """

    def __init__(
        self,
        *,
        image: Any,  # registry ref str OR a modal.Image (from a prior commit)
        app: modal.App,
        cwd: str = "/",
        timeout: int = 600,
        cpus: int = 4,
        env: dict[str, str] | None = None,
        **_ignored: Any,  # swallow docker-only kwargs (executable, run_args, ...)
    ) -> None:
        self.cwd = cwd
        self.default_timeout = timeout
        modal_image = image if isinstance(image, modal.Image) else modal.Image.from_registry(image)
        self._sb = modal.Sandbox.create(
            "sleep", "7200",
            image=modal_image,
            app=app,
            cpu=cpus,
            workdir=cwd if cwd and cwd != "/" else None,
            env=env or {},
            timeout=7200,
        )

    def execute(self, command: str, *, timeout: int | None = None) -> dict[str, Any]:
        timeout = timeout or self.default_timeout
        try:
            p = self._sb.exec("bash", "-lc", command, workdir=self.cwd, timeout=timeout, text=True)
            out = p.stdout.read() + p.stderr.read()
            p.wait()
            return {"output": out, "returncode": p.returncode, "exception_info": ""}
        except Exception as exc:  # noqa: BLE001 — timeouts/errors surface as a soft failure
            return {"output": "", "returncode": -1, "exception_info": f"{type(exc).__name__}: {exc}"}

    def copy_in(self, local_path: Path, container_path: str) -> None:
        if local_path.is_dir():
            # Tar the dir on the host, stream into the sandbox.
            import io
            import tarfile
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                tf.add(str(local_path), arcname=".")
            self._write_and_extract(buf.getvalue(), container_path, gz=False)
        else:
            data = local_path.read_bytes()
            remote = f"{container_path.rstrip('/')}"
            self.execute(f"mkdir -p $(dirname {remote})")
            with self._sb.open(remote, "wb") as f:
                f.write(data)

    def copy_in_tar(self, tar_path: Path, container_path: str) -> None:
        gz = tar_path.name.endswith((".tar.gz", ".tgz")) or tar_path.suffix == ".gz"
        self._write_and_extract(tar_path.read_bytes(), container_path, gz=gz)

    def _write_and_extract(self, tar_bytes: bytes, dest: str, *, gz: bool) -> None:
        remote_tar = "/tmp/_pb_in.tar" + (".gz" if gz else "")
        with self._sb.open(remote_tar, "wb") as f:
            f.write(tar_bytes)
        flags = "-xzf" if gz else "-xf"
        r = self.execute(f"mkdir -p {dest} && tar {flags} {remote_tar} -C {dest} && rm -f {remote_tar}")
        if r["returncode"] != 0:
            raise RuntimeError(f"modal tar extract into {dest} failed: {r['output'][:300]}")

    def commit(self, image_ref: str) -> Any:
        """Snapshot the sandbox filesystem; return a modal.Image the next
        ModalContainerEnvironment can boot from (the docker-commit analogue)."""
        return self._sb.snapshot_filesystem()

    def cleanup(self) -> None:
        try:
            self._sb.terminate()
        except Exception:  # noqa: BLE001
            pass

    def __del__(self) -> None:
        self.cleanup()


# --- quick self-check: the env lifecycle on a real cleanroom image ----------
if __name__ == "__main__":
    app = modal.App.lookup("chimera-pb-grade", create_if_missing=True)
    IMG = "programbench/cmatsuoka_1776_figlet.202a0a8:task_cleanroom"
    print(f"booting Modal sandbox from {IMG} ...", flush=True)
    t0 = time.time()
    env = ModalContainerEnvironment(image=IMG, app=app, cwd="/workspace", cpus=4)
    try:
        r = env.execute("uname -m && ls -la /workspace | head && echo '---' && ls /workspace/_inputs 2>/dev/null | head")
        print(f"[{time.time()-t0:.0f}s] rc={r['returncode']}\n{r['output'][:600]}", flush=True)
        snap = env.commit("ignored")
        print("snapshot ->", type(snap).__name__, isinstance(snap, modal.Image), flush=True)
    finally:
        env.cleanup()
        print("cleaned up.", flush=True)
