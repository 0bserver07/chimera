"""The repo root is a guarded interface, not a scratch space.

Why this exists: one-off benchmark drivers (``pb_*.py``, ``scratch_*.py``)
accumulated at the repo root as the only loose Python files in the tree, six of
them listed in ``.gitignore`` *while tracked* (which asserts something false —
ignore rules only affect untracked files), and their run outputs piled up
1.3 GB of ``pb-runs/`` and ``runs/`` next to them. None of it was caught,
because nothing gated the root. These tests are that gate.

The disciplined homes, for the record:

* datasets      → ``~/.chimera/datasets`` (``chimera.eval.datasets.staging_dir``)
* results       → explicit ``--output`` files; curated receipts committed under
  ``data/`` deliberately — nothing in ``chimera/`` writes a cwd-relative dir
* run scratch   → temp dirs / sandboxes, or OUTSIDE the repo entirely
* one-off drivers → ``scripts/experiments/`` (see its README)

Adding a new top-level entry is a deliberate act: extend the allowlist here,
in the same commit, with a reason.

The static cwd-write gate
-------------------------

The root gate above catches rot *after* it lands. The second gate here is the
manual sweep that proved ``chimera/`` clean, made permanent: an ``ast`` walk
that fails on a **literal relative** path being written — ``os.makedirs("runs")``,
``Path("runs").mkdir()``, ``open("out/x.json", "w")``,
``Path("out/x").write_text(...)``, ``shutil.copytree(src, "runs")``. Code must
root its writes in ``chimera_home()``, a caller-supplied directory, a
``__file__``-anchored directory, or a temp dir — never in whatever directory
the user happened to be standing in.

It walks ``SCANNED_ROOTS`` — ``chimera/``, ``scripts/``, ``tests/``,
``examples/`` — which is every ``*.py`` in the repo (the remaining tracked
roots, ``chimera-plugin/``, ``data/``, ``docs/``, ``research/``, ``site/``,
contain none). It started scoped to the shipped package; widening it to the
rest of the tree found three writers the package-only scan could not see, one
of them live (``scripts/modal_bench_app.py``, since fixed). Scoping a gate to
the code you already believe is clean is how the rot ends up next door.

Deliberately NOT flagged, because each is already disciplined: absolute paths,
``Path.home()``-rooted paths, ``~``-prefixed paths, ``tempfile.*``, and any
path derived from a variable, parameter, or attribute (an ``env.workdir``, an
``--output`` argument). Reads are not flagged either — only writes.

What this gate CANNOT catch (stated plainly, because a guard whose limits are
oversold is a guard people stop checking):

* **External harnesses.** The 944 MB ``runs/`` at the root came from
  Terminal-Bench, which defaults its output to the invocation cwd. No scan of
  Chimera source can see that. It is a habit, enforced by a playbook rule
  (``docs/playbooks/13-live-bench-runs.md``): invoke external harnesses from
  outside the repo.
* **Dynamically constructed paths.** A literal reached through a helper, a
  config value, a format string built from parts, ``getattr``/``eval``, or a
  name assigned in one function and written in another. Resolution here is
  deliberately shallow: literals, ``Path(...)`` of literals, ``/`` joins,
  f-string literal prefixes, and single-assignment locals within one scope.
* **Non-Python writers** — a shell script, a Makefile recipe, a YAML workflow
  step. Only ``*.py`` under ``SCANNED_ROOTS`` is parsed.
* **Runtime behaviour.** This is a source scan. A cwd-relative write reached
  through an import of a third-party library is invisible to it.

``test_the_cwd_write_scanner_detects_seeded_violations`` exists because a gate
that cannot fail is not a gate — the same reasoning as the benchmark canary
(``docs/guides/benchmark-canary.md``): prove the check can go red before
trusting that it is green.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "chimera"

#: Every root the cwd-write scan walks. Together these hold every ``*.py`` in
#: the repo — ``chimera-plugin/``, ``data/``, ``docs/``, ``research/`` and
#: ``site/`` contain none, so this is whole-tree coverage, not a sample. A new
#: root with Python in it must be added here in the same commit that adds it
#: (``test_every_python_root_is_scanned`` fails otherwise).
SCANNED_ROOTS: tuple[str, ...] = ("chimera", "scripts", "tests", "examples")

#: Every top-level path that is ALLOWED to be tracked. Additions require
#: editing this set — that edit is the deliberate act the gate exists to force.
ALLOWED_ROOT_ENTRIES = frozenset({
    # meta / packaging
    ".github", ".gitignore", ".python-version",
    "pyproject.toml", "uv.lock",
    # top-level docs
    "CHANGELOG.md", "CLAUDE.md", "CODE_OF_CONDUCT.md", "CONTEXT.md",
    "CONTRIBUTING.md", "LICENSE", "README.md", "RELEASES.md", "RELEASING.md",
    "SECURITY.md",
    # the directories
    "chimera", "chimera-plugin", "data", "docs", "examples", "research",
    "scripts", "site", "tests",
})


def _tracked_root_entries() -> set[str] | None:
    """Top-level components of every tracked path, or ``None`` off-git."""
    try:
        proc = subprocess.run(
            ["git", "ls-files"], cwd=ROOT,
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
        return None
    if proc.returncode != 0:  # pragma: no cover - not a git checkout
        return None
    return {line.split("/", 1)[0] for line in proc.stdout.splitlines() if line}


def test_no_loose_python_files_at_the_repo_root() -> None:
    stray = sorted(p.name for p in ROOT.glob("*.py"))
    assert stray == [], (
        f"loose Python files at the repo root: {stray} — one-off drivers "
        "belong in scripts/experiments/ (see its README), package code in "
        "chimera/, tooling in scripts/."
    )


def test_tracked_root_entries_are_the_deliberate_set() -> None:
    entries = _tracked_root_entries()
    if entries is None:
        pytest.skip("git unavailable — root gate needs a checkout")
    unexpected = sorted(entries - ALLOWED_ROOT_ENTRIES)
    # Only ADDITIONS gate; a removed entry needs no test edit.
    assert unexpected == [], (
        f"new tracked entries at the repo root: {unexpected} — if deliberate, "
        "add them to ALLOWED_ROOT_ENTRIES in this test, in the same commit, "
        "with a reason."
    )


# --------------------------------------------------------------------------
# The static cwd-relative write scan (see the module docstring for its limits)
# --------------------------------------------------------------------------

#: Module-level names that construct a pathlib path from its arguments.
_PATH_CTORS = frozenset({
    "Path", "PurePath", "PosixPath", "PurePosixPath", "WindowsPath", "PureWindowsPath",
})
#: Path methods that return a path and preserve the literal we care about.
#: ``Path("runs").resolve().mkdir()`` is still a cwd-relative write.
_PASSTHROUGH_CALLS = frozenset({"expanduser", "resolve", "absolute", "joinpath"})
_PASSTHROUGH_ATTRS = frozenset({"parent"})

#: ``os`` functions that create a directory at their first positional argument.
_OS_DIR_MAKERS = frozenset({"makedirs", "mkdir"})
#: ``os``/``shutil`` functions keyed by the positional index of their DESTINATION.
_DEST_ARG_FUNCS = {
    "copytree": 1, "move": 1, "copy": 1, "copy2": 1, "copyfile": 1,  # shutil
    "rename": 1, "replace": 1,                                       # os
}
#: ``Path`` methods that write at the path they are called on.
_PATH_WRITE_METHODS = frozenset({"mkdir", "write_text", "write_bytes", "touch"})

#: Cwd-relative writes that are deliberate, as ``(posix path relative to the
#: repo root, the literal path written)``. An entry needs a comment saying WHY
#: — "the scan went red" is not a reason, and neither is "it is inconvenient to
#: fix". Keyed by literal rather than line number so the allowlist does not rot
#: when code moves.
#:
#: ``chimera/`` contributes NOTHING here and must stay that way: the shipped
#: package roots every write in ``chimera_home()``, a caller-supplied
#: directory, or a temp dir (owner audit 2026-07-27, held by this gate since).
CWD_WRITE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset({
    # The cwd IS the subject under test. ``fake_external_agent.py`` is a
    # scripted stand-in for an external agent CLI, spawned by the
    # ExternalAgentDriver tests with ``cwd=`` set to a per-lane temp
    # workspace; writing ``external.txt`` into that cwd is how workspace
    # isolation and the resulting diff are proven. Anchoring it anywhere else
    # would delete the thing the fixture exists to demonstrate.
    ("tests/assembly/fake_external_agent.py", "external.txt"),

    # Frozen provenance, not maintained code. ``examples/_archive/`` is
    # documented in its own README as superseded scripts kept verbatim for
    # historical reference, explicitly "not guaranteed to run against the
    # current codebase"; the canonical SWE-bench entry points are listed
    # there. This write IS the shape the gate exists to stop — it is
    # allowlisted because rewriting an archived artifact to satisfy a
    # present-day gate falsifies what actually shipped, not because it is
    # good. Anything promoted OUT of ``_archive/`` loses this exemption.
    ("examples/_archive/swe_bench_coding_agent.py", "data/traces"),
})


class CwdWrite(NamedTuple):
    """One literal-relative write found in a source file."""

    path: str      # posix path relative to the repo root, or the fixture name
    lineno: int
    symbol: str    # e.g. "os.makedirs", "Path().write_text", "open"
    literal: str   # the literal relative path being written

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}: {self.symbol}({self.literal!r})"


def _is_cwd_relative(value: str | None) -> bool:
    """True when ``value`` is a literal path resolved against the current cwd.

    Absolute paths, ``~``-rooted paths, Windows drive paths and non-strings are
    all somebody else's anchor, not the process's working directory.
    """
    if not value or "\x00" in value:
        return False
    if value.startswith(("/", "~", "\\")):
        return False
    if len(value) > 1 and value[1] == ":":  # C:\... — a drive anchor
        return False
    return not PurePosixPath(value).is_absolute()


def _module_aliases(tree: ast.Module) -> tuple[dict[str, str], set[str]]:
    """Map local names back to the stdlib modules they came from.

    ``import os as _os`` (which really appears in this repo) and
    ``from pathlib import Path as _Path`` would both slip past a matcher that
    only knows the canonical spellings. Imports are collected from the whole
    module, including function-local ones.

    Returns:
        ``(alias -> "os"|"shutil"|"pathlib", path-constructor names)``.
    """
    modules: dict[str, str] = {}
    ctors = set(_PATH_CTORS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"os", "shutil"}:
                    modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module == "pathlib":
                ctors.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name in _PATH_CTORS
                )
    return modules, ctors


def _scope_bindings(body: list[ast.stmt]) -> dict[str, ast.expr | None]:
    """Names bound in one scope → their value, or ``None`` when ambiguous.

    Only *single*-assignment names keep a value. A name that is rebound, or
    bound by a loop/``with``/``except``/comprehension target, resolves to
    ``None`` so ``p = Path("x"); p = base / p; p.mkdir()`` cannot be misread as
    a cwd-relative write. Nested function and class bodies are their own
    scopes and are not descended into.
    """
    table: dict[str, ast.expr | None] = {}

    def bind(target: ast.expr, value: ast.expr | None) -> None:
        if isinstance(target, ast.Name):
            table[target.id] = None if target.id in table else value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                bind(element, None)

    def walk(nodes: list[ast.stmt]) -> None:
        for stmt in nodes:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # its own scope
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    bind(target, stmt.value)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                bind(stmt.target, stmt.value)
            elif isinstance(stmt, ast.AugAssign):
                bind(stmt.target, None)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                bind(stmt.target, None)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    if item.optional_vars is not None:
                        bind(item.optional_vars, None)
            # Recurse into nested blocks — they share this scope.
            for field in ("body", "orelse", "finalbody"):
                inner = getattr(stmt, field, None)
                if isinstance(inner, list):
                    walk([s for s in inner if isinstance(s, ast.stmt)])
            for handler in getattr(stmt, "handlers", []) or []:
                walk(handler.body)

    walk(body)
    return table


class _CwdWriteScanner(ast.NodeVisitor):
    """Walks one module, reporting writes rooted at a literal relative path."""

    def __init__(self, filename: str, tree: ast.Module) -> None:
        self.filename = filename
        self.modules, self.ctors = _module_aliases(tree)
        self.scopes: list[dict[str, ast.expr | None]] = []
        self.hits: list[CwdWrite] = []

    # -- path resolution ---------------------------------------------------

    def _literal(self, node: ast.expr | None, depth: int = 0) -> str | None:
        """The literal relative path ``node`` denotes, or ``None`` if unknown."""
        if node is None or depth > 8:
            return None
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.JoinedStr):
            # f"runs/{stamp}" is anchored by its literal prefix; f"{base}/x"
            # is anchored by whatever `base` is, so it resolves to None.
            head = node.values[0] if node.values else None
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                return head.value
            return None
        if isinstance(node, ast.Name):
            for scope in reversed(self.scopes):
                if node.id in scope:
                    return self._literal(scope[node.id], depth + 1)
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self._literal(node.left, depth + 1)  # the leftmost anchors it
        if isinstance(node, ast.Attribute) and node.attr in _PASSTHROUGH_ATTRS:
            return self._literal(node.value, depth + 1)
        if isinstance(node, ast.Call):
            return self._literal_call(node, depth)
        return None

    def _literal_call(self, node: ast.Call, depth: int) -> str | None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in self.ctors:
            # Resolve left to right and stop at the first unknown component:
            # `Path("runs", stamp)` is still anchored at "runs". A later
            # ABSOLUTE literal legitimately resets the anchor, which
            # PurePosixPath already models.
            parts: list[str] = []
            for arg in node.args:
                literal = self._literal(arg, depth + 1)
                if literal is None:
                    break
                parts.append(literal)
            if not parts:
                return "." if not node.args else None  # bare Path() is the cwd
            return str(PurePosixPath(*parts))
        if isinstance(func, ast.Attribute):
            if func.attr in _PASSTHROUGH_CALLS:
                return self._literal(func.value, depth + 1)
            # os.path.join("runs", x) is anchored by its first component.
            if func.attr == "join" and node.args:
                owner = func.value
                if isinstance(owner, ast.Attribute) and owner.attr == "path":
                    return self._literal(node.args[0], depth + 1)
        return None

    # -- helpers -----------------------------------------------------------

    def _module_of(self, node: ast.expr) -> str | None:
        """``"os"``/``"shutil"`` when ``node`` names that module (alias-aware)."""
        if isinstance(node, ast.Name):
            return self.modules.get(node.id)
        return None

    @staticmethod
    def _is_write_mode(node: ast.Call, position: int) -> bool:
        mode: object = None
        if len(node.args) > position and isinstance(node.args[position], ast.Constant):
            mode = node.args[position].value  # type: ignore[attr-defined]
        for keyword in node.keywords:
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                mode = keyword.value.value
        return isinstance(mode, str) and any(flag in mode for flag in "wax+")

    def _record(self, node: ast.Call, symbol: str, literal: str) -> None:
        self.hits.append(CwdWrite(self.filename, node.lineno, symbol, literal))

    # -- traversal ---------------------------------------------------------

    def _visit_scope(self, body: list[ast.stmt], args: ast.arguments | None = None) -> None:
        bindings = _scope_bindings(body)
        if args is not None:
            # Parameters SHADOW anything of the same name outside. Without
            # this, a module-level `out = Path("runs")` would be read into
            # every `def f(out): out.mkdir()` in the file — a false positive,
            # and false positives are how a gate gets deleted.
            for arg in (
                *args.posonlyargs, *args.args, *args.kwonlyargs,
                *([args.vararg] if args.vararg else []),
                *([args.kwarg] if args.kwarg else []),
            ):
                bindings[arg.arg] = None
        self.scopes.append(bindings)
        try:
            for stmt in body:
                self.visit(stmt)
        finally:
            self.scopes.pop()

    def visit_Module(self, node: ast.Module) -> None:
        self._visit_scope(node.body)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node.body, node.args)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node.body, node.args)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node.body)

    def visit_Call(self, node: ast.Call) -> None:
        self._check(node)
        self.generic_visit(node)

    def _check(self, node: ast.Call) -> None:
        func = node.func

        # open("out/x.json", "w") — the builtin, by its own name only.
        if isinstance(func, ast.Name) and func.id == "open" and node.args:
            literal = self._literal(node.args[0])
            if _is_cwd_relative(literal) and self._is_write_mode(node, 1):
                self._record(node, "open", str(literal))
            return

        if not isinstance(func, ast.Attribute):
            return
        owner = self._module_of(func.value)

        # os.makedirs("runs") / os.mkdir("runs")
        if owner == "os" and func.attr in _OS_DIR_MAKERS and node.args:
            literal = self._literal(node.args[0])
            if _is_cwd_relative(literal):
                self._record(node, f"os.{func.attr}", str(literal))
            return

        # shutil.copytree(src, "runs") / shutil.move(src, "runs") / os.rename(...)
        if owner in {"os", "shutil"} and func.attr in _DEST_ARG_FUNCS:
            index = _DEST_ARG_FUNCS[func.attr]
            if len(node.args) > index:
                literal = self._literal(node.args[index])
                if _is_cwd_relative(literal):
                    self._record(node, f"{owner}.{func.attr}", str(literal))
            return

        # Path methods. The literal must be in the RECEIVER, which is what keeps
        # `env.write_text("out.txt")` (a write inside a sandbox workdir, and
        # perfectly legitimate) out of the results.
        receiver = self._literal(func.value)
        if not _is_cwd_relative(receiver):
            return
        if func.attr in _PATH_WRITE_METHODS:
            self._record(node, f"Path().{func.attr}", str(receiver))
        elif func.attr == "open" and self._is_write_mode(node, 0):
            self._record(node, "Path().open", str(receiver))


def scan_source(source: str, filename: str = "<fixture>") -> list[CwdWrite]:
    """Report every literal cwd-relative write in one module's source."""
    tree = ast.parse(source, filename)
    scanner = _CwdWriteScanner(filename, tree)
    scanner.visit(tree)
    return scanner.hits


