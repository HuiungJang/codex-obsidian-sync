from __future__ import annotations

import re


INTERVAL_PATTERN = re.compile(r"^\s*(\d+)\s*([smhSMH]?)\s*$")


def parse_interval(value: str) -> int:
    match = INTERVAL_PATTERN.match(value)
    if match is None:
        raise ValueError("Interval must look like 10s, 1m, 5m, or 30")

    amount = int(match.group(1))
    unit = match.group(2).lower() or "s"
    multiplier = {"s": 1, "m": 60, "h": 3600}[unit]
    return max(1, amount * multiplier)


def format_interval(seconds: int) -> str:
    normalized = max(int(seconds), 1)
    if normalized % 3600 == 0:
        return f"{normalized // 3600}h ({normalized}s)"
    if normalized % 60 == 0:
        return f"{normalized // 60}m ({normalized}s)"
    return f"{normalized}s"
