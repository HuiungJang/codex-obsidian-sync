from __future__ import annotations

import os
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Iterable

from .models import SessionEnvelope, TranscriptMessage
from .redaction import redact_text

MANAGED_START = "<!-- BEGIN CODEX MANAGED SECTION -->"
MANAGED_END = "<!-- END CODEX MANAGED SECTION -->"
CONVERSATION_NOTE_MARKER = "<!-- CODEX CONVERSATION NOTE v2 -->"
TRANSCRIPT_MARKER = "<!-- BEGIN CODEX TRANSCRIPT -->"


def validate_vault_root(vault_root: Path) -> Path:
    if not str(vault_root).strip():
        raise ValueError("Vault path is empty")
    if not vault_root.is_absolute():
        raise ValueError("Vault path must be absolute")
    resolved = vault_root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("Vault path must point to a directory")
    return resolved


def write_conversation_note(vault_root: Path, envelope: SessionEnvelope) -> dict[str, object]:
    note = build_conversation_note_record(envelope)
    content = render_conversation_note(envelope=envelope, note=note)
    write_managed_file(vault_root, note["conversation_note_path"], content)
    return note


def build_conversation_note_record(envelope: SessionEnvelope) -> dict[str, object]:
    started_date_key, started_time_key, year_key = conversation_start_parts(envelope)
    daily_entries = render_daily_entries(envelope)
    if daily_entries:
        latest_entry = daily_entries[-1]
        last_activity_date_key = latest_entry["date"]
        last_activity_time_label = latest_entry["time"]
    else:
        last_activity_date_key, last_activity_time_key, _ = activity_parts(envelope)
        last_activity_time_label = render_clock_label(last_activity_time_key)
    relative_path = (
        PurePosixPath("Codex")
        / "Conversations"
        / year_key
        / f"{started_date_key}-{started_time_key}-{envelope.title_seed}.md"
    )
    daily_relative = PurePosixPath("Codex") / "Daily" / f"{last_activity_date_key}.md"
    project_relative = PurePosixPath("Codex") / "Projects" / f"{envelope.project_slug}.md"
    title = envelope.thread_name or envelope.title_seed.replace("-", " ")
    return {
        "conversation_note_path": relative_path.as_posix(),
        "daily_note_path": daily_relative.as_posix(),
        "project_note_path": project_relative.as_posix(),
        "date": last_activity_date_key,
        "time": last_activity_time_label,
        "started_date": started_date_key,
        "started_time": render_clock_label(started_time_key),
        "title": title,
        "project_slug": envelope.project_slug,
        "canonical_session_id": envelope.canonical_session_id,
        "started_at": envelope.started_at or "",
        "updated_at": envelope.updated_at or "",
        "status": session_status(envelope),
        "daily_entries": daily_entries,
    }


def write_daily_notes(vault_root: Path, conversations: Iterable[dict[str, object]]) -> None:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for conversation in conversations:
        daily_entries = conversation.get("daily_entries")
        if isinstance(daily_entries, list) and daily_entries:
            for entry in daily_entries:
                if not isinstance(entry, dict):
                    continue
                date_key = str(entry.get("date", "")).strip()
                time_label = str(entry.get("time", "")).strip()
                if not date_key or not time_label:
                    continue
                grouped[date_key].append(
                    {
                        **conversation,
                        "date": date_key,
                        "time": time_label,
                    }
                )
            continue

        grouped[conversation["date"]].append(conversation)

    for date_key, items in grouped.items():
        sorted_items = sorted(items, key=lambda item: (item["time"], item["title"]))
        managed_content = render_daily_managed_section(sorted_items)
        write_sectioned_note(
            vault_root=vault_root,
            relative_path=f"Codex/Daily/{date_key}.md",
            header=f"# Codex Daily Log - {date_key}",
            managed_content=managed_content,
        )


