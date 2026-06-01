import bench.headless_client as hc
from bench.headless_client import (
    _is_transient,
    parse_event_stream,
    run_headless,
    RunResult,
)

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


def test_is_transient_classifies_throughput_throttles():
    assert _is_transient("RateLimitError: Error code: 429 - RateLimitReached")
    assert _is_transient("APIError: Too Many Requests")
    assert _is_transient("exceeded rate limit")
    # hard failures are NOT transient — retrying won't help
    assert not _is_transient("Headless mode does not support provider kind 'watsonx'")
    assert not _is_transient("QuotaNotAvailableForResource")
    assert not _is_transient(None)
    assert not _is_transient("")


def test_run_headless_retries_transient_then_succeeds(monkeypatch):
    """A transient 429 on the first attempt should be retried; a later
    successful attempt wins. Backoff sleep is stubbed out."""
    attempts = []

    def fake_once(prompt, model, *, tools, timeout):
        attempts.append(model)
        if len(attempts) < 3:
            return RunResult(error="APIError: Too Many Requests")
        return RunResult(answer="recovered")

    monkeypatch.setattr(hc, "_run_once", fake_once)
    monkeypatch.setattr(hc.time, "sleep", lambda *_: None)
    r = run_headless("q", "Kimi-K2-6", tools=False, retries=3)
    assert r.answer == "recovered"
    assert len(attempts) == 3  # failed twice, succeeded on third


def test_run_headless_does_not_retry_hard_error(monkeypatch):
    """A non-transient error must fail fast — no wasted retries."""
    attempts = []

    def fake_once(prompt, model, *, tools, timeout):
        attempts.append(model)
        return RunResult(error="Headless mode does not support provider kind 'watsonx'")

    monkeypatch.setattr(hc, "_run_once", fake_once)
    monkeypatch.setattr(hc.time, "sleep", lambda *_: None)
    r = run_headless("q", "claude-opus-4-6", tools=False, retries=3)
    assert r.error and "watsonx" in r.error
    assert len(attempts) == 1  # tried once, gave up immediately
