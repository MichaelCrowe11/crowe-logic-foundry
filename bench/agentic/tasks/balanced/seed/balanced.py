def is_balanced(s):
    """Return True if brackets ()[]{} are balanced and correctly nested."""
    # BUG: only counts; ignores ordering and bracket type matching.
    return s.count("(") == s.count(")")
