"""Published numbers must cite a receipt that exists and says what they claim.

Docs are the one surface with no compiler. Three failures shipped here. The
first two were found by an owner-requested audit rather than by any gate; the
third was found by this file, while fixing them:

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
* A third, found on 2026-07-28 while fixing the first two, and the reason this
  file now asks git rather than the filesystem: ``data/`` is **gitignored**.
  Receipts are force-added one at a time, so a benchmark run leaves its receipt
  on the author's disk and outside the repo by default. At that moment the
  checkout had **63** files in ``data/`` and **34** in the repo. Four published
  citations pointed into that 29-file gap — including the only evidence that
  ``--env swe-modal`` had ever run. Every one of them resolved on one machine
  and nowhere else, and a gate built on ``Path.exists()`` agreed with them
  there, which is the worst possible place for it to agree.

Both of the first two are the same class the benchmark canary exists for
(``docs/guides/benchmark-canary.md``), one layer out: the canary proves a
*grader* is honest, this proves a *citation* is. Neither the canary nor the
observatory's integrity aborts could catch these, because hand-written prose
bypasses the generator entirely.

Scope, stated honestly: this checks that cited receipt files are **committed**
(not merely present on the author's disk) and that retracted benchmarks are not
quoted as scores. It cannot verify that an arbitrary prose number matches its
receipt — the shapes are too varied — so it is a floor, not a ceiling. Two
known gaps in that floor are pinned by tests rather than hidden: a withdrawal
marker clears its whole line, and a score whose line does not name its
benchmark (a table cell under a benchmark-named column header, say) is not
attributed to it at all.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path



ROOT = Path(__file__).resolve().parents[2]

#: Prose files that publish benchmark numbers to users.
#:
#: This was five hand-picked entries — ``docs/benchmarks``, ``docs/progress``,
#: ``docs/releases`` and the two roots — covering 327 of the repo's markdown
#: files. The other 248 were not clean; they were unread. Widening to all of
#: ``docs/`` surfaced thirteen sites at once, including three receipts absent
#: from disk entirely and one live retracted score in ``docs/specs`` that the
#: 2026-07-28 LiveCodeBench retraction simply never reached.
#:
#: **All of** ``docs/``, not the subdirectories where the violations happened to
#: be. Naming four directories would have re-created the same hole one level
#: down, and the measurement says it costs nothing: sweeping every remaining
#: ``docs/`` subtree — guides, playbooks, plans, ``_archive``, the seven
#: codename dirs — adds zero further violations. An unscanned directory is
#: indistinguishable from a clean one, so the only defensible scope is the one
#: that needs no argument about which prose "counts".
#:
#: Known and deliberate exclusion: ``CHANGELOG.md``. It cites a receipt that is
#: in no commit, and carries two retracted-score lines inside **shipped**
#: 0.9.2/0.9.2.1 entries — bringing it in means editing released history to
#: satisfy a gate, which is an owner call and not a test author's. Excluded, but
#: not unwatched: see ``_CHANGELOG_UNBACKED``, because "declared exclusion" is
#: one rename away from "unscanned directory".
_PUBLISHED = (
    "README.md",
    "docs",
    "site/src/content/docs",
)

#: Receipts ``CHANGELOG.md`` cites that git does not track — a **ratchet**, not
#: an inventory. The file is outside ``_PUBLISHED`` (above), so nothing else in
#: this repo would notice the list growing; naming the existing debt lets the
#: paired test fail on anything beyond it. Old debt named, new debt red.
#:
#: Pinned by filename rather than line number on purpose. The changelog grows at
#: the top every batch, so a line number recorded here is wrong by the next
#: merge while still reading as authoritative — the exact failure mode of a
#: comment standing in for a check.
#:
#: Earned the hard way: the changelog entry announcing that unbacked citations
#: are disclosed at the claim introduced three fresh unbacked citations, by
#: naming the three filenames without the marker. Out of scope, so nothing
#: caught it.
_CHANGELOG_UNBACKED = (
    # The 84% LiveCodeBench figure's receipt. Located on the author's disk
    # holding exactly `passed: 21, total: 25`, so the number was real; in no
    # commit on any branch, and deliberately never committed, because
    # `livecodebench` is retracted and committing it would make a retracted
    # score look evidenced.
    "data/depth-lcb-coding-agent-glm52.json",
)

#: Documents exempt from the **retracted-score rule only** — never from the
#: receipt rule, and never from anything else.
#:
#: Widening the scope caught a class the narrow scope never had to think about:
#: prose that quotes a withdrawn number *in order to explain why it is wrong*.
#: A retraction has to be argued somewhere, and the argument cannot be made
#: without stating what is being retracted. Left unexempted, the gate's loudest
#: complaints would be aimed at the two documents doing the most honest work in
#: the repo, and the pressure would be to delete the analysis rather than the
#: claim.
#:
#: The exemption is per-document and enumerated, never per-directory: a named
#: entry is a line in a diff a reviewer can challenge, whereas an unscanned
#: directory is invisible. Two guards below keep the list from rotting — every
#: entry must exist, and every entry must still *need* the exemption, so an
#: entry that stops earning its place fails the suite instead of quietly
#: shielding whatever the document grows into next.
#:
#: Note what is **not** here. ``docs/guides/benchmark-canary.md`` quotes the
#: same withdrawn ≥18.9% and needs no entry: it does so inside a
#: claim-versus-truth table, which ``_CORRECTION_TABLE_HEADER`` already clears
#: structurally. Structure beats enumeration wherever a document can be
#: restructured to earn its exemption — this list is for the ones that cannot.
_RETRACTION_EXPLAINERS = (
    # The diagnosis that produced the retraction. Its entire subject is why
    # `livecodebench` 44/50 (88.0%) is not a LiveCodeBench score and why the
    # published ≥18.9% is not one either: 63 of 175 staged tasks are
    # `functional` + starter_code while the runner executes
    # `python solution.py < stdin`, so 36% of the denominator cannot pass under
    # any answer. Both numbers appear on five lines because the argument is a
    # comparison between them. Delete them and the document says nothing.
    "docs/notes/bench-diagnosis-darklight1.md",
    # The receipt audit's own gap list. Its first ranked finding *is* the
    # violation — "LiveCodeBench 84% on the public site" — quoted so the entry
    # names what was published and where. A gate that forbade an audit from
    # reciting the claim it caught would forbid the audit.
    "docs/reference/capability-matrix.md",
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

#: The one way to name a receipt this repo does not have.
#:
#: Some published numbers cite a file that was never committed. Deleting the
#: citation would hide that a number is unbacked; leaving it bare asserts
#: evidence that cannot be produced. So the claim stays, the missing receipt
#: stays named, and the line carries this marker — which makes the whole
#: inventory greppable::
#:
#:     grep -rn '⊘ NO RECEIPT' README.md docs/ site/
#:
#: Deliberately a fixed token rather than a phrase list: nothing trips it by
#: accident, and adding one is a visible edit a reviewer sees in the diff.
#: It exempts only the line it appears on.
_NO_RECEIPT = re.compile(r"⊘\s*NO RECEIPT", re.I)


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


def _published_files(root: Path = ROOT) -> list[Path]:
    """Every markdown file in scope under *root*.

    *root* is a parameter purely so the scope-widening tests can build a
    throwaway tree with the same shape. Seeding a violation into the real
    ``docs/`` to prove the gate still fires would mean a tracked directory
    briefly containing a deliberate lie, and a crashed test leaving it there.
    """
    out: list[Path] = []
    for entry in _PUBLISHED:
        p = root / entry
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out.extend(sorted(p.rglob("*.md")))
    return out


def _tracked_paths() -> frozenset[str] | None:
    """Every path git tracks, or ``None`` when git cannot answer.

    Existence on disk is the wrong question to ask about a receipt, and asking
    it was a real defect in the first version of this gate.

    ``data/`` is **gitignored**. Receipts get in one at a time with
    ``git add -f``, so a run that produces a receipt leaves it on the author's
    disk and *outside* the repo unless someone force-adds it. On the machine
    that did the run, ``Path.exists()`` says yes; in CI, in a fresh clone, and
    in any other git worktree of the same repo, the file is simply not there.
    A filesystem check therefore returns a different verdict per checkout, and
    the direction it errs is the dangerous one: it goes **green** exactly where
    the untracked file lives, i.e. for the one person able to add it.

    That is not theoretical. When this pass began, ``data/`` held 63 files on
    the main checkout and 34 in the repo — 29 receipts that every doc could
    cite and no reader could open. Four such citations were shipped.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True,
            check=True,
            text=True,
            timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return frozenset(p for p in out.split("\0") if p)


