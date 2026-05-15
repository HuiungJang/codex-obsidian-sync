from __future__ import annotations

import hashlib
import json
import re
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Iterator

from .models import SessionMeta, SourceClassification, TranscriptMessage

ALLOWED_ASSISTANT_PHASES = {"", "final_answer"}


class RolloutDataError(ValueError):
    pass


def load_rollout_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in iter_rollout_records(path):
        records.append(record)
    return records


def iter_rollout_records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except JSONDecodeError as exc:
                if not raw_line.endswith("\n"):
                    return
                raise RolloutDataError(
                    f"Malformed JSONL record in {path} at line {line_number}"
                ) from exc
            if not isinstance(payload, dict):
                raise RolloutDataError(
                    f"Non-object JSONL record in {path} at line {line_number}"
                )
            yield payload


def load_rollout_records_from_offset(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    with path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read()

    if not chunk:
        return [], offset

    complete_chunk = chunk
    if not chunk.endswith(b"\n"):
        last_newline = chunk.rfind(b"\n")
        if last_newline == -1:
            return [], offset
        complete_chunk = chunk[: last_newline + 1]

    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(complete_chunk.decode("utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except JSONDecodeError as exc:
            raise RolloutDataError(
                f"Malformed JSONL record in {path} after offset {offset} at line {line_number}"
            ) from exc
        if not isinstance(payload, dict):
            raise RolloutDataError(
                f"Non-object JSONL record in {path} after offset {offset} at line {line_number}"
            )
        records.append(payload)

    return records, offset + len(complete_chunk)


def summarize_rollout_file(path: Path) -> tuple[list[SessionMeta], list[TranscriptMessage]]:
    metas: list[SessionMeta] = []
    messages: list[TranscriptMessage] = []
    seen_keys: set[str] = set()

    for record in iter_rollout_records(path):
        if record.get("type") == "session_meta":
            payload = _as_mapping(record.get("payload"))
            classification = classify_source(payload.get("source"))
            session_id = str(payload.get("id", ""))
            if session_id:
                metas.append(
                    SessionMeta(
                        session_id=session_id,
                        timestamp=_string_or_none(payload.get("timestamp")),
                        cwd=_string_or_none(payload.get("cwd")),
                        originator=_string_or_none(payload.get("originator")),
                        source_classification=classification,
                        forked_from_id=_string_or_none(payload.get("forked_from_id")),
                    )
                )
            continue

        message = extract_candidate_message(record, seen_keys)
        if message is not None:
            messages.append(message)

    return metas, messages


def collect_session_metas(records: list[dict[str, Any]]) -> list[SessionMeta]:
    metas: list[SessionMeta] = []
    for record in records:
        if record.get("type") != "session_meta":
            continue
        payload = _as_mapping(record.get("payload"))
        classification = classify_source(payload.get("source"))
        metas.append(
            SessionMeta(
                session_id=str(payload.get("id", "")),
                timestamp=_string_or_none(payload.get("timestamp")),
                cwd=_string_or_none(payload.get("cwd")),
                originator=_string_or_none(payload.get("originator")),
                source_classification=classification,
                forked_from_id=_string_or_none(payload.get("forked_from_id")),
            )
        )
    return [meta for meta in metas if meta.session_id]


def classify_source(source: Any) -> SourceClassification:
    if isinstance(source, str) and source.strip():
        return SourceClassification(source_kind=source, is_subagent=False)

    if isinstance(source, dict):
        subagent = _as_mapping(source.get("subagent"))
        thread_spawn = _as_mapping(subagent.get("thread_spawn"))
        parent_session_id = _string_or_none(thread_spawn.get("parent_thread_id"))
        if thread_spawn:
            return SourceClassification(
                source_kind="subagent.thread_spawn",
                is_subagent=True,
                parent_session_id=parent_session_id,
            )

    return SourceClassification(source_kind="unknown", is_subagent=False)


def extract_candidate_messages(records: list[dict[str, Any]]) -> list[TranscriptMessage]:
    messages: list[TranscriptMessage] = []
    seen_keys: set[str] = set()

    for record in records:
        message = extract_candidate_message(record, seen_keys)
        if message is not None:
            messages.append(message)

    return messages


def extract_candidate_message(
    record: dict[str, Any],
    seen_keys: set[str],
) -> TranscriptMessage | None:
    if record.get("type") != "response_item":
        return None

    payload = _as_mapping(record.get("payload"))
    if payload.get("type") != "message":
        return None

    role = _string_or_none(payload.get("role"))
    if role not in {"user", "assistant"}:
        return None

    phase = _string_or_none(payload.get("phase")) or ""
    if role == "assistant" and phase not in ALLOWED_ASSISTANT_PHASES:
        return None

    text = extract_text(payload.get("content"))
    if not text:
        return None
    if role == "user" and is_control_message(text):
        return None

    timestamp = _string_or_none(record.get("timestamp")) or ""
    key = build_message_key(timestamp=timestamp, role=role, phase=phase, text=text)
    if key in seen_keys:
        return None

    seen_keys.add(key)
    return TranscriptMessage(
        message_key=key,
        role=role,
        phase=phase,
        timestamp=timestamp,
        text=text,
    )


def extract_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {"input_text", "output_text"}:
            continue
        text = _normalize_text_block(_string_or_none(item.get("text")) or "")
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def build_message_key(*, timestamp: str, role: str, phase: str, text: str) -> str:
    normalized = normalize_for_hash(text)
    digest = hashlib.sha1(
        f"{timestamp}\n{role}\n{phase}\n{normalized}".encode("utf-8")
    ).hexdigest()
    return digest


def normalize_for_hash(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def is_control_message(text: str) -> bool:
    stripped = text.strip()
    return (
        stripped.startswith("# AGENTS.md instructions")
        or stripped.startswith("<environment_context>")
        or stripped.startswith("<subagent_notification>")
        or stripped.startswith("<turn_aborted>")
    )


def _normalize_text_block(text: str) -> str:
    lines = [line.strip() for line in text.replace("\r\n", "\n").splitlines()]
    return "\n".join(line for line in lines if line)


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None
