use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use codex_obsidian_sync_rs::discovery::{
    DiscoveryOptions, SessionIndex, build_session_envelope, build_session_envelope_from_records,
    discover_rollout_candidates, load_session_index, parse_rollout_session_id, slugify,
};
use codex_obsidian_sync_rs::parser::RolloutRecord;
use codex_obsidian_sync_rs::state_store::{StateEntry, SyncState, file_fingerprint};
use serde_json::{Map, Value, json};
use time::format_description::well_known::Rfc3339;
use time::{OffsetDateTime, UtcOffset};

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

#[test]
fn discover_rollout_candidates_orders_recent_tracked_then_older() {
    let root = temp_dir("candidate-order");
    let codex_home = root.join(".codex");
    let recent_id = "019d23a7-9258-7810-93cc-c6833b348101";
    let tracked_id = "019d23a7-9258-7810-93cc-c6833b348102";
    let older_id = "019d23a7-9258-7810-93cc-c6833b348103";
    let recent = write_rollout(&codex_home, "2026", "03", "25", recent_id, "recent");
    let tracked = write_rollout(&codex_home, "2026", "03", "24", tracked_id, "tracked");
    let older = write_rollout(&codex_home, "2026", "03", "23", older_id, "older");
    let index = SessionIndex::from([
        index_entry(recent_id, "recent", "2026-03-25T06:30:00Z"),
        index_entry(older_id, "older", "2000-01-01T00:00:00Z"),
    ]);
    let mut state = SyncState::default();
    state
        .files
        .insert(tracked.to_string_lossy().to_string(), stale_state_entry());
    let options = discovery_options(&root, 10, 1024 * 1024, false);

    let result = discover_rollout_candidates(&codex_home, &index, &state, &options).unwrap();

    assert_eq!(result.candidates, vec![recent, tracked, older]);
    assert!(!result.hit_file_cap);
    assert!(!result.hit_byte_cap);
}

#[test]
fn discover_rollout_candidates_applies_file_cap() {
    let root = temp_dir("candidate-file-cap");
    let codex_home = root.join(".codex");
    let first_id = "019d23a7-9258-7810-93cc-c6833b348111";
    let second_id = "019d23a7-9258-7810-93cc-c6833b348112";
    let first = write_rollout(&codex_home, "2026", "03", "25", first_id, "first");
    let _second = write_rollout(&codex_home, "2026", "03", "25", second_id, "second");
    let index = SessionIndex::from([
        index_entry(first_id, "first", "2026-03-25T06:31:00Z"),
        index_entry(second_id, "second", "2026-03-25T06:30:00Z"),
    ]);
    let options = discovery_options(&root, 1, 1024 * 1024, false);

    let result =
        discover_rollout_candidates(&codex_home, &index, &SyncState::default(), &options).unwrap();

    assert_eq!(result.candidates, vec![first]);
    assert!(result.hit_file_cap);
    assert!(!result.hit_byte_cap);
}

#[test]
fn discover_rollout_candidates_applies_byte_cap_after_first_candidate() {
    let root = temp_dir("candidate-byte-cap");
    let codex_home = root.join(".codex");
    let first_id = "019d23a7-9258-7810-93cc-c6833b348121";
    let second_id = "019d23a7-9258-7810-93cc-c6833b348122";
    let first = write_rollout(&codex_home, "2026", "03", "25", first_id, "a");
    let _second = write_rollout(&codex_home, "2026", "03", "25", second_id, "large-second");
    let first_size = fs::metadata(&first).unwrap().len();
    let index = SessionIndex::from([
        index_entry(first_id, "first", "2026-03-25T06:31:00Z"),
        index_entry(second_id, "second", "2026-03-25T06:30:00Z"),
    ]);
    let options = discovery_options(&root, 10, first_size + 1, false);

    let result =
        discover_rollout_candidates(&codex_home, &index, &SyncState::default(), &options).unwrap();

    assert_eq!(result.candidates, vec![first]);
    assert!(!result.hit_file_cap);
    assert!(result.hit_byte_cap);
}