def _missing_citations(
    text: str, tracked: frozenset[str] | None = None
) -> list[tuple[int, str]]:
    """``(line number, path)`` for every cited receipt a reader cannot open.

    *tracked* is the set from :func:`_tracked_paths`. When it is supplied a
    citation counts as backed only if git **tracks** it; when it is ``None``
    the check degrades to filesystem existence, which is weaker for the reason
    documented above and is used only as a fallback where git cannot be run.

    A line carrying ``⊘ NO RECEIPT`` is disclosing an absence, not offering
    evidence, so it is skipped — see ``_NO_RECEIPT``.
    """
    out: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if _NO_RECEIPT.search(line):
            continue
        for rel in dict.fromkeys(_RECEIPT.findall(line)):
            backed = rel in tracked if tracked is not None else (ROOT / rel).exists()
            if not backed:
                out.append((i, rel))
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


def _scan_retracted(root: Path = ROOT) -> list[str]:
    """Every ``path:line: text`` in scope that quotes a retracted score.

    Applies ``_RETRACTION_EXPLAINERS``, which is why this is one function rather
    than a loop repeated per caller: an exemption that some callers honour and
    others do not is worse than no exemption, because the gate's verdict then
    depends on which test you happened to run.
    """
    retracted, known = _registries()
    exempt = {root / rel for rel in _RETRACTION_EXPLAINERS}
    out: list[str] = []
    for doc in _published_files(root):
        if doc in exempt:
            continue
        text = doc.read_text(encoding="utf-8", errors="replace")
        out.extend(
            f"{doc.relative_to(root)}:{i}: {line[:110]}"
            for i, line in _retracted_score_offenders(text, retracted, known)
        )
    return out