def scan_package(
    root: Path = PACKAGE,
    allowlist: frozenset[tuple[str, str]] = CWD_WRITE_ALLOWLIST,
) -> list[CwdWrite]:
    """Scan every ``*.py`` under ``root``, minus the allowlisted pairs.

    ``root``/``allowlist`` are parameters purely so the tests below can drive
    this exact code path over a seeded tree — the gate and its proof must be
    the same function, or the proof proves nothing.
    """
    found: list[CwdWrite] = []
    for path in sorted(root.rglob("*.py")):
        try:
            name = path.relative_to(ROOT).as_posix()
        except ValueError:  # a seeded tree outside the repo
            name = path.name
        for hit in scan_source(path.read_text(encoding="utf-8"), name):
            if (hit.path, hit.literal) not in allowlist:
                found.append(hit)
    return found


def scan_repo(
    allowlist: frozenset[tuple[str, str]] = CWD_WRITE_ALLOWLIST,
) -> list[CwdWrite]:
    """Run :func:`scan_package` over every root in :data:`SCANNED_ROOTS`."""
    found: list[CwdWrite] = []
    for name in SCANNED_ROOTS:
        found.extend(scan_package(ROOT / name, allowlist))
    return found


def test_repo_never_writes_a_cwd_relative_path() -> None:
    hits = scan_repo()
    assert hits == [], (
        "these write to paths resolved against the caller's working "
        "directory:\n  " + "\n  ".join(str(hit) for hit in hits) + "\n"
        "Root writes in chimera_home(), a caller-supplied directory, a "
        "__file__-anchored directory, or a temp dir. If a hit is genuinely "
        "legitimate, add (path, literal) to CWD_WRITE_ALLOWLIST with a "
        "comment saying why."
    )


