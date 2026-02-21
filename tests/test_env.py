# tests/test_env.py
from chimera.env.base import Environment
from chimera.types import CommandResult, TestResult


def test_environment_is_protocol():
    """Environment should be a Protocol class that can be subclassed."""
    class MyEnv(Environment):
        def setup(self) -> None: pass
        def cleanup(self) -> None: pass
        def read_file(self, path: str) -> str: return ""
        def write_file(self, path: str, content: str) -> None: pass
        def list_files(self, pattern: str = "**/*") -> list[str]: return []
        def run_command(self, cmd: str, timeout: int = 120) -> CommandResult:
            return CommandResult(stdout="", stderr="", exit_code=0)
        def run_tests(self) -> TestResult:
            return TestResult(passed=0, failed=0, errors=0, output="")
        def checkpoint(self) -> str: return "0"
        def restore(self, checkpoint_id: str) -> None: pass

    env = MyEnv()
    assert isinstance(env, Environment)
