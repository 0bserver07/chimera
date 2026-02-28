# Environments

An **Environment** is where generated code lives and gets tested. It provides file I/O, command execution, test running, and checkpointing -- everything an agent needs to work with code. Chimera ships three environment implementations, plus a mixin for persistent shell sessions.

## The Environment ABC

The base class in `chimera.env.base` defines the interface every environment must implement:

```python
class Environment(ABC):
    def setup(self) -> None: ...
    def cleanup(self) -> None: ...
    def read_file(self, path: str) -> str: ...
    def write_file(self, path: str, content: str) -> None: ...
    def list_files(self, pattern: str = "**/*") -> list[str]: ...
    def run_command(self, cmd: str, timeout: int = 120, shell_name: str = "main") -> CommandResult: ...
    def run_tests(self) -> TestResult: ...
    def checkpoint(self) -> str: ...
    def restore(self, checkpoint_id: str) -> None: ...
```

The interface covers file I/O (`read_file`, `write_file`, `list_files`), command execution (`run_command`, `run_tests`), and state management (`checkpoint`, `restore`). All environments support the context manager protocol:

```python
from chimera.env.local import LocalEnvironment

with LocalEnvironment(workdir="./output", test_cmd="pytest") as env:
    env.write_file("hello.py", "print('hello')")
    result = env.run_command("python hello.py")
    print(result.stdout)  # "hello\n"
# cleanup() is called automatically on exit
```

## LocalEnvironment

The most commonly used environment. Operates directly on the local filesystem with file-copy-based checkpointing.

```python
from chimera.env.local import LocalEnvironment

env = LocalEnvironment(
    workdir="./project",
    test_cmd="python -m pytest",
    timeout=300,
    session=False,
)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `workdir` | (required) | Path to the working directory |
| `test_cmd` | `"python -m pytest"` | Command to run the test suite |
| `timeout` | `300` | Default timeout for commands (seconds) |
| `session` | `False` | Enable persistent tmux shell session |

### Checkpointing

`LocalEnvironment` uses file-copy checkpointing. Each call to `checkpoint()` copies the entire workspace (excluding the `.chimera_checkpoints` directory) to a numbered subdirectory. `restore()` reverses the process.

```python
with LocalEnvironment(workdir="./project") as env:
    env.write_file("v1.py", "version = 1")
    cp1 = env.checkpoint()  # Save state

    env.write_file("v1.py", "version = 2")
    env.restore(cp1)  # Roll back

    content = env.read_file("v1.py")  # "version = 1"
```

### Test Parsing

`run_tests()` executes the configured `test_cmd` and parses pytest-style output into a `TestResult` with `passed`, `failed`, `errors`, `output`, `pass_rate`, and `all_passed` properties.

## GitEnvironment

Extends `LocalEnvironment` with git-based checkpointing instead of file copies. Checkpoints become git commits, and restore uses `git checkout`.

```python
from chimera.env.git_env import GitEnvironment

env = GitEnvironment(
    workdir="./project",
    test_cmd="python -m pytest",
)
```

On `setup()`, it initializes a git repo if one does not exist. Checkpoints are created via `git add . && git commit`, and restore uses `git checkout <sha> -- .`.

!!! tip "When to use GitEnvironment"
    Use `GitEnvironment` when you want full version history of every synthesis iteration. It is especially useful for debugging failed synthesis runs, since you can inspect the diff at every checkpoint.

## DockerEnvironment

Runs code inside a Docker container for full isolation. Requires the `docker` Python package.

```python
from chimera.env.docker import DockerEnvironment

env = DockerEnvironment(
    image="python:3.11-slim",
    workdir="/workspace",
    test_cmd="python -m pytest",
)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `image` | `"python:3.11-slim"` | Docker image to use |
| `workdir` | `"/workspace"` | Working directory inside the container |
| `test_cmd` | `"python -m pytest"` | Test command |

On `setup()`, it starts a detached container running `sleep infinity`. File operations and commands are executed inside the container via `exec_run`. Checkpointing is in-memory (stores file contents in a Python dict).

!!! warning "Docker must be running"
    `DockerEnvironment` requires a running Docker daemon. Install the `docker` Python package: `pip install docker`.

## SessionMixin (Persistent Shells)

`SessionMixin` adds persistent shell sessions to any environment using tmux. Instead of running each command as an isolated subprocess, commands run inside a persistent tmux session where state (environment variables, working directory, running processes) is preserved.

```python
from chimera.env.local import LocalEnvironment

# Enable persistent sessions
env = LocalEnvironment(workdir="./project", session=True)

with env:
    # Commands share state within the tmux session
    env.run_command("export MY_VAR=hello")
    result = env.run_command("echo $MY_VAR")
    print(result.stdout)  # "hello"

    # Create additional named shells
    env.create_shell("server")
    env.run_command("python -m http.server 8000 &", shell_name="server")

    # Run tests in the main shell
    env.run_command("pytest", shell_name="main")
```

The mixin provides `start_session()`, `end_session()`, `create_shell(name)`, `list_shells()`, and `run_in_session()`. It uses sentinel markers to detect command completion, with polling that backs off from 50ms to 500ms.

!!! note "Requires tmux"
    Persistent sessions require `tmux` to be installed on the system. A `FileNotFoundError` is raised if tmux is not found.

## Code Example: Full Workflow

```python
from chimera.core.agent import Agent
from chimera.core.tool_group import DEFAULT_TOOLS
from chimera.env.git_env import GitEnvironment
from chimera.providers.factory import create_provider

provider = create_provider(model="claude-sonnet-4-20250514")
agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS))

with GitEnvironment(workdir="./my-project", test_cmd="pytest -v") as env:
    # First pass
    result = agent.run("Implement a stack data structure in stack.py", env)
    cp = env.checkpoint()  # Git commit

    # Verify
    test_result = env.run_tests()
    if not test_result.all_passed:
        env.restore(cp)  # Roll back to last good state
```

## API Reference

- `chimera.env.base.Environment` -- abstract base class
- `chimera.env.local.LocalEnvironment` -- local filesystem environment
- `chimera.env.git_env.GitEnvironment` -- git-based checkpointing
- `chimera.env.docker.DockerEnvironment` -- Docker container isolation
- `chimera.env.session.SessionMixin` -- persistent tmux sessions
- `chimera.types.CommandResult` -- command execution result
- `chimera.types.TestResult` -- test suite result