#[test]
fn discover_rollout_candidates_continues_after_oversized_byte_cap_skip() {
    let root = temp_dir("candidate-byte-cap-continue");
    let codex_home = root.join(".codex");
    let first_id = "019d23a7-9258-7810-93cc-c6833b348123";
    let huge_id = "019d23a7-9258-7810-93cc-c6833b348124";
    let third_id = "019d23a7-9258-7810-93cc-c6833b348125";
    let first = write_rollout(&codex_home, "2026", "03", "25", first_id, "a");
    let _huge = write_rollout(&codex_home, "2026", "03", "25", huge_id, &"x".repeat(1024));
    let third = write_rollout(&codex_home, "2026", "03", "25", third_id, "b");
    let max_bytes = fs::metadata(&first).unwrap().len() + fs::metadata(&third).unwrap().len();
    let index = SessionIndex::from([
        index_entry(first_id, "first", "2026-03-25T06:32:00Z"),
        index_entry(huge_id, "huge", "2026-03-25T06:31:00Z"),
        index_entry(third_id, "third", "2026-03-25T06:30:00Z"),
    ]);
    let options = discovery_options(&root, 10, max_bytes, false);

    let result =
        discover_rollout_candidates(&codex_home, &index, &SyncState::default(), &options).unwrap();

    assert_eq!(result.candidates, vec![first, third]);
    assert_eq!(result.total_bytes, max_bytes);
    assert!(result.hit_byte_cap);
}

#[test]
fn discover_rollout_candidates_treats_malformed_and_naive_timestamps_as_older() {
    let root = temp_dir("candidate-invalid-timestamps");
    let codex_home = root.join(".codex");
    let malformed_id = "019d23a7-9258-7810-93cc-c6833b348131";
    let naive_id = "019d23a7-9258-7810-93cc-c6833b348132";
    let valid_id = "019d23a7-9258-7810-93cc-c6833b348133";
    let _malformed = write_rollout(&codex_home, "2026", "03", "25", malformed_id, "malformed");
    let _naive = write_rollout(&codex_home, "2026", "03", "25", naive_id, "naive");
    let valid = write_rollout(&codex_home, "2026", "03", "25", valid_id, "valid");
    let index = SessionIndex::from([
        index_entry(malformed_id, "malformed", "not-a-date"),
        index_entry(naive_id, "naive", "2099-01-02T00:00:00"),
        index_entry(valid_id, "valid", "2000-01-01T00:00:00Z"),
    ]);
    let options = discovery_options(&root, 1, 1024 * 1024, false);

    let result =
        discover_rollout_candidates(&codex_home, &index, &SyncState::default(), &options).unwrap();

    assert_eq!(result.candidates, vec![valid]);
}

#[test]
fn discover_rollout_candidates_searches_utc_and_local_session_dates() {
    let root = temp_dir("candidate-local-date");
    let codex_home = root.join(".codex");
    let session_id = "019d23a7-9258-7810-93cc-c6833b348134";
    let _utc_day_rollout = write_rollout(&codex_home, "2026", "03", "24", session_id, "utc-day");
    let local_day_rollout = write_rollout(&codex_home, "2026", "03", "25", session_id, "local-day");
    let index = SessionIndex::from([index_entry(
        session_id,
        "local-date",
        "2026-03-24T23:30:00Z",
    )]);
    let mut options = discovery_options(&root, 10, 1024 * 1024, false);
    options.local_offset_override = Some(UtcOffset::from_hms(9, 0, 0).unwrap());

    let result =
        discover_rollout_candidates(&codex_home, &index, &SyncState::default(), &options).unwrap();

    assert_eq!(result.candidates, vec![local_day_rollout]);
}

#[test]
fn discover_rollout_candidates_skips_deleted_tracked_and_missing_index_targets() {
    let root = temp_dir("candidate-missing");
    let codex_home = root.join(".codex");
    let missing_id = "019d23a7-9258-7810-93cc-c6833b348141";
    let index = SessionIndex::from([index_entry(missing_id, "missing", "2026-03-25T06:30:00Z")]);
    let mut state = SyncState::default();
    state.files.insert(
        root.join("deleted.jsonl").to_string_lossy().to_string(),
        stale_state_entry(),
    );
    let options = discovery_options(&root, 10, 1024 * 1024, false);

    let result = discover_rollout_candidates(&codex_home, &index, &state, &options).unwrap();

    assert!(result.candidates.is_empty());
}

