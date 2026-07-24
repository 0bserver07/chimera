"""One permission posture for a whole team (issue #150).

Every coding-agent runtime spells "what may this agent touch" its own
way — sandbox flags, a config file's permission block, an in-process
policy object. Configuring each teammate independently means the lead
has no way to say *"this team runs read-only"* and have it hold, and
the failure is silent: a teammate quietly running looser than the lead
intended looks exactly like one running correctly.

This module makes the lead's posture the team's posture:

1. **A policy lives on the team.** ``config.json`` carries
   ``policy``; every teammate resolves it unless explicitly overridden.
   Three postures, from tightest to loosest: ``read-only``,
   ``workspace-write``, ``dangerous``.
2. **Translation, per runtime.** :class:`RuntimeAdapter` maps a policy
   to the argv fragment that runtime understands, spliced into the
   runner's ``--cmd`` via ``{policy_args}``. Adapters are *data*: one
   built-in for Chimera's own CLI, everything else declared by the
   operator under ``[team_runtimes.<name>]`` in ``config.toml`` — the
   command that spawns an agent is the operator's, so the flags that
   configure it are too.
3. **In-process enforcement where we own the loop.** A Chimera teammate
   does not merely receive the posture, it *is bound by* it: the policy
   becomes a ``tool_call`` interceptor
   (:mod:`chimera.core.interception`) that blocks disallowed calls
   before the permission check runs, and writes every denial to the
   team's audit so ``chimera team status`` shows it. For a third-party
   runtime, enforcement stays that runtime's own — the translation just
   stops the operator from having to remember three dialects.

Coordination tools are never blocked
------------------------------------
``team_*`` tools are the substrate a teammate stands on, not the work
it does. A ``read-only`` teammate that cannot call ``team_claim_task``
is not safe, it is broken — and broken in the confusing way, where
tools fail with permission errors that look like the agent
misbehaving. Every posture allows ``team_*`` unconditionally.

Nothing here changes behavior until a policy is configured: with no
team policy and no ``--policy`` flag, teammates run exactly as before.
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from chimera.permissions.base import PermissionAction, PermissionPolicy

if TYPE_CHECKING:
    from chimera.cli.agent_teams import Team
    from chimera.core.interception import Interceptors, ToolCallInterceptor

__all__ = [
    "BUILTIN_RUNTIME_ADAPTERS",
    "COORDINATION_TOOL_PREFIX",
    "POLICY_VALUES",
    "POLICY_DANGEROUS",
    "POLICY_READ_ONLY",
    "POLICY_WORKSPACE_WRITE",
    "PolicyTranslation",
    "RuntimeAdapter",
    "WorkspaceWrite",
    "apply_policy_args",
    "detect_runtime",
    "parse_policy",
    "permission_policy_for",
    "resolve_runtime_adapter",
    "team_interceptors_from_env",
    "team_policy_interceptor",
    "translate_policy",
]

#: Only the read whitelist; every side-effecting tool is denied.
POLICY_READ_ONLY = "read-only"
#: Reads plus writes that stay inside the allowed roots.
POLICY_WORKSPACE_WRITE = "workspace-write"
#: No restriction at all — for sandboxes you already trust.
POLICY_DANGEROUS = "dangerous"

#: The three postures, tightest first.
POLICY_VALUES: tuple[str, ...] = (
    POLICY_READ_ONLY,
    POLICY_WORKSPACE_WRITE,
    POLICY_DANGEROUS,
)

#: Tool-name prefix for team coordination. Always allowed — see the
#: module docstring.
COORDINATION_TOOL_PREFIX = "team_"

_POLICY_ALIASES: dict[str, str] = {
    "read-only": POLICY_READ_ONLY,
    "read_only": POLICY_READ_ONLY,
    "readonly": POLICY_READ_ONLY,
    "workspace-write": POLICY_WORKSPACE_WRITE,
    "workspace_write": POLICY_WORKSPACE_WRITE,
    "write": POLICY_WORKSPACE_WRITE,
    "dangerous": POLICY_DANGEROUS,
    "danger-full-access": POLICY_DANGEROUS,
    "full": POLICY_DANGEROUS,
    "yolo": POLICY_DANGEROUS,
}

#: Tools whose ``path`` argument must stay inside the allowed roots
#: under ``workspace-write``.
_PATH_WRITE_TOOLS: frozenset[str] = frozenset({
    "write_file",
    "edit_file",
    "replace_in_file",
})

#: Argument keys, in preference order, that carry the target path.
_PATH_KEYS: tuple[str, ...] = ("path", "file_path", "filename", "target")


def parse_policy(value: str) -> str:
    """Normalise a policy spelling to one of :data:`POLICY_VALUES`.

    Args:
        value: A canonical name or a common alias (``readonly``,
            ``full``, …).

    Returns:
        The canonical policy string.

    Raises:
        ValueError: If *value* names no known policy.
    """
    key = str(value).strip().lower()
    resolved = _POLICY_ALIASES.get(key)
    if resolved is None:
        raise ValueError(
            f"unknown team policy {value!r}; expected one of "
            f"{', '.join(POLICY_VALUES)}"
        )
    return resolved


# ---------------------------------------------------------------------------
# In-process enforcement
# ---------------------------------------------------------------------------


class WorkspaceWrite(PermissionPolicy):
    """Allow reads and shell; allow writes only inside the allowed roots.

    This is the Chimera-side reading of a ``workspace-write`` sandbox:
    a write whose target escapes the workspace (or any extra root, such
    as the teams home the coordination server must write to) is denied.

    Scope, stated honestly: path confinement here inspects the *write
    tools' arguments*. Shell commands are allowed and are not
    path-checked — confining a subprocess is a sandbox's job, not an
    argument parser's, which is precisely why the policy is also
    translated into each runtime's own sandbox flags.

    Args:
        roots: Directories writes may target. Relative paths resolve
            against the first root.
    """

    #: Reads are allowed under every posture.
    READ_TOOLS: frozenset[str] = frozenset({
        "read_file",
        "search",
        "list_files",
        "repo_map",
        "import_graph",
    })

    def __init__(self, roots: Sequence[str | Path]) -> None:
        resolved = [Path(r).expanduser().resolve() for r in roots]
        if not resolved:
            raise ValueError("workspace-write needs at least one allowed root")
        self._roots = resolved

    @property
    def roots(self) -> list[Path]:
        """The directories writes may target."""
        return list(self._roots)

    def _target(self, args: Mapping[str, Any]) -> Path | None:
        for key in _PATH_KEYS:
            raw = args.get(key)
            if isinstance(raw, str) and raw.strip():
                candidate = Path(raw).expanduser()
                if not candidate.is_absolute():
                    candidate = self._roots[0] / candidate
                # ``resolve`` follows symlinks, so a link pointing out of
                # the workspace is caught rather than trusted.
                return candidate.resolve()
        return None

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        """Allow unless a write tool targets a path outside the roots."""
        if tool_name not in _PATH_WRITE_TOOLS:
            return PermissionAction.ALLOW
        target = self._target(args)
        if target is None:
            # A write tool with no readable path argument: refuse rather
            # than assume it lands somewhere harmless.
            return PermissionAction.DENY
        for root in self._roots:
            if target == root or root in target.parents:
                return PermissionAction.ALLOW
        return PermissionAction.DENY


def permission_policy_for(
    policy: str, *, roots: Sequence[str | Path] = (),
) -> PermissionPolicy:
    """Build the :class:`PermissionPolicy` a team policy stands for.

    Args:
        policy: A canonical policy (see :func:`parse_policy`).
        roots: Allowed write roots — required for
            ``workspace-write``, ignored otherwise.

    Returns:
        A fresh policy instance.

    Raises:
        ValueError: If *policy* is unknown, or ``workspace-write`` is
            requested with no roots.
    """
    from chimera.permissions.presets import AutoApprove, ReadOnly

    resolved = parse_policy(policy)
    if resolved == POLICY_READ_ONLY:
        return ReadOnly()
    if resolved == POLICY_WORKSPACE_WRITE:
        return WorkspaceWrite(roots)
    return AutoApprove()


def team_policy_interceptor(
    policy: str,
    *,
    team: "Team | None" = None,
    agent_id: str = "",
    roots: Sequence[str | Path] = (),
) -> "ToolCallInterceptor":
    """Build a ``tool_call`` interceptor that enforces a team policy.

    The interceptor runs before hooks and before the permission check
    (see :mod:`chimera.core.interception`), so the posture holds
    regardless of how the surrounding agent configured its own
    permissions — which is the whole point: a teammate must not be able
    to out-vote its lead.

    Denials are appended to the team's audit
    (:class:`~chimera.cli.agent_teams.TeamAudit`) so
    ``chimera team status`` reports them instead of the operator having
    to trust that nothing was blocked.

    Args:
        policy: A canonical policy (see :func:`parse_policy`).
        team: Team whose audit records denials. ``None`` enforces
            without recording.
        agent_id: Teammate id recorded on each audit entry.
        roots: Allowed write roots for ``workspace-write``.

    Returns:
        A callable matching
        :data:`~chimera.core.interception.ToolCallInterceptor`.

    Raises:
        ValueError: If *policy* is unknown.
    """
    from chimera.core.interception import InterceptDecision

    resolved = parse_policy(policy)
    permission = permission_policy_for(resolved, roots=roots)

    def _intercept(call: Any) -> "InterceptDecision | None":
        name = str(getattr(call, "name", ""))
        # Coordination is the substrate, never the work — see the module
        # docstring for why blocking it would be a footgun, not safety.
        if name.startswith(COORDINATION_TOOL_PREFIX):
            return None
        args = dict(getattr(call, "arguments", None) or {})
        action = permission.evaluate(name, args)
        if action is PermissionAction.ALLOW:
            return None
        reason = (
            f"team policy '{resolved}' does not allow {name!r} "
            f"(set by the team lead)"
        )
        if team is not None:
            try:
                from chimera.cli.agent_teams import TeamAudit

                TeamAudit(team).record(
                    agent_id=agent_id,
                    tool=name,
                    decision="denied",
                    reason=reason,
                    policy=resolved,
                )
            except Exception:  # noqa: BLE001 - audit must never break the loop
                pass
        return InterceptDecision.block(reason)

    return _intercept


def team_interceptors_from_env(
    workspace: str | Path | None = None,
) -> "Interceptors | None":
    """Build team-policy interceptors from the teammate's environment.

    The runner already propagates identity through the environment
    (``CHIMERA_TEAM`` / ``CHIMERA_AGENT``), so the posture travels the
    same way as ``CHIMERA_TEAM_POLICY``. A Chimera teammate therefore
    inherits its lead's policy with no per-invocation config edit.

    Args:
        workspace: Write root for ``workspace-write``. Defaults to the
            current working directory. The teams home is always added
            so coordination state stays writable.

    Returns:
        An :class:`~chimera.core.interception.Interceptors` chain, or
        ``None`` when no policy is configured — in which case behavior
        is unchanged.

    Raises:
        ValueError: If ``CHIMERA_TEAM_POLICY`` names an unknown policy.
            A posture the operator meant to apply must never be
            silently ignored.
    """
    raw = os.environ.get("CHIMERA_TEAM_POLICY", "").strip()
    if not raw:
        return None

    from chimera.cli.agent_teams import Team, teams_root
    from chimera.core.interception import Interceptors

    policy = parse_policy(raw)
    team_name = os.environ.get("CHIMERA_TEAM", "").strip()
    agent_id = os.environ.get("CHIMERA_AGENT", "").strip()
    team = Team(team_name) if team_name else None

    roots: list[Path] = [Path(workspace or os.getcwd()).expanduser().resolve()]
    # The coordination server writes here; a sandbox that forgets it turns
    # every team_* call into a mysterious failure.
    roots.append(teams_root().expanduser().resolve())

    return Interceptors(
        tool_call=[
            team_policy_interceptor(
                policy, team=team, agent_id=agent_id, roots=roots,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Per-runtime translation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeAdapter:
    """How one teammate runtime spells each team policy.

    Adapters are data, not code: the built-in table covers Chimera's own
    CLI, and the operator declares any other runtime under
    ``[team_runtimes.<name>]`` in ``~/.chimera/config.toml``::

        [team_runtimes.my-agent]
        read-only = "--sandbox read-only"
        workspace-write = "--sandbox write --add-dir {workspace} --add-dir {teams_home}"
        dangerous = "--no-sandbox"

    Attributes:
        name: Adapter name, matched against the first token of the
            runner's ``--cmd`` (or given explicitly).
        args: Policy → argv template tokens. ``{workspace}`` and
            ``{teams_home}`` are substituted at translation time.
        env: Policy → environment overlay for the spawned process.
    """

    name: str
    args: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    env: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @staticmethod
    def from_config(name: str, cfg: Mapping[str, Any]) -> "RuntimeAdapter":
        """Build an adapter from one ``[team_runtimes.<name>]`` table.

        Args:
            name: The table's adapter name.
            cfg: Its keys — one per policy, each a command-line fragment
                (string, split with :func:`shlex.split`, or a list of
                tokens). An optional ``env`` sub-table maps policy to an
                environment overlay.

        Returns:
            A validated adapter.

        Raises:
            ValueError: If a key names no known policy, or a value is
                neither a string nor a list of strings.
        """
        args: dict[str, tuple[str, ...]] = {}
        env: dict[str, Mapping[str, str]] = {}
        for key, value in cfg.items():
            if key == "env":
                if not isinstance(value, dict):
                    raise ValueError(
                        f"team runtime {name!r}: 'env' must be a table"
                    )
                for policy_key, overlay in value.items():
                    if not isinstance(overlay, dict):
                        raise ValueError(
                            f"team runtime {name!r}: env.{policy_key} must be a table"
                        )
                    env[parse_policy(policy_key)] = {
                        str(k): str(v) for k, v in overlay.items()
                    }
                continue
            policy = parse_policy(key)
            if isinstance(value, str):
                args[policy] = tuple(shlex.split(value))
            elif isinstance(value, (list, tuple)):
                args[policy] = tuple(str(v) for v in value)
            else:
                raise ValueError(
                    f"team runtime {name!r}: {key!r} must be a string or a "
                    f"list of strings, got {type(value).__name__}"
                )
        return RuntimeAdapter(name=name, args=args, env=env)


#: Adapters that ship in-tree. Only Chimera's own runtime is built in —
#: its policy needs no flags at all because a Chimera teammate enforces
#: the posture in-process from ``CHIMERA_TEAM_POLICY``
#: (:func:`team_interceptors_from_env`). Third-party runtimes are the
#: operator's own commands, so their flags are the operator's config.
BUILTIN_RUNTIME_ADAPTERS: dict[str, RuntimeAdapter] = {
    "chimera": RuntimeAdapter(name="chimera"),
}


def load_runtime_adapters() -> dict[str, RuntimeAdapter]:
    """Built-in adapters merged under the user's ``[team_runtimes]`` tables.

    Reads the same ``~/.chimera/config.toml`` chain as every Chimera CLI.
    Malformed tables are skipped here (config discovery must never crash
    startup); naming one gets the loud error from
    :func:`resolve_runtime_adapter`.

    Returns:
        Mapping of adapter name to adapter.
    """
    from chimera.cli.config_loader import load_config

    adapters = dict(BUILTIN_RUNTIME_ADAPTERS)
    table = load_config().get("team_runtimes")
    if isinstance(table, dict):
        for name, cfg in table.items():
            if not isinstance(cfg, dict):
                continue
            try:
                adapters[str(name)] = RuntimeAdapter.from_config(str(name), cfg)
            except ValueError:
                continue  # loud on resolve, silent on discovery
    return adapters


def detect_runtime(cmd_template: str) -> str:
    """Guess the runtime name from the first token of a command template.

    Args:
        cmd_template: The runner's ``--cmd`` value.

    Returns:
        The basename of the first token (``"/opt/bin/foo --x"`` →
        ``"foo"``), or ``""`` when the template is empty.
    """
    try:
        parts = shlex.split(cmd_template)
    except ValueError:
        parts = cmd_template.split()
    if not parts:
        return ""
    return Path(parts[0]).name


def resolve_runtime_adapter(name: str) -> RuntimeAdapter:
    """Resolve a runtime name to an adapter, loudly.

    Args:
        name: Adapter name (usually from :func:`detect_runtime`).

    Returns:
        The built-in or user-configured adapter.

    Raises:
        ValueError: If the name is unknown. Refusing here is deliberate:
            running a teammate at an unknown posture is the exact silent
            failure this module exists to remove.
    """
    adapters = load_runtime_adapters()
    adapter = adapters.get(name)
    if adapter is not None:
        return adapter
    raise ValueError(
        f"no policy translation for runtime {name!r}; known runtimes: "
        f"{sorted(adapters)}. Declare one with a [team_runtimes.{name or 'NAME'}] "
        f"table in ~/.chimera/config.toml, or drop --policy to leave this "
        f"teammate's permissions to its own configuration."
    )


@dataclass(frozen=True)
class PolicyTranslation:
    """A team policy rendered for one runtime.

    Attributes:
        policy: The canonical policy translated.
        runtime: The adapter name used.
        args: Argv tokens to splice into the spawn command.
        env: Environment overlay for the spawned process.
    """

    policy: str
    runtime: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)

    @property
    def args_string(self) -> str:
        """The argv tokens, shell-quoted and joined for ``{policy_args}``."""
        return " ".join(shlex.quote(a) for a in self.args)


def translate_policy(
    policy: str,
    runtime: str,
    *,
    workspace: str | Path | None = None,
    teams_home: str | Path | None = None,
) -> PolicyTranslation:
    """Render *policy* into the flags and env *runtime* understands.

    Args:
        policy: A canonical policy (see :func:`parse_policy`).
        runtime: Adapter name (see :func:`resolve_runtime_adapter`).
        workspace: Substituted for ``{workspace}`` in the adapter's
            templates. Defaults to the current working directory.
        teams_home: Substituted for ``{teams_home}``. Defaults to
            :func:`~chimera.cli.agent_teams.teams_root`.

    Returns:
        The rendered translation.

    Raises:
        ValueError: If the policy or the runtime is unknown.
    """
    from chimera.cli.agent_teams import teams_root

    resolved = parse_policy(policy)
    adapter = resolve_runtime_adapter(runtime)
    substitutions = {
        "workspace": str(Path(workspace or os.getcwd()).expanduser().resolve()),
        "teams_home": str(Path(teams_home or teams_root()).expanduser().resolve()),
    }
    args = tuple(
        token.format(**substitutions) for token in adapter.args.get(resolved, ())
    )
    env = dict(adapter.env.get(resolved, {}))
    return PolicyTranslation(
        policy=resolved, runtime=adapter.name, args=args, env=env,
    )


def apply_policy_args(cmd_template: str, translation: PolicyTranslation) -> str:
    """Splice a translation's flags into a command template.

    Args:
        cmd_template: The runner's ``--cmd`` value, which may contain a
            ``{policy_args}`` placeholder.
        translation: The rendered policy.

    Returns:
        The command with ``{policy_args}`` replaced. When the template
        has no placeholder the command is returned unchanged — the
        caller is expected to warn rather than guess where flags belong
        in someone else's command line.
    """
    if "{policy_args}" not in cmd_template:
        return cmd_template
    return cmd_template.replace("{policy_args}", translation.args_string)
