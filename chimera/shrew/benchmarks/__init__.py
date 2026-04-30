"""Small-model benchmark harness for ``chimera shrew bench``.

Wires two third-party benchmarks into the standard
:class:`chimera.eval.harness.Benchmark` interface so they can drive a
shrew Agent:

* :class:`~chimera.shrew.benchmarks.aider_polyglot.AiderPolyglot` — the
  Exercism polyglot exercise suite (per-language code-edit tasks scored
  by either expected-file diff-match or by running the language test
  command).
* :class:`~chimera.shrew.benchmarks.gaia.GAIA` — the GAIA research-task
  suite (open-domain question answering scored by exact / fuzzy
  string-match against a gold answer).

Both adapters refuse to vendor their upstream datasets (license unclear)
and expect users to stage them locally under
``~/.chimera/datasets/aider-polyglot/`` and ``~/.chimera/datasets/gaia/``.
When the dataset is absent the adapters return an empty task list and
the CLI dispatch surfaces a friendly setup hint with a non-zero exit.

Trademark hygiene: the adapters never name the upstream small-model
coding agent in source / docs / help text; ``Aider Polyglot`` and
``GAIA`` are third-party benchmark names — not the upstream brand — so
naming them is fine.
"""

from __future__ import annotations

__all__: list[str] = []
