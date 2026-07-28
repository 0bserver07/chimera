"""Published numbers must cite a receipt that exists and says what they claim.

Docs are the one surface with no compiler. Two failures shipped here, both
found by an owner-requested audit rather than by any gate:

* ``README.md`` cited ``data/humaneval-glm51-results.json`` for "92.7% pass@1
  (152/164)". That file contains **109/164 = 66.5%** — the retired buggy run.
  The number was right; the citation pointed at a file that contradicted it, so
  anyone auditing the claim would have concluded we inflated it. The real
  receipt is the hyphenated ``humaneval-glm-5.1-results.json``.
* ``site/.../guides/coding-agent.md`` published "84% on LiveCodeBench
  code-generation … the best of any agent on that benchmark" — for a benchmark
  formally **retracted** in ``scripts/render_observatory.py``, on the same site
  whose observatory page shows no LiveCodeBench score at all. The receipt
  existed; the benchmark's grader did not measure what its name claims.

Both are the same class the benchmark canary exists for
(``docs/guides/benchmark-canary.md``), one layer out: the canary proves a
*grader* is honest, this proves a *citation* is. Neither the canary nor the
observatory's integrity aborts could catch these, because hand-written prose
bypasses the generator entirely.

Scope, stated honestly: this checks that cited receipt files **exist** and that
retracted benchmarks are not quoted as scores. It cannot verify that an
arbitrary prose number matches its receipt — the shapes are too varied — so it
is a floor, not a ceiling.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path



ROOT = Path(__file__).resolve().parents[2]

#: Prose files that publish benchmark numbers to users.
_PUBLISHED = (
    "README.md",
    "docs/benchmarks",
    "docs/progress",
    "docs/releases",
    "site/src/content/docs",
)

#: A ``data/<file>.json`` citation inside prose or a markdown table.
#:
#: Two corrections over the first draft, both of which made the gate *report the
#: wrong thing*, not merely over-fire:
#:
#: * ``jsonl`` must come first in the alternation. Python's ``|`` is
#:   first-match-wins, so ``(?:json|jsonl)`` chopped ``data/x.jsonl`` down to
#:   ``data/x.json`` and reported three receipts that exist as missing. The
#:   dangerous direction is the other one: a doc citing a missing
#:   ``data/x.jsonl`` would have **passed** whenever ``data/x.json`` existed.
#:   ``(?![A-Za-z0-9])`` pins the extension to the end of the token.
#: * ``(?<![A-Za-z0-9._/-])`` keeps a match repo-relative. Without it the
#:   upstream URL ``…/human-eval/raw/master/data/HumanEval.jsonl.gz`` was read
#:   as a local citation of ``data/HumanEval.json`` — a file this repo never
#:   claimed to have and never should.
_RECEIPT = re.compile(
    r"(?<![A-Za-z0-9._/-])(data/[A-Za-z0-9._@-]+\.(?:jsonl|json))(?![A-Za-z0-9])"
)

#: The separator row that turns the line above it into a markdown table header.
_TABLE_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")

#: A table header that declares its columns as claim-versus-truth. Rows under
#: such a header exist to print the withdrawn number *next to* the correction —
#: the repo's documented way of recording a fabrication
#: (``docs/guides/benchmark-canary.md``), which is the opposite of republishing
#: it. Anchored to the header row on purpose: a results table
#: (``| Task | Benchmark | Model | Result |``) cannot match, so this cannot
#: quietly exempt a scoreboard.
_CORRECTION_TABLE_HEADER = re.compile(
    r"\|\s*what\s+(?:it\s+)?(?:looked\s+like|was\s+true)\s*\|", re.I
)

#: Any percentage a reader would take as a result.
_PERCENT = re.compile(r"\b\d{1,3}(?:\.\d+)?\s*%")

#: Same-line phrasing that marks a number as withdrawn rather than published.
_WITHDRAWN = re.compile(r"retract|withdraw|previously reported|do not cite|⊘|~~", re.I)


def _load_observatory():
    """Import the generator by path (scripts/ is not an importable package)."""
    spec = importlib.util.spec_from_file_location(
        "render_observatory", ROOT / "scripts" / "render_observatory.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["render_observatory"] = mod
    spec.loader.exec_module(mod)
    return mod


def _registries() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(retracted, known)`` benchmark ids, read from the generator.

    Both come from ``scripts/render_observatory.py`` so this gate and the page
    it guards cannot drift: retracting a benchmark there arms this test here.
    """
    obs = _load_observatory()
    retracted = tuple(obs.RETRACTED)
    assert retracted, "RETRACTED registry is empty — was a fix verified?"
    known = tuple({*obs._BENCH_ORDER, *retracted})
    return retracted, known


def _published_files() -> list[Path]:
    out: list[Path] = []
    for entry in _PUBLISHED:
        p = ROOT / entry
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out.extend(sorted(p.rglob("*.md")))
    return out


def _owner_of(line_lower: str, upto: int, known: tuple[str, ...]) -> str | None:
    """The benchmark named closest before *upto*, or ``None`` if none is."""
    best: tuple[int, int] | None = None
    winner: str | None = None
    for name in known:
        at = line_lower.rfind(name, 0, upto)
        if at < 0:
            continue
        # Later mention wins; on a tie the longer name wins, so "mbpp-plus"
        # beats the "mbpp" nested inside it.
        key = (at, len(name))
        if best is None or key > best:
            best, winner = key, name
    return winner


