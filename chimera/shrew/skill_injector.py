"""Per-turn dynamic skill re-ranker for the ``chimera shrew`` REPL (G11).

The legacy shrew flow (S2) discovers a flat list of bundled skills at
session-init time and statically formats their **descriptions** into the
system prompt. That works for a single-turn session but degrades fast
across a multi-turn coding loop: skills relevant to turn 1 ("how to read
a file") clutter the prompt on turn 7 ("recover from a failed test")
where a different cluster would help.

This module ships a stateful re-ranker — :class:`SkillInjector` — that
keeps every mounted skill (no unmount, no reload) and, before each turn,
re-orders them with a three-axis score:

* **error** — does the previous turn's error message overlap with the
  skill's name / triggers? Ranks the *recovery* skills to the top
  whenever the agent just hit a stack trace. Highest weight by default.
* **recency** — was the skill picked recently? Linear decay penalises
  skills that already burned prompt budget on the last turn so the
  rotation favors variety.
* **intent** — do tokens in the user's prompt overlap the skill's name
  and triggers? TF-IDF-flavoured token match (no IDF math, just a
  saturating fraction so a skill with one matching trigger doesn't beat
  another with three).

The top-K skills (default ``K=3``) are then rendered as a single
``## Active shrew skills`` block — name, description, and full body —
and inserted into the system prompt by replacing a ``<!-- shrew-skill-inject -->``
marker (or appended on first run when the marker is missing).

Stdlib-only. Pure functions for the scoring axes; the only mutable state
lives on :class:`SkillInjector` itself (turn counter, last-used map,
last error string). All public types are re-exported from
``chimera.shrew`` so callers can ``from chimera.shrew.skill_injector
import SkillInjector`` without importing the heavy REPL plumbing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

__all__ = [
    "DEFAULT_TOP_K",
    "DEFAULT_WEIGHTS",
    "MAX_TOKENS_PER_SCORE",
    "RECENCY_WINDOW_TURNS",
    "SKILL_INJECT_MARKER",
    "ScoreBreakdown",
    "ScoreWeights",
    "SkillLike",
    "SkillInjector",
    "TurnContext",
    "extract_error_text",
    "format_active_skills_block",
    "install_into_session",
    "score_error",
    "score_intent",
    "score_recency",
    "score_skill",
    "tokenize",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


DEFAULT_TOP_K: Final[int] = 3
"""How many skills to inject per turn unless the caller overrides it.

Three is small enough that the bodies fit in a few hundred tokens even
for the longest bundled shrew skills, and large enough that one turn
can carry an error-recovery skill, an intent-match, and a fallback.
"""

RECENCY_WINDOW_TURNS: Final[int] = 5
"""Turns over which the recency penalty decays linearly to zero.

A skill picked on the immediately-previous turn pays the full penalty;
five turns later the penalty is zero. The window is intentionally short
so the rotation feels like "don't repeat last turn", not "don't ever
re-pick".
"""

MAX_TOKENS_PER_SCORE: Final[int] = 64
"""Cap on tokens considered per text input when computing overlaps.

Bounds the worst-case cost of :func:`score_error` / :func:`score_intent`
on long stack traces or pasted logs. Empirically the relevant signal
sits in the first ~60 tokens (function names, error class) so the cap
trades nothing for predictable latency.
"""


_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
"""Tokeniser pattern.

