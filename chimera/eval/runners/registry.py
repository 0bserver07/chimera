"""AgentSpec + declarative registry — the control surface of the matrix.

One registry enumerates every agent — Chimera-internal or external — behind a
single :class:`AgentSpec`, so ``--agents a,b,c`` resolves uniformly into
:class:`~chimera.eval.runners.base.AgentRunner` instances. This is the "control
variables" enabler of the agent × benchmark matrix: internal loops, ACP
subprocesses, templated CLIs, and native SWE-bench harnesses all enter the grid
through the same spec. See ``docs/specs/agent-benchmark-matrix.md``.

Registry files are **JSON** (the core is zero-dependency — no YAML). Each file
is a list of :meth:`AgentSpec.to_dict` objects; users extend the built-in
roster by pointing :func:`load_registry` at JSON files whose entries override
the built-ins (and each other) by ``id``. The spec's illustrative
``matrix.yaml`` sketch maps onto these JSON entries field-for-field.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.eval.runners.base import AgentRunner

#: Valid :attr:`AgentSpec.kind` values (mapped to a runner by :func:`resolve`).
VALID_KINDS: tuple[str, ...] = ("in-process", "acp", "cli-template", "native-harness")

#: Loop-type map reused verbatim from ``chimera/cli/bench_compare.py`` so the
#: internal roster stays a single source of truth. Values are ``"module:Class"``.
_LOOP_PATHS: dict[str, str] = {
    "react": "chimera.core.loop:ReAct",
    "plan-execute": "chimera.core.loops.plan_execute:PlanAndExecute",
    "reflexion": "chimera.core.loops.reflexion:Reflexion",
    "tree-of-thought": "chimera.core.loops.tree_of_thought:TreeOfThought",
}

#: Back-compat roster-id aliases (former brand name -> canonical loop-descriptive
#: id). :func:`load_registry` maps each alias onto its canonical spec so
#: ``--agents aider`` (etc.) keeps resolving after the rename. An explicit JSON
#: entry for an alias id always wins over the alias.
_ID_ALIASES: dict[str, str] = {
    "swe-agent": "retry-min",
    "aider": "lint-loop",
    "cline": "plan-act",
    "codex": "full-tools",
    "kimi": "action-first",
}


@dataclass
class AgentSpec:
    """One agent's declarative entry in the matrix registry.

    A spec is intentionally a flat, JSON-serialisable record: :func:`resolve`
    turns it into a live runner. Only the fields relevant to a given
    :attr:`kind` are populated; the rest stay ``None``/empty.

    Attributes:
        id: Row label in the matrix; unique within a registry.
        kind: One of :data:`VALID_KINDS` — ``in-process`` (a Chimera agent),
            ``acp`` (an Agent Client Protocol subprocess), ``cli-template``
            (a templated CLI invocation), or ``native-harness`` (a framework's
            own SWE-bench harness whose predictions are graded post-hoc).
        factory: ``"module:callable"`` for ``in-process`` — an
            ``agent_factory(provider) -> agent`` producing an object that
            exposes ``run(prompt, env) -> AgentResult``.
        command: Argv list for ``acp`` (e.g. ``["opencode", "acp"]``).
        cmd: Command template for ``cli-template`` (placeholders such as
            ``{prompt_file}`` / ``{repo}`` / ``{patch_out}``).
        harness_cmd: Command that runs a framework's native harness
            (``native-harness``).
        predictions_glob: Glob locating that harness's ``predictions.jsonl``
            (``native-harness``).
        sandbox: Optional sandbox class name (e.g. ``"docker"``) — a controlled
            variable applied uniformly across a matrix run.
        model: Optional model override for this agent.
        options: Runner-specific extras forwarded to the runner constructor.
    """

    id: str
    kind: str
    factory: str | None = None
    command: list[str] | None = None
    cmd: str | None = None
    harness_cmd: str | None = None
    predictions_glob: str | None = None
    sandbox: str | None = None
    model: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict, omitting unset fields.

        ``id`` and ``kind`` are always present; every other field appears only
        when non-``None`` (and ``options`` only when non-empty), so the output
        is minimal yet round-trippable via :meth:`from_dict`.

        Returns:
            A dict suitable for :func:`json.dumps`.
        """
        out: dict[str, Any] = {"id": self.id, "kind": self.kind}
        if self.factory is not None:
            out["factory"] = self.factory
        if self.command is not None:
            out["command"] = list(self.command)
        if self.cmd is not None:
            out["cmd"] = self.cmd
        if self.harness_cmd is not None:
            out["harness_cmd"] = self.harness_cmd
        if self.predictions_glob is not None:
            out["predictions_glob"] = self.predictions_glob
        if self.sandbox is not None:
            out["sandbox"] = self.sandbox
        if self.model is not None:
            out["model"] = self.model
        if self.options:
            out["options"] = dict(self.options)
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentSpec:
        """Build an :class:`AgentSpec` from a (possibly partial) dict.

        Args:
            d: A mapping as produced by :meth:`to_dict`. Missing optional keys
                fall back to their dataclass defaults.

        Returns:
            The reconstructed :class:`AgentSpec`.

        Raises:
            KeyError: If ``id`` is absent.
        """
        return cls(
            id=d["id"],
            kind=d.get("kind", "in-process"),
            factory=d.get("factory"),
            command=list(d["command"]) if d.get("command") is not None else None,
            cmd=d.get("cmd"),
            harness_cmd=d.get("harness_cmd"),
            predictions_glob=d.get("predictions_glob"),
            sandbox=d.get("sandbox"),
            model=d.get("model"),
            options=dict(d.get("options") or {}),
        )


