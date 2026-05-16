use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use codex_obsidian_sync_rs::config::SyncConfig;
use codex_obsidian_sync_rs::sync::{SyncRunOptions, sync_once_dry_run_with_options};
use insta::{Settings, assert_json_snapshot, assert_snapshot};
use serde_json::{Value, json};
use time::format_description::well_known::Rfc3339;
use time::{OffsetDateTime, UtcOffset};

const SESSION_ID: &str = "019d23a7-9258-7810-93cc-c6833b349401";
const PRIVATE_TRANSCRIPT_MARKER: &str = "PRIVATE_RAW_TRANSCRIPT_SHOULD_NOT_LEAK";
const RAW_BEARER_TOKEN: &str = "abc.def+/ghi==";

#[test]
fn sync_summary_and_rendered_note_snapshots_are_redacted_and_inputs_unchanged() {
    let temp = temp_dir("snapshot-hardening");
    let config = sync_config(&temp);
    let rollout = write_snapshot_fixture(&config);
    let before = input_hashes(&config, std::slice::from_ref(&rollout));

    let output = temp.join("output");
    let summary = sync_once_dry_run_with_options(&config, Some(&output), run_options()).unwrap();
    let summary_json = serde_json::to_value(&summary).unwrap();

    assert_json_snapshot!(summary_json, {
        ".duration_ms" => "[duration-ms]",
        ".output_dir" => "[output-dir]",
        ".temp_state_file" => "[temp-state-file]"
    }, @r###"
{
  "appended": 0,
  "dry_run": true,
  "duration_ms": "[duration-ms]",
  "fast_path": 0,
  "output_dir": "[output-dir]",
  "paused": 0,
  "planned_writes": 4,
  "processed": 1,
  "rewritten": 1,
  "skipped_invalid": 0,
  "skipped_subagents": 0,
  "temp_state_file": "[temp-state-file]",
  "total_rollouts": 1,
  "unchanged": 0
}
"###);

    let conversation_text = fs::read_to_string(single_conversation_note(&output)).unwrap();
    let mut settings = Settings::clone_current();
    settings.add_filter(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        "[SESSION_ID]",
    );
    settings.bind(|| {
        assert_snapshot!(conversation_text, @r###"
---
type: codex-conversation
date: 2026-04-03
session_id: [SESSION_ID]
originator: Codex Desktop
project_slug: snapshot-project
daily_note: Codex/Daily/2026-04-03.md
status: completed
tags:
  - codex
  - codex-conversation
  - project/snapshot-project
---

<!-- CODEX CONVERSATION NOTE v2 -->

# Snapshot Fixture

[[Codex/Daily/2026-04-03]]
[[Codex/Projects/snapshot-project]]

## Transcript

<!-- BEGIN CODEX TRANSCRIPT -->
### User
Snapshot safe prompt

### Assistant
Token check Authorization: [REDACTED_BEARER_TOKEN]
"###);
    });

    let summary_text = serde_json::to_string_pretty(&summary_json).unwrap();
    let output_texts = rendered_output_texts(&output);
    let leak_inputs = std::iter::once(summary_text.as_str())
        .chain(output_texts.iter().map(String::as_str))
        .collect::<Vec<_>>();
    assert_no_snapshot_leaks(&leak_inputs);
    assert_eq!(input_hashes(&config, &[rollout]), before);
    assert!(!config.vault.join("Codex").exists());
    assert!(!config.state_file.exists());
}

fn sync_config(root: &Path) -> SyncConfig {
    let codex_home = root.join(".codex");
    let vault = root.join("vault");
    let state_dir = codex_home.join("obsidian-sync");
    fs::create_dir_all(&codex_home).unwrap();
    fs::create_dir_all(&vault).unwrap();
    fs::create_dir_all(&state_dir).unwrap();
    SyncConfig {
        codex_home,
        vault,
        state_file: state_dir.join("sync-state.json"),
        lock_file: state_dir.join("sync-state.lock"),
        include_subagents: false,
        interval_seconds: 10,
        recent_days: 30,
        candidate_file_limit: 100,
        candidate_bytes_limit: 500 * 1024 * 1024,
        log_level: "INFO".to_owned(),
    }
}

fn run_options() -> SyncRunOptions {
    SyncRunOptions {
        now_utc: OffsetDateTime::parse("2026-04-05T00:00:00Z", &Rfc3339).unwrap(),
        local_offset_override: Some(UtcOffset::UTC),
    }
}

fn write_snapshot_fixture(config: &SyncConfig) -> PathBuf {
    write_jsonl(
        &config.codex_home.join("session_index.jsonl"),
        &[json!({
            "id": SESSION_ID,
            "thread_name": "Snapshot Fixture",
            "updated_at": "2026-04-03T01:00:03Z",
        })],
    );
    let rollout = rollout_path(&config.codex_home);
    write_jsonl(
        &rollout,
        &[
            session_meta(),
            message_record(
                "2026-04-03T01:00:01Z",
                "user",
                None,
                &format!("<environment_context>\n{PRIVATE_TRANSCRIPT_MARKER}"),
            ),
            message_record(
                "2026-04-03T01:00:02Z",
                "assistant",
                Some("analysis"),
                PRIVATE_TRANSCRIPT_MARKER,
            ),
            message_record("2026-04-03T01:00:03Z", "user", None, "Snapshot safe prompt"),
            message_record(
                "2026-04-03T01:00:04Z",
                "assistant",
                Some("final_answer"),
                &format!("Token check Authorization: Bearer {RAW_BEARER_TOKEN}"),
            ),
        ],
    );
    rollout
}

fn session_meta() -> Value {
    json!({
        "timestamp": "2026-04-03T01:00:00Z",
        "type": "session_meta",
        "payload": {
            "id": SESSION_ID,
            "timestamp": "2026-04-03T01:00:00Z",
            "cwd": "/tmp/snapshot-project",
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
        "content": [
            {
                "type": content_type,
                "text": text,
            }
        ]
    });
    if let Some(phase) = phase {
        payload["phase"] = json!(phase);
    }
    json!({
        "timestamp": timestamp,
        "type": "response_item",
        "payload": payload,
    })
}

fn rollout_path(codex_home: &Path) -> PathBuf {
    codex_home
        .join("sessions")
        .join("2026")
        .join("04")
        .join("03")
        .join(format!("rollout-2026-04-03T10-00-00-{SESSION_ID}.jsonl"))
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

fn single_conversation_note(root: &Path) -> PathBuf {
    let conversation_dir = root.join("Codex").join("Conversations").join("2026");
    let notes = fs::read_dir(conversation_dir)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .collect::<Vec<_>>();
    assert_eq!(notes.len(), 1);
    notes[0].clone()
}

fn rendered_output_texts(output: &Path) -> Vec<String> {
    let codex_root = output.join("Codex");
    let mut texts = fs::read_dir(codex_root)
        .unwrap()
        .flat_map(|entry| collect_text_files(entry.unwrap().path()))
        .map(|path| fs::read_to_string(path).unwrap())
        .collect::<Vec<_>>();
    texts.sort();
    texts
}

fn collect_text_files(path: PathBuf) -> Vec<PathBuf> {
    if path.is_file() {
        return vec![path];
    }
    let mut files = fs::read_dir(path)
        .unwrap()
        .flat_map(|entry| collect_text_files(entry.unwrap().path()))
        .collect::<Vec<_>>();
    files.sort();
    files
}

fn input_hashes(config: &SyncConfig, rollouts: &[PathBuf]) -> BTreeMap<String, u64> {
    let mut hashes = BTreeMap::from([
        (
            "session_index".to_owned(),
            file_hash(&config.codex_home.join("session_index.jsonl")),
        ),
        ("state".to_owned(), file_hash(&config.state_file)),
        ("lock".to_owned(), file_hash(&config.lock_file)),
    ]);
    for rollout in rollouts {
        hashes.insert(rollout.to_string_lossy().into_owned(), file_hash(rollout));
    }
    hashes
}

fn file_hash(path: &Path) -> u64 {
    fs::read(path)
        .unwrap_or_default()
        .into_iter()
        .fold(0xcbf2_9ce4_8422_2325, |hash, byte| {
            hash.wrapping_mul(0x100_0000_01b3) ^ u64::from(byte)
        })
}

fn assert_no_snapshot_leaks(values: &[&str]) {
    for value in values {
        assert!(!value.contains(PRIVATE_TRANSCRIPT_MARKER));
        assert!(!value.contains(RAW_BEARER_TOKEN));
        assert!(!value.contains("Bearer abc."));
        assert!(!value.contains("sk-"));
    }
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-{name}-{}-{}",
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
