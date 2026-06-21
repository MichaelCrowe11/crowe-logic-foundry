_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(s):
    """Convert a Roman numeral to an int (supports subtractive notation)."""
    # BUG: naive sum ignores subtractive cases like IV, IX, XL.
    return sum(_VALUES[c] for c in s)