# --------------------------------------------------------------------------- #
# Built-in in-process factory helpers.
#
# Each is a real, importable ``agent_factory(provider) -> agent`` (the contract
# InProcessRunner expects). Loop helpers reuse the real loop classes named in
# :data:`_LOOP_PATHS`; preset helpers reference the real assembly presets by
# name through ``CodingAgentAdapter``. They are referenced from
# ``AgentSpec.factory`` as ``chimera.eval.runners.registry:<name>``.
#
# WHY module-level named functions (not the bare loop classes): a loop class is
# not itself an ``agent_factory`` — it has no ``run(prompt, env)``. Resolving
# ``factory`` to one of these wrappers keeps :func:`resolve` a dumb importer
# while still yielding a live, runnable Agent.
# --------------------------------------------------------------------------- #
def _build_loop_agent(provider: Any, loop_path: str) -> Any:
    """Assemble a default-tooled Agent around the loop named by *loop_path*.

    Args:
        provider: LLM provider handed to the Agent.
        loop_path: ``"module:Class"`` reference to a reasoning loop.

    Returns:
        A :class:`~chimera.core.agent.Agent` wrapping a fresh loop instance.
    """
    mod_path, cls_name = loop_path.rsplit(":", 1)
    loop_cls = getattr(importlib.import_module(mod_path), cls_name)
    from chimera.core.agent import Agent
    from chimera.core.tool_group import DEFAULT_TOOLS

    return Agent(provider=provider, tools=list(DEFAULT_TOOLS), loop=loop_cls())


def react_agent(provider: Any) -> Any:
    """Factory: a ReAct-loop Agent with the default tool set."""
    return _build_loop_agent(provider, _LOOP_PATHS["react"])


def plan_execute_agent(provider: Any) -> Any:
    """Factory: a Plan-and-Execute Agent with the default tool set."""
    return _build_loop_agent(provider, _LOOP_PATHS["plan-execute"])


def reflexion_agent(provider: Any) -> Any:
    """Factory: a Reflexion Agent with the default tool set."""
    return _build_loop_agent(provider, _LOOP_PATHS["reflexion"])


def tree_of_thought_agent(provider: Any) -> Any:
    """Factory: a Tree-of-Thought Agent with the default tool set."""
    return _build_loop_agent(provider, _LOOP_PATHS["tree-of-thought"])


def _build_preset_agent(provider: Any, preset: str) -> Any:
    """Build a ``CodingAgentAdapter`` for the named assembly preset.

    Args:
        provider: LLM provider handed to the adapter.
        preset: A key of ``chimera.assembly.presets.PRESETS``.

    Returns:
        A :class:`~chimera.eval.coding_agent_adapter.CodingAgentAdapter`.

    Raises:
        ValueError: If *preset* is not a known assembly preset.
    """
    from chimera.assembly.presets import PRESETS
    from chimera.eval.coding_agent_adapter import CodingAgentAdapter

    if preset not in PRESETS:
        raise ValueError(f"unknown assembly preset {preset!r}")
    return CodingAgentAdapter(provider, preset=preset)


