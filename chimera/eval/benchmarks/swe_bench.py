"""SWE-bench benchmark implementation.

Beyond the loader/evaluator, this module carries the plumbing that lets each
SWE-bench instance run in *its own* per-instance Docker image on a Modal cloud
sandbox — the container the upstream harness bakes the repository, its
dependencies, and its test fixtures into, so grading is reproducible.

Three pieces cooperate:

* :func:`swe_instance_image` — pure resolver from a task dict to the image
  identifier. It honors an explicit ``image`` / ``docker_image`` field when the
  dataset carries one, and otherwise computes the official convention
  ``<namespace>/sweb.eval.<arch>.<instance_id>:<tag>`` (lowercased, with the
  ``__`` run in an instance id rewritten to ``_1776_`` because Docker
  repository names disallow it) — e.g. ``django__django-12325`` resolves to
  ``swebench/sweb.eval.x86_64.django_1776_django-12325:latest``.
* :func:`swe_modal_env_factory` — task ``->`` an unstarted
  :class:`~chimera.env.modal_sandbox.ModalSandboxEnvironment` on that image,
  mirroring :func:`chimera.eval.benchmarks.harbor.docker_env_factory`.
* :class:`SweModalEnvFactory` — a zero-argument callable that bridges the
  per-task image into the :class:`~chimera.eval.harness.Harness`.

**The Harness limitation this works around.** ``Harness.run`` creates one env
per task via ``env = self.env_factory()`` — a *zero-argument* call. The API has
no seam to hand the current task to the factory, so a per-task image cannot flow
through a plain factory. Rather than change that central contract,
:class:`SweModalEnvFactory` walks the same task list the Harness iterates, in
lockstep (see its docstring for the ordering contract and the one case it does
not cover: ``Harness(resume=True)``).
"""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.eval.harness import Benchmark

if TYPE_CHECKING:
    from chimera.env.modal_sandbox import ModalSandboxEnvironment


#: Docker Hub organisation that hosts the official SWE-bench evaluation images.
DEFAULT_SWE_IMAGE_NAMESPACE = "swebench"
#: Default CPU architecture segment of the image key. The published images are
#: ``linux/amd64`` (``x86_64``); Apple-silicon hosts need emulation but Modal
#: runs them natively.
DEFAULT_SWE_IMAGE_ARCH = "x86_64"
#: Default image tag.
DEFAULT_SWE_IMAGE_TAG = "latest"
#: Working directory the official images check the repository out at, so
#: ``git apply`` + test commands during grading run against the real tree.
DEFAULT_SWE_WORKDIR = "/testbed"

# Docker repository names forbid a bare ``__`` run, so the upstream harness
# rewrites it to ``_1776_`` when forming the *remote* (namespaced) image name.
_INSTANCE_ID_DUNDER = "__"
_INSTANCE_ID_DUNDER_SAFE = "_1776_"


