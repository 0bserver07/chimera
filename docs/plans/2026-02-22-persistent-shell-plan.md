# Persistent Shell Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add persistent shell sessions (via tmux) to Chimera environments so shell state survives between commands.

**Architecture:** A `SessionMixin` class manages a tmux session with named windows. It overrides `run_command()` to route through the session when active. Composable onto `LocalEnvironment`, `DockerEnvironment`, and `GitEnvironment` via multiple inheritance. BashTool and all other tools get persistence for free.

**Tech Stack:** Python 3.11+, tmux (system dependency), subprocess for tmux control, `shutil.which` for tmux detection.

---

### Task 58: SessionMixin core — start/end session

**Files:**
- Create: `chimera/env/session.py`
- Test: `tests/test_env_session.py`

**Step 1: Write the failing tests**

```python
# tests/test_env_session.py
"""Tests for SessionMixin persistent shell."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from chimera.env.session import SessionMixin

# Skip all tests if tmux is not installed
pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None,
    reason="tmux not installed",
)


class ConcreteSession(SessionMixin):
    """Minimal concrete class for testing the mixin."""
    pass


class TestSessionLifecycle:
    def test_has_session_false_by_default(self):
        s = ConcreteSession()
        assert s.has_session is False

    def test_start_creates_tmux_session(self):
        s = ConcreteSession()
        try:
            s.start_session()
            assert s.has_session is True
            # Verify tmux session exists
            result = subprocess.run(
                ["tmux", "has-session", "-t", s._session_name],
                capture_output=True,
            )
            assert result.returncode == 0
        finally:
            s.end_session()

    def test_end_kills_tmux_session(self):
        s = ConcreteSession()
        s.start_session()
        name = s._session_name
        s.end_session()
        assert s.has_session is False
        result = subprocess.run(
            ["tmux", "has-session", "-t", name],
            capture_output=True,
        )
        assert result.returncode != 0

    def test_end_session_when_no_session_is_noop(self):
        s = ConcreteSession()
        s.end_session()  # Should not raise

    def test_double_start_raises(self):
        s = ConcreteSession()
        s.start_session()
        try:
            with pytest.raises(RuntimeError, match="already active"):
                s.start_session()
        finally:
            s.end_session()
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chimera.env.session'`

**Step 3: Write minimal implementation**

```python
# chimera/env/session.py
"""Persistent shell sessions via tmux."""
from __future__ import annotations

import shutil
import subprocess
import uuid


class SessionMixin:
    """Mixin that adds persistent shell sessions to any Environment.

    Uses tmux as the backend. Supports multiple named shells (windows)
    within a single tmux session.

    Usage:
        class MyEnv(SessionMixin, Environment):
            ...

        env = MyEnv()
        env.start_session()
        # Now run_command() routes through the persistent shell
        env.end_session()
    """

    _session_name: str | None = None

    @property
    def has_session(self) -> bool:
        """Whether a persistent session is currently active."""
        return self._session_name is not None

    def start_session(self, shell: str = "/bin/bash") -> None:
        """Start a tmux session with a 'main' window.

        Raises RuntimeError if a session is already active.
        Raises FileNotFoundError if tmux is not installed.
        """
        if self.has_session:
            raise RuntimeError("Session already active")
        if shutil.which("tmux") is None:
            raise FileNotFoundError("tmux is not installed")

        self._session_name = f"chimera-{uuid.uuid4().hex[:8]}"
        subprocess.run(
            [
                "tmux", "new-session",
                "-d",  # detached
                "-s", self._session_name,
                "-n", "main",  # first window name
                shell,
            ],
            check=True,
            capture_output=True,
        )

    def end_session(self) -> None:
        """Kill the tmux session and all its windows."""
        if not self.has_session:
            return
        subprocess.run(
            ["tmux", "kill-session", "-t", self._session_name],
            capture_output=True,
        )
        self._session_name = None
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env_session.py -v`
Expected: 5 passed (or skipped if no tmux)

**Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/env/session.py tests/test_env_session.py && git commit -m "feat: add SessionMixin with tmux session lifecycle"
```

---

### Task 59: Named shells — create, list, target

**Files:**
- Modify: `chimera/env/session.py`
- Modify: `tests/test_env_session.py`

**Step 1: Write the failing tests**

Append to `tests/test_env_session.py`:

```python
class TestNamedShells:
    def test_main_shell_exists_after_start(self):
        s = ConcreteSession()
        s.start_session()
        try:
            assert "main" in s.list_shells()
        finally:
            s.end_session()

    def test_create_shell(self):
        s = ConcreteSession()
        s.start_session()
        try:
            s.create_shell("server")
            shells = s.list_shells()
            assert "main" in shells
            assert "server" in shells
        finally:
            s.end_session()

    def test_create_duplicate_shell_raises(self):
        s = ConcreteSession()
        s.start_session()
        try:
            with pytest.raises(ValueError, match="already exists"):
                s.create_shell("main")
        finally:
            s.end_session()

    def test_create_shell_without_session_raises(self):
        s = ConcreteSession()
        with pytest.raises(RuntimeError, match="No active session"):
            s.create_shell("test")
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env_session.py::TestNamedShells -v`
Expected: FAIL — `AttributeError: 'ConcreteSession' object has no attribute 'list_shells'`

**Step 3: Write minimal implementation**

Add to `SessionMixin` in `chimera/env/session.py`:

```python
    def create_shell(self, name: str) -> None:
        """Create a new named shell (tmux window).

        Raises RuntimeError if no session is active.
        Raises ValueError if a shell with that name already exists.
        """
        if not self.has_session:
            raise RuntimeError("No active session")
        if name in self.list_shells():
            raise ValueError(f"Shell '{name}' already exists")
        subprocess.run(
            ["tmux", "new-window", "-t", self._session_name, "-n", name],
            check=True,
            capture_output=True,
        )

    def list_shells(self) -> list[str]:
        """List names of all active shells in the session."""
        if not self.has_session:
            return []
        result = subprocess.run(
            [
                "tmux", "list-windows",
                "-t", self._session_name,
                "-F", "#{window_name}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [
            line.strip() for line in result.stdout.strip().split("\n")
            if line.strip()
        ]
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env_session.py -v`
Expected: 9 passed

**Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/env/session.py tests/test_env_session.py && git commit -m "feat: add named shell management (create, list)"
```

---

### Task 60: run_in_session — command execution with output capture

**Files:**
- Modify: `chimera/env/session.py`
- Modify: `tests/test_env_session.py`

**Step 1: Write the failing tests**

Append to `tests/test_env_session.py`:

```python
import time

from chimera.types import CommandResult


class TestRunInSession:
    def test_simple_echo(self):
        s = ConcreteSession()
        s.start_session()
        try:
            result = s.run_in_session("echo hello")
            assert result.success
            assert "hello" in result.stdout
        finally:
            s.end_session()

    def test_exit_code_captured(self):
        s = ConcreteSession()
        s.start_session()
        try:
            result = s.run_in_session("false")
            assert not result.success
            assert result.exit_code != 0
        finally:
            s.end_session()

    def test_cd_persists(self):
        s = ConcreteSession()
        s.start_session()
        try:
            s.run_in_session("cd /tmp")
            result = s.run_in_session("pwd")
            assert result.success
            # /tmp may resolve to /private/tmp on macOS
            assert "tmp" in result.stdout
        finally:
            s.end_session()

    def test_export_persists(self):
        s = ConcreteSession()
        s.start_session()
        try:
            s.run_in_session("export CHIMERA_TEST_VAR=hello42")
            result = s.run_in_session("echo $CHIMERA_TEST_VAR")
            assert "hello42" in result.stdout
        finally:
            s.end_session()

    def test_named_shells_are_independent(self):
        s = ConcreteSession()
        s.start_session()
        try:
            s.create_shell("other")
            s.run_in_session("cd /tmp", shell_name="main")
            result = s.run_in_session("pwd", shell_name="other")
            # 'other' shell should NOT be in /tmp
            assert result.stdout.strip() != "/tmp"
            assert result.stdout.strip() != "/private/tmp"
        finally:
            s.end_session()

    def test_timeout(self):
        s = ConcreteSession()
        s.start_session()
        try:
            result = s.run_in_session("sleep 60", timeout=1)
            assert result.exit_code == 124
            assert "timed out" in result.stderr.lower()
        finally:
            s.end_session()

    def test_run_without_session_raises(self):
        s = ConcreteSession()
        with pytest.raises(RuntimeError, match="No active session"):
            s.run_in_session("echo hi")

    def test_multiline_output(self):
        s = ConcreteSession()
        s.start_session()
        try:
            result = s.run_in_session("echo line1; echo line2; echo line3")
            assert "line1" in result.stdout
            assert "line2" in result.stdout
            assert "line3" in result.stdout
        finally:
            s.end_session()
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env_session.py::TestRunInSession -v`
Expected: FAIL — `AttributeError: 'ConcreteSession' object has no attribute 'run_in_session'`

**Step 3: Write minimal implementation**

Add to `SessionMixin` in `chimera/env/session.py` (add `import time` at the top):

```python
    def run_in_session(
        self,
        cmd: str,
        shell_name: str = "main",
        timeout: int = 120,
    ) -> CommandResult:
        """Run a command in a named shell and capture output.

        Uses sentinel markers to detect command completion and extract
        output. Polls tmux capture-pane until the end sentinel appears.

        Args:
            cmd: The shell command to execute.
            shell_name: Which tmux window to run in (default: "main").
            timeout: Max seconds to wait for completion.

        Returns:
            CommandResult with stdout, stderr, and exit_code.

        Raises:
            RuntimeError: If no session is active.
        """
        if not self.has_session:
            raise RuntimeError("No active session")

        marker = uuid.uuid4().hex[:12]
        start_sentinel = f"__CHIMERA_START__{marker}"
        end_sentinel = f"__CHIMERA_END__{marker}"

        # Wrap command with sentinels. The end sentinel includes the exit code.
        wrapped = (
            f"echo {start_sentinel}; "
            f"{{ {cmd} ; }}; "
            f"echo {end_sentinel}_$?"
        )

        target = f"{self._session_name}:{shell_name}"

        # Send the command
        subprocess.run(
            ["tmux", "send-keys", "-t", target, wrapped, "Enter"],
            check=True,
            capture_output=True,
        )

        # Poll for completion
        deadline = time.monotonic() + timeout
        poll_interval = 0.05  # Start at 50ms
        captured = ""

        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            result = subprocess.run(
                [
                    "tmux", "capture-pane",
                    "-t", target,
                    "-p",       # print to stdout
                    "-S", "-",  # capture from start of scrollback
                ],
                capture_output=True,
                text=True,
            )
            captured = result.stdout

            if f"{end_sentinel}_" in captured:
                break

            # Back off: 50ms -> 100ms -> 200ms -> 500ms (cap)
            poll_interval = min(poll_interval * 2, 0.5)
        else:
            # Timeout: send Ctrl-C to stop the command
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "C-c", ""],
                capture_output=True,
            )
            return CommandResult(
                stdout="",
                stderr="Command timed out",
                exit_code=124,
            )

        # Parse output between sentinels
        return self._parse_session_output(captured, start_sentinel, end_sentinel)

    def _parse_session_output(
        self,
        captured: str,
        start_sentinel: str,
        end_sentinel: str,
    ) -> CommandResult:
        """Extract command output and exit code from captured pane text."""
        lines = captured.split("\n")

        # Find sentinel positions
        start_idx = None
        end_idx = None
        exit_code = 0

        for i, line in enumerate(lines):
            if start_sentinel in line and start_idx is None:
                start_idx = i
            if f"{end_sentinel}_" in line:
                end_idx = i
                # Parse exit code: __CHIMERA_END__<marker>_<code>
                try:
                    exit_code = int(line.strip().split("_")[-1])
                except (ValueError, IndexError):
                    exit_code = 1

        if start_idx is None or end_idx is None:
            return CommandResult(stdout=captured, stderr="", exit_code=1)

        # Output is between start and end sentinels (exclusive)
        output_lines = lines[start_idx + 1 : end_idx]
        stdout = "\n".join(output_lines)

        return CommandResult(stdout=stdout, stderr="", exit_code=exit_code)
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env_session.py -v`
Expected: 17 passed

**Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/env/session.py tests/test_env_session.py && git commit -m "feat: add run_in_session with tmux output capture"
```

---

### Task 61: Update Environment ABC — add shell_name parameter

**Files:**
- Modify: `chimera/env/base.py:32`
- Test: `tests/test_env.py` (existing test should still pass)

**Step 1: Write the failing test**

Append to `tests/test_env.py`:

```python
def test_run_command_accepts_shell_name():
    """Verify Environment.run_command signature includes shell_name."""
    import inspect
    from chimera.env.base import Environment
    sig = inspect.signature(Environment.run_command)
    params = list(sig.parameters.keys())
    assert "shell_name" in params
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env.py::test_run_command_accepts_shell_name -v`
Expected: FAIL — `AssertionError`

**Step 3: Write minimal implementation**

In `chimera/env/base.py`, change line 32 from:

```python
    def run_command(self, cmd: str, timeout: int = 120) -> CommandResult:
        """Run a shell command in the workspace."""
```

to:

```python
    def run_command(self, cmd: str, timeout: int = 120, shell_name: str = "main") -> CommandResult:
        """Run a shell command in the workspace.

        Args:
            cmd: The command to execute.
            timeout: Max seconds to wait.
            shell_name: Target shell when a persistent session is active.
                        Ignored when no session is running.
        """
```

Then update `LocalEnvironment.run_command` in `chimera/env/local.py` to accept the new parameter (line 56):

```python
    def run_command(self, cmd: str, timeout: int | None = None, shell_name: str = "main") -> CommandResult:
```

And update `DockerEnvironment.run_command` in `chimera/env/docker.py` (line 109):

```python
    def run_command(self, cmd: str, timeout: int = 120, shell_name: str = "main") -> CommandResult:
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env.py tests/test_env_local.py tests/test_env_docker.py -v`
Expected: All pass (backward compatible — default is "main")

**Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/env/base.py chimera/env/local.py chimera/env/docker.py tests/test_env.py && git commit -m "feat: add shell_name parameter to Environment.run_command"
```

---

### Task 62: Integrate SessionMixin into LocalEnvironment

**Files:**
- Modify: `chimera/env/local.py`
- Test: `tests/test_env_session_integration.py` (new)

**Step 1: Write the failing tests**

```python
# tests/test_env_session_integration.py
"""Integration tests: LocalEnvironment + SessionMixin."""
from __future__ import annotations

import shutil
import tempfile

import pytest

from chimera.env.local import LocalEnvironment

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None,
    reason="tmux not installed",
)


@pytest.fixture
def session_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment(workdir=tmpdir, session=True)
        env.setup()
        yield env
        env.cleanup()


@pytest.fixture
def stateless_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment(workdir=tmpdir, session=False)
        env.setup()
        yield env
        env.cleanup()


class TestLocalSessionIntegration:
    def test_session_env_has_active_session(self, session_env):
        assert session_env.has_session is True

    def test_stateless_env_has_no_session(self, stateless_env):
        assert stateless_env.has_session is False

    def test_cd_persists_with_session(self, session_env):
        session_env.run_command("cd /tmp")
        result = session_env.run_command("pwd")
        assert "tmp" in result.stdout

    def test_cd_does_not_persist_without_session(self, stateless_env):
        stateless_env.run_command("cd /tmp")
        result = stateless_env.run_command("pwd")
        # Should be in workdir, not /tmp
        assert "tmp" not in result.stdout or stateless_env.workdir.name in result.stdout

    def test_export_persists_with_session(self, session_env):
        session_env.run_command("export CHIMERA_INT_TEST=works99")
        result = session_env.run_command("echo $CHIMERA_INT_TEST")
        assert "works99" in result.stdout

    def test_cleanup_kills_session(self, session_env):
        assert session_env.has_session is True
        session_env.cleanup()
        assert session_env.has_session is False

    def test_run_command_with_named_shell(self, session_env):
        session_env.create_shell("worker")
        session_env.run_command("cd /tmp", shell_name="main")
        result = session_env.run_command("pwd", shell_name="worker")
        # worker shell should NOT be in /tmp
        stdout = result.stdout.strip()
        assert stdout != "/tmp" and stdout != "/private/tmp"

    def test_file_ops_still_work_with_session(self, session_env):
        """File operations are filesystem-based, unaffected by session."""
        session_env.write_file("test.txt", "hello")
        assert session_env.read_file("test.txt") == "hello"
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env_session_integration.py -v`
Expected: FAIL — `TypeError: LocalEnvironment.__init__() got an unexpected keyword argument 'session'`

**Step 3: Write minimal implementation**

Modify `chimera/env/local.py`:

1. Add import at top:

```python
from chimera.env.session import SessionMixin
```

2. Change class declaration:

```python
class LocalEnvironment(SessionMixin, Environment):
```

3. Add `session` parameter to `__init__`:

```python
    def __init__(
        self,
        workdir: str,
        test_cmd: str = "python -m pytest",
        timeout: int = 300,
        session: bool = False,
    ) -> None:
        self.workdir = Path(workdir).resolve()
        self.test_cmd = test_cmd
        self.timeout = timeout
        self._checkpoint_dir: Path | None = None
        self._use_session = session
```

4. Update `setup()` to start session if requested:

```python
    def setup(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._checkpoint_dir = self.workdir / ".chimera_checkpoints"
        self._checkpoint_dir.mkdir(exist_ok=True)
        if self._use_session:
            self.start_session()
```

5. Update `cleanup()` to end session:

```python
    def cleanup(self) -> None:
        if self.has_session:
            self.end_session()
```

6. Update `run_command()` to route through session when active:

```python
    def run_command(self, cmd: str, timeout: int | None = None, shell_name: str = "main") -> CommandResult:
        if self.has_session:
            return self.run_in_session(cmd, shell_name=shell_name, timeout=timeout or self.timeout)
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(self.workdir),
                timeout=timeout or self.timeout,
            )
            return CommandResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(stdout="", stderr="Command timed out", exit_code=124)
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env_session_integration.py tests/test_env_local.py -v`
Expected: All pass

**Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/env/local.py tests/test_env_session_integration.py && git commit -m "feat: integrate SessionMixin into LocalEnvironment"
```

---

### Task 63: GitEnvironment inherits session support

**Files:**
- Modify: `chimera/env/git_env.py` (no changes needed — inherits from LocalEnvironment)
- Test: `tests/test_env_git.py` (add one test)

Since `GitEnvironment` extends `LocalEnvironment`, it automatically inherits `SessionMixin` through the MRO. We just need to verify it works.

**Step 1: Write the failing test**

Append to `tests/test_env_git.py`:

```python
def test_git_env_has_session_attr():
    """GitEnvironment inherits session support from LocalEnvironment."""
    import shutil
    import tempfile
    from chimera.env.git_env import GitEnvironment
    with tempfile.TemporaryDirectory() as tmpdir:
        env = GitEnvironment(workdir=tmpdir, session=False)
        assert hasattr(env, "has_session")
        assert env.has_session is False
```

**Step 2: Run test to verify it passes (no implementation needed)**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env_git.py -v`
Expected: All pass (including new test)

**Step 3: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add tests/test_env_git.py && git commit -m "test: verify GitEnvironment inherits session support"
```

---

### Task 64: Export SessionMixin from env package

**Files:**
- Modify: `chimera/env/__init__.py`
- Modify: `chimera/__init__.py`

**Step 1: Write the failing test**

```python
# Append to tests/test_env.py
def test_session_mixin_importable_from_package():
    from chimera.env import SessionMixin
    assert SessionMixin is not None

def test_session_mixin_importable_from_chimera():
    import chimera
    assert hasattr(chimera, "SessionMixin")
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env.py::test_session_mixin_importable_from_package tests/test_env.py::test_session_mixin_importable_from_chimera -v`
Expected: FAIL — `ImportError`

**Step 3: Write minimal implementation**

In `chimera/env/__init__.py`, add the import:

```python
from chimera.env.base import Environment
from chimera.env.git_env import GitEnvironment
from chimera.env.local import LocalEnvironment
from chimera.env.session import SessionMixin

__all__ = ["Environment", "GitEnvironment", "LocalEnvironment", "SessionMixin"]
```

In `chimera/__init__.py`, update the Environment import line from:

```python
from chimera.env import Environment, GitEnvironment, LocalEnvironment
```

to:

```python
from chimera.env import Environment, GitEnvironment, LocalEnvironment, SessionMixin
```

And add `"SessionMixin"` to the `__all__` list in the Environment section.

**Step 4: Run tests to verify they pass**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/test_env.py -v`
Expected: All pass

**Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/env/__init__.py chimera/__init__.py tests/test_env.py && git commit -m "feat: export SessionMixin from env and chimera packages"
```

---

### Task 65: Full regression — verify all 367+ tests still pass

**Files:** None (verification only)

**Step 1: Run full test suite**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest -v --tb=short 2>&1 | tail -20`
Expected: 370+ passed (367 original + new session tests), 0 failed

**Step 2: Commit if any fixes were needed**

If any tests broke, fix them and commit. Otherwise, this step is done.

---

### Task 66: Update docs/task-status.md and CONTEXT.md

**Files:**
- Modify: `docs/task-status.md`
- Modify: `CONTEXT.md`

**Step 1: Update task-status.md**

Add a new Phase 14 section:

```markdown
## Phase 14: Persistent Shell

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 58 | 14 - Persistent Shell | SessionMixin core | `chimera/env/session.py` | 5 | DONE |
| 59 | 14 - Persistent Shell | Named shells | `chimera/env/session.py` | 4 | DONE |
| 60 | 14 - Persistent Shell | run_in_session | `chimera/env/session.py` | 8 | DONE |
| 61 | 14 - Persistent Shell | Environment ABC update | `chimera/env/base.py` | 1 | DONE |
| 62 | 14 - Persistent Shell | LocalEnvironment integration | `chimera/env/local.py` | 8 | DONE |
| 63 | 14 - Persistent Shell | GitEnvironment inheritance | `tests/test_env_git.py` | 1 | DONE |
| 64 | 14 - Persistent Shell | Package exports | `chimera/env/__init__.py`, `chimera/__init__.py` | 2 | DONE |
```

Update the Phase Summary table and total test count.

**Step 2: Update CONTEXT.md**

Add Phase 14 section under Implementation Progress.

**Step 3: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add docs/task-status.md CONTEXT.md && git commit -m "docs: update progress for Phase 14 (persistent shell)"
```
