from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionIndexEntry:
    session_id: str
    thread_name: str | None
    updated_at: str | None


@dataclass(frozen=True)
class SourceClassification:
    source_kind: str
    is_subagent: bool
    parent_session_id: str | None = None


@dataclass(frozen=True)
class SessionMeta:
    session_id: str
    timestamp: str | None
    cwd: str | None
    originator: str | None
    source_classification: SourceClassification
    forked_from_id: str | None = None


@dataclass(frozen=True)
class TranscriptMessage:
    message_key: str
    role: str
    phase: str
    timestamp: str
    text: str


@dataclass(frozen=True)
class SessionEnvelope:
    canonical_session_id: str
    rollout_path: Path
    originator: str | None
    source_kind: str
    is_subagent: bool
    parent_session_id: str | None
    cwd: str | None
    project_slug: str
    thread_name: str | None
    title_seed: str
    started_at: str | None
    updated_at: str | None
    messages: tuple[TranscriptMessage, ...]


def serialize_envelope(envelope: SessionEnvelope) -> dict[str, Any]:
    return {
        "canonical_session_id": envelope.canonical_session_id,
        "rollout_path": str(envelope.rollout_path),
        "originator": envelope.originator,
        "source_kind": envelope.source_kind,
        "is_subagent": envelope.is_subagent,
        "parent_session_id": envelope.parent_session_id,
        "cwd": envelope.cwd,
        "project_slug": envelope.project_slug,
        "thread_name": envelope.thread_name,
        "title_seed": envelope.title_seed,
        "started_at": envelope.started_at,
        "updated_at": envelope.updated_at,
        "messages": [
            {
                "message_key": message.message_key,
                "role": message.role,
                "phase": message.phase,
                "timestamp": message.timestamp,
                "text": message.text,
            }
            for message in envelope.messages
        ],
    }


def deserialize_envelope(data: dict[str, Any], rollout_path: Path) -> SessionEnvelope:
    messages = tuple(
        TranscriptMessage(
            message_key=str(message["message_key"]),
            role=str(message["role"]),
            phase=str(message.get("phase", "")),
            timestamp=str(message["timestamp"]),
            text=str(message["text"]),
        )
        for message in data.get("messages", [])
    )
    return SessionEnvelope(
        canonical_session_id=str(data["canonical_session_id"]),
        rollout_path=rollout_path,
        originator=_string_or_none(data.get("originator")),
        source_kind=str(data.get("source_kind", "unknown")),
        is_subagent=bool(data.get("is_subagent", False)),
        parent_session_id=_string_or_none(data.get("parent_session_id")),
        cwd=_string_or_none(data.get("cwd")),
        project_slug=str(data.get("project_slug", "unknown-project")),
        thread_name=_string_or_none(data.get("thread_name")),
        title_seed=str(data.get("title_seed", "conversation")),
        started_at=_string_or_none(data.get("started_at")),
        updated_at=_string_or_none(data.get("updated_at")),
        messages=messages,
    )


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None
