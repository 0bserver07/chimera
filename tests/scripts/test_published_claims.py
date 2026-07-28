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
_RECEIPT = re.compile(r"`?(data/[A-Za-z0-9._@-]+\.(?:json|jsonl))`?")


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


def _published_files() -> list[Path]:
    out: list[Path] = []
    for entry in _PUBLISHED:
        p = ROOT / entry
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out.extend(sorted(p.rglob("*.md")))
    return out


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
        obs = _load_observatory()
        retracted = set(obs.RETRACTED)
        assert retracted, "RETRACTED registry is empty — was a fix verified?"

        # A score next to a retracted benchmark's name, within one line.
        offenders: list[str] = []
        for doc in _published_files():
            for i, line in enumerate(
                doc.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                low = line.lower()
                if not any(b in low for b in retracted):
                    continue
                if not re.search(r"\b\d{1,3}(?:\.\d+)?\s*%", line):
                    continue
                # Withdrawals are allowed to state the number they withdraw.
                if re.search(
                    r"retract|withdraw|previously reported|do not cite|⊘|~~",
                    line,
                    re.I,
                ):
                    continue
                offenders.append(f"{doc.relative_to(ROOT)}:{i}: {line.strip()[:110]}")
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
