use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use codex_obsidian_sync_rs::service_state::{load_service_state, mutate_service_state};
use serde_json::{Value, json};

#[test]
fn load_service_state_returns_defaults_for_corrupt_file() {
    let root = temp_dir("corrupt");
    let state_path = root.join("service-state.json");
    fs::write(&state_path, "{not-json").unwrap();

    let state = load_service_state(&state_path);

    assert_eq!(state["pending"], false);
    assert_eq!(state["last_summary"], json!({}));
}

#[test]
fn load_service_state_ignores_unknown_fields_without_losing_known_fields() {
    let root = temp_dir("unknown-fields");
    let state_path = root.join("service-state.json");
    fs::write(
        &state_path,
        serde_json::to_string(&json!({
            "pending": true,
            "last_summary": {"processed": 1},
            "future_field": "ignored",
        }))
        .unwrap(),
    )
    .unwrap();

    let state = load_service_state(&state_path);

    assert_eq!(state["pending"], true);
    assert_eq!(state["last_summary"]["processed"], 1);
    assert!(state.get("future_field").is_none());
}

#[test]
fn mutate_service_state_persists_changes_with_updated_timestamp() {
    let root = temp_dir("mutate");
    let state_path = root.join("service-state.json");
    let lock_path = root.join("service-state.lock");

    let result = mutate_service_state(&state_path, &lock_path, |state| {
        state.insert("pending".to_owned(), Value::Bool(true));
        7
    })
    .unwrap();
    let state = load_service_state(&state_path);

    assert_eq!(result, 7);
    assert_eq!(state["schema_version"], 1);
    assert_eq!(state["pending"], true);
    assert!(state["updated_at"].as_str().unwrap().ends_with("+00:00"));
    assert!(lock_path.exists());
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-service-state-{name}-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let path = std::env::temp_dir().join(unique);
    fs::create_dir_all(&path).unwrap();
    path
}
