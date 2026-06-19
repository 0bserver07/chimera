"""Unit tests for the ProgramBench one-shot + compile-repair rebuild strategy.

The loop is exercised with a fake provider (canned completions) and a fake
grade_fn, so these run with no Docker and no LLM.
"""
import json
from types import SimpleNamespace

from chimera.eval.benchmarks.programbench import ProgramBench, ProgramBenchInstance
from chimera.eval.benchmarks.programbench_rebuild import (
    GradeOutcome,
    assemble_spec,
    build_initial_prompt,
    build_repair_prompt,
    focus_errors,
    parse_file_blocks,
    rebuild,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeProvider:
    """Returns canned completion text in order; records calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def complete(self, messages, max_tokens=None, **kwargs):
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        return SimpleNamespace(content=self._responses.pop(0))


def _block(path, body):
    return f">>>> FILE: {path}\n{body}\n>>>> ENDFILE"


def _grader(outcomes):
    """A grade_fn that returns the given outcomes in order, recording each
    file set it was handed."""
    seen = []

    def grade(files):
        seen.append(dict(files))
        return outcomes.pop(0)

    return grade, seen


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_parse_file_blocks_basic_and_prose():
    text = "blah blah\n" + _block("a.c", "int main(){}") + "\n" + _block("Makefile", "all:")
    files = parse_file_blocks(text)
    assert set(files) == {"a.c", "Makefile"}
    assert files["a.c"].strip() == "int main(){}"


def test_parse_file_blocks_strips_fences():
    text = _block("a.py", "```python\nx = 1\n```")
    files = parse_file_blocks(text)
    assert files["a.py"] == "x = 1\n"


def test_parse_file_blocks_empty_on_prose():
    assert parse_file_blocks("the error is foo; fix the Cargo.toml") == {}


def test_focus_errors_prefers_error_lines_over_download_spam():
    log = "\n".join(
        ["Downloaded zip v0.6.6"] * 60
        + ["error[E0599]: no method named `is_encrypted`"]
    )
    focused = focus_errors(log)
    assert "is_encrypted" in focused
    # Not dominated by the download spam.
    assert focused.count("Downloaded zip") < 60


def test_assemble_spec_skips_git_binary_images(tmp_path):
    inp = tmp_path / "_inputs"
    (inp / ".git").mkdir(parents=True)
    (inp / ".git" / "config").write_text("[core]")
    (inp / "README.md").write_text("# Hello\nspec text")
    (inp / "executable").write_bytes(b"\x7fELF binary")
    (inp / "demo.png").write_bytes(b"\x89PNG")
    spec = assemble_spec(inp)
    assert "spec text" in spec
    assert "ELF" not in spec
    assert "config" not in spec  # .git skipped
    assert "PNG" not in spec


def test_assemble_spec_caps_total_and_prioritizes_docs(tmp_path):
    inp = tmp_path / "_inputs"
    inp.mkdir()
    (inp / "README.md").write_text("THE SPEC: cmd --flag does X")
    # Data-heavy input (e.g. figlet's fonts) must not blow the context.
    for i in range(20):
        (inp / f"font{i}.flf").write_text("FONTDATA " * 4000)
    spec = assemble_spec(inp, max_total_chars=8000)
    assert "THE SPEC: cmd --flag does X" in spec  # README wins priority
    assert len(spec) <= 8200  # total capped despite ~640k of font data


def test_initial_prompt_carries_build_contract():
    prompt = build_initial_prompt("cmatrix", "c", "SPEC HERE")
    assert "compile.sh" in prompt
    assert "./executable" in prompt
    assert "SPEC HERE" in prompt


def test_repair_prompt_includes_files_and_errors():
    prompt = build_repair_prompt("p", "rust", {"src/main.rs": "fn main(){}"}, "error: boom")
    assert "src/main.rs" in prompt
    assert "error: boom" in prompt


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def test_rebuild_resolves_first_attempt():
    provider = FakeProvider([_block("compile.sh", "make") + "\n" + _block("a.c", "x")])
    grade, seen = _grader([GradeOutcome(True, "", {"passed": 3, "total": 3})])
    result = rebuild(
        provider, project="p", language="c", spec="s", grade_fn=grade, max_repair=4
    )
    assert result.resolved is True
    assert len(result.attempts) == 1
    assert len(provider.calls) == 1
    assert len(seen) == 1


def test_rebuild_repairs_and_merges_unchanged_files():
    # Attempt 0: two files, fails. Attempt 1 (repair): re-emits ONLY the changed
    # file — the loop must keep the untouched one (merge, not replace).
    provider = FakeProvider([
        _block("compile.sh", "bad") + "\n" + _block("a.c", "v0"),
        _block("compile.sh", "good"),  # repair re-sends only compile.sh
    ])
    grade, seen = _grader([
        GradeOutcome(False, "compile error", {"passed": 0, "total": 5}),
        GradeOutcome(True, "", {"passed": 5, "total": 5}),
    ])
    result = rebuild(
        provider, project="p", language="c", spec="s", grade_fn=grade, max_repair=3
    )
    assert result.resolved is True
    assert len(result.attempts) == 2
    # The second grade saw BOTH files (a.c preserved) with compile.sh updated.
    assert set(seen[1]) == {"compile.sh", "a.c"}
    assert seen[1]["a.c"].strip() == "v0"
    assert seen[1]["compile.sh"].strip() == "good"
    assert set(result.files) == {"compile.sh", "a.c"}


def test_rebuild_prose_reply_nudges_without_grading():
    # First reply is prose (no file blocks) -> nudge + retry, no grade call.
    provider = FakeProvider([
        "I think the Cargo.toml is wrong, here is why...",
        _block("compile.sh", "make") + "\n" + _block("a.c", "x"),
    ])
    grade, seen = _grader([GradeOutcome(True, "", {"passed": 1, "total": 1})])
    result = rebuild(
        provider, project="p", language="c", spec="s", grade_fn=grade, max_repair=3
    )
    assert result.resolved is True
    assert len(provider.calls) == 2  # prose + real
    assert len(seen) == 1  # graded only once
    assert result.attempts[0].summary.get("error") == "no_file_blocks"


def test_rebuild_exhausts_budget_and_tracks_best():
    provider = FakeProvider([
        _block("a.c", "v0"),
        _block("a.c", "v1"),
    ])
    grade, _ = _grader([
        GradeOutcome(False, "e0", {"passed": 1, "total": 5}),
        GradeOutcome(False, "e1", {"passed": 3, "total": 5}),  # better
    ])
    result = rebuild(
        provider, project="p", language="c", spec="s", grade_fn=grade, max_repair=1
    )
    assert result.resolved is False
    assert len(result.attempts) == 2
    assert result.best_summary == {"passed": 3, "total": 5}


def test_rebuild_passes_max_tokens_to_provider():
    provider = FakeProvider([_block("a.c", "x")])
    grade, _ = _grader([GradeOutcome(True, "", {"passed": 1, "total": 1})])
    rebuild(
        provider, project="p", language="c", spec="s", grade_fn=grade,
        max_repair=0, max_tokens=12345,
    )
    assert provider.calls[0]["max_tokens"] == 12345


def test_rebuild_on_attempt_callback_fires_per_round():
    provider = FakeProvider([_block("a.c", "x"), _block("a.c", "y")])
    grade, _ = _grader([
        GradeOutcome(False, "e", {"passed": 0, "total": 2}),
        GradeOutcome(True, "", {"passed": 2, "total": 2}),
    ])
    seen_attempts = []
    rebuild(
        provider, project="p", language="c", spec="s", grade_fn=grade,
        max_repair=3, on_attempt=seen_attempts.append,
    )
    assert [a.index for a in seen_attempts] == [0, 1]
    assert seen_attempts[-1].resolved is True


# ---------------------------------------------------------------------------
# Adapter wiring: ProgramBench.rebuild_instance (mocked grading — no docker/LLM)
# ---------------------------------------------------------------------------


def test_rebuild_instance_wires_spec_generate_grade_repair(tmp_path):
    run_dir = tmp_path / "runs"
    bench = ProgramBench(run_dir=str(run_dir))
    instance = ProgramBenchInstance(
        instance_id="o__demo.abc1234", repo="o/demo", commit="abc1234", language="c",
    )
    # Pre-populate _inputs (extraction is skipped); .git must be ignored.
    ws = tmp_path / "ws"
    (ws / "_inputs").mkdir(parents=True)
    (ws / "_inputs" / "README.md").write_text("# demo\nPrints a Matrix rain banner.")
    (ws / "_inputs" / ".git").mkdir()
    (ws / "_inputs" / ".git" / "HEAD").write_text("ref: secret")

    provider = FakeProvider([
        _block("compile.sh", "gcc -o executable m.c") + "\n" + _block("m.c", "int main(){}"),
        _block("compile.sh", "gcc -O2 -o executable m.c -lncurses"),  # repair
    ])

    state = {"n": 0}

    def fake_evaluate(task, agent_output, env=None):
        iid = task["instance_id"]
        out = run_dir / iid
        out.mkdir(parents=True, exist_ok=True)
        if state["n"] == 0:
            state["n"] += 1
            (out / f"{iid}.eval.json").write_text(json.dumps({
                "error_code": "compile_failed",
                "error_details": "ld: cannot find -lncurses",
                "log": [{"step": "compile",
                         "output": "cc m.c\n/usr/bin/ld: cannot find -lncurses\n"}],
                "test_results": [],
            }))
            return False
        (out / f"{iid}.eval.json").write_text(json.dumps({
            "error_code": None,
            "test_results": [{"name": "t1", "passed": True}],
        }))
        return True

    bench.evaluate = fake_evaluate  # type: ignore[method-assign]

    result = bench.rebuild_instance(
        instance, provider=provider, workspace=ws, max_repair=2,
        pull_image=False, extract_artifacts=False, runtime_check=False,
    )

    assert result.resolved is True
    assert len(result.attempts) == 2
    # Spec was assembled from README; .git content never leaked into the prompt.
    first_prompt = provider.calls[0]["messages"][0].content
    assert "Matrix rain banner" in first_prompt
    assert "secret" not in first_prompt
    # The repair prompt carried the focused compile error from the eval.json.
    repair_prompt = provider.calls[1]["messages"][0].content
    assert "cannot find -lncurses" in repair_prompt
    # The final candidate tree (merged) was written + packaged.
    assert (ws / "compile.sh").exists()
    assert (ws / "m.c").exists()
    assert (ws / "submission.tar.gz").exists()