def swe_instance_image(
    task: dict[str, Any],
    *,
    namespace: str = DEFAULT_SWE_IMAGE_NAMESPACE,
    arch: str = DEFAULT_SWE_IMAGE_ARCH,
    tag: str = DEFAULT_SWE_IMAGE_TAG,
) -> str:
    """Resolve the per-instance Docker image identifier for a SWE task.

    Resolution order:

    1. An explicit ``image`` or ``docker_image`` field on *task* wins verbatim
       (datasets that pin their own images, or a plumbing-test override).
    2. Otherwise the official convention is computed from the instance id
       (``instance_id`` or ``id``): ``sweb.eval.<arch>.<instance_id>:<tag>``
       lowercased. When *namespace* is non-empty the namespace is prefixed and
       the ``__`` run in the instance id is rewritten to ``_1776_`` (the remote
       image name); an empty *namespace* returns the bare local key unchanged.

    Args:
        task: A task dict from :meth:`SWEBench.tasks` (or any mapping carrying
            ``instance_id`` / ``id`` and optionally ``image`` / ``docker_image``).
        namespace: Registry namespace for the remote image. Empty string yields
            the local (un-namespaced, un-rewritten) key.
        arch: Architecture segment (``x86_64`` or ``arm64``).
        tag: Image tag.

    Returns:
        The image identifier, e.g.
        ``swebench/sweb.eval.x86_64.django_1776_django-12325:latest``.

    Raises:
        ValueError: When no explicit image is present and the task carries
            neither ``instance_id`` nor ``id`` to compute one from.
    """
    explicit = task.get("image") or task.get("docker_image")
    if explicit:
        return str(explicit)

    instance_id = task.get("instance_id") or task.get("id")
    if not instance_id:
        raise ValueError(
            "swe_instance_image: task has no explicit image and no "
            "'instance_id'/'id' to compute one from"
        )

    key = f"sweb.eval.{arch}.{str(instance_id).lower()}:{tag}"
    if not namespace:
        return key
    return f"{namespace}/{key}".replace(
        _INSTANCE_ID_DUNDER, _INSTANCE_ID_DUNDER_SAFE
    )


def swe_modal_env_factory(
    task: dict[str, Any],
    *,
    gpu: str | None = None,
    image: str | None = None,
    workdir: str = DEFAULT_SWE_WORKDIR,
    test_cmd: str = "python -m pytest",
    timeout: int = 1800,
    namespace: str = DEFAULT_SWE_IMAGE_NAMESPACE,
    arch: str = DEFAULT_SWE_IMAGE_ARCH,
    tag: str = DEFAULT_SWE_IMAGE_TAG,
) -> "ModalSandboxEnvironment":
    """Provision an (unstarted) Modal sandbox on a SWE task's instance image.

    Mirrors :func:`chimera.eval.benchmarks.harbor.docker_env_factory`: the
    caller owns the lifecycle (call ``setup()`` before use, ``cleanup()`` after
    — the :class:`~chimera.eval.harness.Harness` does both). The image defaults
    to the task's per-instance image via :func:`swe_instance_image`; pass
    *image* to force a fixed image for all tasks (handy for a small plumbing
    image in tests).

    Args:
        task: Task dict resolved by :func:`swe_instance_image`.
        gpu: Optional Modal GPU spec (e.g. ``"T4"``, ``"A100:2"``); ``None``
            provisions a CPU-only sandbox.
        image: Optional fixed image override; when ``None`` (default) the
            per-instance image is resolved from *task*.
        workdir: Sandbox working directory. Defaults to :data:`DEFAULT_SWE_WORKDIR`
            (``/testbed``) where the official images place the repository.
        test_cmd: Default test command for ``env.run_tests()``.
        timeout: Per-command timeout (seconds).
        namespace: Registry namespace forwarded to :func:`swe_instance_image`.
        arch: Architecture segment forwarded to :func:`swe_instance_image`.
        tag: Image tag forwarded to :func:`swe_instance_image`.

    Returns:
        An unstarted :class:`~chimera.env.modal_sandbox.ModalSandboxEnvironment`.
    """
    from chimera.env.modal_sandbox import ModalSandboxEnvironment

    resolved = image or swe_instance_image(
        task, namespace=namespace, arch=arch, tag=tag
    )
    return ModalSandboxEnvironment(
        image=resolved,
        gpu=gpu,
        workdir=workdir,
        test_cmd=test_cmd,
        timeout=timeout,
    )


