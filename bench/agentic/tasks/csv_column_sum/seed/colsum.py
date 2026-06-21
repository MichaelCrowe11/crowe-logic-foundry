def column_sum(rows, col):
    """Sum one integer column of CSV-like rows. rows[0] is the header."""
    # BUG: includes the header row and crashes / miscounts.
    total = 0
    for row in rows:
        total += int(row[col])
    return total