def test_the_shipped_package_needs_no_allowlist_entries() -> None:
    """``chimera/`` must be clean on its own merits, exemptions aside.

    The allowlist exists for fixtures and frozen archives. The moment the
    shipped package needs one, the rule it encodes has been abandoned.
    """
    exempted = sorted(path for path, _ in CWD_WRITE_ALLOWLIST if path.startswith("chimera/"))
    assert exempted == [], (
        f"chimera/ has cwd-write exemptions: {exempted} — package code roots "
        "its writes in chimera_home(), a caller-supplied directory, or a temp "
        "dir. Fix the writer, do not exempt it."
    )


#: Each snippet is a real violation shape; the gate must go red on every one.
SEEDED_VIOLATIONS = [
    ('os.makedirs("runs", exist_ok=True)', "os.makedirs", "runs"),
    ('os.mkdir("pb-runs")', "os.mkdir", "pb-runs"),
    ('Path("runs/latest").mkdir(parents=True)', "Path().mkdir", "runs/latest"),
    ('(Path("runs") / stamp).mkdir()', "Path().mkdir", "runs"),
    ('Path("runs").resolve().mkdir()', "Path().mkdir", "runs"),
    ('open("runs/result.json", "w").close()', "open", "runs/result.json"),
    ('open("out.log", mode="a").close()', "open", "out.log"),
    ('Path("runs/result.json").write_text("{}")', "Path().write_text", "runs/result.json"),
    ('Path("runs/blob.bin").write_bytes(b"")', "Path().write_bytes", "runs/blob.bin"),
    ('Path("runs/marker").touch()', "Path().touch", "runs/marker"),
    ('Path("runs/x.json").open("w").close()', "Path().open", "runs/x.json"),
    ('shutil.copytree(src, "runs/copy")', "shutil.copytree", "runs/copy"),
    ('shutil.move(src, "runs/moved")', "shutil.move", "runs/moved"),
    ('os.makedirs(os.path.join("runs", stamp))', "os.makedirs", "runs"),
    ('os.makedirs(f"runs/{stamp}")', "os.makedirs", "runs/"),
    ('Path("runs", stamp).mkdir(parents=True)', "Path().mkdir", "runs"),
    ('(Path() / "runs").mkdir()', "Path().mkdir", "."),
    ('Path("runs").joinpath(stamp).touch()', "Path().touch", "runs"),
]


