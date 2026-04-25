"""Tests for DemonstrationPrompt — few-shot prompting with solved examples."""

import os
import tempfile

from chimera.core.demonstration import DemonstrationPrompt, Example


def test_render_with_examples():
    dp = DemonstrationPrompt(system="You are helpful.")
    dp.add_example("Add 2+2", "The answer is 4.")
    result = dp.render(task="Add 3+3")
    assert "You are helpful" in result
    assert "Add 2+2" in result
    assert "The answer is 4" in result
    assert "Add 3+3" in result


def test_render_no_examples():
    dp = DemonstrationPrompt(system="System prompt.")
    result = dp.render(task="Do something")
    assert "System prompt" in result
    assert "Do something" in result
    assert "---" not in result


def test_max_examples():
    dp = DemonstrationPrompt(max_examples=2)
    dp.add_example("t1", "s1")
    dp.add_example("t2", "s2")
    dp.add_example("t3", "s3")
    result = dp.render()
    assert "t1" in result
    assert "t2" in result
    assert "t3" not in result


def test_add_from_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("# Task\nFix the bug\n\n# Solution\nChanged + to -\n")
        f.flush()
        path = f.name
    try:
        dp = DemonstrationPrompt()
        dp.add_from_file(path)
        assert len(dp.examples) == 1
        assert dp.examples[0].task == "Fix the bug"
        assert "Changed + to -" in dp.examples[0].solution
        assert dp.examples[0].source == path
    finally:
        os.unlink(path)


def test_add_from_file_task_only():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("# Task\nJust a task, no solution\n")
        f.flush()
        path = f.name
    try:
        dp = DemonstrationPrompt()
        dp.add_from_file(path)
        assert len(dp.examples) == 1
        assert dp.examples[0].task == "Just a task, no solution"
        assert dp.examples[0].solution == ""
    finally:
        os.unlink(path)


def test_add_from_directory():
    with tempfile.TemporaryDirectory() as d:
        for i in range(3):
            with open(os.path.join(d, f"example_{i}.md"), "w") as f:
                f.write(f"# Task\nTask {i}\n\n# Solution\nSolution {i}\n")
        dp = DemonstrationPrompt()
        dp.add_from_directory(d)
        assert len(dp.examples) == 3
        # Should be sorted by filename
        assert dp.examples[0].task == "Task 0"
        assert dp.examples[2].task == "Task 2"


def test_add_from_directory_pattern():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "example.md"), "w") as f:
            f.write("# Task\nMD task\n\n# Solution\nMD solution\n")
        with open(os.path.join(d, "example.txt"), "w") as f:
            f.write("# Task\nTXT task\n\n# Solution\nTXT solution\n")
        dp = DemonstrationPrompt()
        dp.add_from_directory(d, pattern="*.txt")
        assert len(dp.examples) == 1
        assert dp.examples[0].task == "TXT task"


def test_example_dataclass():
    ex = Example(task="t", solution="s", source="file.md")
    assert ex.task == "t"
    assert ex.solution == "s"
    assert ex.source == "file.md"


def test_example_default_source():
    ex = Example(task="t", solution="s")
    assert ex.source == ""


def test_to_prompt():
    dp = DemonstrationPrompt(system="Be helpful.")
    dp.add_example("Q", "A")
    prompt = dp.to_prompt()
    rendered = prompt.render()
    assert "Be helpful" in rendered
    assert "Q" in rendered
    assert "A" in rendered


def test_render_empty():
    dp = DemonstrationPrompt()
    result = dp.render()
    assert result == ""


def test_render_system_only():
    dp = DemonstrationPrompt(system="Hello")
    result = dp.render()
    assert result == "Hello"


def test_custom_prefix():
    dp = DemonstrationPrompt(example_prefix="### Demo")
    dp.add_example("task1", "sol1")
    result = dp.render()
    assert "### Demo 1" in result


def test_examples_property_returns_copy():
    dp = DemonstrationPrompt()
    dp.add_example("t", "s")
    examples = dp.examples
    examples.append(Example(task="extra", solution="extra"))
    assert len(dp.examples) == 1


def test_constructor_with_examples():
    examples = [Example(task="t1", solution="s1"), Example(task="t2", solution="s2")]
    dp = DemonstrationPrompt(examples=examples)
    assert len(dp.examples) == 2
    # Mutating the original list should not affect the prompt
    examples.append(Example(task="t3", solution="s3"))
    assert len(dp.examples) == 2


def test_render_example_numbering():
    dp = DemonstrationPrompt()
    dp.add_example("first", "sol1")
    dp.add_example("second", "sol2")
    result = dp.render()
    assert "## Example 1" in result
    assert "## Example 2" in result


def test_render_separators():
    dp = DemonstrationPrompt(system="sys")
    dp.add_example("t", "s")
    result = dp.render(task="do it")
    assert "---" in result
    assert "Now solve this task:" in result
