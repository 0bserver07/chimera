"""Tests for chimera.training.sketch — Sketch Synthesis (Feature 6)."""

from __future__ import annotations

import os
import tempfile
import textwrap

import pytest

from chimera.training.sketch import Hole, SketchSpec


# ── helpers ────────────────────────────────────────────────────────────

SINGLE_SKETCH = textwrap.dedent("""\
    def add(a: float, b: float) -> float:
        # HOLE: implement addition
        pass

    def divide(a: float, b: float) -> float:
        # HOLE: implement division, raise ValueError on zero
        pass
""")

SKETCH_A = textwrap.dedent("""\
    class Stack:
        def __init__(self):
            # HOLE: initialise internal storage
            pass
""")

SKETCH_B = textwrap.dedent("""\
    def greet(name: str) -> str:
        # HOLE: return greeting string
        pass
""")


# ── test_parse_holes ──────────────────────────────────────────────────

def test_parse_holes() -> None:
    """Finds HOLE markers with correct line numbers."""
    sketch = SketchSpec({"calc.py": SINGLE_SKETCH})
    holes = sketch.holes

    assert len(holes) == 2

    assert holes[0].id == 0
    assert holes[0].description == "implement addition"
    assert holes[0].file_path == "calc.py"
    assert holes[0].line == 2
    assert holes[0].indent == "    "

    assert holes[1].id == 1
    assert holes[1].description == "implement division, raise ValueError on zero"
    assert holes[1].file_path == "calc.py"
    assert holes[1].line == 6
    assert holes[1].indent == "    "


# ── test_from_file ────────────────────────────────────────────────────

def test_from_file(tmp_path: pytest.TempPathFactory) -> None:
    """Loads a sketch from a temporary file on disk."""
    sketch_path = os.path.join(str(tmp_path), "calc.py")
    with open(sketch_path, "w") as f:
        f.write(SINGLE_SKETCH)

    sketch = SketchSpec.from_file(sketch_path)

    assert len(sketch.holes) == 2
    assert sketch.holes[0].file_path == sketch_path
    assert sketch.holes[1].file_path == sketch_path


# ── test_to_prompt ────────────────────────────────────────────────────

def test_to_prompt() -> None:
    """Prompt lists holes with descriptions and file references."""
    sketch = SketchSpec({"calc.py": SINGLE_SKETCH})
    prompt = sketch.to_prompt()

    # Contains section headers
    assert "## Sketch Files" in prompt
    assert "## Holes to Fill" in prompt

    # Contains hole descriptions
    assert "Hole 0" in prompt
    assert "implement addition" in prompt
    assert "Hole 1" in prompt
    assert "implement division" in prompt

    # Contains the source code
    assert "def add(" in prompt
    assert "def divide(" in prompt

    # Contains the instruction
    assert "Fill ONLY the marked holes" in prompt


# ── test_holes_property ───────────────────────────────────────────────

def test_holes_property() -> None:
    """Returns a list of Hole dataclass instances (defensive copy)."""
    sketch = SketchSpec({"calc.py": SINGLE_SKETCH})
    holes = sketch.holes

    # Correct types
    assert all(isinstance(h, Hole) for h in holes)

    # Defensive copy — mutating the returned list doesn't affect the sketch
    holes.pop()
    assert len(sketch.holes) == 2


# ── test_preserves_content ────────────────────────────────────────────

def test_preserves_content() -> None:
    """Sketch files content is preserved exactly as provided."""
    files = {"calc.py": SINGLE_SKETCH}
    sketch = SketchSpec(files)

    # Access the internal sketch files via to_prompt (content is embedded)
    prompt = sketch.to_prompt()
    assert "def add(a: float, b: float) -> float:" in prompt
    assert "def divide(a: float, b: float) -> float:" in prompt

    # The text field should be the default description
    assert sketch.text == "Fill the marked holes in the provided code sketch."


# ── test_multiple_files ───────────────────────────────────────────────

def test_multiple_files() -> None:
    """Handles sketches spread across two or more files."""
    files = {
        "stack.py": SKETCH_A,
        "greet.py": SKETCH_B,
    }
    sketch = SketchSpec(files)
    holes = sketch.holes

    assert len(holes) == 2

    # Holes come from different files
    file_paths = {h.file_path for h in holes}
    assert file_paths == {"stack.py", "greet.py"}

    # IDs are unique and sequential
    assert holes[0].id == 0
    assert holes[1].id == 1

    # Prompt includes both files
    prompt = sketch.to_prompt()
    assert "### stack.py" in prompt
    assert "### greet.py" in prompt