def coding_agent_preset_agent(provider: Any) -> Any:
    """Factory: the assembly ``coding_agent`` preset as an eval agent.

    This is the ``chimera code`` daily-driver stack itself — the flagship
    assembled CodingAgent — so the matrix can measure the same agent users run.
    """
    return _build_preset_agent(provider, "coding_agent")


def codex_preset_agent(provider: Any) -> Any:
    """Factory: the assembly ``codex`` preset as an eval agent."""
    return _build_preset_agent(provider, "codex")


def kimi_preset_agent(provider: Any) -> Any:
    """Factory: the assembly ``kimi`` preset as an eval agent."""
    return _build_preset_agent(provider, "kimi")


def minimal_preset_agent(provider: Any) -> Any:
    """Factory: the assembly ``minimal`` preset as an eval agent."""
    return _build_preset_agent(provider, "minimal")


def explore_preset_agent(provider: Any) -> Any:
    """Factory: the assembly ``explore`` (read-only) preset as an eval agent."""
    return _build_preset_agent(provider, "explore")


def swebench_preset_agent(provider: Any) -> Any:
    """Factory: the assembly ``swebench`` preset as an eval agent."""
    return _build_preset_agent(provider, "swebench")


# --------------------------------------------------------------------------- #
# Built-in loop-style factory helpers.
#
# Each composes a runnable ``chimera.core.agent.Agent`` from a named
# :class:`~chimera.agents.presets.agent_styles.AgentPreset` — the in-tree
# loop-posture styles (retry-minimal / lint-feedback / plan-act). Their names
# are loop-descriptive; the former brand-named ids (``swe-agent`` / ``aider`` /
# ``cline``) survive as back-compat aliases in :func:`load_registry`.
# ``AgentPreset._compose`` is the preset's documented in-tree build hatch: it
# wires the style's tools, loop, and system prompt into a live Agent that
# already satisfies the ``run(prompt, env) -> AgentResult`` factory contract,
# exactly like the loop helpers above. The react-full style is deliberately not
# re-exposed here — the assembly ``codex`` preset already occupies that slot.
# --------------------------------------------------------------------------- #
def _build_style_agent(provider: Any, preset_name: str) -> Any:
    """Compose a runnable Agent from a named :class:`AgentPreset` style.

    Args:
        provider: LLM provider handed to the composed Agent.
        preset_name: Attribute name on
            :class:`~chimera.agents.presets.agent_styles.AgentPreset`
            (e.g. ``"RETRY_MIN"``).

    Returns:
        A :class:`~chimera.core.agent.Agent` wired with the style's tools,
        loop, and system prompt.
    """
    from chimera.agents.presets.agent_styles import AgentPreset

    preset = getattr(AgentPreset, preset_name)
    return preset._compose(provider)


def retry_min_style_agent(provider: Any) -> Any:
    """Factory: the retry-minimal style (retry loop, minimal tools)."""
    return _build_style_agent(provider, "RETRY_MIN")


def lint_loop_style_agent(provider: Any) -> Any:
    """Factory: the lint-feedback style (lint-feedback loop, git-aware tools)."""
    return _build_style_agent(provider, "LINT_LOOP")


def plan_act_style_agent(provider: Any) -> Any:
    """Factory: the plan-act style (plan/act dual-mode loop, full tools)."""
    return _build_style_agent(provider, "PLAN_ACT")


