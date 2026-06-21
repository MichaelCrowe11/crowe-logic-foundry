def retry(fn, attempts=3):
    """Call fn() up to `attempts` times; return its result or re-raise last error."""
    # BUG: tries only once, and swallows the exception returning None.
    try:
        return fn()
    except Exception:
        return None
