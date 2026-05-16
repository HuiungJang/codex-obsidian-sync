use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use assert_cmd::prelude::*;
use serde_json::{Value, json};

const BIN: &str = "codex-obsidian-sync-rs";

#[test]
fn inspect_rollout_redacts_message_previews() {
    let root = temp_dir("inspect-rollout");
    let session_id = "019d23a7-9258-7810-93cc-c6833b3481cc";
    let github_token = format!("ghp_{}", "a".repeat(36));
    let rollout = root.join(format!("rollout-2026-03-25T15-21-17-{session_id}.jsonl"));
    write_jsonl(
        &rollout,
        &[
            session_meta(session_id),
            message_record(
                "2026-03-25T06:21:18Z",
                "user",
                None,
                &format!("deploy with {github_token}"),
            ),
        ],
    );

    let assert = Command::cargo_bin(BIN)
        .unwrap()
        .args([
            "--config",
            root.join("config.toml").to_str().unwrap(),
            "inspect-rollout",
            rollout.to_str().unwrap(),
            "--session-index",
            root.join("missing-session-index.jsonl").to_str().unwrap(),
        ])
        .assert()
        .success();
    let output = String::from_utf8(assert.get_output().stdout.clone()).unwrap();
    let payload: Value = serde_json::from_str(&output).unwrap();

    assert!(!output.contains(&github_token));
    assert_eq!(
        payload["messages"][0]["text_preview"],
        "deploy with [REDACTED_GITHUB_TOKEN]"
    );
}

#[test]
fn inspect_recent_redacts_message_previews() {
    let root = temp_dir("inspect-recent");
    let codex_home = root.join(".codex");
    let session_id = "019d23a7-9258-7810-93cc-c6833b3481cd";
    let slack_token = concat!("xox", "b-123456789012-123456789012-abcdefghijklmnopqrstuvwx");
    let rollout = codex_home
        .join("sessions")
        .join("2026")
        .join("03")
        .join("25")
        .join(format!("rollout-2026-03-25T15-21-17-{session_id}.jsonl"));
    write_jsonl(
        &rollout,
        &[
            session_meta(session_id),
            message_record(
                "2026-03-25T06:21:18Z",
                "user",
                None,
                &format!("deploy with {slack_token}"),
            ),
        ],
    );

    let assert = Command::cargo_bin(BIN)
        .unwrap()
        .args([
            "--config",
            root.join("config.toml").to_str().unwrap(),
            "inspect-recent",
            "--codex-home",
            codex_home.to_str().unwrap(),
            "--limit",
            "1",
        ])
        .assert()
        .success();
    let output = String::from_utf8(assert.get_output().stdout.clone()).unwrap();
    let payload: Value = serde_json::from_str(&output).unwrap();

    assert!(!output.contains(slack_token));
    assert_eq!(
        payload[0]["messages"][0]["text_preview"],
        "deploy with [REDACTED_SLACK_TOKEN]"
    );
}

#[test]
fn inspect_recent_matches_python_session_glob_depth() {
    let root = temp_dir("inspect-recent-depth");
    let codex_home = root.join(".codex");
    let session_id = "019d23a7-9258-7810-93cc-c6833b3481ce";
    let nested_rollout = codex_home
        .join("sessions")
        .join("2026")
        .join("03")
        .join("25")
        .join("nested")
        .join(format!("rollout-2026-03-25T15-21-17-{session_id}.jsonl"));
    write_jsonl(
        &nested_rollout,
        &[
            session_meta(session_id),
            message_record("2026-03-25T06:21:18Z", "user", None, "nested"),
        ],
    );

    let assert = Command::cargo_bin(BIN)
        .unwrap()
        .args([
            "--config",
            root.join("config.toml").to_str().unwrap(),
            "inspect-recent",
            "--codex-home",
            codex_home.to_str().unwrap(),
            "--limit",
            "1",
        ])
        .assert()
        .success();
    let output = String::from_utf8(assert.get_output().stdout.clone()).unwrap();
    let payload: Value = serde_json::from_str(&output).unwrap();

    assert_eq!(payload, json!([]));
}

