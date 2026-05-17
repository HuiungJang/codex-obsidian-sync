use std::fs;
use std::path::{Path, PathBuf};

use codex_obsidian_sync_rs::state_store::{load_state_read_only, render_temp_state};
use serde_json::json;

#[test]
fn missing_state_file_loads_empty_state() {
    let state =
        load_state_read_only(Path::new("/tmp/codex-obsidian-sync-missing-state.json")).unwrap();
    assert!(state.files.is_empty());
}

#[test]
fn valid_state_preserves_known_and_unknown_fields() {
    let root = temp_dir("valid-state");
    let state_path = root.join("sync-state.json");
    fs::write(
        &state_path,
        serde_json::to_string_pretty(&json!({
            "schema_version": 99,
            "files": {
                "/tmp/rollout.jsonl": {
                    "included": true,
                    "size": 123,
                    "mtime_ns": 456,
                    "offset": 78,
                    "conversation_note_fingerprint": {
                        "size": 500,
                        "mtime_ns": 600
                    },
                    "future_entry_field": {
                        "keep": true
                    }
                }
            },
            "future_top_level": "keep"
        }))
        .unwrap(),
    )
    .unwrap();

    let state = load_state_read_only(&state_path).unwrap();
    let entry = state.files.get("/tmp/rollout.jsonl").unwrap();

    assert_eq!(state.extra.get("schema_version"), Some(&json!(99)));
    assert_eq!(state.extra.get("future_top_level"), Some(&json!("keep")));
    assert_eq!(entry.included, Some(true));
    assert_eq!(entry.size, Some(123));
    assert_eq!(entry.mtime_ns, Some(456));
    assert_eq!(entry.offset, Some(78));
    assert_eq!(
        entry.extra.get("future_entry_field"),
        Some(&json!({ "keep": true }))
    );

    let rendered = render_temp_state(&state).unwrap();
    assert!(rendered.ends_with('\n'));
    let reparsed: serde_json::Value = serde_json::from_str(&rendered).unwrap();
    assert_eq!(reparsed["future_top_level"], json!("keep"));
    assert_eq!(
        reparsed["files"]["/tmp/rollout.jsonl"]["future_entry_field"],
        json!({ "keep": true })
    );
}

#[test]
fn old_state_with_missing_entry_fields_loads() {
    let root = temp_dir("old-state");
    let state_path = root.join("sync-state.json");
    fs::write(
        &state_path,
        r#"
{
  "files": {
    "/tmp/old-rollout.jsonl": {
      "included": true
    }
  }
}
"#,
    )
    .unwrap();

    let state = load_state_read_only(&state_path).unwrap();
    let entry = state.files.get("/tmp/old-rollout.jsonl").unwrap();

    assert_eq!(entry.included, Some(true));
    assert_eq!(entry.offset, None);
    assert_eq!(entry.conversation_note_fingerprint, None);
}

#[test]
fn old_state_without_files_loads_as_empty_state() {
    let root = temp_dir("old-state-without-files");
    let state_path = root.join("sync-state.json");
    fs::write(&state_path, r#"{ "schema_version": 1 }"#).unwrap();

    let state = load_state_read_only(&state_path).unwrap();

    assert!(state.files.is_empty());
    assert_eq!(state.extra.get("schema_version"), Some(&json!(1)));
}

#[test]
fn invalid_json_is_content_free_error_without_mutation() {
    let root = temp_dir("invalid-json");
    let state_path = root.join("sync-state.json");
    fs::write(&state_path, br#"{"files": "#).unwrap();
    let before = fs::read(&state_path).unwrap();

    let error = load_state_read_only(&state_path).unwrap_err();

    assert_eq!(error.to_string(), "parse error");
    assert_eq!(fs::read(&state_path).unwrap(), before);
    assert!(root.read_dir().unwrap().all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .contains(".corrupt-")
    }));
}

#[test]
fn empty_file_is_parse_error_without_mutation() {
    let root = temp_dir("empty-file");
    let state_path = root.join("sync-state.json");
    fs::write(&state_path, b"").unwrap();

    let error = load_state_read_only(&state_path).unwrap_err();

    assert_eq!(error.to_string(), "parse error");
    assert_eq!(fs::read(&state_path).unwrap(), b"");
}

#[test]
fn top_level_must_be_object() {
    let root = temp_dir("top-level-array");
    let state_path = root.join("sync-state.json");
    fs::write(&state_path, "[]").unwrap();

    let error = load_state_read_only(&state_path).unwrap_err();

    assert_eq!(error.to_string(), "parse error");
}

#[test]
fn files_field_must_be_object() {
    let root = temp_dir("files-not-object");
    let state_path = root.join("sync-state.json");
    fs::write(&state_path, r#"{ "files": [] }"#).unwrap();

    let error = load_state_read_only(&state_path).unwrap_err();

    assert_eq!(error.to_string(), "parse error");
}

#[test]
fn file_entries_must_be_objects() {
    let root = temp_dir("entry-not-object");
    let state_path = root.join("sync-state.json");
    fs::write(
        &state_path,
        r#"{ "files": { "/tmp/rollout.jsonl": "not-an-object" } }"#,
    )
    .unwrap();

    let error = load_state_read_only(&state_path).unwrap_err();

    assert_eq!(error.to_string(), "parse error");
}

#[test]
fn file_keys_must_be_absolute_paths() {
    let root = temp_dir("relative-file-key");
    let state_path = root.join("sync-state.json");
    fs::write(
        &state_path,
        r#"{ "files": { "relative/rollout.jsonl": { "included": true } } }"#,
    )
    .unwrap();

    let error = load_state_read_only(&state_path).unwrap_err();

    assert_eq!(error.to_string(), "parse error");
}

#[test]
fn numeric_state_fields_must_be_numbers() {
    let root = temp_dir("numeric-types");
    let state_path = root.join("sync-state.json");
    fs::write(
        &state_path,
        r#"
{
  "files": {
    "/tmp/rollout.jsonl": {
      "size": "123"
    }
  }
}
"#,
    )
    .unwrap();

    let error = load_state_read_only(&state_path).unwrap_err();

    assert_eq!(error.to_string(), "parse error");
}

#[test]
fn fingerprint_fields_must_be_numeric() {
    let root = temp_dir("fingerprint-types");
    let state_path = root.join("sync-state.json");
    fs::write(
        &state_path,
        r#"
{
  "files": {
    "/tmp/rollout.jsonl": {
      "conversation_note_fingerprint": {
        "size": 1,
        "mtime_ns": "bad"
      }
    }
  }
}
"#,
    )
    .unwrap();

    let error = load_state_read_only(&state_path).unwrap_err();

    assert_eq!(error.to_string(), "parse error");
}

#[test]
fn rendered_temp_state_always_contains_files_object() {
    let state =
        load_state_read_only(Path::new("/tmp/codex-obsidian-sync-render-missing.json")).unwrap();

    let rendered = render_temp_state(&state).unwrap();

    assert!(rendered.contains("\"files\": {}"));
    assert!(rendered.ends_with('\n'));
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-state-{name}-{}-{}",
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
