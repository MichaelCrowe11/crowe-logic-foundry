from bench.headless_client import parse_event_stream, RunResult

FIXTURE = "\n".join(
    [
        '{"type":"ready"}',
        '{"type":"token","delta":"Hello "}',
        '{"type":"reasoning","delta":"thinking"}',
        '{"type":"token","delta":"world"}',
        '{"type":"done","tokens":2,"reasoning_tokens":1,"elapsed_ms":1500,"ttft_ms":400}',
    ]
)


def test_parse_assembles_answer_and_metrics():
    r = parse_event_stream(FIXTURE)
    assert isinstance(r, RunResult)
    assert r.answer == "Hello world"
    assert r.reasoning == "thinking"
    assert r.tokens == 2
    assert r.reasoning_tokens == 1
    assert r.elapsed_ms == 1500
    assert r.ttft_ms == 400
    assert r.error is None


def test_parse_captures_error_event():
    stream = '{"type":"ready"}\n{"type":"error","message":"boom","kind":"provider"}'
    r = parse_event_stream(stream)
    assert r.error == "boom"


def test_parse_ignores_blank_and_malformed_lines():
    stream = '\n{"type":"token","delta":"a"}\nnot json\n{"type":"done","tokens":1}'
    r = parse_event_stream(stream)
    assert r.answer == "a"
    assert r.tokens == 1
