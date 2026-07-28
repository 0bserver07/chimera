"""User-scope permission rules and hooks must actually load.

They did not. `CodingAgent` — the assembled stack behind `chimera code` —
passed `user_dir=~/.chimera` into `PermissionRuleLoader` and `HookLoader`,
both of which append `.chimera` themselves. Every user-scope lookup therefore
resolved to `~/.chimera/.chimera/settings.json`, a path nothing writes, so a
user's own `deny` rules **and** `PreToolUse` hooks were **silently ignored**.
Both halves are reproduced below; the hooks half was parked unproven for a day
and the reason it was hard to see is written up on `TestUserScopeHooks`.

That is the worst shape a security control can fail in: the file is present,
the syntax is valid, nothing warns, and the rule simply never applies. The
defect dates to the original assembly commit (`a5213636`); a later refactor
only renamed the expression building it.

Found while fixing a *latent* double-join in `CommandRegistry.load_all`
(zero callers, no user impact). The agent sent to fix that one checked the
sibling loaders' actual contract, found the convention ran the other way, and
surfaced this live one — which is the more serious defect by a wide margin.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from chimera.hooks.events import HookEvent
from chimera.hooks.loader import HookLoader
from chimera.permissions.loader import PermissionRuleLoader


def _home_with_user_settings(payload: dict) -> Path:
    home = Path(tempfile.mkdtemp())
    (home / ".chimera").mkdir(parents=True)
    (home / ".chimera" / "settings.json").write_text(json.dumps(payload))
    return home


class TestUserScopePermissionRules:
    def test_a_user_deny_rule_is_actually_loaded(self) -> None:
        home = _home_with_user_settings({"permissions": {"deny": ["Bash(rm -rf *)"]}})
        proj = Path(tempfile.mkdtemp())
        ctx = PermissionRuleLoader(
            project_dir=str(proj), user_dir=str(home)
        ).load()
        assert ctx.deny_rules, (
            "a deny rule in ~/.chimera/settings.json did not load — user-scope "
            "safety rules are being silently ignored"
        )
        assert "Bash(rm -rf *)" in next(iter(ctx.deny_rules.values()))

    def test_the_loader_appends_chimera_itself(self) -> None:
        """Pins the contract the caller must honour.

        `user_dir` is the HOME directory, not `~/.chimera`. Passing the latter
        yields `~/.chimera/.chimera/...` and loads nothing — the exact bug.
        """
        home = _home_with_user_settings({"permissions": {"deny": ["Bash(curl *)"]}})
        proj = Path(tempfile.mkdtemp())
        double_joined = PermissionRuleLoader(
            project_dir=str(proj), user_dir=str(home / ".chimera")
        ).load()
        assert not double_joined.deny_rules, (
            "passing ~/.chimera should find nothing — if this now loads, the "
            "loader's contract changed and CodingAgent must be re-checked"
        )


class TestUserScopeHooks:
    """The hooks half, no longer parked: it was broken the same way.

    This sat unproven for a day behind a fixture that loaded **zero** matchers
    under both scopes — a result that proves nothing, because a fixture failing
    identically everywhere is measuring itself, not the code. The cause was the
    settings schema, not the scope.

    ``HookLoader._parse_hook_config`` reads ``type``, ``matcher`` and
    ``command`` straight off each entry::

        {"hooks": {"PreToolUse": [
            {"type": "command", "matcher": "Bash", "command": "..."}]}}

    The parked fixture used the nested form — an entry that *wraps* a ``hooks``
    list, which is the shape several other tools take::

        {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", ...}]}]}}

    There ``config.get("command", "")`` is empty, ``_parse_hook_config``
    returns ``None``, and the loader yields nothing for any directory you hand
    it. Write the schema the loader actually expects and the scope defect
    reproduces exactly as it does for permissions: one matcher under the home
    dir, none under ``~/.chimera``.

    Generalisable, and the reason this is written out: **a fixture that fails
    under the fix and under the bug has not reproduced the bug.** Before
    concluding "no defect here", change something that must flip the result.
    """

    #: The shape `HookLoader` parses. `matcher` is a sibling of `command`.
    _HOOK = {
        "hooks": {
            "PreToolUse": [
                {"type": "command", "matcher": "Bash", "command": "echo denied"}
            ]
        }
    }

    def test_a_user_hook_is_actually_loaded(self) -> None:
        """Under the loader's real contract, the hook is there."""
        home = _home_with_user_settings(self._HOOK)
        proj = Path(tempfile.mkdtemp())
        matchers = HookLoader(
            project_dir=str(proj), user_dir=str(home)
        ).load_all(HookEvent.PRE_TOOL_USE)
        assert len(matchers) == 1, (
            "a PreToolUse hook in ~/.chimera/settings.json did not load under "
            "the loader's documented contract — the fixture is wrong again"
        )
        assert matchers[0].source == "user"
        assert matchers[0].matcher == "Bash"
        assert matchers[0].hooks[0].command == "echo denied"

    def test_the_scope_coding_agent_shipped_loaded_nothing(self) -> None:
        """The defect, with the same fixture that just succeeded.

        Only the argument changes. `CodingAgent` passed `~/.chimera`; the
        loader appends `.chimera`; the lookup landed on
        `~/.chimera/.chimera/settings.json`, which nothing writes. A user's
        PreToolUse hook — including one written to *block* a tool — never ran,
        and nothing anywhere said so.
        """
        home = _home_with_user_settings(self._HOOK)
        proj = Path(tempfile.mkdtemp())
        shipped = HookLoader(
            project_dir=str(proj), user_dir=str(home / ".chimera")
        ).load_all(HookEvent.PRE_TOOL_USE)
        assert shipped == [], (
            "passing ~/.chimera should find nothing — if this now loads, the "
            "loader's contract changed and CodingAgent must be re-checked"
        )

    def test_a_malformed_entry_is_dropped_without_a_word(self) -> None:
        """Characterisation, and a flagged sharp edge.

        The nested shape yields no matchers and no error, under every scope.
        That silence is what made the scope bug unfalsifiable for a day: two
        different faults present as the identical empty list. Pinned so the
        behaviour is at least *known*; if `_parse_hook_config` ever learns to
        warn or to accept the nested form, this test is the one to update, and
        updating it is the moment to re-read the note above.
        """
        nested = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "echo denied"}],
                    }
                ]
            }
        }
        home = _home_with_user_settings(nested)
        proj = Path(tempfile.mkdtemp())
        for user_dir in (home, home / ".chimera"):
            assert (
                HookLoader(
                    project_dir=str(proj), user_dir=str(user_dir)
                ).load_all(HookEvent.PRE_TOOL_USE)
                == []
            ), f"the nested schema unexpectedly parsed under {user_dir}"


class TestCodingAgentPassesTheRightScope:
    def test_coding_agent_hands_loaders_the_home_dir_not_the_state_dir(self) -> None:
        """The regression, read off the source that actually runs.

        Asserted structurally rather than by booting a CodingAgent (which needs
        provider credentials): the call sites must not pass `chimera_home()`
        directly, because the loaders append `.chimera` to whatever they get.
        """
        src = (
            Path(__file__).resolve().parents[2]
            / "chimera" / "assembly" / "coding_agent.py"
        ).read_text()
        assert "user_dir=str(chimera_home())," not in src, (
            "CodingAgent is passing ~/.chimera into a loader that appends "
            ".chimera — user-scope settings will silently not load"
        )
        assert src.count("user_dir=str(chimera_home().parent)") == 2, (
            "expected both the permissions and hooks call sites to pass the "
            "home dir"
        )
