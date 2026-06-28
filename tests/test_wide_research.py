"""Tests for the wide-research adapter (spec parsing + fan-out runner)."""

from __future__ import annotations

import pytest

from chimera.wide_research import (
    OutputField,
    Subtask,
    WideResearchResult,
    WideResearchRunner,
    WideResearchSpec,
    extract_json_output,
    results_to_csv,
    results_to_jsonl,
)

VALID_TOML = """
brief = "Find the current CEO of each company."
name = "find_ceos"
title = "Find CEOs"
target_count = 3
inputs = ["Apple", "Microsoft", "Alphabet"]

prompt_template = "Research the current CEO of {{ input }}."

[[output_schema]]
name = "ceo"
type = "string"
title = "CEO"
description = "Verified name of the current CEO."
"""


def _spec() -> WideResearchSpec:
    spec = WideResearchSpec.from_toml(VALID_TOML)
    spec.validate()
    return spec


# ----------------------------------------------------------------------
# Spec parsing + validation
# ----------------------------------------------------------------------


def test_from_toml_parses_all_fields() -> None:
    spec = _spec()
    assert spec.name == "find_ceos"
    assert spec.title == "Find CEOs"
    assert spec.inputs == ("Apple", "Microsoft", "Alphabet")
    assert spec.target_count == 3
    assert len(spec.output_schema) == 1
    assert spec.output_schema[0] == OutputField(
        name="ceo",
        type="string",
        title="CEO",
        description="Verified name of the current CEO.",
    )
    assert spec.required_fields == ("ceo",)


def test_from_toml_file_roundtrip(tmp_path) -> None:
    p = tmp_path / "job.toml"
    p.write_text(VALID_TOML, encoding="utf-8")
    spec = WideResearchSpec.from_toml_file(p)
    spec.validate()
    assert spec.name == "find_ceos"


def test_optional_keys_carry_defaults() -> None:
    spec = _spec()
    assert spec.parallelism == 0
    assert spec.max_turns == 100
    assert spec.timeout_seconds == 1800
    assert spec.modal_app_name == "wide-research"


def test_unknown_tables_preserved_in_extra() -> None:
    toml = VALID_TOML + '\n[resources]\ncpu = 0.25\nmemory = 512\n'
    spec = WideResearchSpec.from_toml(toml)
    assert spec.extra["resources"] == {"cpu": 0.25, "memory": 512}


def test_target_count_mismatch_rejected() -> None:
    bad = VALID_TOML.replace("target_count = 3", "target_count = 5")
    with pytest.raises(ValueError, match="target_count"):
        WideResearchSpec.from_toml(bad).validate()


def test_non_snake_name_rejected() -> None:
    bad = VALID_TOML.replace('name = "find_ceos"', 'name = "FindCEOs"')
    with pytest.raises(ValueError, match="snake_case"):
        WideResearchSpec.from_toml(bad).validate()


def test_bad_output_field_type_rejected() -> None:
    bad = VALID_TOML.replace('type = "string"', 'type = "blob"')
    with pytest.raises(ValueError, match="invalid type"):
        WideResearchSpec.from_toml(bad).validate()


def test_duplicate_output_field_rejected() -> None:
    dup = VALID_TOML + '\n[[output_schema]]\nname = "ceo"\ntype = "string"\n'
    # target_count still 3 == len(inputs); duplicate field is the failure
    with pytest.raises(ValueError, match="duplicate"):
        WideResearchSpec.from_toml(dup).validate()


def test_missing_required_key_rejected() -> None:
    with pytest.raises(ValueError, match="output_schema"):
        WideResearchSpec.from_toml('inputs = ["a"]\n')


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------


def test_render_interpolates_input_token() -> None:
    spec = _spec()
    assert spec.render("Apple") == "Research the current CEO of Apple."


def test_render_tolerates_whitespace_in_token() -> None:
    spec = WideResearchSpec.from_toml(
        VALID_TOML.replace("{{ input }}", "{{input}}")
    )
    assert spec.render("X") == "Research the current CEO of X."


# ----------------------------------------------------------------------
# Runner fan-out
# ----------------------------------------------------------------------


def test_runner_collects_rows_in_input_order() -> None:
    spec = _spec()
    runner = WideResearchRunner(spec)
    result = runner.run(lambda st: {"ceo": f"CEO-{st.input}"})
    assert result.success_count == 3
    assert [r["_input"] for r in result.rows] == ["Apple", "Microsoft", "Alphabet"]
    assert [r["_index"] for r in result.rows] == [0, 1, 2]
    assert result.rows[0]["ceo"] == "CEO-Apple"


