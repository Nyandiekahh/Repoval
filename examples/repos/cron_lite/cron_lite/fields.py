"""Expand a single cron field ("*/15", "1-5", "1,15,30") into explicit values."""


def expand_field(expr, lo, hi):
    values = set()
    for part in expr.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/")
            step = int(step_text)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            first, last = part.split("-")
            start, end = int(first), int(last)
        else:
            values.add(int(part))
            continue
        values.update(range(start, end, step))
    return sorted(values)
