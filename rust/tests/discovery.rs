use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use codex_obsidian_sync_rs::discovery::{
    SessionIndex, build_session_envelope, build_session_envelope_from_records, load_session_index,
    parse_rollout_session_id, slugify,
};
use codex_obsidian_sync_rs::parser::RolloutRecord;
use serde_json::{Map, Value, json};

#[test]
fn parse_rollout_session_id_uses_last_uuid() {
    let path = Path::new(
        "/tmp/rollout-2026-03-25T15-21-17-019d23a7-9258-7810-93cc-c6833b3481aa-019d23a7-9258-7810-93cc-c6833b3481cc.jsonl",
    );

    let session_id = parse_rollout_session_id(path).unwrap();

    assert_eq!(session_id, "019d23a7-9258-7810-93cc-c6833b3481cc");
}

#[test]
fn load_session_index_skips_invalid_lines_and_duplicate_uses_last() {
    let root = temp_dir("session-index");
    let index_path = root.join("session_index.jsonl");
    fs::write(
        &index_path,
        [
            "",
            r#"{"id":"valid-session","thread_name":"first","updated_at":"2026-03-25T06:30:00Z"}"#,
            r#"{"thread_name":"missing id"}"#,
            r#"[]"#,
            r#"{"id":"valid-session","thread_name":"last","updated_at":"2026-03-25T06:31:00Z"}"#,
            r#"{"id":"#,
        ]
        .join("\n"),
    )
    .unwrap();

    let entries = load_session_index(&index_path).unwrap();

    assert_eq!(entries.keys().collect::<Vec<_>>(), vec!["valid-session"]);
    assert_eq!(
        entries["valid-session"].thread_name.as_deref(),
        Some("last")
    );
    assert_eq!(
        entries["valid-session"].updated_at.as_deref(),
        Some("2026-03-25T06:31:00Z")
    );
}

#[test]
fn load_session_index_preserves_first_insertion_order() {
    let root = temp_dir("session-index-order");
    let index_path = root.join("session_index.jsonl");
    fs::write(
        &index_path,
        [
            r#"{"id":"b-session","thread_name":"b","updated_at":"2026-03-25T06:30:00Z"}"#,
            r#"{"id":"a-session","thread_name":"a","updated_at":"2026-03-25T06:31:00Z"}"#,
            r#"{"id":"b-session","thread_name":"b-updated","updated_at":"2026-03-25T06:32:00Z"}"#,
        ]
        .join("\n"),
    )
    .unwrap();

    let entries = load_session_index(&index_path).unwrap();

    assert_eq!(
        entries.keys().map(String::as_str).collect::<Vec<_>>(),
        vec!["b-session", "a-session"]
    );
    assert_eq!(
        entries["b-session"].thread_name.as_deref(),
        Some("b-updated")
    );
}

#[test]
fn missing_session_index_loads_empty() {
    let entries =
        load_session_index(Path::new("/tmp/codex-obsidian-sync-rs-missing-index.jsonl")).unwrap();

    assert!(entries.is_empty());
}