def _scan_receipts(root: Path = ROOT) -> list[str]:
    """Every ``path:line -> receipt`` in scope citing a file git does not track.

    Takes no exemption list, deliberately. ``_RETRACTION_EXPLAINERS`` narrows
    the retracted-score rule and *only* that rule: a document earns the right to
    quote a withdrawn number by explaining it, and no amount of explanation
    conjures a receipt into the repo. The one escape here is the per-line
    ``⊘ NO RECEIPT`` marker, which discloses the absence instead of hiding it.
    """
    tracked = _tracked_paths()
    return [
        f"{doc.relative_to(root)}:{i} -> {rel}"
        for doc in _published_files(root)
        for i, rel in _missing_citations(
            doc.read_text(encoding="utf-8", errors="replace"), tracked
        )
    ]


class TestCitedReceiptsExist:
    def test_every_cited_data_file_is_real(self) -> None:
        """A citation pointing at a missing file is worse than no citation.

        It reads as evidence and cannot be checked, so it survives review
        indefinitely.
        """
        missing = _scan_receipts()
        assert not missing, (
            "docs cite receipt files no reader can open:\n  "
            + "\n  ".join(sorted(missing))
            + "\nNote these are checked against `git ls-files`, not the "
            "filesystem: data/ is gitignored, so a receipt sitting in your "
            "working tree is still invisible to CI and to every clone until "
            "`git add -f` puts it in.\nCommit the receipt, stop citing it, "
            "or — if the number is real but its receipt was never committed — "
            "keep both and mark the line '⊘ NO RECEIPT'."
        )

    def test_a_receipt_on_disk_but_untracked_is_still_missing(self) -> None:
        """The whole point of checking git instead of the filesystem.

        Writes a real file into ``data/`` — gitignored, so it lands untracked,
        exactly like a fresh benchmark receipt — and asserts the gate reports a
        citation of it as unbacked *while the file is sitting right there*. The
        second assertion is the one that matters: it pins that the old
        filesystem check would have passed this, so the difference between the
        two is a live, tested property rather than a claim in a comment.
        """
        tracked = _tracked_paths()
        assert tracked is not None, "git could not be run — cannot verify"

        probe = ROOT / "data" / "untracked-probe-for-the-claims-gate.json"
        assert not probe.exists(), f"probe path is not free: {probe}"
        cite = f"Raw data: `data/{probe.name}`"
        probe.write_text('{"passed": 1, "total": 1}\n', encoding="utf-8")
        try:
            assert probe.exists()  # the filesystem is happy
            assert f"data/{probe.name}" not in tracked  # git is not

            # Caught: no reader, reviewer, or CI job can open this file.
            assert _missing_citations(cite, tracked) == [(1, f"data/{probe.name}")]

            # Missed by the check this replaced — the regression being fixed.
            assert _missing_citations(cite, None) == []
        finally:
            probe.unlink(missing_ok=True)

    def test_the_tracked_set_is_real_and_covers_the_receipts(self) -> None:
        # Guard the guard: an empty or bogus set would make the check above
        # fail everything (loudly) or, if it silently became None, nothing.
        tracked = _tracked_paths()
        assert tracked is not None
        assert "README.md" in tracked
        receipts = {p for p in tracked if p.startswith("data/")}
        assert len(receipts) > 20, f"only {len(receipts)} receipts tracked"

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
        offenders = _scan_retracted()
        assert not offenders, (
            "a RETRACTED benchmark is quoted with a score:\n  "
            + "\n  ".join(offenders)
            + "\nRetraction must reach hand-written prose, not just the "
            "generated observatory."
        )

    def test_a_seeded_violation_is_caught(self) -> None:
        # Proof the matcher can fail — a gate that cannot fail is not a gate.
        # This is the exact sentence the site guide shipped.
        retracted, known = _registries()
        bench = sorted(retracted)[0]
        text = f"the agent scored **84% on {bench}**, the best of any agent"
        assert _retracted_score_offenders(text, retracted, known) == [(1, text)]


