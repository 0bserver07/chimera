from chimera.training.spec_inference import SpecInferrer

def test_infer_return_type():
    source = "def add(a: int, b: int) -> int:\n    return a + b\n"
    inferrer = SpecInferrer()
    invs = inferrer.analyze(source)
    type_invs = [i for i in invs if i.pattern == "return_type"]
    assert len(type_invs) >= 1
    assert "int" in type_invs[0].invariant

def test_infer_non_null():
    source = "def greet(name):\n    return f'Hello {name}'\n"
    inferrer = SpecInferrer()
    invs = inferrer.analyze(source)
    non_null = [i for i in invs if i.pattern == "non_null"]
    assert len(non_null) >= 1

def test_infer_has_docstring():
    source = 'def foo():\n    """Does foo."""\n    return 1\n'
    inferrer = SpecInferrer()
    invs = inferrer.analyze(source)
    doc_invs = [i for i in invs if i.pattern == "has_docstring"]
    assert len(doc_invs) >= 1

def test_generate_test_file():
    source = "def add(a: int, b: int) -> int:\n    return a + b\n"
    inferrer = SpecInferrer()
    inferrer.analyze(source)
    content = inferrer.generate_test_file()
    assert "def test_" in content
    assert "invariant" in content.lower() or "Invariant" in content

def test_confidence_values():
    source = "def typed(x: str) -> str:\n    \"\"\"Doc.\"\"\"\n    return x.upper()\n"
    inferrer = SpecInferrer()
    invs = inferrer.analyze(source)
    # return_type and has_docstring should be confidence 1.0
    high_conf = [i for i in invs if i.confidence == 1.0]
    assert len(high_conf) >= 2
