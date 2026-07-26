# tests/test_tools_list_files.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment
from chimera.tools.list_files import ListFilesTool


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestListFilesTool:
    def test_list_all_files(self, env):
        env.write_file("a.py", "x")
        env.write_file("b.txt", "y")
        tool = ListFilesTool()
        result = tool.execute({"path": "."}, env)
        assert result.success
        assert "a.py" in result.output
        assert "b.txt" in result.output

    def test_list_with_glob(self, env):
        env.write_file("a.py", "x")
        env.write_file("b.txt", "y")
        tool = ListFilesTool()
        result = tool.execute({"path": ".", "glob": "*.py"}, env)
        assert result.success
        assert "a.py" in result.output
        assert "b.txt" not in result.output

    def test_list_subdirectory(self, env):
        env.write_file("src/main.py", "x")
        env.write_file("tests/test.py", "y")
        tool = ListFilesTool()
        result = tool.execute({"path": "src"}, env)
        assert result.success
        assert "main.py" in result.output

    def test_empty_directory(self, env):
        tool = ListFilesTool()
        result = tool.execute({"path": "."}, env)
        assert result.success

    def test_schema(self):
        tool = ListFilesTool()
        assert tool.name == "list_files"

    def test_ignores_vendored_dirs(self, env):
        # A recursive listing must skip vendored/generated dirs, or a repo with
        # a node_modules / .venv returns millions of tokens and busts the model's
        # prompt limit (~200k for most models), stalling the agent loop.
        env.write_file("app.py", "x")
        env.write_file("node_modules/dep/index.js", "z")
        env.write_file("__pycache__/app.pyc", "w")
        env.write_file("dist/bundle.js", "b")
        out = ListFilesTool().execute({"path": "."}, env).output
        assert "app.py" in out
        assert "node_modules" not in out
        assert "__pycache__" not in out
        assert "dist/bundle.js" not in out

    def test_explicit_path_into_ignored_dir_still_lists(self, env):
        # The escape hatch: an explicit `path` into an ignored dir still lists it.
        env.write_file("dist/bundle.js", "b")
        out = ListFilesTool().execute({"path": "dist"}, env).output
        assert "bundle.js" in out

    def test_filename_matching_ignored_dir_name_is_kept(self, env):
        # Only directory segments are filtered — a file literally named "build".
        env.write_file("build", "not a dir")
        out = ListFilesTool().execute({"path": "."}, env).output
        assert "build" in out

    def test_large_listing_is_capped(self, env, monkeypatch):
        # `chimera.tools.__init__` shadows the submodule name, so fetch the real
        # module object via importlib (a getattr walk would hit the shadow).
        import importlib

        lf = importlib.import_module("chimera.tools.list_files")
        monkeypatch.setattr(lf, "_MAX_ENTRIES", 3)
        for i in range(20):
            env.write_file(f"f{i:02d}.py", "x")
        out = ListFilesTool().execute({"path": "."}, env).output
        assert "more not shown" in out
        assert "showing 3 of" in out
