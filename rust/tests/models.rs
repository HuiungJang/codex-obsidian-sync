use serde_json::json;

use codex_obsidian_sync_rs::error::SyncError;
use codex_obsidian_sync_rs::models::{
    SessionEnvelope, SessionIndexEntry, SessionMeta, SourceClassification, TranscriptMessage,
};

#[test]
fn session_index_entry_uses_python_index_shape() {
    let entry: SessionIndexEntry = serde_json::from_value(json!({
        "id": "019f5f16-0000-7000-8000-000000000001",
        "thread_name": "  Baseline Conversation  ",
        "updated_at": "2026-05-14T00:00:00Z",
        "future_field": "ignored"
    }))
    .unwrap();

    assert_eq!(entry.session_id, "019f5f16-0000-7000-8000-000000000001");
    assert_eq!(entry.thread_name.as_deref(), Some("Baseline Conversation"));

    let serialized = serde_json::to_value(entry).unwrap();
    assert_eq!(
        serialized,
        json!({
            "id": "019f5f16-0000-7000-8000-000000000001",
            "thread_name": "Baseline Conversation",
            "updated_at": "2026-05-14T00:00:00Z"
        })
    );
}

#[test]
fn session_envelope_deserializes_python_defaults_and_ignores_unknown_fields() {
    let envelope: SessionEnvelope = serde_json::from_value(json!({
        "canonical_session_id": "019f5f16-0000-7000-8000-000000000001",
        "rollout_path": "/tmp/baseline/rollout.jsonl",
        "originator": " ",
        "messages": [],
        "unknown": "ignored"
    }))
    .unwrap();

    assert_eq!(
        envelope.rollout_path.to_string_lossy(),
        "/tmp/baseline/rollout.jsonl"
    );
    assert_eq!(envelope.originator, None);
    assert_eq!(envelope.source_kind, "unknown");
    assert!(!envelope.is_subagent);
    assert_eq!(envelope.project_slug, "unknown-project");
    assert_eq!(envelope.title_seed, "conversation");
    assert!(envelope.messages.is_empty());
}

#[test]
fn session_envelope_round_trips_python_serialized_shape() {
    let payload = json!({
        "canonical_session_id": "019f5f16-0000-7000-8000-000000000001",
        "rollout_path": "/tmp/codex/rollout.jsonl",
        "originator": "Codex Desktop",
        "source_kind": "vscode",
        "is_subagent": false,
        "parent_session_id": null,
        "cwd": "/tmp/baseline-project",
        "project_slug": "baseline-project",
        "thread_name": "Baseline Conversation",
        "title_seed": "baseline-first-question",
        "started_at": "2026-05-14T00:00:00Z",
        "updated_at": "2026-05-14T00:00:00Z",
        "messages": [
            {
                "message_key": "0c51ecc96cf6542fab270ac35978170cc3dcdb9c",
                "role": "user",
                "phase": "",
                "timestamp": "2026-05-14T00:01:00Z",
                "text": "Baseline first question"
            }
        ]
    });

    let envelope: SessionEnvelope = serde_json::from_value(payload.clone()).unwrap();
    assert_eq!(serde_json::to_value(envelope).unwrap(), payload);
}

#[test]
fn transcript_message_phase_defaults_to_empty_string() {
    let message: TranscriptMessage = serde_json::from_value(json!({
        "message_key": "key",
        "role": "user",
        "timestamp": "2026-05-14T00:01:00Z",
        "text": "hello"
    }))
    .unwrap();

    assert_eq!(message.phase, "");
}

#[test]
fn session_meta_source_classification_defaults_to_unknown() {
    let meta: SessionMeta = serde_json::from_value(json!({
        "session_id": "019f5f16-0000-7000-8000-000000000001"
    }))
    .unwrap();

    assert_eq!(
        meta.source_classification,
        SourceClassification {
            source_kind: "unknown".to_owned(),
            is_subagent: false,
            parent_session_id: None,
        }
    );
}

#[test]
fn error_display_is_content_free() {
    let raw_transcript = "Baseline first question";
    let errors = [
        SyncError::Config,
        SyncError::Parse,
        SyncError::Discovery,
        SyncError::Render,
        SyncError::DryRunOutput,
        SyncError::UnsupportedCommand,
    ];

    for error in errors {
        let display = error.to_string();
        assert!(!display.contains(raw_transcript));
        assert!(!display.contains("/tmp/baseline-project"));
    }
}
