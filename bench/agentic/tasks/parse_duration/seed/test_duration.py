from duration import parse_duration


def test_hours_and_minutes():
    assert parse_duration("1h30m") == 5400


def test_minutes_only():
    assert parse_duration("45m") == 2700


def test_seconds():
    assert parse_duration("90s") == 90


def test_combined():
    assert parse_duration("2h15m30s") == 8130