def default_agent_specs() -> list[AgentSpec]:
    """Return the representative built-in roster (all ``in-process``).

    Thirteen ids spanning three internal axes:

    - Four **loop postures** (``react`` / ``plan-execute`` / ``reflexion`` /
      ``tree-of-thought``) reusing the bench-compare loop map.
    - Six **assembly presets** — the ``chimera code`` flagship ``coding-agent``
      plus ``full-tools``, ``action-first``, ``minimal``, ``explore``, and
      ``swebench`` — driven through ``CodingAgentAdapter``. (``full-tools`` and
      ``action-first`` are backed by the assembly ``codex`` / ``kimi`` presets;
      the roster id is loop-descriptive, the preset key is unchanged.)
    - Three **loop styles** (``retry-min``, ``lint-loop``, ``plan-act``)
      composed from :class:`~chimera.agents.presets.agent_styles.AgentPreset`.

    The former brand-named ids (``swe-agent`` / ``aider`` / ``cline`` /
    ``codex`` / ``kimi``) resolve through :func:`load_registry` as back-compat
    aliases of their canonical entries above.

    The roster stays deliberately *representative*, not exhaustive — the spec's
    full internal axis (the 7 codename CLIs and any further presets/styles) is
    still extended by shipping additional JSON registry files that
    :func:`load_registry` merges on top of these built-ins.

    Returns:
        A fresh list of :class:`AgentSpec` (safe for the caller to mutate).
    """
    base = "chimera.eval.runners.registry"
    return [
        AgentSpec(id="react", kind="in-process", factory=f"{base}:react_agent"),
        AgentSpec(id="plan-execute", kind="in-process", factory=f"{base}:plan_execute_agent"),
        AgentSpec(id="reflexion", kind="in-process", factory=f"{base}:reflexion_agent"),
        AgentSpec(
            id="tree-of-thought",
            kind="in-process",
            factory=f"{base}:tree_of_thought_agent",
        ),
        AgentSpec(
            id="coding-agent",
            kind="in-process",
            factory=f"{base}:coding_agent_preset_agent",
        ),
        AgentSpec(id="full-tools", kind="in-process", factory=f"{base}:codex_preset_agent"),
        AgentSpec(id="action-first", kind="in-process", factory=f"{base}:kimi_preset_agent"),
        AgentSpec(id="minimal", kind="in-process", factory=f"{base}:minimal_preset_agent"),
        AgentSpec(id="explore", kind="in-process", factory=f"{base}:explore_preset_agent"),
        AgentSpec(id="swebench", kind="in-process", factory=f"{base}:swebench_preset_agent"),
        AgentSpec(id="retry-min", kind="in-process", factory=f"{base}:retry_min_style_agent"),
        AgentSpec(id="lint-loop", kind="in-process", factory=f"{base}:lint_loop_style_agent"),
        AgentSpec(id="plan-act", kind="in-process", factory=f"{base}:plan_act_style_agent"),
    ]


def load_registry(paths: list[str] | None = None) -> dict[str, AgentSpec]:
    """Build the ``id -> AgentSpec`` registry: built-ins then JSON overrides.

    Starts from :func:`default_agent_specs`, then merges each JSON file in order
    so later files override earlier ones (and the built-ins) by ``id``. Each
    file must contain a JSON list of :class:`AgentSpec` dicts.

    Finally, the back-compat :data:`_ID_ALIASES` are applied: each former
    brand-named id (``swe-agent`` / ``aider`` / ``cline`` / ``codex`` /
    ``kimi``) resolves to the same spec object as its canonical entry, so
    ``--agents aider`` keeps working after the rename. An alias is only added
    when that id is not already present (an explicit JSON entry for the id wins).

    Args:
        paths: Ordered registry file paths. ``None`` yields the built-ins
            alone. Files that do not exist are skipped quietly (project- and
            user-level registries are optional, like ``AgentLoader``).

    Returns:
        Mapping of agent ``id`` to its resolved :class:`AgentSpec`.

    Raises:
        json.JSONDecodeError: If a present file is not valid JSON.
    """
    registry: dict[str, AgentSpec] = {spec.id: spec for spec in default_agent_specs()}
    for path in paths or []:
        p = Path(path)
        if not p.exists():
            continue
        entries = json.loads(p.read_text(encoding="utf-8"))
        for entry in entries:
            spec = AgentSpec.from_dict(entry)
            registry[spec.id] = spec
    # Back-compat: alias the former brand-named ids onto their canonical specs,
    # without clobbering any explicit entry a JSON override already supplied.
    for alias, canonical in _ID_ALIASES.items():
        if alias not in registry and canonical in registry:
            registry[alias] = registry[canonical]
    return registry


def _import_callable(ref: str) -> Any:
    """Import a ``"module.path:callable"`` reference.

    Args:
        ref: A ``"module:attr"`` string.

    Returns:
        The referenced object.

    Raises:
        ValueError: If *ref* is not of the form ``module:callable``.
    """
    if ":" not in ref:
        raise ValueError(f"factory {ref!r} must be of the form 'module.path:callable'")
    mod_path, attr = ref.rsplit(":", 1)
    return getattr(importlib.import_module(mod_path), attr)


