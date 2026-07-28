"""Badger slash-command palette.

Mirrors the upstream's command palette while reusing Chimera's shared
:mod:`chimera.cli.slash_commands` infrastructure for the canonical
handlers. Adds the badger-specific commands ``/parity`` and ``/rerun``
which expose the harness-rewrite knobs from inside the REPL.

Public surface:

* :data:`BADGER_SLASH_COMMANDS` — ``{name: handler}`` dict.
* :data:`BADGER_SLASH_HELP` — ``{name: help_text}`` dict.
* :func:`register_badger_slash` — install the palette onto a REPL state.

Trademark hygiene: comparative language uses neutral phrasing — the
upstream is referenced as "the upstream" or not at all.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "BADGER_SLASH_COMMANDS",
    "BADGER_SLASH_HELP",
    "PrintFn",
    "SlashHandler",
    "cmd_agents",
    "cmd_bughunter",
    "cmd_export",
    "cmd_git_branch",
    "cmd_git_commit",
    "cmd_git_diff",
    "cmd_git_log",
    "cmd_git_push",
    "cmd_git_status",
    "cmd_memory",
    "cmd_parity",
    "cmd_rerun",
    "cmd_skills",
    "cmd_ultraplan",
    "register_badger_slash",
]


PrintFn = Callable[[str], None]
SlashHandler = Callable[[Any, Any, str, PrintFn], None]


# ---------------------------------------------------------------------------
# Shared handlers — pulled from chimera.cli.slash_commands and chimera.cli.code.
# ---------------------------------------------------------------------------

from chimera.cli.slash_commands import (  # noqa: E402
    cmd_compact as _cmd_compact,
    cmd_config as _cmd_config,
    cmd_cost as _cmd_cost,
    cmd_diff as _cmd_diff,
    cmd_doctor as _cmd_doctor,
    cmd_help as _cmd_help,
    cmd_resume as _cmd_resume,
    cmd_status as _cmd_status,
)

from chimera.config.ignore import prune_dirnames  # noqa: E402
from chimera.config.paths import store_path  # noqa: E402
from chimera.cli.code import (  # noqa: E402
    cmd_agent as _cmd_agent,
    cmd_clear as _cmd_clear,
    cmd_exit as _cmd_exit,
    cmd_init as _cmd_init,
    cmd_model as _cmd_model,
    cmd_session as _cmd_session,
    cmd_tools as _cmd_tools,
    cmd_yolo as _cmd_yolo,
)


# ---------------------------------------------------------------------------
# Badger-specific commands
# ---------------------------------------------------------------------------


def cmd_parity(_session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """Run a parity check against a schema file from inside the REPL.

    Usage:
        ``/parity`` — auto-resolve schema (PARITY.md / PARITY.json in cwd).
        ``/parity <path>`` — load the schema at *path*.

    Late-binds :mod:`chimera.badger.parity` so the slash registry is
    importable even on a partial install.
    """
    try:
        from chimera.badger.parity import (
            build_live_snapshot,
            diff_schema,
            format_report,
            load_schema,
        )
    except ImportError as exc:
        out(f"/parity: parity module unavailable ({exc})")
        return

    from pathlib import Path

    raw = args.strip()
    if raw:
        path = Path(raw)
    else:
        cwd = Path.cwd()
        for name in ("PARITY.md", "PARITY.json", "PARITY.yaml", "PARITY.yml"):
            candidate = cwd / name
            if candidate.exists():
                path = candidate
                break
        else:
            out("/parity: no schema found in cwd. Pass /parity <path>.")
            return
    try:
        expected = load_schema(path)
    except Exception as exc:  # noqa: BLE001
        out(f"/parity: load failed: {exc}")
        return
    live = build_live_snapshot()
    report = diff_schema(expected, live)
    out(format_report(report))


def cmd_rerun(session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """Show or set the rerun-on-failure budget for this session.

    Usage:
        ``/rerun`` — print the current budget.
        ``/rerun on`` / ``/rerun off`` — enable / disable rerun.
        ``/rerun <int>`` — set ``max_reruns`` (also enables rerun).

    The session's state lives at ``session.rerun_on_failure`` (bool) and
    ``session.max_reruns`` (int). Missing attributes default to off / 2.
    """
    current_on = bool(getattr(session, "rerun_on_failure", False))
    current_n = int(getattr(session, "max_reruns", 2) or 2)
    raw = args.strip().lower()

    if not raw:
        out(f"/rerun: rerun_on_failure={current_on} max_reruns={current_n}")
        return
    if raw in ("on", "true", "1", "yes"):
        try:
            setattr(session, "rerun_on_failure", True)
        except (AttributeError, TypeError):
            out("/rerun: cannot persist on session")
            return
        out(f"/rerun: enabled (max_reruns={current_n})")
        return
    if raw in ("off", "false", "0", "no"):
        try:
            setattr(session, "rerun_on_failure", False)
        except (AttributeError, TypeError):
            out("/rerun: cannot persist on session")
            return
        out("/rerun: disabled")
        return
    try:
        n = int(raw)
    except ValueError:
        out(f"/rerun: unrecognized argument {args.strip()!r} (use on/off/<int>)")
        return
    if n < 0:
        out("/rerun: max_reruns must be >= 0")
        return
    try:
        setattr(session, "rerun_on_failure", True)
        setattr(session, "max_reruns", n)
    except (AttributeError, TypeError):
        out("/rerun: cannot persist on session")
        return
    out(f"/rerun: enabled, max_reruns={n}")


# ---------------------------------------------------------------------------
# /memory — show or open ~/.chimera/badger/memory.md
# ---------------------------------------------------------------------------


def _badger_memory_path() -> Path:
    """Return the badger-scoped memory file path, honouring ``$CHIMERA_HOME``."""
    return store_path("badger") / "memory.md"


def cmd_memory(_session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """``/memory`` — show or edit ``~/.chimera/badger/memory.md``.

    Forms:

    * ``/memory``                 — print the file (or a hint when
      empty / missing).
    * ``/memory edit``            — open the file in ``$EDITOR``;
      creates the file (and parent dirs) if missing.
    * ``/memory append <text>``   — append a line to the file.
    """
    target = _badger_memory_path()
    raw = args.strip()
    if not raw:
        if not target.exists():
            out(f"/memory: no notes yet ({target}). Try '/memory edit'.")
            return
        try:
            content = target.read_text(encoding="utf-8")
        except OSError as exc:
            out(f"/memory: read failed: {exc}")
            return
        out(content if content.strip() else f"/memory: {target} is empty.")
        return

    head, _, tail = raw.partition(" ")
    head = head.lower().strip()
    tail = tail.strip()

    if head == "edit":
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("", encoding="utf-8")
        editor = os.environ.get("EDITOR", "").strip() or "nano"
        try:
            subprocess.run(  # noqa: S603 — user-controlled $EDITOR is intentional
                [*shlex.split(editor), str(target)],
                check=False,
            )
        except FileNotFoundError:
            out(f"/memory: editor {editor!r} not found")
            return
        out(f"/memory: edited {target}")
        return

    if head == "append":
        if not tail:
            out("/memory append: missing text")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(tail.rstrip("\n") + "\n")
        out(f"/memory: appended to {target}")
        return

    out(f"/memory: unknown action {head!r} (use 'edit' or 'append <text>')")


# ---------------------------------------------------------------------------
# /export — write the current session as JSON or Markdown
# ---------------------------------------------------------------------------


def cmd_export(session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """``/export <format>`` — export the current session to a file.

    Supports ``json`` (default) and ``md``. The output path is
    ``~/.chimera/exports/badger-<session-id>.<ext>`` unless
    ``args`` contains ``to <path>``.
    """
    raw = args.strip().lower()
    if not raw:
        fmt, target_path = "json", None
    else:
        parts = raw.split()
        fmt = parts[0]
        target_path = None
        if len(parts) >= 3 and parts[1] == "to":
            target_path = " ".join(parts[2:])
    if fmt not in {"json", "md"}:
        out(f"/export: unknown format {fmt!r} (use json or md)")
        return

    history = list(getattr(session, "history", None) or [])
    session_id = str(getattr(session, "id", None) or getattr(session, "session_id", "")) or "current"
    out_dir = store_path("exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    if target_path:
        path = Path(target_path)
    else:
        ext = "json" if fmt == "json" else "md"
        path = out_dir / f"badger-{session_id}.{ext}"

    if fmt == "json":
        import json

        payload = {
            "session_id": session_id,
            "history": [
                {"role": role, "content": content}
                for role, content in history
            ],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    else:
        lines: list[str] = [f"# badger session `{session_id}`", ""]
        for role, content in history:
            lines.append(f"## {role}")
            lines.append("")
            lines.append(str(content))
            lines.append("")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out(f"/export: wrote {path}")


# ---------------------------------------------------------------------------
# /agents — list bundled subagent profiles
# ---------------------------------------------------------------------------


def cmd_agents(_session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """``/agents`` — list bundled subagent profiles.

    With ``args == "show <name>"`` prints the markdown body of one
    profile so users can inspect what a preset will actually load.
    """
    try:
        from chimera.agents.config import _parse_frontmatter  # type: ignore[attr-defined]
        from chimera.agents.loader import builtin_subagents_dir
    except Exception as exc:  # noqa: BLE001
        out(f"/agents: registry unavailable ({exc})")
        return

    raw = args.strip()
    sub_dir = builtin_subagents_dir()
    if not sub_dir.is_dir():
        out("/agents: no bundled subagent profiles found")
        return
    profiles = sorted(sub_dir.glob("*.md"))
    if not profiles:
        out("/agents: no bundled subagent profiles found")
        return

    if raw.lower().startswith("show"):
        _, _, name = raw.partition(" ")
        name = name.strip()
        if not name:
            out("/agents show: missing profile name")
            return
        target = sub_dir / f"{name}.md"
        if not target.exists():
            out(f"/agents show: profile {name!r} not found")
            return
        out(target.read_text(encoding="utf-8"))
        return

    rows: list[str] = []
    for md in profiles:
        meta: dict[str, Any] = {}
        try:
            parsed = _parse_frontmatter(md.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                meta = parsed
        except Exception:  # noqa: BLE001
            pass
        name_val = meta.get("name") if isinstance(meta.get("name"), str) else ""
        desc_val = (
            meta.get("description")
            if isinstance(meta.get("description"), str)
            else ""
        )
        rows.append(f"  {(name_val or md.stem):<14} {desc_val or ''}")
    out("\n".join(["Bundled subagent profiles:", *rows]))


# ---------------------------------------------------------------------------
# /skills — list discovered skills
# ---------------------------------------------------------------------------


def cmd_skills(_session: Any, env: Any, _args: str, out: PrintFn) -> None:
    """``/skills`` — list every skill discoverable from the current cwd.

    Late-binds :mod:`chimera.skills.discovery` so badger can be
    imported on a partial install (skills is an optional surface).
    """
    try:
        from chimera.skills.discovery import discover_all_skills
    except Exception as exc:  # noqa: BLE001
        out(f"/skills: discovery unavailable ({exc})")
        return

    workdir = str(getattr(env, "workdir", None) or os.getcwd())
    try:
        # Also lists other harnesses' skills when the opt-in foreign scan is
        # enabled (config / CHIMERA_SKILLS_FOREIGN); default off.
        skills = discover_all_skills(workdir=workdir)
    except Exception as exc:  # noqa: BLE001
        out(f"/skills: discovery failed: {exc}")
        return
    if not skills:
        out("/skills: no skills discovered")
        return
    rows = []
    for s in skills:
        src = getattr(s, "source", "chimera")
        suffix = "" if src == "chimera" else f"  (source: {src})"
        rows.append(f"  {s.name:<24} {s.description}{suffix}")
    out("\n".join([f"Discovered {len(skills)} skill(s):", *rows]))


# ---------------------------------------------------------------------------
# /git — six subcommand wrappers sharing a helper
# ---------------------------------------------------------------------------


def _run_git(out: PrintFn, *git_argv: str, cwd: str | None = None) -> int:
    """Run ``git <argv>``, render stdout + stderr via ``out``.

    Returns the subprocess return code. ``git`` failures (non-zero
    exit, ``FileNotFoundError`` for the binary itself) surface a
    friendly message rather than a raw traceback.
    """
    try:
        proc = subprocess.run(  # noqa: S603, S607 — explicit git invocation
            ["git", *git_argv],
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
        )
    except FileNotFoundError:
        out("/git: 'git' binary not found in PATH")
        return 127
    except OSError as exc:
        out(f"/git: invocation failed: {exc}")
        return 1
    if proc.stdout:
        out(proc.stdout.rstrip("\n"))
    if proc.stderr:
        out(proc.stderr.rstrip("\n"))
    if proc.returncode != 0 and not proc.stdout and not proc.stderr:
        out(f"/git: exit {proc.returncode}")
    return int(proc.returncode)


def _git_cwd(env: Any) -> str | None:
    """Resolve the git working directory from ``env`` (best-effort)."""
    return getattr(env, "workdir", None)


def cmd_git_status(_session: Any, env: Any, _args: str, out: PrintFn) -> None:
    """``/git status`` — short-form working tree status."""
    _run_git(out, "status", "-sb", cwd=_git_cwd(env))


def cmd_git_diff(_session: Any, env: Any, args: str, out: PrintFn) -> None:
    """``/git diff [<paths>]`` — colourless diff vs the current index."""
    extra = shlex.split(args) if args.strip() else []
    _run_git(out, "diff", "--no-color", *extra, cwd=_git_cwd(env))


def cmd_git_log(_session: Any, env: Any, args: str, out: PrintFn) -> None:
    """``/git log [<n>]`` — last ``n`` commits in oneline form (default 10)."""
    raw = args.strip()
    try:
        limit = int(raw) if raw else 10
    except ValueError:
        out(f"/git log: expected an integer, got {raw!r}")
        return
    _run_git(
        out, "log", f"-n{max(1, limit)}", "--oneline", "--no-color",
        cwd=_git_cwd(env),
    )


def cmd_git_commit(_session: Any, env: Any, args: str, out: PrintFn) -> None:
    """``/git commit -m '<msg>'`` — friendlier wrapper.

    ``args`` is forwarded verbatim. If empty, surface a hint rather
    than spawning git's interactive editor (which would block the
    REPL).
    """
    raw = args.strip()
    if not raw:
        out("/git commit: pass '-m \"<msg>\"' or use /memory edit first")
        return
    extra = shlex.split(raw)
    _run_git(out, "commit", *extra, cwd=_git_cwd(env))


def cmd_git_push(_session: Any, env: Any, args: str, out: PrintFn) -> None:
    """``/git push [<remote> <branch>]`` — push to the configured remote."""
    extra = shlex.split(args) if args.strip() else []
    _run_git(out, "push", *extra, cwd=_git_cwd(env))


def cmd_git_branch(_session: Any, env: Any, args: str, out: PrintFn) -> None:
    """``/git branch [<args>]`` — show / create branches."""
    extra = shlex.split(args) if args.strip() else []
    _run_git(out, "branch", *extra, cwd=_git_cwd(env))


# ---------------------------------------------------------------------------
# /bughunter — kick off a multi-perspective bug-hunting workflow
# ---------------------------------------------------------------------------


_BUGHUNTER_PROMPT = (
    "You are running a bug-hunting workflow on the current repository. "
    "Sweep the codebase for: (1) off-by-one + boundary errors, "
    "(2) silently swallowed exceptions, (3) unhandled None / null returns, "
    "(4) race conditions, (5) resource leaks (open files, sockets), "
    "(6) shell-injection / unsafe subprocess usage. For each finding, "
    "report file:line, severity (low/med/high), and a suggested fix."
)


def cmd_bughunter(session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """``/bughunter [<scope>]`` — start a bug-hunting workflow.

    Stashes the multi-perspective prompt on the session under
    ``pending_workflow_prompt`` so the next agent turn picks it up.
    Falls back to printing the prompt when the session can't store it
    (so the user can copy/paste).
    """
    scope = args.strip()
    prompt = _BUGHUNTER_PROMPT
    if scope:
        prompt = f"{prompt}\nScope: {scope}"
    try:
        setattr(session, "pending_workflow_prompt", prompt)
        out(f"/bughunter: queued bug-hunt workflow ({len(prompt)} chars)")
    except (AttributeError, TypeError):
        out(prompt)


# ---------------------------------------------------------------------------
# /ultraplan — multi-step planning workflow (uses planner subagent)
# ---------------------------------------------------------------------------


_ULTRAPLAN_PROMPT_HEADER = (
    "You are running an ULTRAPLAN multi-step planning workflow.\n"
    "Phase 1: clarify the goal in 2-3 questions (no answers yet — surface them).\n"
    "Phase 2: enumerate every option (>=3) with trade-offs.\n"
    "Phase 3: pick the smallest-blast-radius option and emit a step-by-step plan.\n"
    "Phase 4: identify risks + rollback strategy.\n"
    "Phase 5: list the files that will change and the test/lint/type commands "
    "that must pass before declaring done.\n"
    "Do NOT call edit/write/bash tools — this is a planning turn."
)


def cmd_ultraplan(session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """``/ultraplan <goal>`` — queue a five-phase planning prompt.

    The composed prompt is stored on ``session.pending_workflow_prompt``
    so the next agent turn consumes it. Mirrors :func:`cmd_bughunter`'s
    pending-prompt convention so both workflows interop with the same
    REPL plumbing.
    """
    goal = args.strip()
    if not goal:
        out("/ultraplan: missing goal (e.g. '/ultraplan migrate auth')")
        return
    prompt = f"{_ULTRAPLAN_PROMPT_HEADER}\n\nGoal: {goal}"
    try:
        setattr(session, "pending_workflow_prompt", prompt)
        out(f"/ultraplan: queued five-phase plan for {goal!r}")
    except (AttributeError, TypeError):
        out(prompt)


# ---------------------------------------------------------------------------
# W15-2 P2 (CLAW G23): /teleport — symbol/path resolver
# ---------------------------------------------------------------------------


def _teleport_resolve(target: str, cwd: str | None) -> list[tuple[str, str]]:
    """Resolve *target* to a list of ``(path, summary)`` candidates.

    Resolution strategy (cheap, stdlib-only):

    1. If *target* names an existing file/dir, return it directly.
    2. Otherwise, walk the working tree (skipping ``.git`` /
       ``node_modules`` / ``__pycache__`` / ``.venv``) and grep for
       lines matching ``def <target>`` or ``class <target>`` in
       ``.py`` files; ``def <target>(`` / ``function <target>(`` in
       ``.js`` / ``.ts`` / ``.tsx``; ``fn <target>`` in ``.rs``.
    3. Cap the result at 25 hits so the slash never spams.
    """
    import os
    from pathlib import Path

    base = Path(cwd or os.getcwd())
    direct = base / target
    if direct.exists():
        kind = "directory" if direct.is_dir() else "file"
        return [(str(direct), kind)]

    if not target or any(c in target for c in (" ", "\t", "\n")):
        return []

    py_pat = (f"def {target}", f"class {target}")
    js_pat = (f"function {target}(", f"function {target} ", f"const {target} =")
    rs_pat = (f"fn {target}",)
    hits: list[tuple[str, str]] = []
    max_hits = 25

    for root, dirs, files in os.walk(base):
        prune_dirnames(dirs)
        for fname in files:
            if len(hits) >= max_hits:
                break
            ext = Path(fname).suffix.lower()
            patterns: tuple[str, ...]
            if ext == ".py":
                patterns = py_pat
            elif ext in (".js", ".ts", ".tsx", ".jsx", ".mjs"):
                patterns = js_pat
            elif ext == ".rs":
                patterns = rs_pat
            else:
                continue
            full = Path(root) / fname
            try:
                with full.open("r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if any(p in line for p in patterns):
                            rel = full.relative_to(base)
                            summary = line.strip()[:120]
                            hits.append((f"{rel}:{lineno}", summary))
                            if len(hits) >= max_hits:
                                break
            except OSError:
                continue
        if len(hits) >= max_hits:
            break
    return hits


def cmd_teleport(_session: Any, env: Any, args: str, out: PrintFn) -> None:
    """``/teleport <symbol-or-path>`` — locate a symbol or file in the repo.

    Bridges the upstream agent's ``/teleport`` symbol picker. Returns up
    to 25 ``path:line`` hits with a one-line summary so the operator can
    paste a target into their next prompt without leaving the REPL.

    Args (CLI tokens):
        symbol-or-path: file/directory path or Python/JS/TS/Rust symbol.
    """
    target = (args or "").strip()
    if not target:
        out("/teleport: missing symbol-or-path argument")
        return
    cwd = getattr(env, "workdir", None) if env is not None else None
    hits = _teleport_resolve(target, cwd)
    if not hits:
        out(f"/teleport: no results for {target!r}")
        return
    out(f"/teleport: {len(hits)} result(s) for {target!r}")
    for location, summary in hits:
        if summary:
            out(f"  {location}    {summary}")
        else:
            out(f"  {location}")


# ---------------------------------------------------------------------------
# The palette
# ---------------------------------------------------------------------------

BADGER_SLASH_COMMANDS: dict[str, SlashHandler] = {
    # Session
    "clear": _cmd_clear,
    "session": _cmd_session,
    # WHY (G9, w13): /resume + /diff are the cross-CLI standard slashes.
    # badger surfaces them first; the shared registry under
    # :mod:`chimera.cli.slash_commands` already exposes them to mink/
    # otter/ferret REPLs that build off the same registry.
    "resume": _cmd_resume,
    "diff": _cmd_diff,
    # Agent
    "agent": _cmd_agent,
    "model": _cmd_model,
    "tools": _cmd_tools,
    "yolo": _cmd_yolo,
    # Badger-specific (harness-rewrite posture)
    "parity": cmd_parity,
    "rerun": cmd_rerun,
    # W14-4: discoverability palette
    "memory": cmd_memory,
    "export": cmd_export,
    "agents": cmd_agents,
    "skills": cmd_skills,
    # W14-4: git automation (six wrappers sharing _run_git)
    "git status": cmd_git_status,
    "git diff": cmd_git_diff,
    "git log": cmd_git_log,
    "git commit": cmd_git_commit,
    "git push": cmd_git_push,
    "git branch": cmd_git_branch,
    # W14-4: workflows
    "bughunter": cmd_bughunter,
    "ultraplan": cmd_ultraplan,
    # W15-2 P2 (CLAW G23): symbol/path resolver
    "teleport": cmd_teleport,
    # System
    "help": _cmd_help,
    "status": _cmd_status,
    "doctor": _cmd_doctor,
    "config": _cmd_config,
    "cost": _cmd_cost,
    "compact": _cmd_compact,
    "init": _cmd_init,
    "exit": _cmd_exit,
    "quit": _cmd_exit,
}


BADGER_SLASH_HELP: dict[str, str] = {
    "clear": "clear the current context",
    "session": "save / list / fork the current session",
    "resume": "resume a saved session by id (no arg = list recent)",
    "diff": "git diff vs HEAD (no arg = files modified this session)",
    "agent": "list agent presets",
    "model": "show or cycle the active model",
    "tools": "list available tools",
    "yolo": "toggle auto-approve mode",
    "parity": "run a parity check against a schema (e.g. PARITY.md)",
    "rerun": "show / set the rerun-on-failure budget for this session",
    "memory": "show / edit ~/.chimera/badger/memory.md",
    "export": "export the current session (json | md)",
    "agents": "list bundled subagent profiles",
    "skills": "list discoverable skills",
    "git status": "git status (short, with branch info)",
    "git diff": "git diff (no color)",
    "git log": "git log (oneline; arg = commit count)",
    "git commit": "git commit -m '<msg>'",
    "git push": "git push (args forwarded)",
    "git branch": "git branch (args forwarded)",
    "bughunter": "kick off a multi-perspective bug-hunting workflow",
    "ultraplan": "kick off a five-phase planning workflow",
    "teleport": "locate a symbol or path in the repo (Python/JS/TS/Rust)",
    "help": "show this list",
    "status": "one-screen status summary",
    "doctor": "environment health checks",
    "config": "print effective merged settings",
    "cost": "show cumulative cost",
    "compact": "force a HARD threshold compaction now",
    "init": "summarise the project",
    "exit": "leave the REPL",
    "quit": "leave the REPL",
}


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------


def _install_one(
    repl_state: Any, name: str, handler: SlashHandler, help_text: str,
) -> bool:
    """Install a single ``(name, handler, help_text)`` triple onto *repl_state*."""
    register = getattr(repl_state, "register", None)
    if callable(register):
        try:
            register(name, handler, help_text)
            return True
        except TypeError:
            try:
                register(name, handler)
                return True
            except Exception:  # noqa: BLE001
                return False

    for attr in ("commands", "slash_commands"):
        bag = getattr(repl_state, attr, None)
        if isinstance(bag, dict):
            bag[name] = handler
            return True

    try:
        setattr(repl_state, name, handler)
        return True
    except (AttributeError, TypeError):
        return False


def register_badger_slash(repl_state: Any) -> int:
    """Install every badger slash command onto ``repl_state``.

    Args:
        repl_state: Target object onto which the badger palette is
            installed. Accepts a ``register(name, handler, help_text)``
            method, or a ``commands`` / ``slash_commands`` mapping, or
            a plain object (we ``setattr`` as a last resort).

    Returns:
        The count of commands successfully installed.
    """
    installed = 0
    for name, handler in BADGER_SLASH_COMMANDS.items():
        help_text = BADGER_SLASH_HELP.get(name, "")
        if _install_one(repl_state, name, handler, help_text):
            installed += 1
    return installed
