def parse_duration(s):
    """Parse strings like '1h30m', '45m', '2h', '90s' into total seconds."""
    # BUG: only handles a leading hours component.
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    return 0