class TestTheExemptionsStillCatchRealViolations:
    """Every exemption below narrows the gate, so each needs a paired proof.

    An exemption without one is how a gate quietly stops gating: the suite
    stays green and nobody can tell whether that is because the docs are clean
    or because the matcher went blind.
    """

    def test_attribution_clears_only_a_number_another_benchmark_owns(self) -> None:
        retracted, known = _registries()
        bench = sorted(retracted)[0]

        # Cleared: both percentages are explicitly owned by other benchmarks,
        # and the retracted name is only the subject of the sentence after.
        owned = f"mbpp-plus ≥91% and math500 ≥43% ran at cost. The {bench} run"
        assert _retracted_score_offenders(owned, retracted, known) == []

        # NOT cleared: naming another benchmark somewhere on the line does not
        # launder a percentage that the retracted one owns.
        laundered = f"mbpp 99% and {bench} 84% both ran to completion"
        assert _retracted_score_offenders(laundered, retracted, known)

        # NOT cleared: an unowned percentage stays attributed to the retracted
        # benchmark — this is the real modal-cloud-benches.md:77 shape.
        unowned = f"100% on both — the gap is on the harder column ({bench})"
        assert _retracted_score_offenders(unowned, retracted, known)

    def test_a_withdrawal_marker_reaches_only_its_own_line(self) -> None:
        """``_WITHDRAWN`` is line-scoped, and a multi-line strike does not carry.

        Not hypothetical — this is the bug the 2026-07-28 retraction pass hit.
        Striking the "flagship earns premiere" bullet in
        ``modal-cloud-benches.md`` opened ``~~`` on one line and closed it three
        lines later. Markdown renders that struck, so it *looked* retracted, but
        the middle line carried both the benchmark name and ``100%`` with no
        marker of its own and was still a published score. The fix was to reflow
        the claim onto the marked line; widening the exemption to span lines
        would have re-opened the hole for every future retraction.
        """
        retracted, known = _registries()
        bench = sorted(retracted)[0]

        # Cleared: the strike sits on the same line as the score it withdraws.
        same_line = f"~~the agent scored 84% on {bench}~~ **[RETRACTED]**"
        assert _retracted_score_offenders(same_line, retracted, known) == []

        # NOT cleared: the marker opens on an earlier line and closes on a
        # later one. The score in the middle is still live to a reader who
        # copies that line out, and must still be reported.
        spanning = (
            f"~~a conclusion that opens here\n"
            f"and scores 84% on {bench} here\n"
            f"and only closes here~~"
        )
        assert [i for i, _ in _retracted_score_offenders(spanning, retracted, known)] == [2]

    def test_the_withdrawal_exemption_clears_the_whole_line_a_known_limit(self) -> None:
        """Pinned because it is a real hole, not because it is correct.

        ``_WITHDRAWN`` matches anywhere on the line, so a *live* score sharing a
        line with an unrelated withdrawal is exempted. Narrowing it (e.g.
        requiring the marker to precede the percentage) would silently
        un-retract existing prose, so the limit is recorded here instead of
        papered over: this test fails the day someone tightens the rule, which
        is the moment to re-audit every line that currently relies on it.

        The gate's docstring calls itself a floor, not a ceiling. This is one of
        the floorboards.
        """
        retracted, known = _registries()
        bench = sorted(retracted)[0]

        # A published 98% is NOT caught, because "do not cite" appears later on
        # the same line about a different number entirely.
        mixed = f"{bench} scored 98% today; we do not cite the older 18.9% figure"
        assert _retracted_score_offenders(mixed, retracted, known) == []

    def test_correction_table_exemption_requires_the_contrast_header(self) -> None:
        retracted, known = _registries()
        bench = sorted(retracted)[0]
        row = f"| `{bench}` grades via stdin | a lower bound of ≥18.9% | 36% cannot pass |"

        # Cleared: the header declares the columns as claim-versus-truth, so
        # the row prints the withdrawn number beside its correction.
        corrected = "| What broke | What it looked like | What was true |\n|---|---|---|\n" + row
        assert _retracted_score_offenders(corrected, retracted, known) == []

        # NOT cleared: the same row under an ordinary results header. The
        # exemption is the header's doing, not the row's.
        scoreboard = "| Task | Benchmark | Result |\n|---|---|---|\n" + row
        assert _retracted_score_offenders(scoreboard, retracted, known)

        # NOT cleared: a scoreboard row that merely follows a correction table
        # once the table has ended.
        after = corrected + f"\n\nThe agent scored 84% on {bench}.\n"
        assert _retracted_score_offenders(after, retracted, known)

    def test_no_receipt_marker_exempts_only_its_own_line(self) -> None:
        gone = "data/definitely-not-a-real-receipt-42.json"

        # NOT cleared: a bare citation of a file that is not on disk.
        assert _missing_citations(f"Raw data: `{gone}`") == [(1, gone)]

        # Cleared: the same citation disclosed as unbacked.
        assert _missing_citations(f"⊘ NO RECEIPT — `{gone}` was never committed") == []

        # NOT cleared: the marker does not reach a neighbouring line. A
        # document-wide exemption is exactly the hole this gate exists to close.
        two = f"⊘ NO RECEIPT — `{gone}` is absent\nBut see `{gone}` for the run.\n"
        assert _missing_citations(two) == [(2, gone)]

    def test_the_no_receipt_inventory_is_greppable_and_declared(self) -> None:
        # The marker's whole value is that it enumerates unbacked claims. If
        # this list ever empties silently, the exemption has become dead code
        # hiding nothing — or someone deleted the disclosures instead of the
        # claims.
        declared = [
            f"{doc.relative_to(ROOT)}:{i}"
            for doc in _published_files()
            for i, line in enumerate(
                doc.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            )
            if _NO_RECEIPT.search(line)
        ]
        assert declared, (
            "no '⊘ NO RECEIPT' disclosures found. If the unbacked claims were "
            "genuinely backed since, delete this test with the last one."
        )

    def test_a_results_table_header_cannot_match_the_contrast_pattern(self) -> None:
        for header in (
            "| Task | Benchmark | Model | Result | Cost | Wall time | Commit |",
            "| Benchmark | Result | n |",
            "| Column (full n) | Result (lower bound) | ¢/task | Read |",
        ):
            assert not _CORRECTION_TABLE_HEADER.search(header), header