@pytest.mark.parametrize(("snippet", "symbol", "literal"), SEEDED_VIOLATIONS)
def test_the_cwd_write_scanner_detects_seeded_violations(
    snippet: str, symbol: str, literal: str
) -> None:
    """A gate that cannot fail is not a gate. This is the proof that it can."""
    source = (
        "import os\n"
        "import shutil\n"
        "from pathlib import Path\n"
        "\n"
        "def rot(src, stamp):\n"
        f"    {snippet}\n"
    )
    hits = scan_source(source, "seeded.py")
    assert [(hit.symbol, hit.literal) for hit in hits] == [(symbol, literal)], (
        f"scanner missed the seeded violation {snippet!r}; got {hits}"
    )


def test_the_cwd_write_scanner_detects_aliased_and_indirect_violations() -> None:
    """Aliased imports and single-assignment locals must not launder a write.

    ``import os as _os`` is not hypothetical — it appears verbatim in this
    repo's own scripts, and a matcher keyed on the spelling ``os.makedirs``
    walks straight past it.
    """
    source = (
        "import os as _os\n"
        "import shutil as _sh\n"
        "from pathlib import Path as _Path\n"
        "\n"
        "def rot(src):\n"
        '    _os.makedirs("runs")\n'
        '    out = _Path("pb-runs")\n'
        "    out.mkdir()\n"
        '    _sh.copytree(src, "runs/copy")\n'
    )
    hits = scan_source(source, "aliased.py")
    assert [(hit.symbol, hit.literal) for hit in hits] == [
        ("os.makedirs", "runs"),
        ("Path().mkdir", "pb-runs"),
        ("shutil.copytree", "runs/copy"),
    ], hits


