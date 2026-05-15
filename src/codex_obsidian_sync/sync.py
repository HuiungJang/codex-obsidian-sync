from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path

from .config import SyncConfig
from .discovery import build_session_envelope, discover_rollout_candidates, load_session_index
from .event_logger import EventLogger
from .models import SessionEnvelope
from .parser import RolloutDataError, load_rollout_records_from_offset
from .state_store import (
    MISSING,
    append_only_change,
    file_fingerprint,
    included_conversations,
    load_state,
    needs_processing,
    prune_missing_rollouts,
    save_state,
    upsert_state_entry,
)
from .writer import (
    CONVERSATION_NOTE_MARKER,
    append_conversation_transcript,
    build_conversation_note_record,
    render_conversation_header,
    rewrite_conversation_header,
    resolve_note_path,
    validate_vault_root,
    write_conversation_note,
    write_daily_notes,
    write_sectioned_note,
    write_project_notes,
)

PRIVACY_MIGRATION_VERSION = 1


def sync_once(
    *,
    config: SyncConfig,
    logger: EventLogger | None = None,
    allow_pause: bool = False,
) -> dict[str, int]:
    logger = logger or EventLogger(config.log_level)
    started = time.perf_counter()
    summary = {
        "processed": 0,
        "appended": 0,
        "rewritten": 0,
        "skipped_subagents": 0,
        "skipped_invalid": 0,
        "unchanged": 0,
        "total_rollouts": 0,
        "paused": 0,
        "fast_path": 0,
    }

    try:
        vault_root = validate_vault_root(config.vault)
    except (OSError, ValueError) as exc:
        if not allow_pause:
            raise
        summary["paused"] = 1
        summary["duration_ms"] = elapsed_ms(started)
        logger.warning(
            "sync_paused_vault_unavailable",
            vault_path=config.vault,
            error_type=type(exc).__name__,
        )
        return summary

    session_index_path = config.codex_home / "session_index.jsonl"
    state = load_state(config.state_file)
    prune_missing_rollouts(state)
    previous_conversations = included_conversations(state)
    if can_fast_skip_sync(
        state,
        session_index_path=session_index_path,
        vault_root=vault_root,
        include_subagents=config.include_subagents,
    ):
        update_runtime_state(state, session_index_path=session_index_path)
        save_state(config.state_file, state)
        summary["fast_path"] = 1
        summary["unchanged"] = len(state.get("files", {}))
        summary["duration_ms"] = elapsed_ms(started)
        logger.debug(
            "sync_fast_path",
            tracked_files=len(state.get("files", {})),
            duration_ms=summary["duration_ms"],
        )
        return summary

    session_index = load_session_index(session_index_path, logger=logger)
    rollout_files = discover_rollout_candidates(
        config.codex_home,
        session_index,
        state,
        vault_root=vault_root,
        include_subagents=config.include_subagents,
        recent_days=config.recent_days,
        max_files=config.candidate_file_limit,
        max_bytes=config.candidate_bytes_limit,
        logger=logger,
    )
    summary["total_rollouts"] = len(rollout_files)
    logger.info(
        "sync_start",
        candidate_count=len(rollout_files),
        recent_days=config.recent_days,
        candidate_file_limit=config.candidate_file_limit,
        candidate_bytes_limit=config.candidate_bytes_limit,
    )

    for rollout_path in rollout_files:
        entry = state.get("files", {}).get(str(rollout_path))
        session_index_entry = None
        if isinstance(entry, dict):
            canonical_session_id = entry.get("canonical_session_id")
            if isinstance(canonical_session_id, str) and canonical_session_id:
                session_index_entry = session_index.get(canonical_session_id)
        if not needs_processing(
            entry,
            path=rollout_path,
            vault_root=vault_root,
            include_subagents=config.include_subagents,
            current_thread_name=session_index_entry.thread_name if session_index_entry else MISSING,
        ):
            summary["unchanged"] += 1
            continue

        try:
            envelope = load_session_envelope(
                rollout_path=rollout_path,
                session_index=session_index,
                state_entry=entry,
            )
        except (OSError, RolloutDataError, ValueError) as exc:
            summary["skipped_invalid"] += 1
            logger.warning(
                "rollout_skipped_invalid",
                rollout_path=rollout_path,
                error_type=type(exc).__name__,
            )
            continue

        if envelope is None:
            summary["unchanged"] += 1
            continue

        if envelope.is_subagent and not config.include_subagents:
            upsert_state_entry(
                state,
                rollout_path=rollout_path,
                included=False,
                extra={
                    **preserved_note_paths(entry),
                    "canonical_session_id": envelope.canonical_session_id,
                    "is_subagent": True,
                    "project_slug": envelope.project_slug,
                    "updated_at": envelope.updated_at,
                    "offset": rollout_path.stat().st_size,
                },
            )
            summary["skipped_subagents"] += 1
            logger.debug(
                "rollout_skipped_subagent",
                rollout_path=rollout_path,
                canonical_session_id=envelope.canonical_session_id,
            )
            continue

        conversation = build_conversation_note_record(envelope)
        note_path = resolve_note_path(vault_root, conversation["conversation_note_path"])
        note_exists = note_path.exists()
        header = render_conversation_header(envelope=envelope, note=conversation)
        header_hash = build_content_hash(header)
        render_hash = build_render_hash(envelope)
        previous_render_hash = entry.get("render_hash") if entry else None
        previous_header_hash = entry.get("header_hash") if entry else None
        previous_note_fingerprint = entry.get("conversation_note_fingerprint") if entry else None

        wrote_note = False
        processed_rollout = False
        if note_exists and previous_render_hash == render_hash and note_fingerprint_matches(
            note_path,
            previous_note_fingerprint,
        ):
            summary["unchanged"] += 1
            logger.debug(
                "rollout_noop_render",
                rollout_path=rollout_path,
                canonical_session_id=envelope.canonical_session_id,
            )
        else:
            append_start = append_start_index(
                envelope=envelope,
                state_entry=entry,
                note_path=note_path,
                previous_note_fingerprint=previous_note_fingerprint,
            )
            header_rewritable = can_rewrite_conversation_header(
                note_path=note_path,
                previous_note_fingerprint=previous_note_fingerprint,
            )
            header_changed = previous_header_hash != header_hash

            if header_rewritable and header_changed:
                try:
                    rewrite_conversation_header(
                        vault_root,
                        conversation["conversation_note_path"],
                        header,
                    )
                except ValueError:
                    header_rewritable = False
                else:
                    wrote_note = True
                    if not processed_rollout:
                        summary["processed"] += 1
                        processed_rollout = True
                    summary["rewritten"] += 1
                    logger.debug(
                        "rollout_header_rewritten",
                        rollout_path=rollout_path,
                        canonical_session_id=envelope.canonical_session_id,
                        note_path=conversation["conversation_note_path"],
                    )

            if append_start is not None:
                messages_to_append = envelope.messages[append_start:]
                if messages_to_append:
                    append_conversation_transcript(
                        vault_root,
                        conversation["conversation_note_path"],
                        messages_to_append,
                    )
                    wrote_note = True
                    if not processed_rollout:
                        summary["processed"] += 1
                        processed_rollout = True
                    summary["appended"] += 1
                    logger.debug(
                        "rollout_appended",
                        rollout_path=rollout_path,
                        canonical_session_id=envelope.canonical_session_id,
                        message_count=len(envelope.messages),
                        note_path=conversation["conversation_note_path"],
                        appended_count=len(messages_to_append),
                    )

            if not wrote_note:
                write_conversation_note(vault_root, envelope)
                wrote_note = True
                if not processed_rollout:
                    summary["processed"] += 1
                summary["rewritten"] += 1
                logger.debug(
                    "rollout_rewritten",
                    rollout_path=rollout_path,
                    canonical_session_id=envelope.canonical_session_id,
                    message_count=len(envelope.messages),
                    note_path=conversation["conversation_note_path"],
                )

        transcript_hash = hashlib.sha1(
            "\n".join(message.message_key for message in envelope.messages).encode("utf-8")
        ).hexdigest()
        note_fingerprint = file_fingerprint(note_path) if note_exists or wrote_note else {}
        upsert_state_entry(
            state,
            rollout_path=rollout_path,
            included=True,
            extra={
                **conversation,
                "canonical_session_id": envelope.canonical_session_id,
                "is_subagent": envelope.is_subagent,
                "originator": envelope.originator,
                "thread_name": envelope.thread_name,
                "transcript_hash": transcript_hash,
                "render_hash": render_hash,
                "header_hash": header_hash,
                "last_written_message_count": len(envelope.messages),
                "last_written_message_key": last_message_key(envelope),
                "conversation_note_fingerprint": note_fingerprint,
                "offset": rollout_path.stat().st_size,
                "last_note_write_at": utc_now_iso()
                if wrote_note
                else (entry.get("last_note_write_at", "") if entry else ""),
            },
        )

    active_conversations = included_conversations(state)
    write_daily_notes(vault_root, active_conversations)
    write_project_notes(vault_root, active_conversations)
    clear_stale_conversation_notes(vault_root, previous_conversations, active_conversations)
    clear_stale_index_notes(vault_root, previous_conversations, active_conversations)
    update_runtime_state(
        state,
        session_index_path=session_index_path,
        discovery_complete=discovery_complete(
            rollout_files,
            max_files=config.candidate_file_limit,
            max_bytes=config.candidate_bytes_limit,
        ),
    )
    save_state(config.state_file, state)
    summary["duration_ms"] = elapsed_ms(started)
    logger.info("sync_complete", **summary)
    return summary


