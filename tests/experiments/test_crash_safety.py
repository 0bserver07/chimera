"""A crashed run keeps its evidence, and ``resume()`` continues it.

Tested with real child processes that really die, not with a mock that pretends
to. The distinction matters: the guarantee is about data reaching the operating
system before the interpreter stops existing, and a mock cannot fail that way.
Two failure modes are covered, and neither runs any Python cleanup —
``atexit`` does not fire, ``finally`` does not run, and Python's own file
buffers are never flushed by the dying process:

* ``os._exit()`` — the process vanishes mid-loop;
* an unhandled ``SIGTERM`` — the ordinary "something stopped this run" signal.

If :meth:`Run.jsonl` merely buffered, both tests would find an empty ledger.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chimera.experiments import list_runs, load_run, resume

#: Written by the child once its rows are on disk, so the parent never signals
#: a process that has not done its work yet (a sleep would be a race).
READY = "READY"

_CHILD = '''
import os, sys, time
from chimera.experiments import start

name, mode = sys.argv[1], sys.argv[2]
run = start(name, config={{"model": "glm-5.2", "limit": 5}})
for i in range(3):
    run.jsonl("progress.jsonl", {{"task": "t%d" % i, "ok": True, "cost": 0.01}})
run.write_text("{ready}", "1")

if mode == "abort":
    os._exit(9)          # no atexit, no finally, no buffer flush
while True:
    time.sleep(0.02)     # wait to be signalled
'''.format(ready=READY)


def _wait_for_ready(name: str, timeout: float = 30.0) -> Path:
    """Block until the child's run directory contains its READY marker."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for info in list_runs(name):
            if (info.dir / READY).exists():
                return info.dir
        time.sleep(0.02)
    raise AssertionError(f"child never signalled ready for {name!r}")


def _child_script(tmp_path: Path) -> Path:
    script = tmp_path / "crashing_child.py"
    script.write_text(_CHILD, encoding="utf-8")
    return script


def _assert_ledger_survived(name: str) -> None:
    """Every row written before the crash is on disk; the run reads interrupted."""
    (info,) = list_runs(name)
    rows = [
        json.loads(line)
        for line in (info.dir / "progress.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [r["task"] for r in rows] == ["t0", "t1", "t2"]
    assert info.status == "running", "a crash must not look like a completed run"
    assert info.result is None
    assert info.interrupted is True


def test_a_hard_abort_keeps_every_row_written_before_it(tmp_path: Path) -> None:
    """``os._exit`` mid-loop: the ledger is intact, the status stays ``running``."""
    script = _child_script(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(script), "aborted", "abort"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 9, proc.stderr
    _assert_ledger_survived("aborted")


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_an_unhandled_sigterm_keeps_every_row_written_before_it(
    tmp_path: Path,
) -> None:
    """The ordinary way a long run dies: someone stops it."""
    script = _child_script(tmp_path)
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, our own script
        [sys.executable, str(script), "terminated", "wait"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_ready("terminated")
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:  # pragma: no cover — only on an unexpected hang
            proc.terminate()
            proc.wait(timeout=30)
    _assert_ledger_survived("terminated")


def test_resume_continues_an_interrupted_run_where_the_crash_left_it(
    tmp_path: Path,
) -> None:
    """The whole point: no work is repeated and no work is lost."""
    script = _child_script(tmp_path)
    subprocess.run(
        [sys.executable, str(script), "pb-sweep", "abort"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    crashed = load_run("pb-sweep")
    assert crashed.interrupted is True

    run = resume("pb-sweep")
    assert run.dir == crashed.dir
    assert run.config == {"model": "glm-5.2", "limit": 5}

    done = run.seen("progress.jsonl", key="task")
    assert done == {"t0", "t1", "t2"}

    replayed = []
    for i in range(5):
        task = f"t{i}"
        if task in done:
            continue
        replayed.append(task)
        run.jsonl("progress.jsonl", {"task": task, "ok": True, "cost": 0.01})
    assert replayed == ["t3", "t4"], "resume must not re-run completed work"

    rows = run.rows("progress.jsonl")
    run.finish(
        {
            "passed": sum(1 for r in rows if r["ok"]),
            "total": len(rows),
            "cost_usd": round(sum(r["cost"] for r in rows), 4),
        }
    )

    finished = load_run("pb-sweep")
    assert finished.status == "completed"
    assert finished.interrupted is False
    (cell,) = (finished.result or {})["cells"]
    assert (cell["passed"], cell["total"], cell["cost_usd"]) == (5, 5, 0.05)
    assert len(list_runs("pb-sweep")) == 1, "resume continues, it does not fork"
