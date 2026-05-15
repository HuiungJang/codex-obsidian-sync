from __future__ import annotations

import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from .models import SessionEnvelope, SessionIndexEntry, SessionMeta
from .parser import (
    collect_session_metas,
    extract_candidate_messages,
    is_control_message,
    summarize_rollout_file,
)
from .state_store import MISSING, needs_processing

UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def load_session_index(
    path: Path,
    logger: Any | None = None,
) -> dict[str, SessionIndexEntry]:
    entries: dict[str, SessionIndexEntry] = {}
    if not path.exists():
        return entries

    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except JSONDecodeError:
                _log_skip(
                    logger,
                    "session_index_invalid_json",
                    session_index_path=path,
                    line_number=line_number,
                )
                continue
            if not isinstance(payload, dict):
                _log_skip(
                    logger,
                    "session_index_invalid_record",
                    session_index_path=path,
                    line_number=line_number,
                )
                continue

            session_id = _string_or_none(payload.get("id"))
            if not session_id:
                _log_skip(
                    logger,
                    "session_index_missing_id",
                    session_index_path=path,
                    line_number=line_number,
                )
                continue
            entries[session_id] = SessionIndexEntry(
                session_id=session_id,
                thread_name=_string_or_none(payload.get("thread_name")),
                updated_at=_string_or_none(payload.get("updated_at")),
            )
    return entries


def parse_rollout_session_id(path: Path) -> str:
    matches = UUID_PATTERN.findall(path.stem)
    if not matches:
        raise ValueError(f"Could not parse rollout session id from {path}")
    return matches[-1]


def select_canonical_session(rollout_path: Path, metas: list[SessionMeta]) -> SessionMeta:
    if not metas:
        raise ValueError(f"No session_meta records found in {rollout_path}")

    rollout_session_id = parse_rollout_session_id(rollout_path)
    for meta in metas:
        if meta.session_id == rollout_session_id:
            return meta

    non_subagents = [meta for meta in metas if not meta.source_classification.is_subagent]
    if len(non_subagents) == 1:
        return non_subagents[0]

    if len(metas) == 1:
        return metas[0]

    raise ValueError(f"Could not determine canonical session for {rollout_path}")


def build_session_envelope(
    rollout_path: Path,
    session_index: dict[str, SessionIndexEntry],
) -> SessionEnvelope:
    metas, message_list = summarize_rollout_file(rollout_path)
    canonical_meta = select_canonical_session(rollout_path, metas)
    index_entry = session_index.get(canonical_meta.session_id)
    messages = tuple(message_list)

    title_source = first_title_candidate_text(messages) or (index_entry.thread_name if index_entry else None)
    title_seed = slugify(title_source or canonical_meta.session_id)
    project_slug = slugify(project_name_from_cwd(canonical_meta.cwd))
    updated_at = (
        (index_entry.updated_at if index_entry else None)
        or (messages[-1].timestamp if messages else None)
        or canonical_meta.timestamp
    )

    return SessionEnvelope(
        canonical_session_id=canonical_meta.session_id,
        rollout_path=rollout_path,
        originator=canonical_meta.originator,
        source_kind=canonical_meta.source_classification.source_kind,
        is_subagent=canonical_meta.source_classification.is_subagent,
        parent_session_id=canonical_meta.source_classification.parent_session_id,
        cwd=canonical_meta.cwd,
        project_slug=project_slug,
        thread_name=index_entry.thread_name if index_entry else None,
        title_seed=title_seed,
        started_at=canonical_meta.timestamp,
        updated_at=updated_at,
        messages=messages,
    )