#: Writes that are already disciplined. Flagging any of these would be a false
#: positive, and false positives are how a gate gets deleted.
LEGITIMATE_WRITES = [
    'os.makedirs("/var/tmp/chimera", exist_ok=True)',          # absolute
    '(Path.home() / ".chimera" / "runs").mkdir(parents=True)',  # user home
    'Path("~/.chimera/runs").expanduser().mkdir()',             # ~-rooted
    'Path(tempfile.mkdtemp()).joinpath("x").write_text("{}")',  # temp dir
    'Path(tempfile.gettempdir(), "runs").mkdir(exist_ok=True)',
    'os.makedirs(output_dir, exist_ok=True)',                   # caller-supplied
    '(base / "runs").mkdir(parents=True)',                      # variable root
    'Path(env.workdir, "runs").mkdir()',                        # attribute root
    'env.write_text("out.txt", "{}")',                          # sandbox-relative
    'workdir.write_text("{}")',                                 # opaque receiver
    'open("chimera/config.toml").close()',                      # a READ
    'Path("pyproject.toml").read_text()',                       # a READ
    'os.makedirs(f"{base}/runs")',                              # variable prefix
    'shutil.copytree("runs", dest)',                            # relative SOURCE
    'self.render("runs/x")',                                    # unrelated call
]


