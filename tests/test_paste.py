"""Paste compression: large pastes show as placeholders, expand on submit."""

from cli.paste import PasteStash, paste_stash


def test_small_paste_passes_through():
    stash = PasteStash()
    assert stash.compress("one line") == "one line"


def test_large_paste_compresses_to_line_placeholder():
    stash = PasteStash()
    data = "\n".join(f"line {i}" for i in range(40))
    assert stash.compress(data) == "[paste #1: 40 lines]"


def test_long_single_line_compresses_to_char_placeholder():
    stash = PasteStash()
    assert stash.compress("x" * 500) == "[paste #1: 500 chars]"


def test_expand_restores_payload_in_context():
    stash = PasteStash()
    data = "\n".join(f"line {i}" for i in range(10))
    ph = stash.compress(data)
    assert stash.expand(f"explain this: {ph} please") == f"explain this: {data} please"


def test_multiple_pastes_number_independently():
    stash = PasteStash()
    a = "a\n" * 10
    b = "b\n" * 20
    pa, pb = stash.compress(a), stash.compress(b)
    assert pa.startswith("[paste #1:") and pb.startswith("[paste #2:")
    assert stash.expand(f"{pa} and {pb}") == f"{a} and {b}"


def test_deleted_placeholder_expands_to_what_was_seen():
    stash = PasteStash()
    stash.compress("z\n" * 50)
    assert stash.expand("just my own words") == "just my own words"


def test_crlf_normalized_before_threshold_check():
    stash = PasteStash()
    data = "\r\n".join(f"line {i}" for i in range(10))
    assert stash.compress(data) == "[paste #1: 10 lines]"


def test_clear_resets_numbering():
    stash = PasteStash()
    stash.compress("q\n" * 9)
    stash.clear()
    assert stash.compress("r\n" * 9).startswith("[paste #1:")


def test_module_singleton_exists():
    assert isinstance(paste_stash, PasteStash)
