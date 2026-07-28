"""Run an experiment and keep the evidence.

The public surface is deliberately four names::

    from chimera.experiments import start, resume, list_runs, load_run

``start`` opens a stamped run directory with a provenance manifest, ``resume``
reattaches to one a crash left unfinished, and the two readers back
``chimera experiments list`` / ``show``. Everything a run writes is confined to
its own directory under the path registry's ``experiment-runs`` store, so
``chimera gc`` can reclaim it and nothing lands beside the code.

See ``docs/guides/experiments.md`` for the walkthrough and
``chimera/experiments/run.py`` for the reasoning behind each guarantee.
"""
from chimera.experiments.run import (
    ExperimentError,
    NoSuchRun,
    OutsideRun,
    Run,
    RunInfo,
    git_provenance,
    iter_runs,
    list_runs,
    load_run,
    resume,
    runs_root,
    start,
)

__all__ = [
    "ExperimentError",
    "NoSuchRun",
    "OutsideRun",
    "Run",
    "RunInfo",
    "git_provenance",
    "iter_runs",
    "list_runs",
    "load_run",
    "resume",
    "runs_root",
    "start",
]
