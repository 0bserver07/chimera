"""Smoke tests for ``chimera.shrew.repl`` (agent S1).

The shrew REPL is a thin delegation layer over
:func:`chimera.cli.code.run_code` with two extras over weasel's REPL:

* It late-binds to optional shrew **skills** (S2) and **extensions**
  (S3) and injects a ``_post_session_init`` hook into the namespace
  when those modules are available.
* It honors shrew's smaller ``max_steps`` default (30) and the
  restricted ``--allowed-tools`` posture.

These tests verify the namespace adaptor and the late-binding without
booting a real provider or REPL.
"""
from __future__ import annotations

import argparse
import os
import sys
import types

from chimera.shrew import repl as shrew_repl


# ---------------------------------------------------------------------------
# Namespace adaptor
# ---------------------------------------------------------------------------


def test_build_run_code_namespace_translates_cwd_to_workdir(tmp_path) -> None:
    """``--cwd`` becomes ``workdir`` so :func:`run_code` reads it correctly."""
    args = argparse.Namespace(
        cwd=str(tmp_path),
        model="qwen3.6-35b-a3b",
        max_steps=30,
    )
    adapted = shrew_repl._build_run_code_namespace(args)  # noqa: SLF001
    assert adapted.workdir == str(tmp_path)
    assert adapted.model == "qwen3.6-35b-a3b"
    assert adapted.max_steps == 30
    assert adapted.mode == "interactive"
    assert adapted.print_mode is None
    assert adapted.preset is None


def test_build_run_code_namespace_defaults_workdir_to_cwd() -> None:
    """A missing ``--cwd`` falls back to :func:`os.getcwd`."""
    args = argparse.Namespace(
        cwd=None,
        model=None,
        max_steps=None,
    )
    adapted = shrew_repl._build_run_code_namespace(args)  # noqa: SLF001
    assert adapted.workdir == os.getcwd()
    # WHY: max_steps None / 0 should coerce to shrew's documented 30 default.
    assert adapted.max_steps == 30


def test_build_run_code_namespace_carries_max_steps() -> None:
    """User-supplied ``max_steps`` flows through to the inner namespace."""
    args = argparse.Namespace(cwd=None, model=None, max_steps=15)
    adapted = shrew_repl._build_run_code_namespace(args)  # noqa: SLF001
    assert adapted.max_steps == 15


# ---------------------------------------------------------------------------
# Skills + extensions late-binding
# ---------------------------------------------------------------------------


def test_mount_skills_returns_empty_when_module_missing(monkeypatch) -> None:
    """No :mod:`chimera.shrew.skills` => empty list, no crash."""
    monkeypatch.setitem(sys.modules, "chimera.shrew.skills", None)
    # Simulate ImportError on attribute resolution.
    skills = shrew_repl._mount_skills(workdir=os.getcwd())  # noqa: SLF001
    assert skills == []


def test_mount_skills_collects_records(monkeypatch) -> None:
    """When the skills module exposes ``discover_skills``, records flow through."""
    fake_module = types.SimpleNamespace(
        discover_skills=lambda workdir: [
            {"name": "search", "kind": "tool"},
            {"name": "git-bisect", "kind": "protocol"},
        ],
    )
    monkeypatch.setitem(sys.modules, "chimera.shrew.skills", fake_module)
    skills = shrew_repl._mount_skills(workdir=os.getcwd())  # noqa: SLF001
    assert len(skills) == 2
    assert skills[0]["name"] == "search"


def test_mount_skills_handles_discovery_failure(monkeypatch) -> None:
    """A raising ``discover_skills`` is downgraded to a warning + empty list."""

    def _boom(workdir):
        raise RuntimeError("bad skill markdown")

    fake_module = types.SimpleNamespace(discover_skills=_boom)
    monkeypatch.setitem(sys.modules, "chimera.shrew.skills", fake_module)
    skills = shrew_repl._mount_skills(workdir=os.getcwd())  # noqa: SLF001
    assert skills == []


def test_mount_extensions_returns_empty_when_module_missing(monkeypatch) -> None:
    """No :mod:`chimera.shrew.extensions` => empty list, no crash."""
    monkeypatch.setitem(sys.modules, "chimera.shrew.extensions", None)
    handles = shrew_repl._mount_extensions(workdir=os.getcwd())  # noqa: SLF001
    assert handles == []