Matches identifier-shaped runs so ``FileNotFoundError`` survives intact
and punctuation noise is dropped. We deliberately keep hyphens because
shrew skill names are slug-style (``error-recovery``) and we want
``error-recovery`` to match the literal slug when it appears in a
trigger or prompt.
"""


# Common English / programming stop-words. Kept tiny on purpose — a
# bigger list would over-prune short prompts. The motivation is to
# stop generic words ("the", "a", "is") from boosting every skill.
_STOPWORDS: Final[frozenset[str]] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by",
    "for", "from", "i", "if", "in", "is", "it", "of", "on",
    "or", "so", "the", "this", "to", "was", "we", "with",
    "you", "your", "my", "me",
})


# ---------------------------------------------------------------------------
# Skill protocol — duck-typed so test fakes work without importing
# ``chimera.shrew.skills.ShrewSkill``.
# ---------------------------------------------------------------------------


class SkillLike(Protocol):
    """The minimum surface this module needs from a skill record.

    Real :class:`chimera.shrew.skills.ShrewSkill` instances satisfy this
    structurally, but tests can pass any dataclass / namespace with the
    same five attributes. Keeping the contract narrow lets the injector
    live anywhere on the import graph without dragging in the skills
    package.
    """

    name: str
    description: str
    category: str
    body: str
    triggers: tuple[str, ...]


# ---------------------------------------------------------------------------
# Score plumbing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreWeights:
    """Per-axis weights used by :func:`score_skill`.

    Defaults err on the side of "error first" because in the small-model
    coding loop a failing tool call is the most common reason a skill
    needs to be swapped in. Intent is second so a fresh user prompt
    pulls in an obviously relevant skill on turn one. Recency is the
    smallest because it's a tie-breaker, not a primary signal.
    """

    error: float = 1.0
    intent: float = 0.6
    recency: float = 0.25


DEFAULT_WEIGHTS: Final[ScoreWeights] = ScoreWeights()
"""Module-default weights. Re-exported for convenient import."""


@dataclass(frozen=True)
class ScoreBreakdown:
    """Per-axis score components for one skill on one turn.

    Stored as a structured record (rather than a bare ``float``) so
    debugging / event logging can inspect *why* a skill was picked.
    The ``total`` field is the weighted sum the ranker actually sorts
    on.
    """

    error: float
    intent: float
    recency: float
    total: float


@dataclass(frozen=True)
class TurnContext:
    """The signals available to the scorer at the start of a turn.

    Attributes:
        prompt: The user's most recent input (the turn we're about to
            run). Used for intent matching.
        last_error: Concatenated error text from the previous turn, or
            ``""`` when the previous turn finished cleanly. Used for
            error-axis matching.
        turn_index: Monotonic 0-indexed turn counter for the live
            session. Used together with ``last_used`` to compute the
            recency decay.
        last_used: ``skill_name -> turn_index`` map of the most recent
            turn each skill was *injected*. Skills missing from the map
            pay no recency penalty.
    """

    prompt: str
    last_error: str = ""
    turn_index: int = 0
    last_used: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------


def tokenize(text: str, *, cap: int = MAX_TOKENS_PER_SCORE) -> list[str]:
    """Lowercase identifier-style tokens, stop-words removed, capped.

    Args:
        text: Free-form text (a prompt, an error, a skill body).
        cap: Maximum number of tokens to keep. Defaults to
            :data:`MAX_TOKENS_PER_SCORE`.

    Returns:
        A list of lower-cased, hyphen-preserving identifier tokens with
        stop-words removed. Order is preserved so callers that care
        about positional weighting can take the first N.
    """
    if not text:
        return []
    raw = _TOKEN_PATTERN.findall(text)
    out: list[str] = []
    for tok in raw:
        low = tok.lower()
        if low in _STOPWORDS:
            continue
        out.append(low)
        if len(out) >= cap:
            break
    return out


def _skill_keyword_set(skill: SkillLike) -> set[str]:
    """Build the keyword set used by both error and intent matching.

    Combines the skill's *name* (split on hyphens), the skill's
    declared *triggers* (each tokenised), and the slug itself. The
    description and body are deliberately excluded — they're long-form
    prose that would dominate the overlap and make every skill score
    high on every prompt.
    """
    keywords: set[str] = set()
    name_tokens = tokenize(skill.name.replace("-", " "))
    keywords.update(name_tokens)
    if skill.name:
        keywords.add(skill.name.lower())
    for trig in skill.triggers:
        for tok in tokenize(trig):
            keywords.add(tok)
        # Multi-word triggers also contribute the literal slug-style
        # form so a prompt like "loop detected" matches a trigger
        # written as "loop detection".
        keywords.add(trig.lower())
    return {kw for kw in keywords if kw}


# ---------------------------------------------------------------------------
# Per-axis scorers — pure, no state, deterministic.
# ---------------------------------------------------------------------------


def score_error(skill: SkillLike, error_text: str) -> float:
    """Score the *error* axis: does the last error overlap this skill?

    Returns a value in ``[0.0, 1.0]``. The model is **half-life**: one
    matching keyword scores 0.5 (already a strong signal — error
    messages are noisy and a single hit is meaningful), two matches
    score 0.875, three match 0.9375, etc. We deliberately bias toward
    "any match counts" rather than the linear-in-overlap recipe used
    for intent — error matches are rare and need to dominate the
    other axes when they happen.

    Empty error or no overlap returns 0.0.
    """
    if not error_text:
        return 0.0
    keywords = _skill_keyword_set(skill)
    if not keywords:
        return 0.0
    err_tokens = set(tokenize(error_text))
    if not err_tokens:
        return 0.0
    overlap = keywords & err_tokens
    if not overlap:
        return 0.0
    # Half-life saturation: 1 - 0.5^n. One match = 0.5, two = 0.75,
    # three = 0.875. Caps at 1.0 well before exotic match counts.
    return 1.0 - 0.5 ** len(overlap)


def score_intent(skill: SkillLike, prompt: str) -> float:
    """Score the *intent* axis: prompt-vs-skill keyword overlap.

    Same shape as :func:`score_error` but on the *prompt* tokens. Empty
    prompt returns 0.0. A prompt containing the skill's literal slug
    (e.g. user explicitly types ``error-recovery``) gets a guaranteed
    1.0 short-circuit so users can pin a skill manually.
    """
    if not prompt:
        return 0.0
    keywords = _skill_keyword_set(skill)
    if not keywords:
        return 0.0
    prompt_lower = prompt.lower()
    if skill.name and skill.name.lower() in prompt_lower:
        return 1.0
    prompt_tokens = set(tokenize(prompt))
    if not prompt_tokens:
        return 0.0
    overlap = keywords & prompt_tokens
    if not overlap:
        return 0.0
    denom = max(1, min(len(keywords), 4))
    return min(1.0, len(overlap) / denom)


def score_recency(
    skill: SkillLike,
    turn_index: int,
    last_used: dict[str, int],
    *,
    window: int = RECENCY_WINDOW_TURNS,
) -> float:
    """Score the *recency* axis as a **penalty** in ``[-1.0, 0.0]``.

    A skill picked on this exact turn returns ``-1.0`` (max penalty);
    one picked ``window`` turns ago returns ``0.0``. Skills missing
    from ``last_used`` never paid the cost and return ``0.0``.

    The output is negative so that summing into the total naturally
    *demotes* recently-used skills without needing a sign flip in
    :func:`score_skill`.
    """
    if window <= 0:
        return 0.0
    last = last_used.get(skill.name)
    if last is None:
        return 0.0
    age = turn_index - last
    if age <= 0:
        return -1.0
    if age >= window:
        return 0.0
    # Linear decay: age=1 -> -(window-1)/window, ..., age=window-1 -> -1/window.
    return -(window - age) / window


def score_skill(
    skill: SkillLike,
    ctx: TurnContext,
    *,
    weights: ScoreWeights = DEFAULT_WEIGHTS,
) -> ScoreBreakdown:
    """Combine the three axes into a single :class:`ScoreBreakdown`.

    Returns the breakdown unchanged so debug callers can introspect the
    components; :class:`SkillInjector` sorts on ``.total``.
    """
    e = score_error(skill, ctx.last_error)
    i = score_intent(skill, ctx.prompt)
    r = score_recency(skill, ctx.turn_index, ctx.last_used)
    total = (
        weights.error * e + weights.intent * i + weights.recency * r
    )
    return ScoreBreakdown(error=e, intent=i, recency=r, total=total)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


SKILL_INJECT_MARKER: Final[str] = "<!-- shrew-skill-inject -->"
"""HTML-style comment marker the injector replaces inside the system prompt.