def watch(
    *,
    config: SyncConfig,
    logger: EventLogger | None = None,
) -> None:
    logger = logger or EventLogger(config.log_level)
    while True:
        try:
            sync_once(config=config, logger=logger, allow_pause=True)
        except Exception as exc:
            logger.error("watch_loop_error", error_type=type(exc).__name__)
        time.sleep(max(config.interval_seconds, 1))


def load_session_envelope(
    *,
    rollout_path: Path,
    session_index: dict[str, object],
    state_entry: dict[str, object] | None,
) -> SessionEnvelope | None:
    if append_only_change(state_entry, path=rollout_path):
        offset = state_entry.get("offset") if state_entry else None
        if isinstance(offset, int):
            try:
                records, next_offset = load_rollout_records_from_offset(rollout_path, offset)
            except RolloutDataError:
                return build_session_envelope(rollout_path, session_index)
            if not records and next_offset == offset:
                return None

    return build_session_envelope(rollout_path, session_index)


def can_fast_skip_sync(
    state: dict[str, object],
    *,
    session_index_path: Path,
    vault_root: Path,
    include_subagents: bool,
) -> bool:
    if state.get("discovery_complete") is not True:
        return False
    if not session_index_path.exists():
        return False
    previous = state.get("session_index")
    if not isinstance(previous, dict):
        return False
    if previous != file_fingerprint(session_index_path):
        return False
    if state.get("privacy_migration_version") != PRIVACY_MIGRATION_VERSION:
        return False

    for raw_path, state_entry in state.get("files", {}).items():
        if not isinstance(raw_path, str) or not isinstance(state_entry, dict):
            return False
        rollout_path = Path(raw_path)
        if not rollout_path.exists():
            return False
        if needs_processing(
            state_entry,
            path=rollout_path,
            vault_root=vault_root,
            include_subagents=include_subagents,
        ):
            return False
    return True