def recent_rollout_files(codex_home: Path, limit: int) -> list[Path]:
    sessions_root = codex_home / "sessions"
    rollout_files = sorted(
        sessions_root.glob("*/*/*/*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return rollout_files[:limit]


def discover_rollout_candidates(
    codex_home: Path,
    session_index: dict[str, SessionIndexEntry],
    state: dict[str, Any],
    *,
    vault_root: Path,
    include_subagents: bool,
    recent_days: int = 30,
    max_files: int = 100,
    max_bytes: int = 500 * 1024 * 1024,
    logger: Any | None = None,
) -> list[Path]:
    sessions_root = codex_home / "sessions"
    latest_by_session_id: dict[str, Path] = {}

    oldest_updated_at = datetime.min.replace(tzinfo=UTC)
    ordered_index_entries = sorted(
        session_index.values(),
        key=lambda entry: parse_updated_at(entry.updated_at) or oldest_updated_at,
        reverse=True,
    )
    recent_cutoff = datetime.now(UTC) - timedelta(days=recent_days)
    recent_entries: list[SessionIndexEntry] = []
    older_entries: list[SessionIndexEntry] = []
    for entry in ordered_index_entries:
        updated_at = parse_updated_at(entry.updated_at)
        if updated_at is not None and updated_at >= recent_cutoff:
            recent_entries.append(entry)
        else:
            older_entries.append(entry)

    candidates: list[Path] = []
    seen: set[Path] = set()
    total_bytes = 0

    for entry in recent_entries:
        rollout_path = latest_rollout_for_entry(
            sessions_root,
            entry,
            latest_by_session_id,
        )
        total_bytes = try_add_candidate(
            rollout_path,
            candidates,
            seen,
            session_index_entry=entry,
            state=state,
            vault_root=vault_root,
            include_subagents=include_subagents,
            total_bytes=total_bytes,
            max_files=max_files,
            max_bytes=max_bytes,
        )
        if len(candidates) >= max_files or total_bytes >= max_bytes:
            return candidates

    for tracked_path in state.get("files", {}):
        rollout_path = Path(tracked_path)
        state_entry = state.get("files", {}).get(tracked_path)
        tracked_index_entry = None
        if isinstance(state_entry, dict):
            canonical_session_id = _string_or_none(state_entry.get("canonical_session_id"))
            if canonical_session_id:
                tracked_index_entry = session_index.get(canonical_session_id)
        total_bytes = try_add_candidate(
            rollout_path if rollout_path.exists() else None,
            candidates,
            seen,
            session_index_entry=tracked_index_entry,
            state=state,
            vault_root=vault_root,
            include_subagents=include_subagents,
            total_bytes=total_bytes,
            max_files=max_files,
            max_bytes=max_bytes,
        )
        if len(candidates) >= max_files or total_bytes >= max_bytes:
            return candidates

    for entry in older_entries:
        rollout_path = latest_rollout_for_entry(
            sessions_root,
            entry,
            latest_by_session_id,
        )
        total_bytes = try_add_candidate(
            rollout_path,
            candidates,
            seen,
            session_index_entry=entry,
            state=state,
            vault_root=vault_root,
            include_subagents=include_subagents,
            total_bytes=total_bytes,
            max_files=max_files,
            max_bytes=max_bytes,
        )
        if len(candidates) >= max_files or total_bytes >= max_bytes:
            return candidates

    if include_subagents:
        all_rollouts = list_all_rollouts(sessions_root, logger=logger)
        for rollout_path in sorted(all_rollouts, key=lambda path: path.stat().st_mtime_ns, reverse=True):
            subagent_index_entry = None
            try:
                subagent_index_entry = session_index.get(parse_rollout_session_id(rollout_path))
            except ValueError:
                subagent_index_entry = None
            total_bytes = try_add_candidate(
                rollout_path,
                candidates,
                seen,
                session_index_entry=subagent_index_entry,
                state=state,
                vault_root=vault_root,
                include_subagents=include_subagents,
                total_bytes=total_bytes,
                max_files=max_files,
                max_bytes=max_bytes,
            )
            if len(candidates) >= max_files or total_bytes >= max_bytes:
                return candidates

    return candidates


def latest_rollout_for_entry(
    sessions_root: Path,
    entry: SessionIndexEntry,
    cache: dict[str, Path],
) -> Path | None:
    cached = cache.get(entry.session_id)
    if cached is not None:
        return cached

    candidates: list[Path] = []
    for session_dir in candidate_session_dirs(sessions_root, entry.updated_at):
        candidates.extend(session_dir.glob(f"*-{entry.session_id}.jsonl"))

    if not candidates:
        candidates.extend(sessions_root.glob(f"*/*/*/*-{entry.session_id}.jsonl"))

    if not candidates:
        return None

    latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    cache[entry.session_id] = latest
    return latest


def candidate_session_dirs(sessions_root: Path, updated_at: str | None) -> list[Path]:
    parsed = parse_updated_at(updated_at)
    if parsed is None:
        return []

    dates = {
        parsed.astimezone(UTC).date(),
        parsed.astimezone().date(),
    }
    dirs = [
        sessions_root / f"{date:%Y}" / f"{date:%m}" / f"{date:%d}"
        for date in sorted(dates, reverse=True)
    ]
    return [path for path in dirs if path.is_dir()]


def list_all_rollouts(sessions_root: Path, *, logger: Any | None = None) -> list[Path]:
    rollouts: list[Path] = []
    for rollout_path in sessions_root.glob("*/*/*/*.jsonl"):
        try:
            parse_rollout_session_id(rollout_path)
        except ValueError:
            _log_skip(
                logger,
                "rollout_invalid_filename",
                rollout_path=rollout_path,
            )
            continue
        rollouts.append(rollout_path)
    return rollouts


def try_add_candidate(
    rollout_path: Path | None,
    candidates: list[Path],
    seen: set[Path],
    *,
    session_index_entry: SessionIndexEntry | None,
    state: dict[str, Any],
    vault_root: Path,
    include_subagents: bool,
    total_bytes: int,
    max_files: int,
    max_bytes: int,
) -> int:
    if rollout_path is None or rollout_path in seen:
        return total_bytes
    state_entry = state.get("files", {}).get(str(rollout_path))
    if state_entry is not None and not needs_processing(
        state_entry,
        path=rollout_path,
        vault_root=vault_root,
        include_subagents=include_subagents,
        current_thread_name=session_index_entry.thread_name if session_index_entry else MISSING,
    ):
        seen.add(rollout_path)
        return total_bytes
    if len(candidates) >= max_files:
        return total_bytes

    file_size = rollout_path.stat().st_size
    if candidates and total_bytes + file_size > max_bytes:
        return total_bytes

    candidates.append(rollout_path)
    seen.add(rollout_path)
    return total_bytes + file_size


def _log_skip(logger: Any | None, event: str, **fields: object) -> None:
    if logger is None:
        return
    warning = getattr(logger, "warning", None)
    if callable(warning):
        warning(event, **fields)


def parse_updated_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def first_user_text(messages: tuple[Any, ...]) -> str | None:
    for message in messages:
        if getattr(message, "role", None) == "user" and getattr(message, "text", "").strip():
            return message.text
    return None


def first_title_candidate_text(messages: tuple[Any, ...]) -> str | None:
    for message in messages:
        if getattr(message, "role", None) != "user":
            continue
        text = getattr(message, "text", "").strip()
        if not text or is_control_message(text):
            continue
        return text
    return first_user_text(messages)


def project_name_from_cwd(cwd: str | None) -> str:
    if not cwd:
        return "unknown-project"
    name = Path(cwd).name.strip()
    return name or "unknown-project"


def slugify(value: str, max_length: int = 80) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("/", " ").replace("\\", " ")
    normalized = re.sub(r"`+", " ", normalized)
    normalized = re.sub(r"[^\w\s-]", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"[-\s]+", "-", normalized.strip().lower()).strip("-_")
    if not normalized:
        return "conversation"
    return normalized[:max_length].rstrip("-") or "conversation"




def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None
