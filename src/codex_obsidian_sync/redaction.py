from __future__ import annotations

import re

# Pattern-based redaction is intentionally best-effort; keep this list focused on
# common credential shapes to avoid broad high-entropy masking false positives.
PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+(?=$|[\s,;)\]}\"'`])")
OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
GITHUB_TOKEN_PATTERN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
)
AWS_ACCESS_KEY_PATTERN = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
SLACK_TOKEN_PATTERN = re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")
NPM_TOKEN_PATTERN = re.compile(r"\bnpm_[A-Za-z0-9_]{20,}\b")
GOOGLE_API_KEY_PATTERN = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")
STRIPE_SECRET_KEY_PATTERN = re.compile(
    r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b"
)
JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
ASSIGNMENT_SECRET_PATTERN = re.compile(
    r"(?i)\b"
    r"(?P<key>[A-Z0-9_-]*"
    r"(?:api[_-]?key|access[_-]?key|secret[_-]?access[_-]?key|secret[_-]?key|"
    r"client[_-]?secret|"
    r"access[_-]?token|refresh[_-]?token|auth[_-]?token|id[_-]?token|github[_-]?token|"
    r"slack[_-]?(?:bot[_-]?)?token|npm[_-]?token|password|passwd|secret)"
    r"[A-Z0-9_-]*)"
    r"\b(?P<separator>\s*[:=]\s*)"
    r"(?P<quote>['\"]?)(?P<value>[^\s,'\"\[\]]{6,})(?P=quote)"
)


def redact_text(text: str) -> str:
    redacted = PRIVATE_KEY_PATTERN.sub("[REDACTED_PRIVATE_KEY]", text)
    redacted = BEARER_PATTERN.sub("[REDACTED_BEARER_TOKEN]", redacted)
    redacted = OPENAI_KEY_PATTERN.sub("[REDACTED_API_KEY]", redacted)
    redacted = GITHUB_TOKEN_PATTERN.sub("[REDACTED_GITHUB_TOKEN]", redacted)
    redacted = AWS_ACCESS_KEY_PATTERN.sub("[REDACTED_AWS_ACCESS_KEY]", redacted)
    redacted = SLACK_TOKEN_PATTERN.sub("[REDACTED_SLACK_TOKEN]", redacted)
    redacted = NPM_TOKEN_PATTERN.sub("[REDACTED_NPM_TOKEN]", redacted)
    redacted = GOOGLE_API_KEY_PATTERN.sub("[REDACTED_GOOGLE_API_KEY]", redacted)
    redacted = STRIPE_SECRET_KEY_PATTERN.sub("[REDACTED_STRIPE_SECRET_KEY]", redacted)
    redacted = JWT_PATTERN.sub("[REDACTED_JWT]", redacted)
    redacted = ASSIGNMENT_SECRET_PATTERN.sub(_redact_assignment, redacted)
    return redacted


def _redact_assignment(match: re.Match[str]) -> str:
    quote = match.group("quote")
    return f"{match.group('key')}{match.group('separator')}{quote}[REDACTED_SECRET]{quote}"