def _quotes_a_score_for(line: str, bench: str, known: tuple[str, ...]) -> bool:
    """Whether *line* publishes a percentage a reader would read as *bench*'s.

    Attribution is deliberately narrow. A percentage is cleared only when some
    **other** benchmark the observatory knows explicitly owns it earlier on the
    same line, which is the real shape in ``modal-cloud-benches.md``::

        mbpp-plus ≥91% and math500 ≥43% ran at full-work cost. **The
        livecodebench reasoning in this section is retained …

    where 91% is mbpp-plus's and 43% is math500's, and ``livecodebench`` is
    only named as the subject of the withdrawal that follows. An *unowned*
    percentage stays attributed to *bench* — ``100% on both … (LiveCodeBench)``
    is still a violation — so the rule cannot launder an unlabelled score.
    """
    low = line.lower()
    for m in _PERCENT.finditer(line):
        owner = _owner_of(low, m.start(), known)
        if owner is None or owner == bench:
            return True
    return False


def _retracted_score_offenders(
    text: str, retracted: tuple[str, ...], known: tuple[str, ...]
) -> list[tuple[int, str]]:
    """Lines that publish a score for a retracted benchmark, with line numbers."""
    offenders: list[tuple[int, str]] = []
    in_correction_table = False
    prev = ""
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if _TABLE_SEPARATOR.match(stripped):
            # The line above a `|---|` row is that table's header.
            in_correction_table = bool(_CORRECTION_TABLE_HEADER.search(prev))
        elif not stripped.startswith("|"):
            in_correction_table = False
        prev = line

        low = line.lower()
        for bench in retracted:
            if bench not in low:
                continue
            if not _quotes_a_score_for(line, bench, known):
                continue
            # Withdrawals are allowed to state the number they withdraw …
            if _WITHDRAWN.search(line):
                continue
            # … and so is a row of a table that prints the claim beside the truth.
            if in_correction_table:
                continue
            offenders.append((i, stripped))
            break
    return offenders


class TestCitedReceiptsExist:
    def test_every_cited_data_file_is_real(self) -> None:
        """A citation pointing at a missing file is worse than no citation.

        It reads as evidence and cannot be checked, so it survives review
        indefinitely.
        """
        missing: list[str] = []
        for doc in _published_files():
            text = doc.read_text(encoding="utf-8", errors="replace")
            for rel in set(_RECEIPT.findall(text)):
                if not (ROOT / rel).exists():
                    missing.append(f"{doc.relative_to(ROOT)} -> {rel}")
        assert not missing, (
            "docs cite receipt files that do not exist:\n  "
            + "\n  ".join(sorted(missing))
            + "\nEither add the receipt or stop citing it."
        )

    def test_the_scan_actually_finds_citations(self) -> None:
        # Guard the guard: a broken regex would make the test above vacuous.
        found = sum(
            len(set(_RECEIPT.findall(d.read_text(encoding="utf-8", errors="replace"))))
            for d in _published_files()
        )
        assert found > 10, f"only {found} citations matched — the regex is broken"


class TestRetractedBenchmarksAreNotQuoted:
    """A retraction that only covers the generated page is not a retraction."""

    def test_no_published_page_quotes_a_retracted_benchmark_score(self) -> None:
        retracted, known = _registries()
        offenders: list[str] = []
        for doc in _published_files():
            text = doc.read_text(encoding="utf-8", errors="replace")
            offenders.extend(
                f"{doc.relative_to(ROOT)}:{i}: {line[:110]}"
                for i, line in _retracted_score_offenders(text, retracted, known)
            )
        assert not offenders, (
            "a RETRACTED benchmark is quoted with a score:\n  "
            + "\n  ".join(offenders)
            + "\nRetraction must reach hand-written prose, not just the "
            "generated observatory."
        )

    def test_a_seeded_violation_is_caught(self) -> None:
        # Proof the matcher can fail — a gate that cannot fail is not a gate.
        obs = _load_observatory()
        bench = sorted(obs.RETRACTED)[0]
        line = f"the agent scored **84% on {bench}**, the best of any agent"
        assert any(b in line.lower() for b in obs.RETRACTED)
        assert re.search(r"\b\d{1,3}(?:\.\d+)?\s*%", line)
        assert not re.search(r"retract|withdraw|previously reported|⊘", line, re.I)


class TestKnownRegressions:
    """The two exact failures that motivated this file."""

    def test_readme_humaneval_citation_points_at_the_matching_receipt(self) -> None:
        import json

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        row = next(
            (l for l in readme.splitlines() if "HumanEval (164" in l and "92.7" in l),
            None,
        )
        assert row, "the HumanEval headline row moved — re-point this test"
        cited = _RECEIPT.search(row)
        assert cited, f"no receipt cited in: {row}"
        data = json.loads((ROOT / cited.group(1)).read_text())
        passed, total = data.get("passed"), data.get("total")
        assert (passed, total) == (152, 164), (
            f"README claims 92.7% (152/164) but cites {cited.group(1)}, which "
            f"contains {passed}/{total}"
        )