#[test]
fn build_session_envelope_prefers_matching_meta_and_filters_messages() {
    let child_session_id = "019d23a7-9258-7810-93cc-c6833b3481cc";
    let parent_session_id = "019d2378-4ee8-7620-879c-ef43a2a89a3e";
    let rollout_path =
        Path::new("/tmp/rollout-2026-03-25T15-21-17-019d23a7-9258-7810-93cc-c6833b3481cc.jsonl");
    let index = SessionIndex::from([(
        child_session_id.to_owned(),
        serde_json::from_value(json!({
            "id": child_session_id,
            "thread_name": "Deepen plan",
            "updated_at": "2026-03-25T06:30:00Z"
        }))
        .unwrap(),
    )]);
    let records = vec![
        object(json!({
            "timestamp": "2026-03-25T06:21:17Z",
            "type": "session_meta",
            "payload": {
                "id": child_session_id,
                "timestamp": "2026-03-25T06:21:17Z",
                "cwd": "/tmp/demo-project",
                "originator": "Codex Desktop",
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": parent_session_id,
                            "depth": 1
                        }
                    }
                }
            }
        })),
        object(json!({
            "timestamp": "2026-03-25T06:21:18Z",
            "type": "session_meta",
            "payload": {
                "id": parent_session_id,
                "timestamp": "2026-03-25T05:29:40Z",
                "cwd": "/tmp/demo-project",
                "originator": "Codex Desktop",
                "source": "vscode"
            }
        })),
        message_record(
            "2026-03-25T06:21:20Z",
            "developer",
            None,
            "ignore developer message",
        ),
        message_record(
            "2026-03-25T06:21:21Z",
            "user",
            None,
            "Keep only this user question",
        ),
        message_record(
            "2026-03-25T06:21:22Z",
            "assistant",
            Some("commentary"),
            "ignore commentary",
        ),
        message_record(
            "2026-03-25T06:21:23Z",
            "assistant",
            Some("final_answer"),
            "Keep this final answer",
        ),
        message_record(
            "2026-03-25T06:21:23Z",
            "assistant",
            Some("final_answer"),
            "Keep this final answer",
        ),
        message_record(
            "2026-03-25T06:21:24Z",
            "assistant",
            None,
            "Keep this empty phase answer too",
        ),
    ];

    let envelope = build_session_envelope_from_records(rollout_path, &index, &records).unwrap();

    assert_eq!(envelope.canonical_session_id, child_session_id);
    assert!(envelope.is_subagent);
    assert_eq!(
        envelope.parent_session_id.as_deref(),
        Some(parent_session_id)
    );
    assert_eq!(envelope.source_kind, "subagent.thread_spawn");
    assert_eq!(envelope.project_slug, "demo-project");
    assert_eq!(envelope.title_seed, "keep-only-this-user-question");
    assert_eq!(envelope.thread_name.as_deref(), Some("Deepen plan"));
    assert_eq!(envelope.updated_at.as_deref(), Some("2026-03-25T06:30:00Z"));
    assert_eq!(
        envelope
            .messages
            .iter()
            .map(|message| message.role.as_str())
            .collect::<Vec<_>>(),
        vec!["user", "assistant", "assistant"]
    );
}

#[test]
fn build_session_envelope_falls_back_to_thread_name_for_title() {
    let session_id = "019d23a7-9258-7810-93cc-c6833b3481cc";
    let rollout_path =
        Path::new("/tmp/rollout-2026-03-25T15-21-17-019d23a7-9258-7810-93cc-c6833b3481cc.jsonl");
    let index = SessionIndex::from([(
        session_id.to_owned(),
        serde_json::from_value(json!({
            "id": session_id,
            "thread_name": "Fallback title from index",
            "updated_at": "2026-03-25T06:30:00Z"
        }))
        .unwrap(),
    )]);
    let records = vec![
        session_meta(session_id, "/tmp/demo-project", "cli"),
        message_record(
            "2026-03-25T06:21:23Z",
            "assistant",
            Some("final_answer"),
            "Assistant only conversation",
        ),
    ];

    let envelope = build_session_envelope_from_records(rollout_path, &index, &records).unwrap();

    assert!(!envelope.is_subagent);
    assert_eq!(envelope.title_seed, "fallback-title-from-index");
    assert_eq!(envelope.project_slug, "demo-project");
}