class TestTheWidenedScopeReallyScansAndStillFails:
    """Scope is the half of a gate nobody reviews.

    Every other test here interrogates the matchers. None of them notices if
    ``_PUBLISHED`` quietly stops naming a directory — the suite goes green
    either way, and green is exactly what an unscanned tree looks like. These
    tests read the scope itself.

    They build a throwaway tree with the same layout rather than seeding a
    violation into the real ``docs/``: a gate proving itself by writing a
    deliberate falsehood into a tracked directory is one crashed test away from
    publishing it.
    """

    @staticmethod
    def _tree(tmp_path: Path, rel: str, body: str) -> Path:
        doc = tmp_path / rel
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(body, encoding="utf-8")
        return doc

    def test_a_violation_in_a_newly_covered_directory_goes_red(
        self, tmp_path: Path
    ) -> None:
        """The point of widening: ``docs/specs`` was invisible, now it is not.

        Not hypothetical. ``docs/specs/modal-bench-fanout.md`` published
        "Flagship 100%/100% (only agent to sweep both)" — half of "both" being
        the retracted ``livecodebench`` column — for as long as the scope
        stopped at ``docs/benchmarks``. The 2026-07-28 retraction reached the
        benchmarks write-up and not its own spec, and nothing said so.
        """
        retracted, _ = _registries()
        bench = sorted(retracted)[0]
        for rel in (
            "docs/specs/seeded.md",
            "docs/notes/seeded.md",
            "docs/reference/seeded.md",
            "docs/mink/seeded.md",
            "docs/guides/seeded.md",
            "docs/playbooks/seeded.md",
        ):
            tree = tmp_path / rel.split("/")[1]  # one clean tree per directory
            tree.mkdir(exist_ok=True)
            self._tree(tmp_path, rel, f"The agent scored 84% on {bench}.\n")
            found = _scan_retracted(tmp_path)
            assert found == [f"{rel}:1: The agent scored 84% on {bench}."], (
                f"{rel} is not being scanned — a violation there would ship"
            )
            (tmp_path / rel).unlink()

    def test_an_exempt_document_still_fails_the_receipt_rule(
        self, tmp_path: Path
    ) -> None:
        """The exemption is one rule wide, and this is what holds it there.

        Uses a real entry from ``_RETRACTION_EXPLAINERS`` at its real path, so
        the day someone widens the exemption to cover receipts too — or moves
        the check behind the same skip — this fails.
        """
        retracted, _ = _registries()
        bench = sorted(retracted)[0]
        gone = "data/no-such-receipt-for-the-scope-test.json"
        rel = _RETRACTION_EXPLAINERS[0]
        self._tree(
            tmp_path, rel, f"It looked like 84% on {bench}.\nRaw data: `{gone}`\n"
        )

        # Exempt from the retracted-score rule …
        assert _scan_retracted(tmp_path) == []
        # … and still fully subject to the receipt rule.
        assert _scan_receipts(tmp_path) == [f"{rel}:2 -> {gone}"]

    def test_the_scope_names_no_directory_that_is_gone(self) -> None:
        # A stale entry scans nothing and reports nothing — the failure mode
        # this whole class exists to catch, arriving by rename instead of edit.
        for entry in _PUBLISHED:
            assert (ROOT / entry).exists(), f"_PUBLISHED names a missing path: {entry}"

    def test_the_widened_scope_reaches_the_directories_it_claims(self) -> None:
        files = {str(p.relative_to(ROOT)) for p in _published_files()}
        for d in ("docs/notes", "docs/reference", "docs/specs", "docs/mink"):
            assert any(f.startswith(d + "/") for f in files), f"{d} is not in scope"


