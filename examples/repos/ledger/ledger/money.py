"""Money helpers. Amounts are integer cents to avoid float drift."""


def split_evenly(total_cents, parts):
    """Split ``total_cents`` into ``parts`` shares."""
    if parts <= 0:
        raise ValueError("parts must be positive")
    share = round(total_cents / parts)
    return [share] * parts
