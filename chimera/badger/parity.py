"""``chimera badger parity`` — runtime parity-schema check.

Implements the upstream's PARITY.md pattern as a runtime check: declare
a schema (tool list, max-step budget, allowed slash commands, default
model, optional flags), then diff the live agent's behaviour against it.

Schema format:
    Either JSON or a tiny YAML-ish flat dict (we parse both with stdlib
    only). The recognised keys are:

    * ``tools`` — list[str] of tool names that must be present
    * ``max_steps`` — int, the expected default budget
    * ``slash_commands`` — list[str] of slash command names that must
      be available in the badger REPL
    * ``model`` — str, expected default model id (when not overridden
      by env)
    * ``rerun_on_failure`` — bool, expected default for ``--rerun-on-failure``

Usage:
    ``chimera badger parity --against PARITY.md`` — returns 0 when the
    live agent matches; 1 with a diff report otherwise; 2 on usage
    error (missing schema, parse error, etc.).

Trademark hygiene: this module references the upstream's PARITY.md
pattern as a *technique*, not a brand. The schema file itself can be
called anything the operator wants.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "ParitySchema",
    "ParityReport",
    "load_schema",
    "build_live_snapshot",
    "diff_schema",
    "format_report",
    "run_parity_check",
]


@dataclass
class ParitySchema:
    """Declarative target schema for ``chimera badger parity``.

    Attributes:
        tools: Tool names that must be present in the live agent.
        max_steps: Expected default ``--max-steps`` value.
        slash_commands: Slash-command names the badger REPL must expose.
        model: Expected default model id (when no env override is set).
        rerun_on_failure: Expected default for the flag.
        extras: Any additional keys the operator declared (used only
            for round-tripping; not diffed).
    """

    tools: list[str] = field(default_factory=list)
    max_steps: int | None = None
    slash_commands: list[str] = field(default_factory=list)
    model: str | None = None
    rerun_on_failure: bool | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Render as a JSON-serializable dict."""
        out: dict[str, Any] = {}
        if self.tools:
            out["tools"] = list(self.tools)
        if self.max_steps is not None:
            out["max_steps"] = self.max_steps
        if self.slash_commands:
            out["slash_commands"] = list(self.slash_commands)
        if self.model is not None:
            out["model"] = self.model
        if self.rerun_on_failure is not None:
            out["rerun_on_failure"] = self.rerun_on_failure
        out.update(self.extras)
        return out


@dataclass
class ParityReport:
    """Diff result from comparing a live snapshot against a schema.

    Attributes:
        missing_tools: Tools in the schema but absent from the live agent.
        extra_tools: Tools live but not declared (informational; never
            fails the check on its own).
        missing_slash: Slash commands in the schema but absent live.
        extra_slash: Slash commands live but not declared (informational).
        max_steps_mismatch: Pair of (expected, actual) when they differ.
        model_mismatch: Pair of (expected, actual) when they differ.
        rerun_mismatch: Pair of (expected, actual) when they differ.
        ok: Convenience: True when no load-bearing diffs were found.
    """

    missing_tools: list[str] = field(default_factory=list)
    extra_tools: list[str] = field(default_factory=list)
    missing_slash: list[str] = field(default_factory=list)
    extra_slash: list[str] = field(default_factory=list)
    max_steps_mismatch: tuple[int, int] | None = None
    model_mismatch: tuple[str, str] | None = None
    rerun_mismatch: tuple[bool, bool] | None = None

    @property
    def ok(self) -> bool:
        """True when the live snapshot matches the schema's required fields."""
        return not (
            self.missing_tools
            or self.missing_slash
            or self.max_steps_mismatch
            or self.model_mismatch
            or self.rerun_mismatch
        )

    def to_dict(self) -> dict[str, Any]:
        """Render as a JSON-serializable dict for ``--json`` output."""
        return {
            "ok": self.ok,
            "missing_tools": list(self.missing_tools),
            "extra_tools": list(self.extra_tools),
            "missing_slash": list(self.missing_slash),
            "extra_slash": list(self.extra_slash),
            "max_steps_mismatch": list(self.max_steps_mismatch)
            if self.max_steps_mismatch is not None
            else None,
            "model_mismatch": list(self.model_mismatch)
            if self.model_mismatch is not None
            else None,
            "rerun_mismatch": list(self.rerun_mismatch)
            if self.rerun_mismatch is not None
            else None,
        }


