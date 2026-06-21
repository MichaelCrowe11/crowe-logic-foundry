def wrap(text, width):
    """Greedy word-wrap: return lines, each <= width chars, never splitting words."""
    # BUG: slices by raw character width, splitting words mid-token.
    return [text[i:i + width] for i in range(0, len(text), width)]
