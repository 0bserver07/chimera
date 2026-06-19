"""Tests for RAG-augmented repair: missing-symbol parsing, Cargo dep parsing,
and the doc-injection path in rebuild(). All offline (no network, no LLM)."""
from types import SimpleNamespace

from chimera.eval.benchmarks.programbench_rebuild import GradeOutcome, rebuild
from chimera.eval.benchmarks.rebuild_docs import (
    DocQuery,
    crates_from_cargo_toml,
    parse_missing_symbols,
)


# ---------------------------------------------------------------------------
# parse_missing_symbols
# ---------------------------------------------------------------------------


def test_parse_rust_method_and_assoc_item():
    err = (
        "error[E0599]: no method named `is_encrypted` found for struct `ZipFile<'a>`\n"
        "error[E0599]: no function or associated item named `open` found for struct `ZipArchive<R>`"
    )
    qs = parse_missing_symbols(err, "rust")
    assert DocQuery("is_encrypted", "ZipFile", "rust") in qs
    assert DocQuery("open", "ZipArchive", "rust") in qs


def test_parse_rust_import_crate_and_trait():
    err = (
        "error[E0432]: unresolved import `zip::read::ZipFile`\n"
        "error[E0433]: use of undeclared crate or module `rayon`\n"
        "the trait `From<ZipError>` is not implemented for `MyError`"
    )
    qs = parse_missing_symbols(err, "rust")
    assert DocQuery("ZipFile", "zip", "rust") in qs
    assert DocQuery("", "rayon", "rust") in qs
    assert DocQuery("From", "", "rust") in qs


def test_parse_c_symbols():
    err = (
        "main.c:5: warning: implicit declaration of function 'strlen'\n"
        "/usr/bin/ld: undefined reference to `compute_hash'"
    )
    qs = parse_missing_symbols(err, "c")
    assert DocQuery("strlen", "", "c") in qs
    assert DocQuery("compute_hash", "", "c") in qs


def test_parse_dedupes_and_respects_language():
    err = "no method named `foo` found for struct `Bar`\nno method named `foo` found for struct `Bar`"
    assert len(parse_missing_symbols(err, "rust")) == 1
    # A Rust error yields nothing under a C-only parse.
    assert parse_missing_symbols(err, "c") == []


def test_parse_empty_on_clean_log():
    assert parse_missing_symbols("Compiling foo v0.1.0\nFinished", "rust") == []


# ---------------------------------------------------------------------------
# crates_from_cargo_toml
# ---------------------------------------------------------------------------


def test_crates_inline_table_and_dev_deps():
    toml = (
        "[package]\nname = \"x\"\n\n"
        "[dependencies]\nzip = \"0.6\"\nclap = { version = \"4\", features = [\"derive\"] }\n\n"
        "[dependencies.rayon]\nversion = \"1.7\"\n\n"
        "[dev-dependencies]\ntempfile = \"3\"\n"
    )
    crates = crates_from_cargo_toml(toml)
    assert crates == ["zip", "clap", "rayon", "tempfile"]


def test_crates_ignores_package_keys():
    toml = "[package]\nname = \"x\"\nversion = \"0.1.0\"\nedition = \"2021\"\n"
    assert crates_from_cargo_toml(toml) == []


# ---------------------------------------------------------------------------
# rebuild() doc-injection path
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def complete(self, messages, max_tokens=None, **kwargs):
        self.calls.append({"messages": messages})
        return SimpleNamespace(content=self._responses.pop(0))


class _FakeDocs:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def fetch(self, symbols, crates):
        self.calls.append((symbols, crates))
        return self.text


def _block(path, body):
    return f">>>> FILE: {path}\n{body}\n>>>> ENDFILE"


def test_rebuild_injects_docs_into_repair_prompt():
    provider = _FakeProvider([
        _block("Cargo.toml", '[dependencies]\nzip = "0.6"')
        + "\n" + _block("src/main.rs", "fn main(){ f.is_encrypted(); }"),
        _block("src/main.rs", "fn main(){}"),  # repair
    ])
    outcomes = [
        GradeOutcome(
            False,
            "error[E0599]: no method named `is_encrypted` found for struct `ZipFile`",
            {"passed": 0, "total": 5},
        ),
        GradeOutcome(True, "", {"passed": 5, "total": 5}),
    ]

    def grade(files):
        return outcomes.pop(0)

    docs = _FakeDocs("docs.rs/zip: use ZipArchive::by_index(i)?.decrypt(pw)")
    result = rebuild(
        provider, project="p", language="rust", spec="s",
        grade_fn=grade, max_repair=3, doc_provider=docs,
    )

    assert result.resolved is True
    # The doc provider was called with the parsed symbol + the Cargo crate.
    assert docs.calls[0][0][0].symbol == "is_encrypted"
    assert "zip" in docs.calls[0][1]
    # The fetched docs were spliced into the repair prompt.
    repair_prompt = provider.calls[1]["messages"][0].content
    assert "ZipArchive::by_index(i)?.decrypt(pw)" in repair_prompt


def test_rebuild_no_doc_provider_is_unchanged():
    provider = _FakeProvider([
        _block("src/main.rs", "fn main(){}"),
        _block("src/main.rs", "fn main(){}"),
    ])
    outcomes = [
        GradeOutcome(False, "no method named `x` found for struct `Y`", {"passed": 0, "total": 2}),
        GradeOutcome(True, "", {"passed": 2, "total": 2}),
    ]
    result = rebuild(
        provider, project="p", language="rust", spec="s",
        grade_fn=lambda f: outcomes.pop(0), max_repair=2,
    )
    assert result.resolved is True
    # Without a doc provider, no docs header leaks into the repair prompt.
    assert "RELEVANT LIBRARY DOCS" not in provider.calls[1]["messages"][0].content