@pytest.mark.parametrize("snippet", LEGITIMATE_WRITES)
def test_the_cwd_write_scanner_ignores_disciplined_writes(snippet: str) -> None:
    source = (
        "import os\n"
        "import shutil\n"
        "import tempfile\n"
        "from pathlib import Path\n"
        "\n"
        "def fine(self, base, dest, env, output_dir, workdir):\n"
        f"    {snippet}\n"
    )
    assert scan_source(source, "fine.py") == [], snippet


def test_a_parameter_shadows_a_module_level_literal() -> None:
    """The caller's directory is the caller's, whatever the module named it."""
    source = (
        "from pathlib import Path\n"
        "\n"
        'DEFAULT = Path("runs")\n'
        "\n"
        "def write(DEFAULT):\n"
        "    DEFAULT.mkdir(parents=True)\n"
    )
    assert scan_source(source, "shadowed.py") == []
    # ...but the module-level literal itself is still reachable and still red.
    leaky = source + "\ndef rot():\n    DEFAULT.mkdir()\n"
    assert [hit.symbol for hit in scan_source(leaky, "leaky.py")] == ["Path().mkdir"]


def test_rebinding_defeats_local_path_resolution() -> None:
    """A rebound name is unknowable without dataflow — it must not be guessed."""
    source = (
        "from pathlib import Path\n"
        "\n"
        "def maybe(base):\n"
        '    out = Path("runs")\n'
        "    out = base / out\n"
        "    out.mkdir()\n"
    )
    assert scan_source(source, "rebound.py") == []