def write_project_notes(vault_root: Path, conversations: Iterable[dict[str, object]]) -> None:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for conversation in conversations:
        grouped[conversation["project_slug"]].append(conversation)

    for project_slug, items in grouped.items():
        sorted_items = sorted(
            items,
            key=lambda item: (item["date"], item["time"], item["title"]),
            reverse=True,
        )
        first_seen = min(items, key=lambda item: (item["date"], item["time"]))
        header = (
            "---\n"
            "type: codex-project\n"
            f"project_slug: {project_slug}\n"
            f"first_seen_at: {first_seen['started_at']}\n"
            "---\n\n"
            f"# {project_slug}"
        )
        managed_content = render_project_managed_section(sorted_items)
        write_sectioned_note(
            vault_root=vault_root,
            relative_path=f"Codex/Projects/{project_slug}.md",
            header=header,
            managed_content=managed_content,
        )


def render_conversation_note(
    *,
    envelope: SessionEnvelope,
    note: dict[str, object],
) -> str:
    header = render_conversation_header(envelope=envelope, note=note)
    transcript = render_transcript_messages(envelope.messages)
    return f"{header}{transcript}".rstrip() + "\n"


def render_conversation_header(
    *,
    envelope: SessionEnvelope,
    note: dict[str, object],
) -> str:
    date_key, _, _ = conversation_start_parts(envelope)
    title = envelope.thread_name or envelope.title_seed.replace("-", " ")
    body_sections = [
        "---",
        "type: codex-conversation",
        f"date: {date_key}",
        f"session_id: {envelope.canonical_session_id}",
        f"originator: {envelope.originator or 'unknown'}",
        f"project_slug: {envelope.project_slug}",
        f"daily_note: {note['daily_note_path']}",
        f"status: {session_status(envelope)}",
        "tags:",
        "  - codex",
        "  - codex-conversation",
        f"  - project/{envelope.project_slug}",
        "---",
        "",
        CONVERSATION_NOTE_MARKER,
        "",
        f"# {title}",
        "",
        f"[[{obsidian_target(str(note['daily_note_path']))}]]",
        f"[[{obsidian_target(str(note['project_note_path']))}]]",
        "",
        "## Transcript",
        "",
        TRANSCRIPT_MARKER,
        "",
    ]
    return "\n".join(body_sections)


def render_transcript_messages(messages: Iterable[TranscriptMessage]) -> str:
    body_sections: list[str] = []
    for message in messages:
        body_sections.extend(render_transcript_message(message))
        body_sections.append("")
    return "\n".join(section for section in body_sections if section is not None)


def append_conversation_transcript(
    vault_root: Path,
    relative_path: str,
    messages: Iterable[TranscriptMessage],
) -> None:
    content = render_transcript_messages(messages).rstrip()
    if not content:
        return
    target = resolve_note_path(vault_root, relative_path)
    with target.open("a", encoding="utf-8") as handle:
        handle.write("\n")
        handle.write(content)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def extract_transcript_body(existing: str) -> str | None:
    marker = f"{TRANSCRIPT_MARKER}\n"
    marker_index = existing.find(marker)
    if marker_index == -1:
        return None
    return existing[marker_index + len(marker):]


def rewrite_conversation_header(
    vault_root: Path,
    relative_path: str,
    header: str,
) -> None:
    target = resolve_note_path(vault_root, relative_path)
    if not target.exists():
        raise ValueError(f"Conversation note missing: {target}")
    existing = target.read_text(encoding="utf-8")
    transcript_body = extract_transcript_body(existing)
    if transcript_body is None:
        raise ValueError(f"Conversation note transcript marker missing: {target}")
    write_atomic(target, f"{header}{transcript_body}".rstrip() + "\n")


def render_daily_managed_section(conversations: list[dict[str, object]]) -> str:
    sections: list[str] = []
    for conversation in conversations:
        sections.extend(
            [
                f"## {conversation['time']} {conversation['title']}",
                "> [!note]- Conversation",
                f"> [[{obsidian_target(conversation['conversation_note_path'])}|{conversation['title']}]]",
                f"> Project: [[{obsidian_target(conversation['project_note_path'])}|{conversation['project_slug']}]]",
                f"> Session: `{conversation['canonical_session_id']}`",
                f"> Status: `{conversation['status']}`",
                "",
            ]
        )
    return "\n".join(sections).rstrip()


def render_project_managed_section(conversations: list[dict[str, object]]) -> str:
    lines = ["## Conversations", ""]
    for conversation in conversations:
        lines.append(
            f"- {conversation['date']} {conversation['time']} [[{obsidian_target(conversation['conversation_note_path'])}|{conversation['title']}]]"
        )
    return "\n".join(lines).rstrip()


