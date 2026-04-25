"""Regression tests for the four MEDIUMs that W2 left deferred (M-10, M-17,
M-19, M-22).

Per the FIX-C task split, only **M-19** lands in this agent's territory; the
other three live entirely inside ``chimera/mink/cli.py`` (FIX-B's slot) and
have follow-up issues filed in ``research/mink/FIX-C-REPORT.md``.  This file
keeps a slot for each so the per-finding mapping in the audit and the
test-file layout stay symmetric — tests for the FIX-B-owned items skip
explicitly with the deferral reason, which is preferable to silently
omitting them.
"""
from __future__ import annotations

import inspect
import textwrap

import pytest


# ---------------------------------------------------------------------------
# M-10 — RedactionMiddleware not wired into mink stream-json output
# (chimera/mink/cli.py:_run_stream_json — owned by FIX-B)
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "M-10 fix lives in chimera/mink/cli.py:_run_stream_json which is "
        "FIX-B's exclusive territory; deferred to a follow-up commit "
        "(see research/mink/FIX-C-REPORT.md section 'Deferrals')."
    )
)
def test_m10_redaction_wired_into_mink_stream_json() -> None:
    """Placeholder. Once FIX-B wires ``StreamJsonHandler(redaction=...)`` into
    ``_run_stream_json``, replace this skip with an end-to-end repro that
    drives the CLI with a registered fake key in a tool argument and asserts
    the key is absent from the captured stdout."""
    raise AssertionError("placeholder — skipped above")


# ---------------------------------------------------------------------------
# M-17 — _StubAgent / _StubPrompt structural-typing escape hatch in
# chimera/mink/cli.py (owned by FIX-B)
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "M-17 lives in chimera/mink/cli.py (FIX-B); the suggested refactor "
        "extracts the stub into chimera/sessions/resume_helpers.py shared "
        "between mink/cli.py and slash_commands.py. Deferred."
    )
)
def test_m17_resume_stub_extracted_to_sessions_helpers() -> None:
    """Placeholder. Verify ``chimera.sessions.resume_helpers.ResumeStubAgent``
    exists and ``chimera.mink.cli`` imports it (no nested class redefinition)."""
    raise AssertionError("placeholder — skipped above")


# ---------------------------------------------------------------------------
# M-19 — bare ``except: pass`` in chimera/cli/code.py (FIX-C territory)
# ---------------------------------------------------------------------------


def test_m19_project_config_failure_is_logged_not_swallowed() -> None:
    """The project-context discovery branch must surface its error on stderr
    instead of silently dropping it.  We assert against the source AST text
    rather than running the REPL: the failure mode the audit flagged was a
    lexical ``except: pass`` so the lexical fix is the load-bearing one.
    """
    from chimera.cli import code

    src = inspect.getsource(code)
    # The two surfaces the audit named: project-config discovery and skills
    # discovery.  Both used to be ``except Exception:\n    pass``.
    assert "[project-config] discovery failed" in src, (
        "project-config error path no longer logs to stderr "
        "(M-19 regression: the bare except: pass was reintroduced)"
    )
    assert "[skills] discovery failed" in src, (
        "skills discovery error path no longer logs to stderr "
        "(M-19 regression: the bare except: pass was reintroduced)"
    )


def test_m19_no_bare_except_pass_in_code_module() -> None:
    """Stronger structural check: the discovery blocks no longer contain a
    bare ``except Exception:\\n            pass`` around either of the two
    discovery imports.  We use a textual probe rather than full AST so the
    test stays robust to nearby unrelated edits.
    """
    from pathlib import Path

    src = Path(
        inspect.getfile(__import__("chimera.cli.code", fromlist=["code"]))
    ).read_text()

    # The two specific blocks the audit named.
    bad_project_block = textwrap.dedent(
        """\
        from chimera.config.loader import ProjectConfig
                project = ProjectConfig.from_directory(workdir)
                if project and project.rules_text:
                    system += "\\n\\n# Project Context\\n" + project.rules_text
            except Exception:
                pass
        """
    )
    bad_skills_block_marker = "except Exception:\n        pass"

    # Coarse: the literal pattern from the audit must not survive verbatim.
    assert bad_project_block not in src, (
        "project-context block still has the bare except: pass pattern"
    )

    # Stricter: count how many ``except Exception:\\n        pass`` shapes
    # remain inside the ``run_chimera`` flow (the discovery section sits at
    # ~520-570). The fix replaces both, leaving zero in that range.
    discovery_window = src.split("# Auto-discover project context")[1].split(
        "# --- Wire all pi-mono features ---"
    )[0]
    assert bad_skills_block_marker not in discovery_window, (
        "auto-discover window still contains bare except: pass — M-19 fix "
        "regressed"
    )


# ---------------------------------------------------------------------------
# M-22 — --allowed-tools help text wording in chimera/mink/cli.py
# (owned by FIX-B)
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "M-22 is purely a help-text reword inside chimera/mink/cli.py "
        "(FIX-B). The substantive concern (flag wired) is already closed "
        "by an earlier overnight pass; only the ecosystem-parity wording "
        "remains. Deferred to FIX-B."
    )
)
def test_m22_allowed_tools_help_text_describes_filter() -> None:
    """Placeholder. Once FIX-B updates the help string, assert the new
    wording appears in ``chimera mink --help`` output."""
    raise AssertionError("placeholder — skipped above")