def test_the_package_scan_actually_reads_files() -> None:
    """Guards the scan against silently becoming a no-op over an empty set."""
    modules = list(PACKAGE.rglob("*.py"))
    assert len(modules) > 100, f"only {len(modules)} modules found under {PACKAGE}"


def test_every_scanned_root_actually_contributes_files() -> None:
    """A root that reads zero files is a scope claim the gate cannot back."""
    empty = [
        name for name in SCANNED_ROOTS
        if not any((ROOT / name).rglob("*.py"))
    ]
    assert empty == [], f"SCANNED_ROOTS entries with no Python: {empty}"


def test_every_python_root_is_scanned() -> None:
    """No tracked top-level directory may hold Python the gate never reads.

    The package-only scope is exactly how ``scripts/modal_bench_app.py`` wrote
    a cwd-relative ``data/`` for months without a single red test. A new root
    with Python in it must join SCANNED_ROOTS in the commit that adds it.
    """
    unscanned = sorted(
        name for name in ALLOWED_ROOT_ENTRIES
        if (ROOT / name).is_dir()
        and name not in SCANNED_ROOTS
        and name != ".github"  # workflow YAML, no Python
        and any((ROOT / name).rglob("*.py"))
    )
    assert unscanned == [], (
        f"tracked roots holding unscanned Python: {unscanned} — add them to "
        "SCANNED_ROOTS, in the same commit, or explain the exemption here."
    )


