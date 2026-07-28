"""Resume-helper primitives shared by the otter / ferret / weasel / shrew CLIs.

Wave 9 (C1) introduces a uniform ``--resume <id>`` and ``--continue`` /
``-c`` flag pair across every coding-agent CLI in the Chimera tree. Wave 4
(L4 / L7) had already wired the SQLite store + snapshot-driven fast resume
into :class:`~chimera.sessions.eventlog.session.EventSourcedSession`; this
module is the thin glue that lets a plain CLI flag drive that resume path
without each CLI re-deriving the same lookup logic.

Two primitives only — ``find_latest_run`` and ``resume_run`` — both
stdlib-only so they're safe to import from any CLI module.

Semantics:

* ``--resume <id>``  — load the named JSONL eventlog directly (the
  ``id`` is the directory name under ``~/.chimera/eventlog/``) and
  return a hydrated :class:`EventSourcedSession`.
* ``--continue`` / ``-c`` — equivalent to ``--resume <newest-id>`` for
  the calling CLI's prefix (``otter-``, ``ferret-``, ``weasel-``,
  ``shrew-``). When the working directory is supplied, only runs whose
  ``summary.json`` ``cwd`` field matches are considered, so ``-c``
  inside one project never resumes another's chat.

The two functions intentionally accept *prefix* explicitly rather than
auto-detecting: each CLI passes its own ``otter-`` / ``weasel-`` /
etc. constant so a single helper covers all four flag wirings without
the helper having to know which CLI invoked it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.sessions.eventlog.session import EventSourcedSession
from chimera.config.paths import store_path

if TYPE_CHECKING:
    from chimera.sessions.session import SessionResumeAgent

__all__ = [
    "ResumeAgentShim",
    "build_resume_prefix",
    "default_eventlog_root",
    "find_latest_run",
    "resolve_resume_id",
    "resume_run",
]


class _ResumePromptShim:
    """Render-only ``prompt`` stand-in for :class:`ResumeAgentShim`.

    Implements the structural ``_PromptLike`` protocol declared in
    :mod:`chimera.sessions.session` — a single ``render`` method whose
    return value is overwritten by the replayed Context the moment
    :meth:`EventSourcedSession.resume` finishes hydrating it.
    """

    def render(self, tools: list[str] | None = None) -> str:
        return ""


class ResumeAgentShim:
    """Minimal :class:`SessionResumeAgent` impl shared by every CLI.

    :meth:`Session.__init__` (called by both :meth:`Session.resume` and
    :meth:`EventSourcedSession.resume`) reads
    ``self.prompt.render(tools=[...])`` and iterates ``self.tools`` for
    tool-name derivation. An empty ``tools`` list is fine — the resumed
    Context is overlaid with replayed state immediately afterwards, so
    this shim's only job is to satisfy the protocol surface without
    pulling in a real :class:`Agent`.
    """

    def __init__(self) -> None:
        # ``Any`` so mypy uses structural matching against the
        # ``SessionResumeAgent`` Protocol (which expects ``_PromptLike``)
        # rather than rejecting the concrete subtype name.
        self.prompt: Any = _ResumePromptShim()
        self.tools: list[Any] = []


def default_eventlog_root() -> Path:
    """Return the canonical eventlog root.

    Mirrors the per-CLI ``default_eventlog_root`` helpers in
    ``chimera.{otter,ferret,weasel,shrew}.sessions``; pulled up here so a
    CLI that doesn't yet expose its own copy still has a sensible default.

    Returns:
        ``~/.chimera/eventlog/`` honoring the current ``Path.home()``.
    """
    return store_path("eventlog")


def _read_summary(session_dir: Path) -> dict[str, Any] | None:
    """Read and parse ``summary.json`` for ``session_dir``.

    Args:
        session_dir: Path to a single ``<prefix>-<utc>-<uuid>`` directory.

    Returns:
        Parsed summary dict, or ``None`` when the file is missing or
        unparseable. Errors are silent — ``find_latest_run`` falls back
        to mtime-based ordering when summaries are missing.
    """
    summary_path = session_dir / "summary.json"
    if not summary_path.exists():
        return None
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _matches_cwd(summary: dict[str, Any] | None, cwd: str | None) -> bool:
    """Return whether ``summary.cwd`` matches the requested ``cwd``.

    Args:
        summary: Parsed ``summary.json`` for the candidate run.
        cwd: Caller's working directory (already absolute). When
            ``None`` the cwd filter is disabled — every candidate
            matches.

    Returns:
        ``True`` when ``cwd`` is ``None`` (filter disabled) or when the
        summary's persisted cwd resolves to the same absolute path.
        ``False`` otherwise. A missing or malformed summary fails the
        filter so ``-c`` doesn't accidentally resume a foreign-cwd run.
    """
    if cwd is None:
        return True
    if summary is None:
        return False
    raw = summary.get("cwd")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        return os.path.abspath(raw) == os.path.abspath(cwd)
    except (OSError, ValueError):
        return False


def find_latest_run(
    prefix: str,
    eventlog_root: Path | None = None,
    *,
    cwd: str | None = None,
) -> str | None:
    """Find the newest run id whose directory name starts with ``prefix``.

    Used by ``--continue`` / ``-c`` to resolve "the last run for this
    CLI in this directory". Sorting is purely lexical because all CLIs
    mint run ids of the form ``<prefix>-YYYYMMDDTHHMMSS-<uuid8>`` — the
    UTC timestamp segment makes lexical descending order equivalent to
    chronological reverse order, with no clock-skew traps.

    When ``cwd`` is supplied, only runs whose ``summary.json`` ``cwd``
    field matches are considered. Runs without a ``summary.json`` (or
    with one that's malformed) are skipped under the cwd filter so a
    crashed run doesn't shadow a clean prior one.

    Args:
        prefix: The CLI prefix to scan for (``"otter-"``, ``"ferret-"``,
            ``"weasel-"``, ``"shrew-"``, ``"mink-"``).
        eventlog_root: Optional override for the eventlog root. Defaults
            to :func:`default_eventlog_root`.
        cwd: When set, only runs whose persisted summary ``cwd`` matches
            this absolute path are returned.

    Returns:
        The newest matching run id (the directory name), or ``None``
        when nothing matches. ``None`` is also returned when the
        eventlog root doesn't exist yet — first-run callers get a clean
        no-op rather than an exception.
    """
    root = eventlog_root or default_eventlog_root()
    if not root.exists() or not root.is_dir():
        return None

    candidates: list[tuple[str, dict[str, Any] | None]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if not child.name.startswith(prefix):
            continue
        summary = _read_summary(child)
        if not _matches_cwd(summary, cwd):
            continue
        candidates.append((child.name, summary))

    if not candidates:
        return None

    # Sort lexically descending — run ids are ``<prefix>-<UTC>-<uuid8>``
    # so the UTC segment makes lexical reverse-order equivalent to
    # chronological reverse-order.
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][0]


def resume_run(
    run_id: str,
    *,
    agent: SessionResumeAgent | None = None,
    eventlog_root: Path | None = None,
) -> EventSourcedSession:
    """Resume a JSONL-backed run by id and return the hydrated session.

    Thin wrapper around :meth:`EventSourcedSession.resume` so the four
    CLIs can call one helper instead of each re-deriving the resume
    arguments. The agent argument satisfies the ``SessionResumeAgent``
    protocol — a render-only ``prompt`` plus an iterable ``tools`` is
    enough; CLIs typically pass a tiny shim because the resumed
    context is overwritten with the replayed events immediately.

    Args:
        run_id: The run directory name (e.g.
            ``"otter-20260430T101501-71032a5e"``).
        agent: A ``SessionResumeAgent``-compatible object — see
            :class:`chimera.sessions.session.SessionResumeAgent` for
            the protocol surface. Defaults to a fresh
            :class:`ResumeAgentShim` when ``None``, so callers that
            only need replayed messages don't have to construct one.
        eventlog_root: Optional override for the eventlog root. Defaults
            to :func:`default_eventlog_root`.

    Returns:
        A fully-replayed :class:`EventSourcedSession` whose
        ``messages`` reflect the prior conversation.

    Raises:
        ValueError: When the run directory is missing or
            :meth:`EventSourcedSession.resume` rejects the inputs.
    """
    root = eventlog_root or default_eventlog_root()
    resolved_agent: Any = agent if agent is not None else ResumeAgentShim()
    return EventSourcedSession.resume(
        log_dir=root,
        session_id=run_id,
        agent=resolved_agent,
    )


def resolve_resume_id(
    *,
    explicit_id: str | None,
    continue_latest: bool,
    prefix: str,
    eventlog_root: Path | None = None,
    cwd: str | None = None,
) -> str | None:
    """Combine the ``--resume`` and ``--continue`` flag inputs into an id.

    Argparse exposes the two flags as separate slots: ``args.resume``
    (the explicit id, or ``None``) and ``args.continue_latest`` (the
    boolean ``-c`` toggle). Each CLI runs the same resolution logic, so
    we centralise it here. Explicit ``--resume`` always wins over
    ``--continue``; when neither is set the resolver returns ``None``
    so the caller falls through to a fresh run.

    Args:
        explicit_id: Value of ``args.resume`` (``None`` when unset).
        continue_latest: Value of ``args.continue_latest`` (``True``
            when the user passed ``-c`` / ``--continue``).
        prefix: The CLI prefix (``"otter-"``, ``"ferret-"`` etc.) used
            when ``--continue`` is set.
        eventlog_root: Optional override for the eventlog root.
        cwd: Optional cwd filter for ``--continue`` resolution. Ignored
            when ``explicit_id`` is set.

    Returns:
        The run id to resume, or ``None`` when neither flag is set or
        ``--continue`` finds no candidate.
    """
    if explicit_id:
        return explicit_id
    if continue_latest:
        return find_latest_run(prefix, eventlog_root, cwd=cwd)
    return None


def build_resume_prefix(messages: list[Any], *, max_chars: int = 8000) -> str:
    """Render replayed ``messages`` as a transcript block for a fresh prompt.

    The four CLI ``-p`` paths re-build a fresh :class:`Agent` for each
    invocation rather than re-using the resumed session's loop, so the
    cleanest way to feed the prior conversation back into the new turn
    is to render it as a transcript prefix that we glue onto the
    user's new ``-p`` prompt. The block is XML-tagged
    (``<prior_conversation>``) so it's recognisable to the model and
    deterministic for tests.

    Args:
        messages: Messages from the resumed session (typically
            ``EventSourcedSession.messages``).
        max_chars: Soft cap on the rendered block size. When the
            transcript would exceed this length we drop oldest
            messages first and prepend a ``[truncated]`` marker so the
            model sees newer turns intact. Defaults to 8000 chars
            (≈ 2K tokens) — generous enough for most multi-turn
            workflows without blowing the context budget on resume.

    Returns:
        A ``<prior_conversation>``-wrapped transcript ending with two
        newlines, ready to concatenate before the new user prompt.
        Returns ``""`` when ``messages`` is empty so callers can
        unconditionally prepend the result.
    """
    if not messages:
        return ""

    rendered: list[str] = []
    for msg in messages:
        role = getattr(msg, "role", "user")
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            # Tool-message content can be a list of blocks; flatten
            # to a single string for the transcript view.
            try:
                content = json.dumps(content, ensure_ascii=False)
            except (TypeError, ValueError):
                content = str(content)
        rendered.append(f"<{role}>\n{content}\n</{role}>")

    block = "\n".join(rendered)
    truncated = False
    while len(block) > max_chars and len(rendered) > 1:
        rendered.pop(0)
        block = "\n".join(rendered)
        truncated = True

    header = "<prior_conversation>"
    if truncated:
        header += "\n[truncated: oldest turns dropped to fit context budget]"
    return f"{header}\n{block}\n</prior_conversation>\n\n"