class SweModalEnvFactory:
    """Zero-argument env factory that runs each SWE task in its instance image.

    ``Harness.run`` builds one env per task with ``env = self.env_factory()`` —
    a zero-argument call with no seam to pass the task (see the module
    docstring). This adapter closes that gap by walking the *same* task list the
    Harness iterates, in lockstep: the k-th call returns a
    :class:`~chimera.env.modal_sandbox.ModalSandboxEnvironment` on the k-th
    task's per-instance image. Indexing is modulo the task count, so the
    repeated full passes of a ``bench-matrix`` run (one pass per agent row) line
    up automatically without a manual reset between rows.

    Images are resolved eagerly at construction, so a task missing its instance
    id fails fast here rather than mid-run.

    **Ordering contract.** Correctness relies on the Harness consuming tasks in
    order, one env per task, and running each row to completion — which is true
    for :func:`~chimera.eval.matrix.run_matrix` / ``bench-matrix``. It is *not*
    compatible with ``Harness(resume=True)``, which skips already-graded tasks
    and would desync the cursor; for a resumed run, rebuild the factory over the
    remaining (un-graded) tasks.

    Args:
        tasks: The task list the Harness will iterate (e.g.
            ``benchmark.tasks()``). Must be non-empty.
        gpu: Optional Modal GPU spec applied to every task's sandbox.
        image: Optional fixed image override applied to every task (skips
            per-instance resolution).
        workdir: Sandbox working directory (default ``/testbed``).
        test_cmd: Default test command for ``env.run_tests()``.
        timeout: Per-command timeout (seconds).
        namespace: Registry namespace forwarded to :func:`swe_instance_image`.
        arch: Architecture segment forwarded to :func:`swe_instance_image`.
        tag: Image tag forwarded to :func:`swe_instance_image`.

    Raises:
        ValueError: When *tasks* is empty, or a task cannot resolve an image.
    """

    def __init__(
        self,
        tasks: list[dict[str, Any]],
        *,
        gpu: str | None = None,
        image: str | None = None,
        workdir: str = DEFAULT_SWE_WORKDIR,
        test_cmd: str = "python -m pytest",
        timeout: int = 1800,
        namespace: str = DEFAULT_SWE_IMAGE_NAMESPACE,
        arch: str = DEFAULT_SWE_IMAGE_ARCH,
        tag: str = DEFAULT_SWE_IMAGE_TAG,
    ) -> None:
        self._tasks = list(tasks)
        if not self._tasks:
            raise ValueError("SweModalEnvFactory requires a non-empty task list")
        self._gpu = gpu
        self._workdir = workdir
        self._test_cmd = test_cmd
        self._timeout = timeout
        self._cursor = 0
        # Resolve eagerly so a bad instance id surfaces at construction.
        self._images = [
            image or swe_instance_image(t, namespace=namespace, arch=arch, tag=tag)
            for t in self._tasks
        ]

    def __call__(self) -> "ModalSandboxEnvironment":
        """Return the sandbox for the next task in lockstep order."""
        from chimera.env.modal_sandbox import ModalSandboxEnvironment

        idx = self._cursor % len(self._images)
        self._cursor += 1
        return ModalSandboxEnvironment(
            image=self._images[idx],
            gpu=self._gpu,
            workdir=self._workdir,
            test_cmd=self._test_cmd,
            timeout=self._timeout,
        )

    @property
    def images(self) -> list[str]:
        """The resolved per-task image identifiers, in task order."""
        return list(self._images)

    def reset(self) -> None:
        """Reset the lockstep cursor to the first task."""
        self._cursor = 0


# --------------------------------------------------------------------------- #
# Faithful FAIL_TO_PASS / PASS_TO_PASS grading
# --------------------------------------------------------------------------- #
#
# Official SWE-bench does not grade a submission by running the *whole* repo
# suite. Each instance ships two explicit lists of pytest node ids:
#
#   * ``FAIL_TO_PASS`` — tests that fail on the base commit and must PASS once
#     the fix is applied (they prove the issue is resolved).
#   * ``PASS_TO_PASS`` — tests that already pass and must STILL pass (they prove
#     the fix introduced no regression).
#
# An instance is *resolved* iff every FAIL_TO_PASS test passes AND every
# PASS_TO_PASS test passes after the model patch + the instance's ``test_patch``
# are applied. :meth:`SWEBench._grade_named_tests` runs exactly those tests.