def _seed_violating_package(tmp_path: Path) -> Path:
    package = tmp_path / "chimera"
    (package / "eval").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "eval" / "clean.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "def fine(output_dir):\n"
        '    (Path(output_dir) / "runs").mkdir(parents=True, exist_ok=True)\n',
        encoding="utf-8",
    )
    (package / "eval" / "rotter.py").write_text(
        "import os\n"
        "\n"
        "def run():\n"
        '    os.makedirs("pb-runs", exist_ok=True)\n',
        encoding="utf-8",
    )
    return package


def test_the_package_scan_goes_red_on_a_seeded_violation(tmp_path: Path) -> None:
    """The same walk the gate runs, over a tree that contains one rotter."""
    hits = scan_package(root=_seed_violating_package(tmp_path))
    assert [(hit.path, hit.symbol, hit.literal) for hit in hits] == [
        ("rotter.py", "os.makedirs", "pb-runs")
    ], hits


def test_the_allowlist_suppresses_exactly_its_entry(tmp_path: Path) -> None:
    """The allowlist must be a live code path, not a decorative constant."""
    package = _seed_violating_package(tmp_path)
    assert scan_package(root=package, allowlist=frozenset({("rotter.py", "pb-runs")})) == []
    # ...and it is keyed on BOTH fields — a near-miss must still go red.
    assert scan_package(root=package, allowlist=frozenset({("rotter.py", "runs")})) != []


@pytest.mark.parametrize("root", [r for r in SCANNED_ROOTS if r != "chimera"])
def test_a_violation_in_a_newly_covered_root_goes_red(
    root: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Widening the scope is only real if each new root can fail the suite.

    Drives the exact function the gate runs (``scan_repo``) over a fake repo
    whose roots mirror the real ones, with one rotter seeded in the root under
    test. ``chimera`` is excluded only because it was already proven — every
    root this commit ADDED is proven here.
    """
    for name in SCANNED_ROOTS:
        (tmp_path / name).mkdir()
    rotter = tmp_path / root / "driver.py"
    rotter.write_text(
        "import os\n"
        "\n"
        "def run():\n"
        '    os.makedirs("runs", exist_ok=True)\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(f"{__name__}.ROOT", tmp_path)

    hits = scan_repo(allowlist=frozenset())
    assert [(hit.path, hit.symbol, hit.literal) for hit in hits] == [
        (f"{root}/driver.py", "os.makedirs", "runs")
    ], hits
    # ...and the allowlist reaches the new roots too.
    assert scan_repo(allowlist=frozenset({(f"{root}/driver.py", "runs")})) == []