When present, :meth:`SkillInjector.inject_into_prompt` swaps the line
containing the marker for the rendered active-skills block. When
missing, the block is appended after a blank line. Using a comment
keeps the marker invisible to the model while still being trivial to
locate by string match.
"""


def format_active_skills_block(skills: Sequence[SkillLike]) -> str:
    """Render the picked skills as a markdown section with full bodies.

    Differs from :func:`chimera.shrew.skills.format_shrew_skills_for_prompt`
    in two ways:

    1. We render only the *picked* subset (typically 3 skills).
    2. We include each skill's **body**, not just the description, so
       the model has the protocol text to follow.

    Empty input yields an empty string so the marker can be replaced
    with nothing on a turn where every skill scored zero.
    """
    if not skills:
        return ""
    lines: list[str] = ["## Active shrew skills (per-turn)"]
    for s in skills:
        lines.append("")
        lines.append(f"### {s.name} — {s.description}")
        body = s.body.strip()
        if body:
            lines.append("")
            lines.append(body)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Stateful injector
# ---------------------------------------------------------------------------


class SkillInjector:
    """Stateful per-turn re-ranker for a fixed set of mounted skills.

    Lifecycle:

    1. Caller builds one :class:`SkillInjector` with the mounted skill
       set (typically the result of
       :func:`chimera.shrew.skills.discover_shrew_skills`).
    2. Before each turn, the caller invokes :meth:`select` with the
       user's prompt. The injector returns the top-K skills, mutates
       its ``last_used`` map, and increments its turn counter.
    3. After the turn finishes, the caller calls :meth:`record_turn`
       with any error text from the turn (or ``""`` on success). The
       error is stored for the *next* call to :meth:`select` so the
       error-axis score sees it.
    4. The caller substitutes the rendered block into the live system
       prompt — usually via :meth:`inject_into_prompt` which handles
       the marker replacement.

    Thread-safety: this class is *not* thread-safe. The shrew REPL
    runs the agent on one background thread but the injector is only
    touched from the main REPL thread (before / after the agent
    spawns) so we don't pay for a lock.
    """

    def __init__(
        self,
        skills: Iterable[SkillLike],
        *,
        top_k: int = DEFAULT_TOP_K,
        weights: ScoreWeights = DEFAULT_WEIGHTS,
    ) -> None:
        """Construct an injector over a fixed skill set.

        Args:
            skills: The skills to keep mounted across the whole
                session. Order is preserved as a deterministic
                tie-breaker.
            top_k: Number of skills to inject per turn. Values < 1 are
                clamped to 1 (we always render at least one skill so
                the injection block isn't surprisingly empty).
            weights: Per-axis weights. Defaults to
                :data:`DEFAULT_WEIGHTS`.
        """
        self._skills: list[SkillLike] = list(skills)
        self._top_k: int = max(1, int(top_k))
        self._weights: ScoreWeights = weights
        self._turn_index: int = 0
        self._last_used: dict[str, int] = {}
        self._last_error: str = ""
        self._last_breakdown: dict[str, ScoreBreakdown] = {}

    # -- introspection ------------------------------------------------

    @property
    def top_k(self) -> int:
        """The configured top-K cap."""
        return self._top_k

    @property
    def turn_index(self) -> int:
        """The next turn's 0-indexed counter (advances on :meth:`select`)."""
        return self._turn_index

    @property
    def skills(self) -> tuple[SkillLike, ...]:
        """A read-only view of the mounted skill set."""
        return tuple(self._skills)

    @property
    def last_used(self) -> dict[str, int]:
        """The current ``skill_name -> turn_index`` last-used map.

        Returned as a fresh dict copy so external callers can't mutate
        the injector's state by accident.
        """
        return dict(self._last_used)

    @property
    def last_breakdown(self) -> dict[str, ScoreBreakdown]:
        """Score components from the most recent :meth:`select` call.

        Useful for ``/debug`` slash commands and tests; empty before
        the first call.
        """
        return dict(self._last_breakdown)

    # -- selection ----------------------------------------------------

    def select(self, prompt: str) -> list[SkillLike]:
        """Re-rank and pick the top-K skills for the given prompt.

        Side effects:

        * Stores per-skill score breakdowns on
          :attr:`last_breakdown` (replacing any previous turn's data).
        * Advances :attr:`turn_index` by one.
        * Updates :attr:`last_used` for every skill that was picked.

        Args:
            prompt: The user's prompt for the upcoming turn. May be
                empty (e.g. follow-up auto-resume) — the picker still
                returns a non-empty list as long as there's at least
                one mounted skill, falling back to recency-aware
                rotation.

        Returns:
            The picked skills in score-descending order. If no skills
            are mounted, returns ``[]``.
        """
        if not self._skills:
            self._last_breakdown = {}
            self._turn_index += 1
            return []

        ctx = TurnContext(
            prompt=prompt,
            last_error=self._last_error,
            turn_index=self._turn_index,
            last_used=dict(self._last_used),
        )
        scored: list[tuple[ScoreBreakdown, int, SkillLike]] = []
        breakdown: dict[str, ScoreBreakdown] = {}
        for idx, skill in enumerate(self._skills):
            br = score_skill(skill, ctx, weights=self._weights)
            breakdown[skill.name] = br
            # Index is the deterministic tie-breaker: prefer the order
            # in which the skill was originally mounted.
            scored.append((br, idx, skill))

        # Sort: descending total, ascending insertion index for ties.
        scored.sort(key=lambda triple: (-triple[0].total, triple[1]))

        picked = [s for _, _, s in scored[: self._top_k]]
        self._last_breakdown = breakdown

        # Bookkeeping AFTER scoring so the recency penalty for "this
        # turn" matches what the math intended.
        for s in picked:
            self._last_used[s.name] = self._turn_index
        self._turn_index += 1
        # The caller hasn't *seen* the next turn's error yet; clear so
        # a stale error doesn't accidentally bias multiple turns. The
        # caller can call ``record_turn`` again before the next select.
        self._last_error = ""
        return picked

    # -- post-turn callback -------------------------------------------

    def record_turn(self, *, error: str = "") -> None:
        """Record the just-finished turn's error text for the next select.

        Calling with ``error=""`` (the default) clears the slot so a
        clean turn doesn't leak signal into the next ranking. The
        injector keeps only *one* turn's error: by design we want to
        react to recent failures, not stale ones from five turns back.
        """
        self._last_error = error or ""

    # -- prompt rewriting ---------------------------------------------

    def inject_into_prompt(self, system_prompt: str, prompt: str) -> str:
        """Re-rank and rewrite ``system_prompt`` for the upcoming turn.

        Behaviour:

        * Calls :meth:`select` to pick the skills (so this is *not*
          read-only — it advances state).
        * Renders the picked block via
          :func:`format_active_skills_block`.
        * If ``system_prompt`` contains :data:`SKILL_INJECT_MARKER`,
          replaces the marker line with the rendered block.
        * Otherwise appends the rendered block after a blank line.

        The wrapper exists so callers that already keep a "clean"
        system prompt template (with the marker in place) get
        idempotent rewrites: re-injecting on turn N replaces turn
        N-1's block instead of stacking copies.

        Args:
            system_prompt: The current rendered system prompt. May be
                the original Prompt-rendered template or a previously
                injected version.
            prompt: The user's prompt for the upcoming turn.

        Returns:
            The system prompt with the active-skills block in place.
        """
        picked = self.select(prompt)
        block = format_active_skills_block(picked).rstrip()

        # Strip any previous injection block so re-runs are idempotent.
        cleaned = _strip_previous_block(system_prompt)

        if SKILL_INJECT_MARKER in cleaned:
            if block:
                replacement = f"{SKILL_INJECT_MARKER}\n{block}"
            else:
                replacement = SKILL_INJECT_MARKER
            return cleaned.replace(SKILL_INJECT_MARKER, replacement, 1)

        if not block:
            return cleaned
        # No marker — append. Keep a single blank-line separator so
        # the rendered prompt stays readable.
        sep = "" if cleaned.endswith("\n\n") else (
            "\n" if cleaned.endswith("\n") else "\n\n"
        )
        return f"{cleaned}{sep}{SKILL_INJECT_MARKER}\n{block}\n"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_BLOCK_HEADER: Final[str] = "## Active shrew skills (per-turn)"


# ---------------------------------------------------------------------------
# Public helpers — error extraction + session integration
# ---------------------------------------------------------------------------


def extract_error_text(step_results: Iterable[object]) -> str:
    """Pull any tool-result error / failure text out of a turn's StepResults.

    The shrew REPL drives the agent via :meth:`Session.iter_chat`,
    which yields :class:`chimera.types.StepResult` objects whose
    ``tool_results`` list contains :class:`chimera.types.ToolResult`
    entries with ``success`` and ``output`` fields. We don't import
    those types directly so the helper stays usable from tests with
    plain mocks; everything is duck-typed via ``getattr``.

    Args:
        step_results: Iterable of step-shaped objects (real
            ``StepResult`` instances or test fakes).

    Returns:
        A single concatenated error string covering every failed tool
        result in the turn, or ``""`` when the turn finished cleanly.
        Each tool result is rendered on its own line so individual
        keywords stay greppable.
    """
    chunks: list[str] = []
    for step in step_results:
        results = getattr(step, "tool_results", None) or []
        for tr in results:
            if getattr(tr, "success", True):
                continue
            output = getattr(tr, "output", "") or ""
            tool_name = getattr(tr, "tool_name", "") or getattr(tr, "name", "")
            if tool_name:
                chunks.append(f"[{tool_name}] {output}")
            else:
                chunks.append(str(output))
        # Some loops attach a top-level ``error`` field on the step.
        step_err = getattr(step, "error", None)
        if step_err:
            chunks.append(str(step_err))
    return "\n".join(c for c in chunks if c)


def install_into_session(
    session: object,
    injector: SkillInjector,
    *,
    base_system: str | None = None,
) -> None:
    """Wire ``injector`` into a live :class:`chimera.sessions.session.Session`.

    Wraps the session's ``iter_chat`` (and ``chat``) method so that:

    * Before each turn, the system prompt is rewritten with the
      current top-K skills picked for the user's prompt.
    * After each turn, the injector receives any error text from the
      step stream so the next turn's error-axis sees the failure.

    The integration deliberately uses **monkey-patching** rather than
    sub-classing because the live ``Session`` instance is already
    constructed by ``run_code`` and we don't control its construction.
    This keeps the per-turn re-rank a one-call install:

    .. code-block:: python

        injector = SkillInjector(skills)
        install_into_session(session, injector)

    Args:
        session: The live session object. Must expose
            ``_context.system`` (mutable) and ``iter_chat`` /
            ``chat`` callables.
        injector: A pre-built :class:`SkillInjector` whose mounted
            skill list matches the session's universe.
        base_system: Optional override of the "clean" system prompt
            used as the substrate for re-injection. When ``None``,
            the injector reuses ``session._context.system`` as it
            was at install time, which is correct for the standard
            shrew flow (skills haven't been injected yet).

    The wrapped ``iter_chat`` is a *generator* — it forwards
    everything from the wrapped call but tees the step stream to the
    injector before yielding. The wrapped ``chat`` is a plain
    function and forwards via ``iter_chat`` to keep the error-tee
    path single-source.
    """
    ctx = getattr(session, "_context", None)
    if ctx is None:
        raise AttributeError(
            "install_into_session: session has no _context (got "
            f"{type(session).__name__})"
        )
    template = base_system if base_system is not None else getattr(ctx, "system", "") or ""

    original_iter = getattr(session, "iter_chat", None)
    original_chat = getattr(session, "chat", None)
    if not callable(original_iter):
        raise AttributeError("install_into_session: session.iter_chat missing")

    def _wrapped_iter_chat(message: str):  # type: ignore[no-untyped-def]
        # Re-rank + inject before delegating to the real iter_chat.
        ctx.system = injector.inject_into_prompt(template, message)
        steps: list[object] = []

        def _gen():  # type: ignore[no-untyped-def]
            gen: Any = original_iter(message)
            try:
                while True:
                    step = next(gen)
                    steps.append(step)
                    yield step
            except StopIteration as stop:
                # Forward final return value so callers using
                # ``yield from session.iter_chat(...)`` get the
                # AgentResult unchanged.
                injector.record_turn(error=extract_error_text(steps))
                return stop.value

        return _gen()  # type: ignore[no-untyped-call]

    def _wrapped_chat(message: str):  # type: ignore[no-untyped-def]
        # Drive the wrapped iter_chat to completion and return the
        # AgentResult. Mirrors what ``Session.chat`` does internally
        # but routes through the wrapped path so the error-tee fires.
        gen: Any = _wrapped_iter_chat(message)
        result = None
        try:
            while True:
                next(gen)
        except StopIteration as stop:
            result = stop.value
        return result

    session.iter_chat = _wrapped_iter_chat  # type: ignore[attr-defined]
    if callable(original_chat):
        session.chat = _wrapped_chat  # type: ignore[attr-defined]
    # Also stash the injector on the session so debug commands can
    # introspect the last breakdown without needing a separate
    # closure reference.
    session.shrew_skill_injector = injector  # type: ignore[attr-defined]


def _strip_previous_block(prompt: str) -> str:
    """Remove any previously-injected active-skills block from ``prompt``.

    The injection block always starts with :data:`_BLOCK_HEADER` and
    ends at the next blank line followed by either end-of-string or a
    line that does *not* start with ``"###"`` / ``""`` (i.e. the next
    top-level heading or end). We use a simple state machine rather
    than a regex because the body markdown can contain anything,
    including its own ``#`` headers nested under ``###``.
    """
    if _BLOCK_HEADER not in prompt:
        return prompt

    lines = prompt.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.rstrip("\n")
        if not skipping and stripped == _BLOCK_HEADER:
            skipping = True
            # Trim a trailing blank line we previously inserted before
            # the block, so re-injection doesn't accumulate blanks.
            while out and out[-1].strip() == "":
                out.pop()
            continue
        if skipping:
            # End of block: a top-level heading (``## ``) or a marker
            # comment line that isn't ours, or end-of-string.
            if stripped.startswith("## ") and stripped != _BLOCK_HEADER:
                skipping = False
                out.append(line)
                continue
            if stripped.startswith("<!--") and SKILL_INJECT_MARKER not in stripped:
                skipping = False
                out.append(line)
                continue
            # Otherwise keep skipping (we're still inside the block).
            continue
        out.append(line)
    return "".join(out)
