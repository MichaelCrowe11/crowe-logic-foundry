from bench.scoring import score_code


def test_code_passes_when_correct():
    answer = "def add(a, b):\n    return a + b"
    tests = "assert add(2, 3) == 5\nassert add(-1, 1) == 0"
    assert score_code(answer, tests) == 1.0


def test_code_fails_on_wrong_impl():
    answer = "def add(a, b):\n    return a - b"
    tests = "assert add(2, 3) == 5"
    assert score_code(answer, tests) == 0.0


def test_code_fails_on_syntax_error():
    answer = "def add(a, b) return a + b"  # missing colon
    tests = "assert add(2, 3) == 5"
    assert score_code(answer, tests) == 0.0


def test_code_times_out_safely():
    answer = "def loop():\n    while True:\n        pass"
    tests = "loop()"
    # must not hang the test suite; returns 0.0 on timeout
    assert score_code(answer, tests, timeout=2) == 0.0
