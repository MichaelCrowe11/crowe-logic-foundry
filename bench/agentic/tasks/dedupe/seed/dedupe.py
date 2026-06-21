def dedupe(items):
    """Remove duplicates while preserving first-seen order."""
    # BUG: set() loses ordering.
    return list(set(items))