#[test]
fn status_json_matches_python_field_shape_for_configured_state() {
    let root = temp_dir("status-json");
    let codex_home = root.join(".codex");
    let vault = root.join("vault");
    let state_dir = codex_home.join("obsidian-sync");
    fs::create_dir_all(&state_dir).unwrap();
    fs::create_dir_all(&vault).unwrap();
    let config_path = root.join("config.toml");
    let service_state = state_dir.join("service-state.json");
    fs::write(
        &config_path,
        format!(
            "vault = \"{}\"\ncodex_home = \"{}\"\ninterval_seconds = 60\nlaunchd_plist_path = \"{}\"\n",
            vault.display(),
            codex_home.display(),
            root.join("agent.plist").display(),
        ),
    )
    .unwrap();
    fs::write(
        service_state,
        serde_json::to_string(&json!({
            "pending": true,
            "next_eligible_at": "2026-05-16T00:01:00+00:00",
            "last_run_finished_at": "2026-05-16T00:00:00Z",
            "last_success_at": "2026-05-16T00:00:00Z",
            "last_error_type": null,
            "last_error_summary": null,
            "last_summary": {"processed": 1, "duration_ms": 12, "ignored": "no"},
        }))
        .unwrap(),
    )
    .unwrap();

    let assert = Command::cargo_bin(BIN)
        .unwrap()
        .args([
            "--config",
            config_path.to_str().unwrap(),
            "status",
            "--json",
        ])
        .assert()
        .success();
    let output = String::from_utf8(assert.get_output().stdout.clone()).unwrap();
    let payload: Value = serde_json::from_str(&output).unwrap();

    for key in [
        "configured",
        "config_path",
        "vault",
        "cooldown",
        "launchd_label",
        "launchd_loaded",
        "plist_path",
        "pending",
        "next_eligible_run",
        "last_run",
        "last_success",
        "last_error",
        "last_summary",
    ] {
        assert!(payload.get(key).is_some(), "missing status key {key}");
    }
    assert_eq!(payload["configured"], true);
    assert_eq!(payload["vault"], vault.to_string_lossy().as_ref());
    assert_eq!(payload["cooldown"], "1m (60s)");
    assert_eq!(payload["pending"], "yes");
    assert_eq!(payload["next_eligible_run"], "2026-05-16T00:01:00+00:00");
    assert_eq!(payload["last_error"], "none");
    assert_eq!(payload["last_summary"]["processed"], 1);
    assert_eq!(payload["last_summary"]["duration_ms"], 12);
    assert!(payload["last_summary"].get("ignored").is_none());
}

#[test]
fn status_json_reports_unconfigured_defaults() {
    let root = temp_dir("status-unconfigured");
    let config_path = root.join("missing-config.toml");

    let assert = Command::cargo_bin(BIN)
        .unwrap()
        .args([
            "--config",
            config_path.to_str().unwrap(),
            "status",
            "--json",
        ])
        .assert()
        .success();
    let output = String::from_utf8(assert.get_output().stdout.clone()).unwrap();
    let payload: Value = serde_json::from_str(&output).unwrap();

    assert_eq!(payload["configured"], false);
    assert_eq!(payload["vault"], "not configured");
    assert_eq!(payload["pending"], "not configured");
    assert_eq!(payload["last_summary"], json!({}));
}

fn session_meta(session_id: &str) -> Value {
    json!({
        "timestamp": "2026-03-25T06:21:17Z",
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "timestamp": "2026-03-25T06:21:17Z",
            "cwd": "/tmp/demo-project",
            "originator": "Codex Desktop",
            "source": "vscode",
        }
    })
}

fn message_record(timestamp: &str, role: &str, phase: Option<&str>, text: &str) -> Value {
    let content_type = if role == "user" {
        "input_text"
    } else {
        "output_text"
    };
    let mut payload = json!({
        "type": "message",
        "role": role,
        "content": [{"type": content_type, "text": text}]
    });
    if let Some(phase) = phase {
        payload["phase"] = json!(phase);
    }
    json!({"timestamp": timestamp, "type": "response_item", "payload": payload})
}

fn write_jsonl(path: &Path, records: &[Value]) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    let mut content = records
        .iter()
        .map(|record| serde_json::to_string(record).unwrap())
        .collect::<Vec<_>>()
        .join("\n");
    content.push('\n');
    fs::write(path, content).unwrap();
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-read-only-{name}-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let path = std::env::temp_dir().join(unique);
    if path.exists() {
        fs::remove_dir_all(&path).unwrap();
    }
    fs::create_dir_all(&path).unwrap();
    path
}