def write_sectioned_note(
    *,
    vault_root: Path,
    relative_path: str,
    header: str,
    managed_content: str,
) -> None:
    target = resolve_note_path(vault_root, relative_path)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    base = existing if existing else f"{header}\n"
    merged = merge_managed_section(base, managed_content)
    write_atomic(target, merged)


def write_managed_file(vault_root: Path, relative_path: str, content: str) -> None:
    target = resolve_note_path(vault_root, relative_path)
    write_atomic(target, content)


def merge_managed_section(existing: str, managed_content: str) -> str:
    managed_block = f"{MANAGED_START}\n{managed_content.rstrip()}\n{MANAGED_END}"
    if MANAGED_START in existing and MANAGED_END in existing:
        start = existing.index(MANAGED_START)
        end = existing.index(MANAGED_END) + len(MANAGED_END)
        merged = f"{existing[:start].rstrip()}\n\n{managed_block}\n{existing[end:].lstrip()}"
        return merged.rstrip() + "\n"

    existing = existing.rstrip()
    if existing:
        return f"{existing}\n\n{managed_block}\n"
    return f"{managed_block}\n"


def resolve_note_path(vault_root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe relative path: {relative_path}")

    target = vault_root.joinpath(*relative.parts)
    if target.is_symlink():
        raise ValueError(f"Refusing to overwrite symlink note: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = target.parent.resolve(strict=True)
    if not is_within_root(resolved_parent, vault_root):
        raise ValueError(f"Target parent escapes vault root: {target}")
    return target


def write_atomic(target: Path, content: str) -> None:
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        temp_name = None
        _fsync_directory(target.parent)
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            return
    finally:
        os.close(fd)


def render_transcript_message(message: TranscriptMessage) -> list[str]:
    heading = "User" if message.role == "user" else "Assistant"
    text = redact_text(message.text)
    return [f"### {heading}", text]


def session_status(envelope: SessionEnvelope) -> str:
    if any(message.role == "assistant" for message in envelope.messages):
        return "completed"
    return "in_progress"


def note_time_parts(envelope: SessionEnvelope) -> tuple[str, str, str]:
    timestamp = envelope.updated_at or envelope.started_at or ""
    dt = parse_iso_timestamp(timestamp)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H%M"), dt.strftime("%Y")


def conversation_start_parts(envelope: SessionEnvelope) -> tuple[str, str, str]:
    timestamp = envelope.started_at or envelope.updated_at or ""
    dt = parse_iso_timestamp(timestamp)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H%M"), dt.strftime("%Y")


def activity_parts(envelope: SessionEnvelope) -> tuple[str, str, str]:
    return note_time_parts(envelope)


def render_daily_entries(envelope: SessionEnvelope) -> list[dict[str, str]]:
    daily_last_activity: dict[str, str] = {}
    timestamps = [message.timestamp for message in envelope.messages if message.timestamp]
    if not timestamps:
        timestamps = [envelope.updated_at or envelope.started_at or ""]

    for timestamp in timestamps:
        if not timestamp:
            continue
        dt = parse_iso_timestamp(timestamp)
        date_key = dt.strftime("%Y-%m-%d")
        time_key = dt.strftime("%H%M")
        previous = daily_last_activity.get(date_key)
        if previous is None or time_key > previous:
            daily_last_activity[date_key] = time_key

    return [
        {"date": date_key, "time": render_clock_label(time_key)}
        for date_key, time_key in sorted(daily_last_activity.items())
    ]


def render_clock_label(time_key: str) -> str:
    return f"{time_key[:2]}:{time_key[2:]}"


def parse_iso_timestamp(value: str) -> datetime:
    normalized = (value or "").replace("Z", "+00:00")
    if normalized:
        return datetime.fromisoformat(normalized).astimezone()
    return datetime.now(UTC).astimezone()


def obsidian_target(relative_path: str) -> str:
    return relative_path[:-3] if relative_path.endswith(".md") else relative_path


def is_within_root(target: Path, root: Path) -> bool:
    return target == root or root in target.parents
