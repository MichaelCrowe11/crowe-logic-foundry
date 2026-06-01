"""The Crowe Logic thinking spinner drifts its crest color over time while
keeping the travelling-pulse motion. Color and motion both derive from the wall
clock, so ``frame(now)`` is pure and testable without a live terminal."""

from cli import branding


def _crest_style(text):
    """The ◆ mark (first span) always carries the live crest hue."""
    return str(text.spans[0].style)


def test_crest_color_drifts_over_time():
    sp = branding.thinking_spinner()
    assert _crest_style(sp.frame(0.0)) != _crest_style(sp.frame(2.0))


def test_crest_color_cycles_back():
    # The continuous hue function loops every len(_SPINNER_PALETTE) phase units.
    n = len(branding._SPINNER_PALETTE)
    assert branding._crest_color(0.0) == branding._crest_color(float(n))
    assert branding._crest_color(1.3) == branding._crest_color(1.3 + n)


def test_frame_is_pure_for_same_time():
    sp = branding.thinking_spinner()
    assert str(sp.frame(1.7)) == str(sp.frame(1.7))


def test_lerp_hex_endpoints_and_midpoint():
    assert branding._lerp_hex("#000000", "#ffffff", 0.0) == "#000000"
    assert branding._lerp_hex("#000000", "#ffffff", 1.0) == "#ffffff"
    assert branding._lerp_hex("#000000", "#ffffff", 0.5) == "#808080"


def test_crest_color_always_valid_hex():
    for k in range(0, 200):
        c = branding._crest_color(k * 0.05)
        assert c.startswith("#") and len(c) == 7
        int(c[1:], 16)  # parses as hex


def test_label_appears_in_frame():
    sp = branding.thinking_spinner("running tool")
    assert "running tool" in sp.frame(0.0).plain