def test_runner_executor_exception_isolated() -> None:
    spec = _spec()

    def flaky(st: Subtask) -> dict[str, str]:
        if st.input == "Microsoft":
            raise RuntimeError("boom")
        return {"ceo": f"CEO-{st.input}"}

    result = WideResearchRunner(spec).run(flaky)
    assert result.success_count == 2
    failures = result.failures
    assert len(failures) == 1
    assert failures[0].input == "Microsoft"
    assert "boom" in failures[0].error


def test_runner_missing_required_field_fails_subtask() -> None:
    spec = _spec()
    result = WideResearchRunner(spec).run(lambda st: {})
    assert result.success_count == 0
    assert all("missing required field" in r.error for r in result.failures)


def test_runner_optional_field_may_be_omitted() -> None:
    toml = VALID_TOML + (
        '\n[[output_schema]]\nname = "hq"\ntype = "string"\n'
        'title = "HQ"\ndescription = "Headquarters."\nrequired = false\n'
    )
    spec = WideResearchSpec.from_toml(toml)
    spec.validate()
    result = WideResearchRunner(spec).run(lambda st: {"ceo": "x"})
    assert result.success_count == 3


def test_runner_parallelism_one_is_deterministic() -> None:
    spec = _spec()
    runner = WideResearchRunner(spec, max_workers=1)
    assert runner.max_workers == 1
    result = runner.run(lambda st: {"ceo": st.input})
    assert result.success_count == 3


def test_runner_default_workers_capped() -> None:
    spec = _spec()  # parallelism 0 -> min(len(inputs), DEFAULT_MAX_WORKERS)
    assert WideResearchRunner(spec).max_workers == 3


def test_subtasks_render_every_input() -> None:
    spec = _spec()
    subs = WideResearchRunner(spec).subtasks()
    assert [s.index for s in subs] == [0, 1, 2]
    assert subs[0].prompt == "Research the current CEO of Apple."


# ----------------------------------------------------------------------
# JSON extraction
# ----------------------------------------------------------------------


def test_extract_plain_json() -> None:
    assert extract_json_output('{"ceo": "Tim Cook"}') == {"ceo": "Tim Cook"}


def test_extract_from_fenced_block_with_prose() -> None:
    text = 'Here is the result:\n```json\n{"ceo": "Tim Cook"}\n```\nDone.'
    assert extract_json_output(text) == {"ceo": "Tim Cook"}


def test_extract_prefers_field_match_among_multiple() -> None:
    text = 'debug {"status": "ok"} then answer {"ceo": "Satya"}'
    assert extract_json_output(text, fields=["ceo"]) == {"ceo": "Satya"}


def test_extract_handles_nested_objects() -> None:
    text = 'final {"ceo": "x", "meta": {"verified": true}}'
    assert extract_json_output(text) == {"ceo": "x", "meta": {"verified": True}}


def test_extract_returns_empty_when_no_json() -> None:
    assert extract_json_output("no structured output here") == {}


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------


def test_results_to_jsonl() -> None:
    spec = _spec()
    result = WideResearchRunner(spec).run(lambda st: {"ceo": st.input})
    lines = results_to_jsonl(result).strip().splitlines()
    assert len(lines) == 3
    import json as _json

    assert _json.loads(lines[0]) == {"_index": 0, "_input": "Apple", "ceo": "Apple"}


def test_results_to_csv_has_schema_ordered_header() -> None:
    spec = _spec()
    result = WideResearchRunner(spec).run(lambda st: {"ceo": st.input})
    csv_text = results_to_csv(result, spec)
    header = csv_text.splitlines()[0]
    assert header == "_index,_input,ceo"


def test_empty_inputs_runner_returns_empty_result() -> None:
    # An empty-inputs spec can't pass validate(), but the runner must not crash.
    spec = WideResearchSpec(
        brief="b",
        name="n",
        title="t",
        prompt_template="{{ input }}",
        inputs=(),
        output_schema=(OutputField(name="x", type="string"),),
    )
    result = WideResearchRunner(spec).run(lambda st: {"x": "1"})
    assert isinstance(result, WideResearchResult)
    assert result.results == ()