#: Conda activation prefix for the official per-instance evaluation images.
#: The images bake the repo's environment into a conda env named ``testbed`` at
#: ``/opt/miniconda3``; activating it puts the right interpreter + installed
#: dependencies on ``PATH`` before pytest runs. The ``2>/dev/null || true``
#: makes the prefix a harmless no-op on any host that lacks that conda layout,
#: so it never breaks a command outright.
DEFAULT_CONDA_ACTIVATE = (
    "source /opt/miniconda3/bin/activate testbed 2>/dev/null || true; "
)
#: Substring that marks an image as an official SWE-bench evaluation image
#: (the ``sweb.eval.<arch>.<instance>`` convention from :func:`swe_instance_image`).
#: Auto conda activation keys on this marker (see :meth:`SWEBench.__init__`).
_OFFICIAL_IMAGE_MARKER = "sweb.eval"
#: Default base command used to run the named tests during grading.
DEFAULT_PYTEST_CMD = "python -m pytest"
#: Default number of test node ids per pytest invocation. Long PASS_TO_PASS
#: lists (hundreds of ids on some instances) are chunked to stay well under the
#: OS ``ARG_MAX`` command-length limit.
DEFAULT_TEST_CHUNK_SIZE = 100
#: Hard cap on a single pytest command's length (characters). A chunk is split
#: further if quoting its ids would exceed this, as a belt-and-suspenders guard
#: against ``ARG_MAX`` even when ``chunk_size`` is large.
_MAX_CMD_CHARS = 100_000


