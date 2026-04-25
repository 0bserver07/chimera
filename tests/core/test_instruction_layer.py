import os
import tempfile
from chimera.core.instruction import InstructionLayer, Layer

def test_add_and_render():
    il = InstructionLayer()
    il.add("base", "You are helpful.")
    il.add("extra", "Be concise.")
    result = il.render()
    assert "You are helpful" in result
    assert "Be concise" in result

def test_priority_ordering():
    il = InstructionLayer()
    il.add("low", "LOW", priority=1)
    il.add("high", "HIGH", priority=10)
    result = il.render()
    assert result.index("HIGH") < result.index("LOW")

def test_disable_layer():
    il = InstructionLayer()
    il.add("a", "VISIBLE")
    il.add("b", "HIDDEN")
    il.disable("b")
    result = il.render()
    assert "VISIBLE" in result
    assert "HIDDEN" not in result

def test_enable_layer():
    il = InstructionLayer()
    il.add("a", "content", enabled=False)
    assert len(il.active_layers) == 0
    il.enable("a")
    assert len(il.active_layers) == 1

def test_remove_layer():
    il = InstructionLayer()
    il.add("temp", "content")
    assert il.remove("temp")
    assert il.get("temp") is None
    assert not il.remove("nonexistent")

def test_variable_substitution():
    il = InstructionLayer()
    il.add("greeting", "Hello {name}, you are a {role}.")
    result = il.render(name="Alice", role="developer")
    assert "Hello Alice" in result
    assert "developer" in result

def test_from_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("# Project Rules\nAlways use type hints.")
        path = f.name
    try:
        il = InstructionLayer()
        il.add_from_file("rules", path, priority=50)
        assert "type hints" in il.render()
    finally:
        os.unlink(path)

def test_from_directory():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "01-base.md"), "w") as f:
            f.write("Be helpful.")
        with open(os.path.join(d, "02-style.md"), "w") as f:
            f.write("Use PEP 8.")
        il = InstructionLayer()
        il.add_from_directory(d)
        assert len(il.layers) == 2
        assert "Be helpful" in il.render()

def test_to_prompt():
    il = InstructionLayer()
    il.add("base", "System prompt.")
    prompt = il.to_prompt()
    assert "System prompt" in prompt.render()

def test_preset_coding_agent():
    il = InstructionLayer.coding_agent(project_context="Python 3.12 project")
    result = il.render()
    assert "coding agent" in result.lower()
    assert "Python 3.12" in result

def test_preset_reviewer():
    il = InstructionLayer.reviewer()
    result = il.render()
    assert "reviewer" in result.lower()

def test_chaining():
    il = InstructionLayer()
    il.add("a", "A").add("b", "B").add("c", "C")
    assert len(il.layers) == 3

def test_layer_dataclass():
    l = Layer(name="test", content="content", priority=5, enabled=True)
    assert l.name == "test"
    assert l.priority == 5
