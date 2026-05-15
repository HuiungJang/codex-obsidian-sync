from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .writer import write_atomic

MISSING = object()


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"files": {}}
    try:
        with path.open(encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return {"files": {}}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _quarantine_corrupt_state(path)
        return {"files": {}}

    if not isinstance(state, dict) or not isinstance(state.get("files", {}), dict):
        _quarantine_corrupt_state(path)
        return {"files": {}}

    state.setdefault("files", {})
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_atomic(path, content)


def file_fingerprint(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def needs_processing(
    state_entry: dict[str, Any] | None,
    *,
    path: Path,
    vault_root: Path,
    include_subagents: bool,
    current_thread_name: Any = MISSING,
) -> bool:
    if state_entry is None:
        return True

    fingerprint = file_fingerprint(path)
    if state_entry.get("size") != fingerprint["size"] or state_entry.get("mtime_ns") != fingerprint["mtime_ns"]:
        return True

    if include_subagents and not state_entry.get("included", False):
        return True
    if not include_subagents and state_entry.get("included") and state_entry.get("is_subagent"):
        return True

    if current_thread_name is not MISSING and state_entry.get("thread_name") != current_thread_name:
        return True

    conversation_note_path = state_entry.get("conversation_note_path")
    if state_entry.get("included") and isinstance(conversation_note_path, str):
        note_path = _resolve_conversation_note_path(vault_root, conversation_note_path)
        if not note_path.exists():
            return True
        note_fingerprint = state_entry.get("conversation_note_fingerprint")
        if isinstance(note_fingerprint, dict):
            if note_fingerprint != file_fingerprint(note_path):
                return True

    return False


def upsert_state_entry(
    state: dict[str, Any],
    *,
    rollout_path: Path,
    included: bool,
    extra: dict[str, Any],
) -> None:
    fingerprint = file_fingerprint(rollout_path)
    state.setdefault("files", {})
    state["files"][str(rollout_path)] = {
        "included": included,
        "size": fingerprint["size"],
        "mtime_ns": fingerprint["mtime_ns"],
        **extra,
    }


def prune_missing_rollouts(state: dict[str, Any]) -> None:
    files = state.setdefault("files", {})
    missing_paths = [path for path in files if not Path(path).exists()]
    for path in missing_paths:
        files.pop(path, None)


def included_conversations(state: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entry in state.get("files", {}).values():
        if not entry.get("included"):
            continue
        entries.append(entry)
    return entries


def append_only_change(state_entry: dict[str, Any] | None, *, path: Path) -> bool:
    if state_entry is None:
        return False

    previous_size = state_entry.get("size")
    previous_offset = state_entry.get("offset")
    if not isinstance(previous_size, int) or not isinstance(previous_offset, int):
        return False

    fingerprint = file_fingerprint(path)
    return (
        fingerprint["size"] >= previous_size
        and previous_offset < fingerprint["size"]
    )


def _resolve_conversation_note_path(vault_root: Path, relative_path: str) -> Path:
    from .writer import resolve_note_path

    return resolve_note_path(vault_root, relative_path)


def _quarantine_corrupt_state(path: Path) -> None:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.corrupt-{timestamp}")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.corrupt-{timestamp}-{suffix}")
        suffix += 1
    path.replace(candidate)
