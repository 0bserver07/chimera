import os
import tempfile

import pytest

from chimera.training.impact import CallerInfo, ImpactAnalyzer, ImpactReport


@pytest.fixture
def project_dir():
    with tempfile.TemporaryDirectory() as d:
        # utils.py with a function
        with open(os.path.join(d, "utils.py"), "w") as f:
            f.write("def calculate_tax(amount):\n    return amount * 0.1\n")
        # billing.py that imports and calls it
        with open(os.path.join(d, "billing.py"), "w") as f:
            f.write("from utils import calculate_tax\ndef process(a):\n    return calculate_tax(a)\n")
        # test file
        os.makedirs(os.path.join(d, "tests"), exist_ok=True)
        with open(os.path.join(d, "tests", "test_billing.py"), "w") as f:
            f.write("from utils import calculate_tax\ndef test_tax():\n    assert calculate_tax(100) == 10\n")
        yield d


def test_find_callers(project_dir):
    a = ImpactAnalyzer(project_dir)
    report = a.analyze("utils.py", "calculate_tax")
    assert len(report.callers) >= 1
    assert report.callers[0].function == "process"


def test_find_importers(project_dir):
    a = ImpactAnalyzer(project_dir)
    report = a.analyze("utils.py", "calculate_tax")
    assert any("billing" in f for f in report.importers)


def test_find_tests(project_dir):
    a = ImpactAnalyzer(project_dir)
    report = a.analyze("utils.py", "calculate_tax")
    assert len(report.tests) >= 1


def test_to_prompt_section(project_dir):
    a = ImpactAnalyzer(project_dir)
    report = a.analyze("utils.py", "calculate_tax")
    section = report.to_prompt_section()
    assert "calculate_tax" in section
    assert "Callers" in section or "safe to modify" in section


def test_no_callers():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "isolated.py"), "w") as f:
            f.write("def lonely():\n    return 42\n")
        a = ImpactAnalyzer(d)
        report = a.analyze("isolated.py", "lonely")
        assert len(report.callers) == 0
        assert "safe to modify" in report.to_prompt_section()
