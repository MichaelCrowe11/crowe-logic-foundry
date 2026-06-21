def flatten(items):
    """Recursively flatten an arbitrarily nested list of ints."""
    # BUG: only flattens one level deep.
    out = []
    for x in items:
        if isinstance(x, list):
            out.extend(x)
        else:
            out.append(x)
    return out