#[test]
fn discover_rollout_candidates_skips_unchanged_state_entry() {
    let root = temp_dir("candidate-unchanged");
    let codex_home = root.join(".codex");
    let session_id = "019d23a7-9258-7810-93cc-c6833b348151";
    let rollout = write_rollout(&codex_home, "2026", "03", "25", session_id, "unchanged");
    let fingerprint = file_fingerprint(&rollout).unwrap();
    let index = SessionIndex::from([index_entry(
        session_id,
        "same thread",
        "2026-03-25T06:30:00Z",
    )]);
    let mut state = SyncState::default();
    state.files.insert(
        rollout.to_string_lossy().to_string(),
        StateEntry {
            included: Some(true),
            size: Some(fingerprint.size),
            mtime_ns: Some(fingerprint.mtime_ns),
            extra: std::collections::BTreeMap::from([(
                "thread_name".to_owned(),
                json!("same thread"),
            )]),
            ..StateEntry::default()
        },
    );
    let options = discovery_options(&root, 10, 1024 * 1024, false);

    let result = discover_rollout_candidates(&codex_home, &index, &state, &options).unwrap();

    assert!(result.candidates.is_empty());
}

#[test]
fn discover_rollout_candidates_reprocesses_when_index_thread_name_is_cleared() {
    let root = temp_dir("candidate-cleared-thread-name");
    let codex_home = root.join(".codex");
    let session_id = "019d23a7-9258-7810-93cc-c6833b348152";
    let rollout = write_rollout(&codex_home, "2026", "03", "25", session_id, "changed-title");
    let fingerprint = file_fingerprint(&rollout).unwrap();
    let index = SessionIndex::from([(
        session_id.to_owned(),
        serde_json::from_value(json!({
            "id": session_id,
            "thread_name": null,
            "updated_at": "2026-03-25T06:30:00Z"
        }))
        .unwrap(),
    )]);
    let mut state = SyncState::default();
    state.files.insert(
        rollout.to_string_lossy().to_string(),
        StateEntry {
            included: Some(true),
            size: Some(fingerprint.size),
            mtime_ns: Some(fingerprint.mtime_ns),
            extra: std::collections::BTreeMap::from([(
                "thread_name".to_owned(),
                json!("old thread"),
            )]),
            ..StateEntry::default()
        },
    );
    let options = discovery_options(&root, 10, 1024 * 1024, false);

    let result = discover_rollout_candidates(&codex_home, &index, &state, &options).unwrap();

    assert_eq!(result.candidates, vec![rollout]);
}

#[cfg(unix)]
#[test]
fn discover_rollout_candidates_reprocesses_when_note_path_is_symlink() {
    let root = temp_dir("candidate-note-symlink");
    let codex_home = root.join(".codex");
    let vault = root.join("vault");
    fs::create_dir_all(&vault).unwrap();
    let session_id = "019d23a7-9258-7810-93cc-c6833b348153";
    let rollout = write_rollout(&codex_home, "2026", "03", "25", session_id, "note-symlink");
    let rollout_fingerprint = file_fingerprint(&rollout).unwrap();
    let outside_note = root.join("outside.md");
    fs::write(&outside_note, "managed note").unwrap();
    let symlink_note = vault.join("conversation.md");
    std::os::unix::fs::symlink(&outside_note, &symlink_note).unwrap();
    let mut state = SyncState::default();
    state.files.insert(
        rollout.to_string_lossy().to_string(),
        StateEntry {
            included: Some(true),
            size: Some(rollout_fingerprint.size),
            mtime_ns: Some(rollout_fingerprint.mtime_ns),
            conversation_note_fingerprint: Some(file_fingerprint(&symlink_note).unwrap()),
            extra: std::collections::BTreeMap::from([(
                "conversation_note_path".to_owned(),
                json!("conversation.md"),
            )]),
            ..StateEntry::default()
        },
    );
    let options = discovery_options(&vault, 10, 1024 * 1024, false);

    let result =
        discover_rollout_candidates(&codex_home, &SessionIndex::new(), &state, &options).unwrap();

    assert_eq!(result.candidates, vec![rollout]);
}