class TestTheChangelogExclusionIsWatched:
    """The one file in scope-shaped limbo, held by a ratchet.

    ``CHANGELOG.md`` cannot join ``_PUBLISHED`` without editing shipped release
    entries. Left at that, the exclusion decays into the very thing this gate's
    widening was about: a surface nobody reads, indistinguishable from a clean
    one. So the debt is enumerated and anything past it fails.
    """

    def test_the_changelog_adds_no_unbacked_citation_beyond_the_known_debt(
        self,
    ) -> None:
        tracked = _tracked_paths()
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        found = {rel for _, rel in _missing_citations(text, tracked)}
        new = sorted(found - set(_CHANGELOG_UNBACKED))
        assert not new, (
            "CHANGELOG.md cites receipts git does not track, beyond the debt "
            f"declared in _CHANGELOG_UNBACKED: {new}\nCommit the receipt, stop "
            "citing it, or mark the line '⊘ NO RECEIPT'. Do not extend "
            "_CHANGELOG_UNBACKED to make this pass — that list is history, not "
            "an allowance."
        )

    def test_the_declared_debt_is_still_real(self) -> None:
        # A ratchet with stale teeth is slack: an entry that is no longer cited
        # (or has since been committed) silently widens what "beyond the known
        # debt" means.
        tracked = _tracked_paths()
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        found = {rel for _, rel in _missing_citations(text, tracked)}
        for rel in _CHANGELOG_UNBACKED:
            assert rel in found, (
                f"{rel} is declared as CHANGELOG debt but is no longer an "
                "unbacked citation there — delete the entry"
            )

    def test_the_changelog_is_deliberately_out_of_scope(self) -> None:
        # If it ever joins _PUBLISHED, the ratchet is dead weight and the two
        # retracted-score lines in shipped entries become a hard failure. That
        # is a decision, so it should arrive as a failing test, not a surprise.
        assert (ROOT / "CHANGELOG.md") not in _published_files(), (
            "CHANGELOG.md is now scanned — delete _CHANGELOG_UNBACKED and its "
            "tests, and resolve the retracted-score lines in the shipped "
            "0.9.2 / 0.9.2.1 entries"
        )


