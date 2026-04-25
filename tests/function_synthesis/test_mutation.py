from chimera.training.mutation import MutationTester


def test_generate_swap_operator():
    source = "def add(a, b):\n    return a + b\n"
    tester = MutationTester()
    mutants = tester.generate_mutants(source)
    assert len(mutants) >= 1
    assert any("swap" in m.operator.lower() for m in mutants)


def test_generate_negate_condition():
    source = "def check(x):\n    if x > 0:\n        return True\n    return False\n"
    tester = MutationTester()
    mutants = tester.generate_mutants(source)
    assert any("negate" in m.operator for m in mutants)


def test_mutation_score():
    source = "def add(a, b):\n    return a + b\n"
    tester = MutationTester()
    # test_fn that catches all mutations
    result = tester.run(source, test_fn=lambda m: False)  # False = tests fail = mutation killed
    assert result.mutation_score == 1.0
    assert result.survived == 0


def test_survived_mutation_reported():
    source = "def add(a, b):\n    return a + b\n"
    tester = MutationTester()
    # test_fn that misses all mutations
    result = tester.run(source, test_fn=lambda m: True)  # True = tests still pass = survived
    assert result.survived > 0
    assert len(result.survivors) > 0


def test_max_mutants_respected():
    source = "def f(a,b,c):\n    return a+b+c+a-b-c+a*b*c\n"
    tester = MutationTester(max_mutants=3)
    mutants = tester.generate_mutants(source)
    assert len(mutants) <= 3