# ---------------------------------------------------------------------------
# Schema loading — JSON, YAML, or PARITY.md fenced code-block.
# ---------------------------------------------------------------------------


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Tiny YAML-ish parser for ``key: value`` and ``- item`` lists.

    Handles the subset we ship as schema examples: top-level scalar
    keys (``model: foo``, ``max_steps: 25``, ``rerun_on_failure: true``)
    plus list-valued keys with ``- `` items. No anchors, no nested maps,
    no flow style. Anything more complex should be expressed as JSON.

    Args:
        text: The raw YAML-ish source.

    Returns:
        A flat dict suitable for :func:`_dict_to_schema`.
    """
    out: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current_list_key is None:
                continue
            value = stripped[2:].strip().strip("'\"")
            existing = out.setdefault(current_list_key, [])
            if isinstance(existing, list):
                existing.append(value)
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not val:
            current_list_key = key
            out.setdefault(key, [])
            continue
        # scalar
        current_list_key = None
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            items = [
                item.strip().strip("'\"")
                for item in inner.split(",")
                if item.strip()
            ]
            out[key] = items
            continue
        if val.lower() in ("true", "false"):
            out[key] = val.lower() == "true"
            continue
        try:
            out[key] = int(val)
            continue
        except ValueError:
            pass
        out[key] = val.strip("'\"")
    return out


def _extract_fenced_block(text: str) -> str | None:
    """Pull the first fenced code-block out of a PARITY.md-style document."""
    lines = text.splitlines()
    in_block = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_block:
                return "\n".join(out)
            in_block = True
            continue
        if in_block:
            out.append(line)
    return None


def _dict_to_schema(data: dict[str, Any]) -> ParitySchema:
    """Build a :class:`ParitySchema` from a parsed dict."""
    tools_raw = data.get("tools") or []
    if not isinstance(tools_raw, list):
        tools_raw = []
    slash_raw = data.get("slash_commands") or []
    if not isinstance(slash_raw, list):
        slash_raw = []
    extras = {
        k: v for k, v in data.items()
        if k not in {"tools", "max_steps", "slash_commands", "model", "rerun_on_failure"}
    }
    max_steps = data.get("max_steps")
    if max_steps is not None:
        try:
            max_steps = int(max_steps)
        except (TypeError, ValueError):
            max_steps = None
    rerun = data.get("rerun_on_failure")
    if rerun is not None and not isinstance(rerun, bool):
        rerun = bool(rerun)
    model = data.get("model")
    if model is not None:
        model = str(model)
    return ParitySchema(
        tools=[str(t) for t in tools_raw],
        max_steps=max_steps,
        slash_commands=[str(s) for s in slash_raw],
        model=model,
        rerun_on_failure=rerun,
        extras=extras,
    )


def load_schema(path: Path) -> ParitySchema:
    """Load a parity schema from JSON, YAML, or a PARITY.md fenced block.

    Args:
        path: Path to the schema file. Suffix selects the parser:
            ``.json`` -> JSON; ``.yaml``/``.yml`` -> YAML-ish; anything
            else -> try fenced code block, then JSON, then YAML-ish.

    Returns:
        A :class:`ParitySchema` instance.

    Raises:
        FileNotFoundError: When ``path`` does not exist.
        ValueError: When the file cannot be parsed in any supported form.
    """
    if not path.exists():
        raise FileNotFoundError(f"parity schema not found: {path}")
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()

    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"parity schema must be a JSON object: {path}")
        return _dict_to_schema(data)

    if suffix in (".yaml", ".yml"):
        return _dict_to_schema(_parse_simple_yaml(text))

    # Mixed: try fenced first, then JSON, then YAML-ish.
    fenced = _extract_fenced_block(text)
    if fenced:
        try:
            data = json.loads(fenced)
            if isinstance(data, dict):
                return _dict_to_schema(data)
        except json.JSONDecodeError:
            pass
        return _dict_to_schema(_parse_simple_yaml(fenced))

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return _dict_to_schema(data)
    except json.JSONDecodeError:
        pass
    return _dict_to_schema(_parse_simple_yaml(text))


# ---------------------------------------------------------------------------
# Live snapshot
# ---------------------------------------------------------------------------


def build_live_snapshot(
    *,
    tools: list[Any] | None = None,
    slash_commands: list[str] | None = None,
    max_steps: int | None = None,
    model: str | None = None,
    rerun_on_failure: bool | None = None,
) -> ParitySchema:
    """Build a :class:`ParitySchema` describing the live badger agent.

    All arguments are optional injection points so tests can build a
    deterministic snapshot. When omitted, we read the canonical defaults:

    * ``tools`` -> :data:`chimera.core.tool_group.AGENT_TOOLS`
    * ``slash_commands`` -> :data:`chimera.badger.slash.BADGER_SLASH_COMMANDS`
    * ``max_steps`` -> :data:`chimera.badger.cli._DEFAULT_MAX_STEPS`
    * ``model`` -> :data:`chimera.badger.cli._DEFAULT_MODEL`
    * ``rerun_on_failure`` -> ``False`` (the CLI default)

    Args:
        tools: Override tool list.
        slash_commands: Override slash command list.
        max_steps: Override max-step budget.
        model: Override default model.
        rerun_on_failure: Override default flag value.

    Returns:
        A :class:`ParitySchema` mirroring the live defaults.
    """
    if tools is None:
        try:
            from chimera.core.tool_group import AGENT_TOOLS

            tools = list(AGENT_TOOLS)
        except Exception:  # noqa: BLE001
            tools = []
    if slash_commands is None:
        try:
            from chimera.badger.slash import BADGER_SLASH_COMMANDS

            slash_commands = sorted(BADGER_SLASH_COMMANDS.keys())
        except Exception:  # noqa: BLE001
            slash_commands = []
    if max_steps is None:
        try:
            from chimera.badger.cli import _DEFAULT_MAX_STEPS as _ms

            max_steps = _ms
        except Exception:  # noqa: BLE001
            max_steps = 25
    if model is None:
        try:
            from chimera.badger.cli import _DEFAULT_MODEL as _m

            model = _m
        except Exception:  # noqa: BLE001
            model = "claude-sonnet-4-6"
    if rerun_on_failure is None:
        rerun_on_failure = False

    tool_names = [getattr(t, "name", str(t)) for t in tools]
    return ParitySchema(
        tools=sorted(tool_names),
        max_steps=max_steps,
        slash_commands=sorted(slash_commands),
        model=model,
        rerun_on_failure=rerun_on_failure,
    )


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def diff_schema(expected: ParitySchema, live: ParitySchema) -> ParityReport:
    """Compute a :class:`ParityReport` from the expected vs live schemas.

    The diff is **asymmetric**: only fields declared on *expected* are
    enforced. Live extras are reported informationally but never fail
    the check. This mirrors the upstream's PARITY.md pattern, where the
    target document declares the contract and live deviations are
    surfaced as schema-extension candidates.

    Args:
        expected: The declared schema.
        live: The live-snapshot schema.

    Returns:
        A populated :class:`ParityReport`.
    """
    report = ParityReport()
    if expected.tools:
        live_set = set(live.tools)
        report.missing_tools = sorted(set(expected.tools) - live_set)
        report.extra_tools = sorted(live_set - set(expected.tools))
    if expected.slash_commands:
        live_set = set(live.slash_commands)
        report.missing_slash = sorted(set(expected.slash_commands) - live_set)
        report.extra_slash = sorted(live_set - set(expected.slash_commands))
    if expected.max_steps is not None and live.max_steps is not None:
        if expected.max_steps != live.max_steps:
            report.max_steps_mismatch = (expected.max_steps, live.max_steps)
    if expected.model is not None and live.model is not None:
        if expected.model != live.model:
            report.model_mismatch = (expected.model, live.model)
    if (
        expected.rerun_on_failure is not None
        and live.rerun_on_failure is not None
    ):
        if expected.rerun_on_failure != live.rerun_on_failure:
            report.rerun_mismatch = (
                expected.rerun_on_failure,
                live.rerun_on_failure,
            )
    return report


def format_report(report: ParityReport) -> str:
    """Render a :class:`ParityReport` as printable text."""
    lines: list[str] = []
    if report.ok:
        lines.append("badger parity: OK (live agent matches schema)")
        return "\n".join(lines)
    lines.append("badger parity: FAIL")
    if report.missing_tools:
        lines.append(
            f"  missing tools ({len(report.missing_tools)}): "
            + ", ".join(report.missing_tools)
        )
    if report.missing_slash:
        lines.append(
            f"  missing slash commands ({len(report.missing_slash)}): "
            + ", ".join(report.missing_slash)
        )
    if report.max_steps_mismatch is not None:
        exp, act = report.max_steps_mismatch
        lines.append(f"  max_steps mismatch: expected={exp} actual={act}")
    if report.model_mismatch is not None:
        exp_s, act_s = report.model_mismatch
        lines.append(f"  model mismatch: expected={exp_s!r} actual={act_s!r}")
    if report.rerun_mismatch is not None:
        exp_b, act_b = report.rerun_mismatch
        lines.append(
            f"  rerun_on_failure mismatch: expected={exp_b} actual={act_b}"
        )
    if report.extra_tools:
        suffix = "..." if len(report.extra_tools) > 8 else ""
        lines.append(
            "  (informational) live-only tools: "
            + ", ".join(report.extra_tools[:8])
            + suffix
        )
    if report.extra_slash:
        suffix = "..." if len(report.extra_slash) > 8 else ""
        lines.append(
            "  (informational) live-only slash commands: "
            + ", ".join(report.extra_slash[:8])
            + suffix
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _resolve_default_schema_path(cwd: Path) -> Path | None:
    """Find a default parity schema under ``cwd`` (PARITY.md, PARITY.json, ...)."""
    for name in ("PARITY.md", "PARITY.json", "PARITY.yaml", "PARITY.yml", "parity.md"):
        candidate = cwd / name
        if candidate.exists():
            return candidate
    return None


def run_parity_check(args: argparse.Namespace) -> int:
    """Implement ``chimera badger parity --against <schema>``.

    Args:
        args: Parsed namespace; reads ``parity_against`` (path) and
            ``output_format`` (text or json).

    Returns:
        0 on parity, 1 on diff, 2 on usage error.
    """
    raw_path = getattr(args, "parity_against", None)
    cwd = Path(getattr(args, "cwd", None) or Path.cwd())
    if raw_path:
        schema_path = Path(raw_path)
    else:
        resolved = _resolve_default_schema_path(cwd)
        if resolved is None:
            print(
                "badger parity: no schema found. Pass --against PARITY.md "
                "or place PARITY.md / PARITY.json in the working directory.",
                file=sys.stderr,
            )
            return 2
        schema_path = resolved

    try:
        expected = load_schema(schema_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"badger parity: {exc}", file=sys.stderr)
        return 2

    live = build_live_snapshot()
    report = diff_schema(expected, live)

    output_format = getattr(args, "output_format", "text") or "text"
    if output_format == "json":
        payload = {
            "schema_path": str(schema_path),
            "expected": expected.to_dict(),
            "live": live.to_dict(),
            "report": report.to_dict(),
        }
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
    else:
        print(format_report(report))
    return 0 if report.ok else 1