class TestTheRetractionExemptionsAreLive:
    """An exemption nobody can see is an unscanned directory with extra steps.

    ``_RETRACTION_EXPLAINERS`` is auditable only while every entry is real and
    every entry is load-bearing. Both halves rot silently: a renamed file makes
    an entry inert, and a rewritten document makes it unnecessary while it goes
    on shielding whatever that document becomes next.
    """

    def test_every_exempt_document_exists(self) -> None:
        for rel in _RETRACTION_EXPLAINERS:
            assert (ROOT / rel).is_file(), f"exempt document is gone: {rel}"

    def test_every_exempt_document_still_needs_its_exemption(self) -> None:
        retracted, known = _registries()
        for rel in _RETRACTION_EXPLAINERS:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
            assert _retracted_score_offenders(text, retracted, known), (
                f"{rel} no longer quotes a retracted score, so its exemption "
                "now protects nothing and hides whatever it grows next — "
                "delete the entry"
            )

    def test_the_exemption_is_enumerated_not_a_prefix(self) -> None:
        # A directory prefix would re-create the hole the widening just closed:
        # every future file under it inherits an exemption nobody chose.
        for rel in _RETRACTION_EXPLAINERS:
            assert rel.endswith(".md"), f"exemptions are files, not trees: {rel}"


class TestTheReceiptRegexReadsWholePaths:
    """Both bugs below made the gate report the wrong file, not just over-fire."""

    def test_a_jsonl_receipt_is_matched_whole(self) -> None:
        # `(?:json|jsonl)` is first-match-wins, so this used to yield
        # `data/x.json` — a file that does not exist — for a citation of a
        # receipt that does. The dangerous direction: a doc citing a MISSING
        # `data/x.jsonl` would pass whenever `data/x.json` happened to exist.
        line = "Raw data: `data/swebench-lite-glm51-results.jsonl`"
        assert _RECEIPT.findall(line) == ["data/swebench-lite-glm51-results.jsonl"]

    def test_a_url_path_is_not_read_as_a_repo_citation(self) -> None:
        url = "https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz"
        assert _RECEIPT.findall(url) == []

    def test_a_real_citation_beside_a_url_is_still_found(self) -> None:
        # The URL guard must not blind the scan to the citation next to it.
        line = f"from {'https://x.test/data/HumanEval.jsonl.gz'} → `data/mbpp-glm-5.1-results.json`"
        assert _RECEIPT.findall(line) == ["data/mbpp-glm-5.1-results.json"]


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
