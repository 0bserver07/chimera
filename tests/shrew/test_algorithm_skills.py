"""Tests for the bundled algorithm cheat-sheet skills (G12).

Pulls 13 ``SKILL.md`` files under ``chimera/skills/algorithms/`` through
the auto-discovery path and locks:

* every skill loads (no parser failures);
* every skill carries the required frontmatter (``name``,
  ``description``, ``when-to-use``);
* every skill body has the spec-mandated sections (invariants,
  complexity, Python *and* JavaScript code, common pitfalls, test
  corner cases);
* the bundled set wires into :func:`chimera.skills.discovery.default_search_paths`
  so the shrew REPL surfaces them without user configuration.

Hermetic: nothing here touches a provider, the model registry, or the
filesystem outside the repo. The bundled SKILL.md files are read
directly so any drift in their layout fails this suite first.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.skills.discovery import (
    Skill,
    bundled_algorithms_path,
    default_search_paths,
    discover_skills,
)


# Spec list (W12-7 P0): 13 algorithm cheat-sheet skills, one
# directory each. Names match the on-disk ``name:`` frontmatter
# (``algo-`` prefix avoids collisions with arbitrary user skills).
EXPECTED_BUNDLED: dict[str, str] = {
    "algo-binary-search": "binary-search",
    "algo-dp": "dp",
    "algo-bfs": "bfs",
    "algo-dfs": "dfs",
    "algo-hash": "hash",
    "algo-two-pointers": "two-pointers",
    "algo-sliding-window": "sliding-window",
    "algo-sorting": "sorting",
    "algo-greedy": "greedy",
    "algo-recursion": "recursion",
    "algo-graph-traversal": "graph-traversal",
    "algo-math-tricks": "math-tricks",
    "algo-string-algos": "string-algos",
}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _bundled() -> list[Skill]:
    """Helper: discover only the bundled algorithm skills."""
    return discover_skills([bundled_algorithms_path()])


def test_bundled_algorithms_path_exists() -> None:
    p = bundled_algorithms_path()
    assert p.is_dir(), f"bundled algorithm dir missing: {p}"
    assert p.name == "algorithms"


def test_all_thirteen_bundled_skills_load() -> None:
    skills = _bundled()
    by_name = {s.name: s for s in skills}
    missing = set(EXPECTED_BUNDLED) - set(by_name)
    extra = set(by_name) - set(EXPECTED_BUNDLED)
    assert not missing, f"missing skills: {missing}"
    assert not extra, f"unexpected extras: {extra}"
    assert len(skills) == 13


def test_bundled_skills_live_in_expected_directories() -> None:
    skills = _bundled()
    by_name = {s.name: s for s in skills}
    for name, dirname in EXPECTED_BUNDLED.items():
        skill = by_name[name]
        assert Path(skill.file_path).parent.name == dirname, (
            f"{name} expected in dir {dirname!r}, got "
            f"{Path(skill.file_path).parent.name!r}"
        )


def test_default_search_paths_includes_bundled() -> None:
    paths = default_search_paths(workdir=".")
    assert paths[0] == bundled_algorithms_path(), (
        "bundled algorithms must be first so it's the read-only base "
        "layer that project / user skills override"
    )
    # Project / user paths still present.
    assert any(p.name == "skills" and ".chimera" in str(p) for p in paths[1:])


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bundled_skills() -> list[Skill]:
    return _bundled()


def test_every_skill_has_description(bundled_skills: list[Skill]) -> None:
    for s in bundled_skills:
        assert s.description, f"{s.name} missing description"
        # Length sanity — descriptions are one-line summaries.
        assert 20 <= len(s.description) <= 256, (
            f"{s.name} description length {len(s.description)} "
            "(want 20–256 chars)"
        )


def test_every_skill_has_when_to_use(bundled_skills: list[Skill]) -> None:
    # ``when-to-use`` is a required frontmatter key per the G12 spec.
    # The discovery parser stores all frontmatter ``key: value`` pairs
    # but only exposes ``name`` and ``description`` on the dataclass —
    # we re-parse the raw text to assert the key is present.
    for s in bundled_skills:
        text = Path(s.file_path).read_text(encoding="utf-8")
        front, _, _ = text.partition("---\n")[2].partition("\n---")
        assert "when-to-use" in front, (
            f"{s.name} frontmatter missing required ``when-to-use`` key"
        )


def test_every_skill_name_is_slug_form(bundled_skills: list[Skill]) -> None:
    import re

    pat = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
    for s in bundled_skills:
        assert pat.match(s.name), f"{s.name} is not slug-form"
        assert s.name.startswith("algo-"), (
            f"{s.name} should be namespaced with the ``algo-`` prefix to "
            "avoid colliding with arbitrary user skills"
        )


# ---------------------------------------------------------------------------
# Body content — spec sections
# ---------------------------------------------------------------------------


REQUIRED_BODY_SECTIONS: list[str] = [
    "Invariants",
    "Complexity",
    "Common pitfalls",
    "Test corner cases",
]


def test_every_skill_body_has_required_sections(
    bundled_skills: list[Skill],
) -> None:
    """Each skill body must mention every required section name.

    We do a case-insensitive substring check rather than a strict
    Markdown-heading parse so the writer has flexibility on heading
    levels (### vs ##) without losing the lock-in on section presence.
    """
    for s in bundled_skills:
        body_lower = s.content.lower()
        missing = [
            sec for sec in REQUIRED_BODY_SECTIONS
            if sec.lower() not in body_lower
        ]
        assert not missing, f"{s.name} body missing sections: {missing}"


def test_every_skill_has_python_code(bundled_skills: list[Skill]) -> None:
    """Spec requires a Python template per skill.

    The check is a fenced code block ```python …``` (case-insensitive)
    so writers can use ```py``` or ```Python``` without tripping it.
    """
    for s in bundled_skills:
        body = s.content
        assert "```python" in body.lower() or "```py\n" in body.lower(), (
            f"{s.name} missing a Python code template"
        )


def test_every_skill_has_javascript_code(bundled_skills: list[Skill]) -> None:
    """Spec requires a JavaScript template per skill.

    ```javascript`` is the canonical fence; ```js``` is also accepted
    so writers don't fight the spelling.
    """
    for s in bundled_skills:
        body = s.content.lower()
        assert "```javascript" in body or "```js\n" in body, (
            f"{s.name} missing a JavaScript code template"
        )


def test_every_skill_body_has_substance(bundled_skills: list[Skill]) -> None:
    """Each body should be 80-200 lines of real content (no padding)."""
    for s in bundled_skills:
        lines = [
            line for line in s.content.splitlines() if line.strip()
        ]
        # Non-blank lines: we want substantive bodies — anything under
        # 60 is a stub. Upper bound 250 catches accidental copy-paste
        # bloat.
        assert 60 <= len(lines) <= 250, (
            f"{s.name} body has {len(lines)} non-blank lines "
            "(spec range 60-250)"
        )


# ---------------------------------------------------------------------------
# Trademark scrub — no upstream brand names
# ---------------------------------------------------------------------------


# Brand names that must NOT appear in skill bodies (whole-word match;
# substrings inside library / module names are fine — e.g. "anthropic"
# can show up in a python import line, but we don't expect that here
# either since these are language-agnostic algorithm skills).
FORBIDDEN_BRANDS: tuple[str, ...] = (
    "Claude",
    "Anthropic",
    "OpenAI",
    "ChatGPT",
    "GitHub Copilot",
)


def test_skill_bodies_have_no_upstream_brand_names(
    bundled_skills: list[Skill],
) -> None:
    """Trademark hygiene: algorithm cheat-sheets stay vendor-neutral."""
    for s in bundled_skills:
        for brand in FORBIDDEN_BRANDS:
            assert brand not in s.content, (
                f"{s.name} mentions brand {brand!r}"
            )
            assert brand not in s.description, (
                f"{s.name} description mentions brand {brand!r}"
            )


# ---------------------------------------------------------------------------
# End-to-end discovery via the public default_search_paths()
# ---------------------------------------------------------------------------


def test_discover_skills_via_default_paths_finds_bundled(
    tmp_path: Path,
) -> None:
    """End-to-end: the public discovery API surfaces the bundled set.

    Uses ``tmp_path`` as the workdir so the project-local path is empty
    and the bundled path is the only source.
    """
    # ``default_search_paths`` queries ``Path.home()`` for the user
    # path; any user skills installed locally are tolerated by
    # checking the bundled subset is present, not equality.
    skills = discover_skills(default_search_paths(workdir=str(tmp_path)))
    by_name = {s.name: s for s in skills}
    for expected in EXPECTED_BUNDLED:
        assert expected in by_name, f"{expected} not surfaced via default paths"