#[test]
fn discover_rollout_candidates_full_scans_when_including_subagents() {
    let root = temp_dir("candidate-full-scan");
    let codex_home = root.join(".codex");
    let first_id = "019d23a7-9258-7810-93cc-c6833b348161";
    let second_id = "019d23a7-9258-7810-93cc-c6833b348162";
    let _first = write_rollout(&codex_home, "2026", "03", "25", first_id, "first");
    let _second = write_rollout(&codex_home, "2026", "03", "24", second_id, "second");
    let options = discovery_options(&root, 1, 1024 * 1024, true);

    let result = discover_rollout_candidates(
        &codex_home,
        &SessionIndex::new(),
        &SyncState::default(),
        &options,
    )
    .unwrap();

    assert_eq!(result.candidates.len(), 1);
    assert!(result.hit_file_cap);
}

#[test]
fn discover_rollout_candidates_bounds_large_full_scan_fixture() {
    let root = temp_dir("candidate-large-full-scan");
    let codex_home = root.join(".codex");
    let mut first_size = None;
    for index in 0..120 {
        let session_id = format!("019d23a7-9258-7810-93cc-c6833b34{index:04x}");
        let rollout = write_rollout(
            &codex_home,
            "2026",
            "03",
            "25",
            &session_id,
            &format!("large-{index:03}-{}\n", "x".repeat(80)),
        );
        first_size.get_or_insert_with(|| fs::metadata(&rollout).unwrap().len());
    }
    let file_size = first_size.unwrap();
    let options = discovery_options(&root, 120, file_size * 10, true);

    let result = discover_rollout_candidates(
        &codex_home,
        &SessionIndex::new(),
        &SyncState::default(),
        &options,
    )
    .unwrap();

    assert_eq!(result.candidates.len(), 10);
    assert_eq!(result.total_bytes, file_size * 10);
    assert!(!result.hit_file_cap);
    assert!(result.hit_byte_cap);
}

#[cfg(unix)]
#[test]
fn discover_rollout_candidates_handles_symlinked_session_files() {
    let root = temp_dir("candidate-symlink");
    let codex_home = root.join(".codex");
    let sessions_dir = codex_home
        .join("sessions")
        .join("2026")
        .join("03")
        .join("25");
    fs::create_dir_all(&sessions_dir).unwrap();
    let session_id = "019d23a7-9258-7810-93cc-c6833b348171";
    let target = root.join("target-rollout.jsonl");
    fs::write(&target, "symlink-target").unwrap();
    let symlink = sessions_dir.join(format!("rollout-2026-03-25T15-21-17-{session_id}.jsonl"));
    std::os::unix::fs::symlink(&target, &symlink).unwrap();
    let index = SessionIndex::from([index_entry(session_id, "symlink", "2026-03-25T06:30:00Z")]);
    let options = discovery_options(&root, 10, 1024 * 1024, false);

    let result =
        discover_rollout_candidates(&codex_home, &index, &SyncState::default(), &options).unwrap();

    assert_eq!(result.candidates, vec![symlink]);
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

fn index_entry(
    session_id: &str,
    thread_name: &str,
    updated_at: &str,
) -> (String, codex_obsidian_sync_rs::models::SessionIndexEntry) {
    (
        session_id.to_owned(),
        serde_json::from_value(json!({
            "id": session_id,
            "thread_name": thread_name,
            "updated_at": updated_at
        }))
        .unwrap(),
    )
}

fn discovery_options<'a>(
    vault_root: &'a Path,
    max_files: usize,
    max_bytes: u64,
    include_subagents: bool,
) -> DiscoveryOptions<'a> {
    DiscoveryOptions {
        vault_root,
        include_subagents,
        recent_days: 30,
        max_files,
        max_bytes,
        now_utc: OffsetDateTime::parse("2026-03-26T00:00:00Z", &Rfc3339).unwrap(),
        local_offset_override: None,
    }
}

fn write_rollout(
    codex_home: &Path,
    year: &str,
    month: &str,
    day: &str,
    session_id: &str,
    content: &str,
) -> PathBuf {
    let sessions_dir = codex_home.join("sessions").join(year).join(month).join(day);
    fs::create_dir_all(&sessions_dir).unwrap();
    let path = sessions_dir.join(format!(
        "rollout-{year}-{month}-{day}T15-21-17-{session_id}.jsonl"
    ));
    fs::write(&path, content).unwrap();
    path
}

fn stale_state_entry() -> StateEntry {
    StateEntry {
        included: Some(true),
        size: Some(0),
        mtime_ns: Some(0),
        ..StateEntry::default()
    }
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
