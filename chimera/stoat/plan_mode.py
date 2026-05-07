"""Plan-mode posture for the stoat REPL — the third mode beyond agent / shell.

Stoat already toggles between an agent posture (LLM with tools) and a
shell posture (every input runs as ``bash -c <input>``). Plan mode adds
a third, deliberately constrained posture:

* The model is asked to **produce a plan and ask for confirmation, not
  to act**. No edits, no writes, no bash side effects — the plan is a
  read-only deliberation step.
* The resulting plan text is persisted to ``~/.chimera/plans/`` so the
  user can ``/resume`` it later (or another tool can fetch the latest
  plan ID).
* Exiting plan mode returns to whichever posture the user came from
  (typically ``agent``).

The state machine is driven by :class:`PlanModeManager`; the
:class:`Plan` dataclass is the on-disk record. Everything in this module
is stdlib-only — provider/agent invocation is the REPL's concern.

Trademark hygiene: plan mode is described as the "third posture"; the
upstream brand that pioneered it is never named in source.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

__all__ = [
    "MODE_PLAN",
    "Plan",
    "PlanModeManager",
    "default_plans_dir",
    "save_plan",
    "load_plan",
    "iter_plans",
    "build_plan_system_prompt",
    "PLAN_SYSTEM_PROMPT",
]


MODE_PLAN = "plan"
"""Identifier for plan mode: agent emits a plan only and asks for
confirmation. Mirrors the shell-mode constants from :mod:`shell_mode`."""


_DEFAULT_PLAN_PROMPT = "stoat? "
"""Prompt prefix rendered while plan mode is active. Distinct from the
agent (``stoat> ``) and shell (``stoat$ ``) prefixes so users see the
posture at a glance."""


PLAN_SYSTEM_PROMPT = (
    "You are Stoat in PLAN mode. Produce a step-by-step plan that "
    "addresses the user's request and explicitly ASK for confirmation "
    "before any action is taken. Do NOT call edit / write / bash tools "
    "in this turn. Read-only inspection (read / search / list) is fine. "
    "Always end with an explicit confirmation question such as 'Approve "
    "this plan? (y/n)'."
)
"""Default system-prompt addition for the plan-mode turn. The REPL
appends this to its base instruction so plan turns produce *plans*
rather than actions."""


def build_plan_system_prompt(base: str) -> str:
    """Return *base* with the plan-mode addendum appended.

    Args:
        base: The agent-mode system prompt the REPL would normally use.

    Returns:
        ``base`` with a blank line + :data:`PLAN_SYSTEM_PROMPT` added.
        When ``base`` is empty, only the plan addendum is returned.
    """
    base = (base or "").strip()
    if not base:
        return PLAN_SYSTEM_PROMPT
    return f"{base}\n\n{PLAN_SYSTEM_PROMPT}"


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


@dataclass
class PlanModeManager:
    """Track whether the REPL is currently in plan mode.

    Plan mode rides alongside the existing :class:`ShellModeManager` —
    it doesn't replace it. The REPL consults *both*: shell mode answers
    "should this input run as bash?" and plan mode answers "should this
    agent turn be plan-only?".

    Attributes:
        active: ``True`` while plan mode is on.
        plan_prompt: Prompt prefix to render when plan mode is active.
            Distinct from the agent / shell prefixes so the user knows
            which posture they're in.
        last_plan_id: The most-recently saved plan id (or ``None``).
            Useful for ``/resume`` semantics.
    """

    active: bool = False
    plan_prompt: str = _DEFAULT_PLAN_PROMPT
    last_plan_id: str | None = None

    def is_active(self) -> bool:
        """Return ``True`` while the REPL is in plan mode."""
        return bool(self.active)

    def enable(self) -> None:
        """Enter plan mode (idempotent)."""
        self.active = True

    def disable(self) -> None:
        """Leave plan mode (idempotent)."""
        self.active = False

    def toggle(self) -> bool:
        """Flip plan mode and return the new state.

        Returns:
            ``True`` after the flip turns plan mode on, ``False`` when
            it turns it off.
        """
        self.active = not self.active
        return self.active


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


_PLAN_ID_RE = re.compile(r"^plan-[0-9TZ\-]+-[0-9a-f]+$")


def _new_plan_id() -> str:
    """Return a fresh ``plan-<utc>-<short-uuid>`` identifier.

    The format matches the eventlog session prefix style so on-disk
    listings stay grep-friendly:

    >>> _new_plan_id()  # doctest: +SKIP
    'plan-2026-05-06T19-07-21Z-a1b2c3d4'
    """
    stamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    short = uuid.uuid4().hex[:8]
    return f"plan-{stamp}-{short}"


@dataclass
class Plan:
    """One persisted plan-mode turn.

    Attributes:
        plan_id: Unique identifier (``plan-<utc>-<short-uuid>``).
        created_at: ISO-8601 UTC timestamp the plan was authored.
        prompt: The user's original input that produced the plan.
        content: The plan text the agent emitted.
        model: Optional model id that produced the plan.
        cwd: Working directory captured at plan time.
        tags: Free-form list of strings (e.g. ``["draft"]``); never
            used by the manager itself, just round-tripped through JSON.
    """

    plan_id: str
    created_at: str
    prompt: str
    content: str
    model: str | None = None
    cwd: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        """Render the plan as compact JSON (one object per file)."""
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def new(
        cls,
        *,
        prompt: str,
        content: str,
        model: str | None = None,
        cwd: str | None = None,
    ) -> "Plan":
        """Construct a fresh :class:`Plan` with a generated id and timestamp.

        Args:
            prompt: The user request that triggered the plan-mode turn.
            content: The plan text the agent produced.
            model: Optional model id (round-tripped, not validated).
            cwd: Optional working directory captured at plan time.

        Returns:
            A new :class:`Plan` with ``plan_id`` and ``created_at``
            populated.
        """
        return cls(
            plan_id=_new_plan_id(),
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            prompt=prompt,
            content=content,
            model=model,
            cwd=cwd,
        )


def default_plans_dir() -> Path:
    """Return the directory plans are persisted to (``~/.chimera/plans``).

    The directory is created lazily on first :func:`save_plan` call;
    callers that need a guaranteed path can ``mkdir(parents=True,
    exist_ok=True)`` themselves before reading.

    Honors ``$CHIMERA_HOME`` for embedders / tests that want to relocate
    persistent state. Falls back to ``$HOME`` then ``Path.home()``.
    """
    base = os.environ.get("CHIMERA_HOME") or os.environ.get("HOME") or str(Path.home())
    return Path(base).expanduser() / ".chimera" / "plans"


def save_plan(plan: Plan, *, root: Path | None = None) -> Path:
    """Persist *plan* to ``<root>/<plan_id>.json``.

    Args:
        plan: The :class:`Plan` to write.
        root: Override the default plans directory. ``None`` uses
            :func:`default_plans_dir`.

    Returns:
        The path the plan was written to.

    Raises:
        OSError: When the directory cannot be created or the file cannot
            be written. Caller decides whether to surface or swallow.
    """
    target_dir = root if root is not None else default_plans_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{plan.plan_id}.json"
    target.write_text(plan.to_json() + "\n", encoding="utf-8")
    return target


def load_plan(plan_id: str, *, root: Path | None = None) -> Plan:
    """Return the persisted :class:`Plan` for ``plan_id``.

    Args:
        plan_id: The plan identifier (``plan-<utc>-<short-uuid>``).
        root: Override the default plans directory.

    Returns:
        The :class:`Plan` parsed from disk.

    Raises:
        FileNotFoundError: When no file matches ``plan_id``.
        ValueError: When the on-disk JSON is malformed.
    """
    if not _PLAN_ID_RE.match(plan_id):
        # Be permissive — the manager-issued ids match this regex but we
        # don't reject manually-typed IDs that don't, just bypass the
        # cheap sanity check.
        pass
    target_dir = root if root is not None else default_plans_dir()
    target = target_dir / f"{plan_id}.json"
    if not target.is_file():
        raise FileNotFoundError(f"plan not found: {plan_id}")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed plan {plan_id}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"malformed plan {plan_id}: top-level not an object")
    return Plan(
        plan_id=str(raw.get("plan_id") or plan_id),
        created_at=str(raw.get("created_at") or ""),
        prompt=str(raw.get("prompt") or ""),
        content=str(raw.get("content") or ""),
        model=raw.get("model"),
        cwd=raw.get("cwd"),
        tags=list(raw.get("tags") or []),
    )


def iter_plans(*, root: Path | None = None) -> Iterator[Plan]:
    """Yield every persisted plan, newest first.

    Args:
        root: Override the default plans directory.

    Yields:
        :class:`Plan` instances in reverse chronological order
        (newest plan id first). Malformed files are skipped silently —
        the listing is best-effort, not a strict integrity check.
    """
    target_dir = root if root is not None else default_plans_dir()
    if not target_dir.is_dir():
        return
    files: Iterable[Path] = sorted(
        (p for p in target_dir.glob("plan-*.json") if p.is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    for path in files:
        try:
            yield load_plan(path.stem, root=target_dir)
        except (FileNotFoundError, ValueError):
            continue