def _as_test_list(value: Any) -> list[str]:
    """Normalise a ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` field to a list of ids.

    The official dataset stores these columns as *JSON-encoded strings* (e.g.
    ``'["path/test_x.py::test_a", "path/test_x.py::test_b"]'``); other sources
    hand over a native list. Both — plus the empty/absent case — resolve here.

    Args:
        value: The raw field value: a JSON-string list, a native list/tuple, a
            bare string id, ``None``, or empty.

    Returns:
        A list of test node ids (empty when *value* is ``None``/blank). Blank
        entries are dropped.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            # Not JSON — treat the whole string as a single node id.
            return [text]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _chunk_test_ids(
    test_ids: list[str],
    chunk_size: int = DEFAULT_TEST_CHUNK_SIZE,
    max_chars: int = _MAX_CMD_CHARS,
) -> list[list[str]]:
    """Split *test_ids* into invocation-sized chunks.

    A chunk is closed when it reaches *chunk_size* ids or when adding the next
    (shell-quoted) id would push the joined command past *max_chars*. This keeps
    every ``python -m pytest <ids...>`` command under the OS argument-length
    limit for the long PASS_TO_PASS lists some instances carry.

    Args:
        test_ids: Test node ids to chunk (blank entries are dropped).
        chunk_size: Maximum ids per chunk (clamped to at least 1).
        max_chars: Approximate maximum length, in characters, of the quoted ids
            in one chunk.

    Returns:
        A list of non-empty id chunks preserving input order. Empty when
        *test_ids* is empty.
    """
    ids = [t for t in test_ids if t]
    limit = max(1, chunk_size)
    chunks: list[list[str]] = []
    current: list[str] = []
    length = 0
    for tid in ids:
        cost = len(shlex.quote(tid)) + 1  # +1 for the joining space
        if current and (len(current) >= limit or length + cost > max_chars):
            chunks.append(current)
            current = []
            length = 0
        current.append(tid)
        length += cost
    if current:
        chunks.append(current)
    return chunks


def _is_official_swe_image(image: str) -> bool:
    """Return ``True`` when *image* is an official SWE-bench evaluation image."""
    return _OFFICIAL_IMAGE_MARKER in image.lower()


@dataclass
class SWEBenchInstance:
    """A single SWE-bench task instance.

    Attributes:
        image: Optional explicit per-instance Docker image. When set it wins
            over the computed convention in :func:`swe_instance_image`; when
            empty the image is derived from ``instance_id``.
        fail_to_pass: Test node ids that must pass once the fix is applied
            (they fail on the base commit). Parsed from the dataset's
            ``FAIL_TO_PASS`` column.
        pass_to_pass: Test node ids that must still pass after the fix (the
            regression guard). Parsed from the dataset's ``PASS_TO_PASS``
            column.
    """
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    test_patch: str = ""
    patch: str = ""  # gold patch for reference
    image: str = ""  # explicit per-instance image; empty => derive from id
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)

    def to_task(self) -> dict[str, Any]:
        return {
            "id": self.instance_id,
            "instance_id": self.instance_id,
            "prompt": self.problem_statement,
            "description": self.problem_statement,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "hints": self.hints_text,
            "test_patch": self.test_patch,
            "docker_image": self.image,
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
        }


class SWEBench(Benchmark):
    """SWE-bench benchmark: real GitHub issues with test verification.

    Loads instances from a JSON lines file. Each instance contains a
    repository, base commit, problem statement, and test patch.

    Args:
        dataset_path: Path to JSON lines file with SWE-bench instances.
        limit: Maximum number of tasks to load.
        split: Dataset split to use (e.g., "test", "dev").
        conda_prefix: Shell prefix prepended to every named-test command so the
            repo's environment is active. This is the **conda-activation seam**:

            * ``None`` (default) — *auto*: emit :data:`DEFAULT_CONDA_ACTIVATE`
              only when the grading env runs an official per-instance evaluation
              image (detected via its public ``image`` attribute, marker
              ``sweb.eval``). That makes activation **on for the swe-modal path**
              (each instance in its ``sweb.eval.*`` image) and **off for plain
              envs** (a :class:`~chimera.env.local.LocalEnvironment` exposes no
              such image), with zero external wiring.
            * ``""`` — force activation *off* even on official images.
            * any other string — use that exact prefix verbatim (e.g. a custom
              activation for a non-standard image).
        pytest_cmd: Base command used to run the named tests. Defaults to
            :data:`DEFAULT_PYTEST_CMD` (``python -m pytest``); the node ids are
            appended, shell-quoted.
        test_chunk_size: Maximum test node ids per pytest invocation
            (:data:`DEFAULT_TEST_CHUNK_SIZE`); long lists are chunked to respect
            ``ARG_MAX``.
        test_timeout: Per-command timeout (seconds) for each named-test run.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
        split: str = "test",
        conda_prefix: str | None = None,
        pytest_cmd: str = DEFAULT_PYTEST_CMD,
        test_chunk_size: int = DEFAULT_TEST_CHUNK_SIZE,
        test_timeout: int = 1800,
    ) -> None:
        self._dataset_path = dataset_path
        self._limit = limit
        self._split = split
        self._conda_prefix = conda_prefix
        self._pytest_cmd = pytest_cmd
        self._test_chunk_size = test_chunk_size
        self._test_timeout = test_timeout
        self._instances: list[SWEBenchInstance] = []
        self._cached_tasks: list[dict[str, Any]] | None = None
        if dataset_path:
            self._load(dataset_path)

    def _load(self, path: str) -> None:
        """Load instances from JSON lines or JSON array file."""
        data_path = Path(path)
        if not data_path.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")

        text = data_path.read_text()
        # Try JSON array first, then JSON lines
        try:
            items = json.loads(text)
            # Support nested {"tasks": [...]} format
            if isinstance(items, dict) and "tasks" in items:
                items = items["tasks"]
            if not isinstance(items, list):
                items = [items]
        except json.JSONDecodeError:
            items = []
            for line in text.strip().splitlines():
                line = line.strip()
                if line:
                    items.append(json.loads(line))

        for item in items:
            self._instances.append(SWEBenchInstance(
                instance_id=item.get("instance_id", item.get("id", "")),
                repo=item.get("repo", ""),
                base_commit=item.get("base_commit", ""),
                problem_statement=item.get(
                    "problem_statement",
                    item.get("description", item.get("prompt", "")),
                ),
                hints_text=item.get("hints_text", ""),
                test_patch=item.get("test_patch", ""),
                patch=item.get("patch", ""),
                image=(
                    item.get("docker_image")
                    or item.get("image")
                    or item.get("image_name")
                    or ""
                ),
                fail_to_pass=_as_test_list(
                    item.get("FAIL_TO_PASS", item.get("fail_to_pass"))
                ),
                pass_to_pass=_as_test_list(
                    item.get("PASS_TO_PASS", item.get("pass_to_pass"))
                ),
            ))

        if self._limit:
            self._instances = self._instances[:self._limit]

    def name(self) -> str:
        return "swe-bench"

    def tasks(self) -> list[dict[str, Any]]:
        if self._cached_tasks is None:
            self._cached_tasks = [inst.to_task() for inst in self._instances]
        return self._cached_tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any = None) -> bool:
        """Evaluate whether the agent's output resolves the issue.

        The agent has already edited the repository in-place inside *env* (the
        harness runs it there before grading), so this method only applies the
        instance's ``test_patch`` — the tests that verify the fix — and then
        checks the outcome. Grading follows the official SWE-bench contract when
        the instance carries its ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` lists, and
        falls back to the legacy blanket run otherwise:

        1. Apply ``test_patch`` (``git apply``); a failed apply grades as False.
        2. **Faithful path** — when the task carries ``fail_to_pass`` /
           ``pass_to_pass`` and *env* can run commands: run exactly those tests
           (see :meth:`_grade_named_tests`). Pass iff every FAIL_TO_PASS and
           every PASS_TO_PASS test passes.
        3. **Fallback path** — otherwise run the env's blanket suite via
           ``run_tests()`` and pass iff nothing failed (back-compat).
        4. **No-env / no-runner** — pass iff the output is non-trivial.

        Args:
            task: The task dict (from :meth:`SWEBenchInstance.to_task`); may
                carry ``fail_to_pass`` / ``pass_to_pass`` and ``test_patch``.
            agent_output: The agent's final answer (used only by the last-resort
                fallback when no env can run tests).
            env: The execution environment the agent worked in.

        Returns:
            ``True`` when the instance is judged resolved, else ``False``.
        """
        if env is None:
            return False

        # Accept both the lowercase keys surfaced by ``to_task`` and the raw
        # official column names (a caller may hand an unprocessed dataset row).
        fail_to_pass = _as_test_list(
            task.get("fail_to_pass", task.get("FAIL_TO_PASS"))
        )
        pass_to_pass = _as_test_list(
            task.get("pass_to_pass", task.get("PASS_TO_PASS"))
        )

        test_patch = task.get("test_patch", "")

        if test_patch and hasattr(env, "write_file") and hasattr(env, "run_command"):
            try:
                env.write_file("_test_patch.diff", test_patch)
                result = env.run_command("git apply _test_patch.diff")
                if not result.success:
                    return False
            except Exception:
                return False

        # Faithful grading: run the instance's own FAIL_TO_PASS / PASS_TO_PASS
        # tests explicitly rather than the whole suite.
        if (fail_to_pass or pass_to_pass) and hasattr(env, "run_command"):
            return self._grade_named_tests(env, fail_to_pass, pass_to_pass)

        # Fallback: blanket suite (instances without F2P/P2P — back-compat).
        if hasattr(env, "run_tests"):
            try:
                test_result = env.run_tests()
                # Vacuity guard (live-proven on Modal): ``all_passed`` is
                # ``failed == 0 and errors == 0``, so a run that executed ZERO
                # tests (pytest absent without conda activation; the output
                # counters parse 0/0/0) reads as a pass. Absence of failure is
                # not success — require at least one test to have actually run.
                # ``getattr``: only a result that REPORTS zero-run counters is
                # vacuous; duck-typed results without counters fall through to
                # their ``all_passed`` verdict.
                if getattr(test_result, "total", None) == 0:
                    return False
                return bool(test_result.all_passed)
            except Exception:
                return False

        # Last resort: check if output contains meaningful content.
        return bool(agent_output and len(agent_output.strip()) > 10)

    def _resolve_conda_prefix(self, env: Any) -> str:
        """Resolve the shell prefix for named-test commands against *env*.

        Honors the ``conda_prefix`` constructor knob: an explicit value (a
        prefix string, or ``""`` to disable) wins; ``None`` auto-enables
        :data:`DEFAULT_CONDA_ACTIVATE` only for official per-instance evaluation
        images (see :meth:`__init__`).

        Args:
            env: The grading environment; its public ``image`` attribute, when
                present, drives auto-detection.

        Returns:
            The prefix to prepend (possibly empty).
        """
        if self._conda_prefix is not None:
            return self._conda_prefix
        image = str(getattr(env, "image", "") or "")
        if _is_official_swe_image(image):
            return DEFAULT_CONDA_ACTIVATE
        return ""

    def _grade_named_tests(
        self,
        env: Any,
        fail_to_pass: list[str],
        pass_to_pass: list[str],
    ) -> bool:
        """Run the instance's FAIL_TO_PASS / PASS_TO_PASS tests explicitly.

        Both lists are run (chunked to respect ``ARG_MAX``) under the resolved
        conda prefix. Because grading happens *after* the fix + ``test_patch``
        are applied, the resolve criterion collapses to: every named test must
        pass now. A pytest run's exit code is authoritative — ``0`` iff every
        selected test passed and none errored — so any non-zero chunk (a
        FAIL_TO_PASS still failing, a PASS_TO_PASS regressing, or a collection
        error) fails the instance.

        Args:
            env: The environment to run commands in.
            fail_to_pass: Node ids that must pass once the fix is applied.
            pass_to_pass: Node ids that must still pass (regression guard).

        Returns:
            ``True`` iff every named test in both lists passes.
        """
        prefix = self._resolve_conda_prefix(env)
        for group in (fail_to_pass, pass_to_pass):
            for chunk in _chunk_test_ids(group, self._test_chunk_size):
                command = prefix + self._pytest_command(chunk)
                try:
                    result = env.run_command(command, timeout=self._test_timeout)
                except Exception:
                    return False
                if not getattr(result, "success", False):
                    return False
        return True

    def _pytest_command(self, test_ids: list[str]) -> str:
        """Build a ``python -m pytest <ids...>`` command for *test_ids*.

        Each node id is shell-quoted so parametrized ids (``test[a-b]``) and any
        other shell metacharacters are passed to pytest literally.

        Args:
            test_ids: Node ids for a single invocation (one chunk).

        Returns:
            The full command string.
        """
        quoted = " ".join(shlex.quote(t) for t in test_ids)
        return f"{self._pytest_cmd} {quoted}"

    @property
    def instances(self) -> list[SWEBenchInstance]:
        return list(self._instances)

    def add_instance(self, instance: SWEBenchInstance) -> None:
        """Add an instance programmatically (useful for testing)."""
        self._instances.append(instance)


__all__ = [
    "SWEBench",
    "SWEBenchInstance",
    "SweModalEnvFactory",
    "swe_instance_image",
    "swe_modal_env_factory",
    "DEFAULT_SWE_IMAGE_NAMESPACE",
    "DEFAULT_SWE_IMAGE_ARCH",
    "DEFAULT_SWE_IMAGE_TAG",
    "DEFAULT_SWE_WORKDIR",
    "DEFAULT_CONDA_ACTIVATE",
    "DEFAULT_PYTEST_CMD",
    "DEFAULT_TEST_CHUNK_SIZE",
]