def _external_runner_cls(module_path: str, cls_name: str, label: str) -> Any:
    """Lazily import an external runner class, or fail with a clear message.

    The ACP / CLI-template / native-harness runner modules are built in later
    phases of the matrix; this keeps :func:`resolve` importable today while
    turning an absent runner into an actionable error rather than a raw
    ``ImportError``.

    Args:
        module_path: Dotted module holding the runner class.
        cls_name: The runner class name.
        label: Human-readable runner name for the error message.

    Returns:
        The runner class.

    Raises:
        RuntimeError: If the module cannot be imported or lacks the class.
    """
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise RuntimeError(
            f"{label} is unavailable — '{module_path}' could not be imported "
            f"({exc}); it is provided by a later phase of the "
            "agent × benchmark matrix."
        ) from exc
    try:
        return getattr(module, cls_name)
    except AttributeError as exc:
        raise RuntimeError(
            f"{label} module '{module_path}' is missing class '{cls_name}'."
        ) from exc


def resolve(spec: AgentSpec, provider: Any = None) -> AgentRunner:
    """Instantiate the :class:`AgentRunner` for *spec*.

    ``in-process`` specs import :attr:`AgentSpec.factory` and wrap it in an
    :class:`~chimera.eval.runners.in_process.InProcessRunner`. External kinds
    lazily import their runner class and construct it from the spec fields; an
    absent runner module raises a clear :class:`RuntimeError` naming what is
    missing.

    Field mapping for external kinds (their constructors do not take model or
    sandbox — those are *matrix-level* controlled variables: ``sandbox`` is
    applied via the run's ``env_factory`` and an external agent uses its own
    model/keys):

    - ``acp``: ``command`` builds an
      :class:`~chimera.acp.types.ACPSessionConfig`; ``options`` forward to
      :class:`~chimera.eval.runners.acp.ACPRunner` (e.g. ``client_factory``).
    - ``cli-template``: ``cmd`` is the required template; ``options`` forward
      to :class:`~chimera.eval.runners.cli_template.CliTemplateRunner`
      (``patch_from`` / ``timeout`` / ``cwd``).
    - ``native-harness``: ``harness_cmd`` + ``predictions_glob`` are required;
      ``options`` forward to
      :class:`~chimera.eval.runners.native_harness.NativeHarnessRunner`
      (``timeout``).

    Args:
        spec: The declarative agent entry.
        provider: Provider passed to in-process agent factories (ignored by
            external runners, which manage their own model access).

    Returns:
        A ready :class:`~chimera.eval.runners.base.AgentRunner`.

    Raises:
        ValueError: If :attr:`AgentSpec.kind` is unknown, an ``in-process``
            spec has no ``factory``, or an external spec is missing a field its
            runner requires (``cmd`` / ``harness_cmd`` / ``predictions_glob``).
        RuntimeError: If an external runner module is not importable.
    """
    if spec.kind == "in-process":
        if not spec.factory:
            raise ValueError(f"in-process agent {spec.id!r} needs a 'factory'")
        factory = _import_callable(spec.factory)
        from chimera.eval.runners.in_process import InProcessRunner

        return InProcessRunner(spec.id, agent_factory=factory, provider=provider)

    if spec.kind == "acp":
        cls = _external_runner_cls("chimera.eval.runners.acp", "ACPRunner", "the ACP runner")
        from chimera.acp.types import ACPSessionConfig

        config = ACPSessionConfig(command=list(spec.command or []))
        return cls(spec.id, config, **spec.options)  # type: ignore[no-any-return]

    if spec.kind == "cli-template":
        if not spec.cmd:
            raise ValueError(f"cli-template agent {spec.id!r} needs a 'cmd'")
        cls = _external_runner_cls(
            "chimera.eval.runners.cli_template", "CliTemplateRunner", "the CLI-template runner"
        )
        return cls(spec.id, spec.cmd, **spec.options)  # type: ignore[no-any-return]

    if spec.kind == "native-harness":
        if not spec.harness_cmd or not spec.predictions_glob:
            raise ValueError(
                f"native-harness agent {spec.id!r} needs 'harness_cmd' and 'predictions_glob'"
            )
        cls = _external_runner_cls(
            "chimera.eval.runners.native_harness",
            "NativeHarnessRunner",
            "the native-harness runner",
        )
        return cls(  # type: ignore[no-any-return]
            spec.id, spec.harness_cmd, spec.predictions_glob, **spec.options
        )

    raise ValueError(
        f"unknown agent kind {spec.kind!r} for {spec.id!r} "
        f"(valid: {', '.join(VALID_KINDS)})"
    )
