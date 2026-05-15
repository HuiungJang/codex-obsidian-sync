from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO
import sys


LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
REDACTED = "[REDACTED]"
SENSITIVE_TOKENS = ("text", "content", "message", "messages", "transcript", "preview")


class EventLogger:
    def __init__(self, level: str = "INFO", stream: TextIO | None = None) -> None:
        self.level = LOG_LEVELS.get(level.upper(), LOG_LEVELS["INFO"])
        self.stream = stream or sys.stderr

    def debug(self, event: str, **fields: Any) -> None:
        self._emit("DEBUG", event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit("INFO", event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit("WARNING", event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit("ERROR", event, fields)

    def _emit(self, level: str, event: str, fields: dict[str, Any]) -> None:
        if LOG_LEVELS[level] < self.level:
            return
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": level,
            "event": event,
        }
        payload.update(sanitize_fields(fields))
        self.stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.stream.flush()


def sanitize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: sanitize_value(key, value) for key, value in fields.items()}


def sanitize_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(token in lowered for token in SENSITIVE_TOKENS):
        return REDACTED
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {"keys": sorted(value.keys())}
    if isinstance(value, (list, tuple, set)):
        return {"count": len(value)}
    return value