#[test]
fn build_session_envelope_uses_control_text_only_as_last_resort() {
    let session_id = "019d23a7-9258-7810-93cc-c6833b3481cd";
    let rollout_path =
        Path::new("/tmp/rollout-2026-03-25T15-21-17-019d23a7-9258-7810-93cc-c6833b3481cd.jsonl");
    let index = SessionIndex::from([(
        session_id.to_owned(),
        serde_json::from_value(json!({
            "id": session_id,
            "thread_name": "Index fallback",
            "updated_at": "2026-03-25T06:30:00Z"
        }))
        .unwrap(),
    )]);
    let records = vec![
        session_meta(session_id, "/tmp/demo-project", "vscode"),
        message_record(
            "2026-03-25T06:21:18Z",
            "user",
            None,
            "# AGENTS.md instructions for /tmp/demo-project",
        ),
        message_record(
            "2026-03-25T06:21:19Z",
            "user",
            None,
            "<environment_context>\n<cwd>/tmp/demo-project</cwd>\n</environment_context>",
        ),
        message_record(
            "2026-03-25T06:21:20Z",
            "user",
            None,
            "/workflows:brainstorm codex 를 통해 대화한 내용을 저장하고 싶어",
        ),
    ];

    let envelope = build_session_envelope_from_records(rollout_path, &index, &records).unwrap();

    assert_eq!(
        envelope.title_seed,
        "workflows-brainstorm-codex-를-통해-대화한-내용을-저장하고-싶어"
    );
    assert_eq!(
        envelope
            .messages
            .iter()
            .map(|message| message.text.as_str())
            .collect::<Vec<_>>(),
        vec!["/workflows:brainstorm codex 를 통해 대화한 내용을 저장하고 싶어"]
    );
}

#[test]
fn build_session_envelope_reads_file_and_ignores_trailing_partial_jsonl_record() {
    let root = temp_dir("envelope-from-file");
    let session_id = "019d23a7-9258-7810-93cc-c6833b3481ce";
    let rollout_path = root.join(format!("rollout-2026-03-25T15-21-17-{session_id}.jsonl"));
    write_jsonl(
        &rollout_path,
        &[
            session_meta(session_id, "/tmp/demo-project", "vscode"),
            message_record("2026-03-25T06:21:19Z", "user", None, "complete message"),
        ],
    );
    fs::OpenOptions::new()
        .append(true)
        .open(&rollout_path)
        .unwrap()
        .write_all(br#"{"timestamp":"2026-03-25T06:21:20Z","type":"response_item","payload":"#)
        .unwrap();

    let index = SessionIndex::from([(
        session_id.to_owned(),
        serde_json::from_value(json!({
            "id": session_id,
            "thread_name": "partial",
            "updated_at": "2026-03-25T06:30:00Z"
        }))
        .unwrap(),
    )]);
    let envelope = build_session_envelope(&rollout_path, &index).unwrap();

    assert_eq!(
        envelope
            .messages
            .iter()
            .map(|message| message.text.as_str())
            .collect::<Vec<_>>(),
        vec!["complete message"]
    );
}

#[test]
fn slugify_normalizes_unicode_whitespace_punctuation_and_length() {
    assert_eq!(
        slugify("  Plan 기반 구현!! / next step  ", 80),
        "plan-기반-구현-next-step"
    );
    assert_eq!(slugify("ＡＢＣ／테스트", 80), "abc-테스트");
    assert_eq!(slugify("a\u{093c}b", 80), "a-b");
    assert_eq!(slugify("!!!", 80), "conversation");
    assert_eq!(slugify("abcdef-", 6), "abcdef");
}

fn session_meta(session_id: &str, cwd: &str, source: &str) -> RolloutRecord {
    object(json!({
        "timestamp": "2026-03-25T06:21:17Z",
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "timestamp": "2026-03-25T06:21:17Z",
            "cwd": cwd,
            "originator": "Codex Desktop",
            "source": source
        }
    }))
}

fn message_record(timestamp: &str, role: &str, phase: Option<&str>, text: &str) -> RolloutRecord {
    let mut payload = json!({
        "type": "message",
        "role": role,
        "content": [
            {
                "type": if role == "assistant" { "output_text" } else { "input_text" },
                "text": text
            }
        ]
    });
    if let Some(phase) = phase {
        payload
            .as_object_mut()
            .unwrap()
            .insert("phase".to_owned(), json!(phase));
    }

    object(json!({
        "timestamp": timestamp,
        "type": "response_item",
        "payload": payload
    }))
}

fn write_jsonl(path: &Path, records: &[RolloutRecord]) {
    let mut content = String::new();
    for record in records {
        content.push_str(&serde_json::to_string(record).unwrap());
        content.push('\n');
    }
    fs::write(path, content).unwrap();
}

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(object) => object,
        _ => panic!("test value must be object"),
    }
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-discovery-{name}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let path = std::env::temp_dir().join(unique);
    fs::create_dir_all(&path).unwrap();
    path
}
