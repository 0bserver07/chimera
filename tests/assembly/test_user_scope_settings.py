"""User-scope permission rules and hooks must actually load.

They did not. `CodingAgent` — the assembled stack behind `chimera code` —
passed `user_dir=~/.chimera` into `PermissionRuleLoader` and `HookLoader`,
both of which append `.chimera` themselves. Every user-scope lookup therefore
resolved to `~/.chimera/.chimera/settings.json`, a path nothing writes, so a
user's own `deny` rules were **silently ignored**.

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


# NOTE: the hooks half of this defect is NOT asserted here. CodingAgent passes
# the same wrong scope to HookLoader, and HookLoader joins ".chimera" the same
# way — but a fixture writing a PreToolUse hook into ~/.chimera/settings.json
# loads ZERO matchers under BOTH scopes, so the fixture (or the settings shape
# it assumes) is wrong, not necessarily the code. The call site is corrected
# alongside permissions because the contract is identical; claiming a proven
# user-visible hooks bug without a reproduction would be exactly the kind of
# unbacked assertion this repo keeps having to retract.
