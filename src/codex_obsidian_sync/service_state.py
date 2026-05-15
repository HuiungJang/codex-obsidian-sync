from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, TypeVar

from .locking import process_lock
from .state_store import save_state


T = TypeVar("T")
SERVICE_STATE_SCHEMA_VERSION = 1


def default_service_state() -> dict[str, Any]:
    return {
        "schema_version": SERVICE_STATE_SCHEMA_VERSION,
        "pending": False,
        "last_trigger_at": None,
        "next_eligible_at": None,
        "last_run_started_at": None,
        "last_run_finished_at": None,
        "last_success_at": None,
        "last_error_type": None,
        "last_error_summary": None,
        "last_summary": {},
        "updated_at": None,
    }


def load_service_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_service_state()
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default_service_state()

    if not isinstance(data, dict):
        return default_service_state()

    state = default_service_state()
    for key in state:
        if key in data:
            state[key] = data[key]
    return state


def save_service_state(path: Path, state: dict[str, Any]) -> None:
    save_state(path, state)


def mutate_service_state(
    *,
    state_path: Path,
    lock_path: Path,
    mutator: Callable[[dict[str, Any]], T],
) -> T:
    with process_lock(lock_path, blocking=True):
        state = load_service_state(state_path)
        result = mutator(state)
        state["schema_version"] = SERVICE_STATE_SCHEMA_VERSION
        state["updated_at"] = utc_now_iso()
        save_service_state(state_path, state)
        return result


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
