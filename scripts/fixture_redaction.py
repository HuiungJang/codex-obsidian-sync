from __future__ import annotations

import re
from pathlib import Path


REDACTIONS = [
    (re.compile(r"BEGIN PRIVATE|PRIVATE_RAW_TRANSCRIPT|DO_NOT_COMMIT|CONFIDENTIAL"), "[REDACTED_PRIVATE_MARKER]"),
    (re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{20,}\b"), "[REDACTED_API_KEY]"),
    (
        re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._+/=-]{12,}", re.IGNORECASE),
        "Authorization: [REDACTED_BEARER_TOKEN]",
    ),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "[REDACTED_AWS_ACCESS_KEY]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"), "[REDACTED_NPM_TOKEN]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "[REDACTED_GOOGLE_API_KEY]"),
    (re.compile(r"\bsk_(?:live|test)_[0-9A-Za-z]{16,}\b"), "[REDACTED_STRIPE_SECRET_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[REDACTED_JWT]"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
        "[REDACTED_PRIVATE_KEY]",
    ),
]


def dynamic_redactions() -> list[tuple[re.Pattern[str], str]]:
    redactions = list(REDACTIONS)
    home = str(Path.home())
    if home and home != "/":
        redactions.append((re.compile(re.escape(home)), "<HOME>"))
    return redactions


def apply_redactions(text: str) -> tuple[str, int]:
    total = 0
    redacted = text
    for pattern, replacement in dynamic_redactions():
        redacted, count = pattern.subn(replacement, redacted)
        total += count
    return redacted, total


def find_first_leak(text: str) -> str | None:
    for pattern, _ in dynamic_redactions():
        if pattern.search(text):
            return pattern.pattern
    return None


def is_binary(data: bytes) -> bool:
    return b"\0" in data