def update_runtime_state(
    state: dict[str, object],
    *,
    session_index_path: Path,
    discovery_complete: bool | None = None,
) -> None:
    state["last_poll_at"] = utc_now_iso()
    state["session_index"] = file_fingerprint(session_index_path) if session_index_path.exists() else {}
    state["privacy_migration_version"] = PRIVACY_MIGRATION_VERSION
    if discovery_complete is not None:
        state["discovery_complete"] = discovery_complete


def discovery_complete(rollout_files: list[Path], *, max_files: int, max_bytes: int) -> bool:
    if len(rollout_files) >= max_files:
        return False
    total_bytes = 0
    for rollout_path in rollout_files:
        try:
            total_bytes += rollout_path.stat().st_size
        except OSError:
            return False
    return total_bytes < max_bytes


def preserved_note_paths(entry: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(entry, dict):
        return {}
    preserved: dict[str, object] = {}
    for key in ("conversation_note_path", "daily_note_path", "project_note_path", "daily_entries"):
        value = entry.get(key)
        if value:
            preserved[key] = value
    return preserved


def clear_stale_index_notes(
    vault_root: Path,
    previous_conversations: list[dict[str, object]],
    active_conversations: list[dict[str, object]],
) -> None:
    previous_daily, previous_projects = index_note_paths(previous_conversations)
    active_daily, active_projects = index_note_paths(active_conversations)

    for daily_path in sorted(previous_daily - active_daily):
        target = resolve_note_path(vault_root, daily_path)
        if not target.exists():
            continue
        date_key = Path(daily_path).stem
        write_sectioned_note(
            vault_root=vault_root,
            relative_path=daily_path,
            header=f"# Codex Daily Log - {date_key}",
            managed_content="",
        )

    for project_path in sorted(previous_projects - active_projects):
        target = resolve_note_path(vault_root, project_path)
        if not target.exists():
            continue
        project_slug = Path(project_path).stem
        write_sectioned_note(
            vault_root=vault_root,
            relative_path=project_path,
            header=f"# {project_slug}",
            managed_content="",
        )


def clear_stale_conversation_notes(
    vault_root: Path,
    previous_conversations: list[dict[str, object]],
    active_conversations: list[dict[str, object]],
) -> None:
    active_paths = conversation_note_paths(active_conversations)
    for conversation in previous_conversations:
        relative_path = conversation.get("conversation_note_path")
        if not isinstance(relative_path, str) or not relative_path:
            continue
        if relative_path in active_paths:
            continue
        delete_stale_conversation_note(
            vault_root,
            relative_path,
            conversation.get("conversation_note_fingerprint"),
        )


def conversation_note_paths(conversations: list[dict[str, object]]) -> set[str]:
    paths: set[str] = set()
    for conversation in conversations:
        relative_path = conversation.get("conversation_note_path")
        if isinstance(relative_path, str) and relative_path:
            paths.add(relative_path)
    return paths


def delete_stale_conversation_note(
    vault_root: Path,
    relative_path: str,
    expected_fingerprint: object,
) -> bool:
    if not isinstance(expected_fingerprint, dict):
        return False
    target = resolve_note_path(vault_root, relative_path)
    if not target.exists():
        return False
    if expected_fingerprint != file_fingerprint(target):
        return False
    if CONVERSATION_NOTE_MARKER not in target.read_text(encoding="utf-8"):
        return False
    if expected_fingerprint != file_fingerprint(target):
        return False
    target.unlink()
    return True


def index_note_paths(conversations: list[dict[str, object]]) -> tuple[set[str], set[str]]:
    daily_paths: set[str] = set()
    project_paths: set[str] = set()
    for conversation in conversations:
        daily_entries = conversation.get("daily_entries")
        if isinstance(daily_entries, list) and daily_entries:
            for entry in daily_entries:
                if not isinstance(entry, dict):
                    continue
                date_key = str(entry.get("date", "")).strip()
                if date_key:
                    daily_paths.add(f"Codex/Daily/{date_key}.md")
        else:
            daily_path = conversation.get("daily_note_path")
            if isinstance(daily_path, str) and daily_path:
                daily_paths.add(daily_path)

        project_path = conversation.get("project_note_path")
        if isinstance(project_path, str) and project_path:
            project_paths.add(project_path)
    return daily_paths, project_paths


def build_render_hash(envelope: SessionEnvelope) -> str:
    fingerprint = [
        envelope.canonical_session_id,
        envelope.originator or "",
        envelope.project_slug,
        envelope.thread_name or "",
        envelope.title_seed,
        envelope.started_at or "",
        session_status(envelope),
        *[message.message_key for message in envelope.messages],
    ]
    return hashlib.sha1("\n".join(fingerprint).encode("utf-8")).hexdigest()


def build_content_hash(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


def note_fingerprint_matches(note_path: Path, expected: object) -> bool:
    if not isinstance(expected, dict):
        return False
    if not note_path.exists():
        return False
    return expected == file_fingerprint(note_path)


def append_start_index(
    *,
    envelope: SessionEnvelope,
    state_entry: dict[str, object] | None,
    note_path: Path,
    previous_note_fingerprint: object,
) -> int | None:
    if state_entry is None or not note_path.exists():
        return None
    if not note_fingerprint_matches(note_path, previous_note_fingerprint):
        return None

    previous_count = state_entry.get("last_written_message_count")
    previous_key = state_entry.get("last_written_message_key")
    if not isinstance(previous_count, int) or previous_count < 0:
        return None
    if previous_count > len(envelope.messages):
        return None
    if previous_count > 0:
        expected_key = envelope.messages[previous_count - 1].message_key
        if previous_key != expected_key:
            return None
    return previous_count


def can_rewrite_conversation_header(
    *,
    note_path: Path,
    previous_note_fingerprint: object,
) -> bool:
    return note_path.exists() and note_fingerprint_matches(note_path, previous_note_fingerprint)


def last_message_key(envelope: SessionEnvelope) -> str:
    if not envelope.messages:
        return ""
    return envelope.messages[-1].message_key


def session_status(envelope: SessionEnvelope) -> str:
    if any(message.role == "assistant" for message in envelope.messages):
        return "completed"
    return "in_progress"


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
