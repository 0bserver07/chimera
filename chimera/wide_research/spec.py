"""wide-research job specification — parse + validate the Datacurve TOML schema.

:class:`WideResearchSpec` mirrors the wide-research job config (upstream
``skills/wide-research/references/CONFIG.md``): a ``brief`` plus a list of
``inputs``, each turned into one sub-agent subtask by interpolating
``{{ input }}`` into ``prompt_template``, with each subtask's result shaped by
``output_schema``.

This is the spec/validation half of Chimera's wide-research adapter; the
fan-out execution lives in :mod:`chimera.wide_research.runner`.  Parsing uses
the stdlib :mod:`tomllib` (Python 3.11+), so the core stays dependency-free.

Example:
    ```python
    from chimera.wide_research import WideResearchSpec

    spec = WideResearchSpec.from_toml_file("find_ceos.toml")
    spec.validate()
    print(spec.render(spec.inputs[0]))
    ```
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# snake_case identifier (job name + output field names)
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
_FIELD_TYPES = frozenset({"string", "number", "boolean", "file", "directory"})
# Matches `{{ input }}` with arbitrary inner whitespace (the one interpolation
# the upstream prompt_template guarantees). A minimal subset of Jinja2.
_INPUT_TOKEN = re.compile(r"\{\{\s*input\s*\}\}")


@dataclass(frozen=True)
class OutputField:
    """One column of a subtask's structured output.

    Attributes:
        name: ``snake_case`` field key the agent must set via ``submit``.
        type: One of ``string``/``number``/``boolean``/``file``/``directory``.
        title: Human-readable label.
        description: What the field is (guides the agent).
        format: Free-text format hint (optional).
        required: When ``True`` (default), a subtask omitting this field is
            rejected; set ``False`` to let the agent leave it out.
    """

    name: str
    type: str
    title: str = ""
    description: str = ""
    format: str = ""
    required: bool = True

    def validate(self) -> None:
        """Raise :class:`ValueError` if the field is malformed."""
        if not _SNAKE.match(self.name):
            raise ValueError(
                f"output_schema field name {self.name!r} must be snake_case"
            )
        if self.type not in _FIELD_TYPES:
            raise ValueError(
                f"output_schema field {self.name!r} has invalid type {self.type!r}; "
                f"expected one of {sorted(_FIELD_TYPES)}"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutputField:
        """Build an :class:`OutputField` from one ``[[output_schema]]`` table."""
        try:
            name = data["name"]
            ftype = data["type"]
        except KeyError as exc:
            raise ValueError(
                f"output_schema entry missing required key {exc.args[0]!r}: {data!r}"
            ) from exc
        return cls(
            name=str(name),
            type=str(ftype),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            format=str(data.get("format", "")),
            required=bool(data.get("required", True)),
        )


@dataclass(frozen=True)
class WideResearchSpec:
    """A parsed, validatable wide-research job.

    The six required keys (``brief``, ``name``, ``title``, ``prompt_template``,
    ``target_count``, ``inputs``) plus ``output_schema`` come straight from the
    upstream schema; the optional keys carry their documented defaults.

    Attributes:
        brief: One-sentence summary of the operation.
        name: ``snake_case`` identifier, used in output paths.
        title: Human-readable label.
        prompt_template: Jinja2-flavoured template; ``{{ input }}`` is
            interpolated per subtask (see :meth:`render`).
        inputs: One subtask per element.
        output_schema: Structured-output column definitions.
        target_count: Must equal ``len(inputs)`` (guards truncated pastes).
        model: Model id the executor should resolve (optional).
        parallelism: Max concurrent subtasks; ``0`` means "unbounded" (the
            runner applies a safe default cap).
        max_turns: Per-subtask step budget.
        timeout_seconds: Per-subtask wall-clock budget (not whole-batch).
        modal_app_name: Sandbox app name (carried through for execution).
        output_dir: Base directory for run artifacts (optional).
        extra: Any other top-level tables (``resources``/``image``/``mount_files``
            /``secrets``/…) preserved verbatim for the execution layer.
    """

    brief: str
    name: str
    title: str
    prompt_template: str
    inputs: tuple[str, ...]
    output_schema: tuple[OutputField, ...]
    target_count: int = 0
    model: str = ""
    parallelism: int = 0
    max_turns: int = 100
    timeout_seconds: int = 1800
    modal_app_name: str = "wide-research"
    output_dir: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Validate against the upstream schema rules.

        Raises:
            ValueError: On any missing/empty required field, a
                ``target_count`` that disagrees with ``len(inputs)``, a
                non-``snake_case`` name, or a malformed ``output_schema``.
        """
        for key in ("brief", "name", "title", "prompt_template"):
            if not str(getattr(self, key)).strip():
                raise ValueError(f"wide-research spec missing required {key!r}")
        if not _SNAKE.match(self.name):
            raise ValueError(f"spec name {self.name!r} must be snake_case")
        if not self.inputs:
            raise ValueError("wide-research spec needs at least one input")
        if self.target_count != len(self.inputs):
            raise ValueError(
                f"target_count ({self.target_count}) != len(inputs) "
                f"({len(self.inputs)}) — guard against a truncated paste"
            )
        if not self.output_schema:
            raise ValueError("wide-research spec needs a non-empty output_schema")
        seen: set[str] = set()
        for f in self.output_schema:
            f.validate()
            if f.name in seen:
                raise ValueError(f"duplicate output_schema field {f.name!r}")
            seen.add(f.name)
        if self.parallelism < 0:
            raise ValueError("parallelism must be >= 0 (0 = unbounded)")

    @property
    def required_fields(self) -> tuple[str, ...]:
        """Names of the output fields a subtask must provide."""
        return tuple(f.name for f in self.output_schema if f.required)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self, value: str) -> str:
        """Render ``prompt_template`` for one input by interpolating ``{{ input }}``.

        Only the ``{{ input }}`` token is substituted — a deliberate minimal
        subset of the upstream Jinja2 templating, sufficient for the documented
        configs and free of a Jinja dependency.

        Args:
            value: The input string for this subtask.

        Returns:
            The rendered prompt.
        """
        return _INPUT_TOKEN.sub(lambda _: value, self.prompt_template)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_toml(cls, text: str) -> WideResearchSpec:
        """Parse a :class:`WideResearchSpec` from a TOML string.

        Does not call :meth:`validate` — construct then validate so callers can
        inspect a partially-valid spec if they choose.

        Raises:
            ValueError: If the TOML is malformed or a required key is absent.
        """
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"invalid wide-research TOML: {exc}") from exc

        try:
            inputs = tuple(str(x) for x in data["inputs"])
            schema_raw = data["output_schema"]
        except KeyError as exc:
            raise ValueError(
                f"wide-research spec missing required key {exc.args[0]!r}"
            ) from exc
        if not isinstance(schema_raw, list):
            raise ValueError("output_schema must be an array of tables")

        known = {
            "brief", "name", "title", "prompt_template", "inputs", "output_schema",
            "target_count", "model", "parallelism", "max_turns", "timeout_seconds",
            "modal_app_name", "output_dir",
        }
        extra = {k: v for k, v in data.items() if k not in known}

        return cls(
            brief=str(data.get("brief", "")),
            name=str(data.get("name", "")),
            title=str(data.get("title", "")),
            prompt_template=str(data.get("prompt_template", "")),
            inputs=inputs,
            output_schema=tuple(OutputField.from_dict(d) for d in schema_raw),
            target_count=int(data.get("target_count", len(inputs))),
            model=str(data.get("model", "")),
            parallelism=int(data.get("parallelism", 0)),
            max_turns=int(data.get("max_turns", 100)),
            timeout_seconds=int(data.get("timeout_seconds", 1800)),
            modal_app_name=str(data.get("modal_app_name", "wide-research")),
            output_dir=str(data.get("output_dir", "")),
            extra=extra,
        )

    @classmethod
    def from_toml_file(cls, path: str | Path) -> WideResearchSpec:
        """Read and parse a :class:`WideResearchSpec` from a ``.toml`` file."""
        return cls.from_toml(Path(path).read_text(encoding="utf-8"))