def test_mount_extensions_collects_handles(monkeypatch) -> None:
    """When ``load_extensions`` exists, handles flow through."""
    fake_module = types.SimpleNamespace(
        load_extensions=lambda workdir: ["moe_offload", "tool_filter"],
    )
    monkeypatch.setitem(sys.modules, "chimera.shrew.extensions", fake_module)
    handles = shrew_repl._mount_extensions(workdir=os.getcwd())  # noqa: SLF001
    assert handles == ["moe_offload", "tool_filter"]


def test_make_post_session_init_returns_none_when_empty() -> None:
    """No skills + no extensions => no hook (run_code stays on fast path)."""
    hook = shrew_repl._make_post_session_init([], [])  # noqa: SLF001
    assert hook is None


def test_make_post_session_init_applies_extensions() -> None:
    """The post-init hook calls ``apply`` on every extension handle."""
    applied: list[str] = []

    class _Ext:
        def __init__(self, name):
            self.name = name

        def apply(self, session):
            applied.append(self.name)

    skills = [{"name": "x"}]
    extensions = [_Ext("moe"), _Ext("tools")]
    hook = shrew_repl._make_post_session_init(skills, extensions)  # noqa: SLF001
    assert callable(hook)
    session = types.SimpleNamespace()
    hook(session)
    assert applied == ["moe", "tools"]
    # WHY: skills get stashed on the session for skills-aware slash commands.
    assert getattr(session, "shrew_skills", None) == skills


def test_make_post_session_init_swallows_extension_errors(capsys) -> None:
    """A raising extension is logged but doesn't break the REPL."""

    class _BadExt:
        def apply(self, session):
            raise RuntimeError("extension blew up")

    hook = shrew_repl._make_post_session_init([], [_BadExt()])  # noqa: SLF001
    assert callable(hook)
    hook(types.SimpleNamespace())
    captured = capsys.readouterr()
    assert "extension apply failed" in captured.err


def test_build_run_code_namespace_attaches_post_session_init(monkeypatch) -> None:
    """The adapter sets ``_post_session_init`` only when something is mounted."""
    fake_skills = types.SimpleNamespace(
        discover_skills=lambda workdir: [{"name": "s"}],
    )
    fake_ext = types.SimpleNamespace(load_extensions=lambda workdir: [])
    monkeypatch.setitem(sys.modules, "chimera.shrew.skills", fake_skills)
    monkeypatch.setitem(sys.modules, "chimera.shrew.extensions", fake_ext)
    args = argparse.Namespace(cwd=None, model=None, max_steps=None)
    adapted = shrew_repl._build_run_code_namespace(args)  # noqa: SLF001
    assert callable(getattr(adapted, "_post_session_init", None))


def test_build_run_code_namespace_skips_post_session_init_when_empty(
    monkeypatch,
) -> None:
    """No skills + no extensions => no ``_post_session_init`` attribute."""
    monkeypatch.setitem(sys.modules, "chimera.shrew.skills", None)
    monkeypatch.setitem(sys.modules, "chimera.shrew.extensions", None)
    args = argparse.Namespace(cwd=None, model=None, max_steps=None)
    adapted = shrew_repl._build_run_code_namespace(args)  # noqa: SLF001
    assert not hasattr(adapted, "_post_session_init")


# ---------------------------------------------------------------------------
# Top-level run
# ---------------------------------------------------------------------------


def test_run_delegates_to_code_run_code(monkeypatch) -> None:
    """:func:`shrew.repl.run` forwards an adapted namespace to ``run_code``."""
    captured: dict[str, object] = {}

    def _fake_run_code(ns):
        captured["ns"] = ns
        return 0

    monkeypatch.setattr("chimera.cli.code.run_code", _fake_run_code)
    args = argparse.Namespace(
        cwd="/tmp",
        model="qwen3.6-35b-a3b",
        max_steps=30,
    )
    rc = shrew_repl.run(args)
    assert rc == 0
    ns = captured["ns"]
    assert isinstance(ns, argparse.Namespace)
    assert ns.workdir == "/tmp"
    assert ns.mode == "interactive"


def test_run_coerces_non_int_return(monkeypatch) -> None:
    """Non-int returns from ``run_code`` collapse to 0 (defensive)."""
    monkeypatch.setattr("chimera.cli.code.run_code", lambda _ns: None)
    args = argparse.Namespace(cwd=None, model=None, max_steps=None)
    rc = shrew_repl.run(args)
    assert rc == 0


def test_run_propagates_int_return(monkeypatch) -> None:
    """Int returns are forwarded as-is."""
    monkeypatch.setattr("chimera.cli.code.run_code", lambda _ns: 7)
    args = argparse.Namespace(cwd=None, model=None, max_steps=None)
    assert shrew_repl.run(args) == 7
