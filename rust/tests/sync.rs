use std::collections::BTreeMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(unix)]
use std::os::unix::fs::symlink;

use assert_cmd::prelude::*;
use codex_obsidian_sync_rs::config::SyncConfig;
use codex_obsidian_sync_rs::lock::ProcessLock;
use codex_obsidian_sync_rs::sync::{
    SyncRunOptions, sync_once_dry_run_with_options, sync_once_write_with_options,
};
use serde_json::{Value, json};
use time::format_description::well_known::Rfc3339;
use time::{OffsetDateTime, UtcOffset};

const BIN: &str = "codex-obsidian-sync-rs";

#[test]
fn dry_run_first_write_outputs_notes_state_summary_and_preserves_inputs() {
    let root = temp_dir("first-write");
    let config = sync_config(&root, false);
    let session_id = "019d23a7-9258-7810-93cc-c6833b348301";
    let rollout = write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        session_id,
        &[
            session_meta(session_id, "2026-04-03T01:00:00Z", json!("vscode")),
            message_record("2026-04-03T01:00:01Z", "user", None, "첫 질문"),
            message_record(
                "2026-04-03T01:00:02Z",
                "assistant",
                Some("final_answer"),
                "응답 Authorization: Bearer abc.def+/ghi==",
            ),
        ],
    );
    write_index(
        &config.codex_home,
        &[index_entry(
            session_id,
            "첫 dry run",
            "2026-04-03T01:00:02Z",
        )],
    );
    fs::write(&config.lock_file, "python lock").unwrap();
    let before = input_hashes(&config, std::slice::from_ref(&rollout));

    let output = root.join("output");
    let summary = sync_once_dry_run_with_options(&config, Some(&output), run_options()).unwrap();

    assert_eq!(summary.processed, 1);
    assert_eq!(summary.rewritten, 1);
    assert_eq!(summary.appended, 0);
    assert_eq!(summary.skipped_invalid, 0);
    assert_eq!(summary.total_rollouts, 1);
    assert_eq!(summary.planned_writes, 4);
    assert!(summary.dry_run);
    assert!(summary.lock_exists);
    assert_eq!(summary.output_dir, output.to_string_lossy());
    assert_eq!(
        summary.temp_state_file,
        output.join("sync-state.json").to_string_lossy()
    );

    let conversation = single_conversation_note(&output);
    let conversation_text = fs::read_to_string(&conversation).unwrap();
    assert!(conversation_text.contains("첫 질문"));
    assert!(conversation_text.contains("[REDACTED_BEARER_TOKEN]"));
    assert!(output.join("Codex/Daily/2026-04-03.md").exists());
    assert!(output.join("Codex/Projects/demo-project.md").exists());
    let state = read_json(output.join("sync-state.json"));
    let entry = &state["files"][rollout.to_string_lossy().as_ref()];
    assert_eq!(entry["included"], true);
    assert!(entry["header_hash"].as_str().is_some());
    assert!(entry["render_hash"].as_str().is_some());
    assert!(entry["transcript_hash"].as_str().is_some());
    assert!(
        entry["conversation_note_fingerprint"]["size"]
            .as_u64()
            .is_some()
    );
    assert_eq!(input_hashes(&config, &[rollout]), before);
    assert!(!config.vault.join("Codex").exists());
    assert!(!config.state_file.exists());
}

#[test]
fn dry_run_appends_using_real_note_fingerprint_without_rewriting_header() {
    let root = temp_dir("append");
    let config = sync_config(&root, false);
    let session_id = "019d23a7-9258-7810-93cc-c6833b348302";
    let rollout = write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        session_id,
        &[
            session_meta(session_id, "2026-04-03T01:05:00Z", json!("vscode")),
            message_record("2026-04-03T01:05:01Z", "user", None, "첫 질문"),
        ],
    );
    write_index(
        &config.codex_home,
        &[index_entry(
            session_id,
            "append only",
            "2026-04-03T01:05:01Z",
        )],
    );
    let first_output = root.join("output-1");
    sync_once_dry_run_with_options(&config, Some(&first_output), run_options()).unwrap();
    materialize_output_as_real_state(&config, &first_output);
    let real_conversation = single_conversation_note(&config.vault);
    let before_real_note = fs::read_to_string(&real_conversation).unwrap();
    append_jsonl(
        &rollout,
        &message_record("2026-04-03T01:05:02Z", "user", None, "두 번째 질문"),
    );

    let second_output = root.join("output-2");
    let summary =
        sync_once_dry_run_with_options(&config, Some(&second_output), run_options()).unwrap();

    assert_eq!(summary.processed, 1);
    assert_eq!(summary.appended, 1);
    assert_eq!(summary.rewritten, 0);
    let dry_conversation = single_conversation_note(&second_output);
    let dry_text = fs::read_to_string(dry_conversation).unwrap();
    assert!(dry_text.contains("첫 질문"));
    assert!(dry_text.contains("두 번째 질문"));
    assert_eq!(
        fs::read_to_string(real_conversation).unwrap(),
        before_real_note
    );
}

#[test]
fn dry_run_falls_back_to_full_rebuild_when_incremental_offset_is_stale() {
    let root = temp_dir("stale-offset");
    let config = sync_config(&root, false);
    let session_id = "019d23a7-9258-7810-93cc-c6833b348303";
    let rollout = write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        session_id,
        &[
            session_meta(session_id, "2026-04-03T01:06:00Z", json!("vscode")),
            message_record("2026-04-03T01:06:01Z", "user", None, "첫 질문"),
        ],
    );
    write_index(
        &config.codex_home,
        &[index_entry(
            session_id,
            "stale offset",
            "2026-04-03T01:06:01Z",
        )],
    );
    let first_output = root.join("output-1");
    sync_once_dry_run_with_options(&config, Some(&first_output), run_options()).unwrap();
    materialize_output_as_real_state(&config, &first_output);
    append_jsonl(
        &rollout,
        &message_record(
            "2026-04-03T01:06:02Z",
            "assistant",
            Some("final_answer"),
            "offset 복구 응답",
        ),
    );
    bump_state_offset(&config.state_file, &rollout);

    let second_output = root.join("output-2");
    let summary =
        sync_once_dry_run_with_options(&config, Some(&second_output), run_options()).unwrap();

    assert_eq!(summary.processed, 1);
    assert_eq!(summary.skipped_invalid, 0);
    assert_eq!(summary.appended, 1);
    assert_eq!(summary.rewritten, 1);
    let dry_text = fs::read_to_string(single_conversation_note(&second_output)).unwrap();
    assert!(dry_text.contains("offset 복구 응답"));
}

#[cfg(unix)]
#[test]
fn dry_run_non_fast_path_does_not_read_unchanged_historical_daily_notes() {
    let root = temp_dir("skip-unchanged-index-notes");
    let config = sync_config(&root, false);
    let old_id = "019d23a7-9258-7810-93cc-c6833b3483a0";
    let current_id = "019d23a7-9258-7810-93cc-c6833b3483a1";
    write_rollout(
        &config.codex_home,
        "2026",
        "01",
        "23",
        old_id,
        &[
            session_meta(old_id, "2026-01-23T01:00:00Z", json!("vscode")),
            message_record("2026-01-23T01:00:01Z", "user", None, "오래된 질문"),
        ],
    );
    let current_rollout = write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        current_id,
        &[
            session_meta(current_id, "2026-04-03T01:00:00Z", json!("vscode")),
            message_record("2026-04-03T01:00:01Z", "user", None, "현재 질문"),
        ],
    );
    write_index(
        &config.codex_home,
        &[
            index_entry(old_id, "old", "2026-01-23T01:00:01Z"),
            index_entry(current_id, "current", "2026-04-03T01:00:01Z"),
        ],
    );
    let first_output = root.join("output-1");
    sync_once_dry_run_with_options(&config, Some(&first_output), run_options()).unwrap();
    materialize_output_as_real_state(&config, &first_output);

    let old_daily = config.vault.join("Codex/Daily/2026-01-23.md");
    fs::remove_file(&old_daily).unwrap();
    symlink(root.join("outside-old-daily.md"), &old_daily).unwrap();
    append_jsonl(
        &current_rollout,
        &message_record(
            "2026-04-03T01:00:02Z",
            "assistant",
            Some("final_answer"),
            "현재 응답",
        ),
    );

    let second_output = root.join("output-2");
    let summary =
        sync_once_dry_run_with_options(&config, Some(&second_output), run_options()).unwrap();

    assert_eq!(summary.processed, 1);
    assert_eq!(summary.appended, 1);
    assert!(second_output.join("Codex/Daily/2026-04-03.md").exists());
    assert!(!second_output.join("Codex/Daily/2026-01-23.md").exists());
}

#[test]
fn dry_run_skips_invalid_rollouts_and_cli_still_exits_zero() {
    let root = temp_dir("invalid-skip");
    let config = sync_config(&root, false);
    let valid_id = "019d23a7-9258-7810-93cc-c6833b348304";
    let invalid_id = "019d23a7-9258-7810-93cc-c6833b348305";
    write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        valid_id,
        &[
            session_meta(valid_id, "2026-04-03T01:07:00Z", json!("vscode")),
            message_record("2026-04-03T01:07:01Z", "user", None, "정상 세션"),
        ],
    );
    let invalid_rollout = rollout_path(&config.codex_home, "2026", "04", "03", invalid_id);
    fs::create_dir_all(invalid_rollout.parent().unwrap()).unwrap();
    fs::write(
        &invalid_rollout,
        format!(
            "{}\n{{\"timestamp\":\n",
            serde_json::to_string(&session_meta(
                invalid_id,
                "2026-04-03T01:07:00Z",
                json!("vscode")
            ))
            .unwrap()
        ),
    )
    .unwrap();
    write_index(
        &config.codex_home,
        &[
            index_entry(valid_id, "valid", "2026-04-03T01:07:02Z"),
            index_entry(invalid_id, "invalid", "2026-04-03T01:07:01Z"),
        ],
    );
    let config_path = root.join("config.toml");
    fs::write(
        &config_path,
        format!("vault = \"{}\"\n", config.vault.display()),
    )
    .unwrap();
    let output = root.join("cli-output");

    let assert = Command::cargo_bin(BIN)
        .unwrap()
        .args([
            "--config",
            config_path.to_str().unwrap(),
            "sync-once",
            "--codex-home",
            config.codex_home.to_str().unwrap(),
            "--state-file",
            config.state_file.to_str().unwrap(),
            "--lock-file",
            config.lock_file.to_str().unwrap(),
            "--dry-run-output",
            output.to_str().unwrap(),
        ])
        .assert()
        .success();
    let stdout = String::from_utf8(assert.get_output().stdout.clone()).unwrap();
    let summary: Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(summary["processed"], 1);
    assert_eq!(summary["skipped_invalid"], 1);
}

#[test]
fn dry_run_subagent_opt_out_updates_temp_state_without_notes() {
    let root = temp_dir("subagent-opt-out");
    let config = sync_config(&root, false);
    let session_id = "019d23a7-9258-7810-93cc-c6833b348306";
    write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        session_id,
        &[
            session_meta(
                session_id,
                "2026-04-03T01:08:00Z",
                json!({"subagent": {"thread_spawn": {"parent_thread_id": "parent-session"}}}),
            ),
            message_record("2026-04-03T01:08:01Z", "user", None, "subagent body"),
        ],
    );
    write_index(
        &config.codex_home,
        &[index_entry(
            session_id,
            "subagent title",
            "2026-04-03T01:08:01Z",
        )],
    );

    let output = root.join("output");
    let summary = sync_once_dry_run_with_options(&config, Some(&output), run_options()).unwrap();

    assert_eq!(summary.processed, 0);
    assert_eq!(summary.skipped_subagents, 1);
    assert_eq!(summary.planned_writes, 1);
    assert!(!output.join("Codex").exists());
    let state = read_json(output.join("sync-state.json"));
    let entry = state["files"].as_object().unwrap().values().next().unwrap();
    assert_eq!(entry["included"], false);
    assert_eq!(entry["is_subagent"], true);
    assert_eq!(entry["project_slug"], "demo-project");
}

#[test]
fn dry_run_fast_path_writes_runtime_state_only_to_temp_state() {
    let root = temp_dir("fast-path");
    let config = sync_config(&root, false);
    let session_id = "019d23a7-9258-7810-93cc-c6833b348307";
    write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        session_id,
        &[
            session_meta(session_id, "2026-04-03T01:09:00Z", json!("vscode")),
            message_record("2026-04-03T01:09:01Z", "user", None, "fast path"),
        ],
    );
    write_index(
        &config.codex_home,
        &[index_entry(session_id, "fast path", "2026-04-03T01:09:01Z")],
    );
    let first_output = root.join("output-1");
    sync_once_dry_run_with_options(&config, Some(&first_output), run_options()).unwrap();
    materialize_output_as_real_state(&config, &first_output);
    let before_state = fs::read(&config.state_file).unwrap();

    let second_output = root.join("output-2");
    let summary =
        sync_once_dry_run_with_options(&config, Some(&second_output), run_options()).unwrap();

    assert_eq!(summary.fast_path, 1);
    assert_eq!(summary.processed, 0);
    assert_eq!(summary.unchanged, 1);
    assert_eq!(summary.planned_writes, 1);
    assert!(second_output.join("sync-state.json").exists());
    assert!(!second_output.join("Codex").exists());
    assert_eq!(fs::read(&config.state_file).unwrap(), before_state);
}

#[test]
fn write_mode_writes_notes_and_state_to_real_paths() {
    let root = temp_dir("write-first");
    let config = sync_config(&root, false);
    let session_id = "019d23a7-9258-7810-93cc-c6833b348308";
    let rollout = write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        session_id,
        &[
            session_meta(session_id, "2026-04-03T01:10:00Z", json!("vscode")),
            message_record("2026-04-03T01:10:01Z", "user", None, "실제 쓰기 질문"),
            message_record(
                "2026-04-03T01:10:02Z",
                "assistant",
                Some("final_answer"),
                "실제 쓰기 응답",
            ),
        ],
    );
    write_index(
        &config.codex_home,
        &[index_entry(
            session_id,
            "write mode",
            "2026-04-03T01:10:02Z",
        )],
    );

    let summary = sync_once_write_with_options(&config, run_options()).unwrap();

    assert!(!summary.dry_run);
    assert_eq!(summary.processed, 1);
    assert_eq!(summary.rewritten, 1);
    assert!(config.vault.join("Codex/Daily/2026-04-03.md").exists());
    assert!(config.state_file.exists());
    let conversation_text = fs::read_to_string(single_conversation_note(&config.vault)).unwrap();
    assert!(conversation_text.contains("실제 쓰기 질문"));
    let state = read_json(&config.state_file);
    assert_eq!(
        state["files"][rollout.to_string_lossy().as_ref()]["included"],
        true
    );
}

#[test]
fn write_mode_recovers_when_index_note_write_fails_after_conversation_note() {
    let root = temp_dir("write-index-failure-recovery");
    let config = sync_config(&root, false);
    let session_id = "019d23a7-9258-7810-93cc-c6833b348320";
    let rollout = write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        session_id,
        &[
            session_meta(session_id, "2026-04-03T01:20:00Z", json!("vscode")),
            message_record("2026-04-03T01:20:01Z", "user", None, "부분 실패 첫 질문"),
        ],
    );
    write_index(
        &config.codex_home,
        &[index_entry(
            session_id,
            "index failure recovery",
            "2026-04-03T01:20:02Z",
        )],
    );
    sync_once_write_with_options(&config, run_options()).unwrap();
    let old_state = fs::read(&config.state_file).unwrap();
    append_jsonl(
        &rollout,
        &message_record(
            "2026-04-03T01:20:02Z",
            "assistant",
            Some("final_answer"),
            "부분 실패 후속 응답",
        ),
    );
    let daily_note = config.vault.join("Codex/Daily/2026-04-03.md");
    fs::remove_file(&daily_note).unwrap();
    fs::create_dir(&daily_note).unwrap();

    let error = sync_once_write_with_options(&config, run_options()).unwrap_err();

    assert_eq!(error.to_string(), "write error");
    let conversation_text = fs::read_to_string(single_conversation_note(&config.vault)).unwrap();
    assert!(conversation_text.contains("부분 실패 후속 응답"));
    assert_eq!(fs::read(&config.state_file).unwrap(), old_state);

    fs::remove_dir(&daily_note).unwrap();
    let summary = sync_once_write_with_options(&config, run_options()).unwrap();

    assert_eq!(summary.processed, 1);
    assert!(daily_note.is_file());
    assert!(config.vault.join("Codex/Projects/demo-project.md").exists());
    assert_eq!(
        read_json(&config.state_file)["files"][rollout.to_string_lossy().as_ref()]["included"],
        true
    );
}

#[cfg(unix)]
#[test]
fn write_mode_recovers_when_state_save_fails_after_note_writes() {
    let root = temp_dir("write-state-failure-recovery");
    let config = sync_config(&root, false);
    let session_id = "019d23a7-9258-7810-93cc-c6833b348321";
    let rollout = write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        session_id,
        &[
            session_meta(session_id, "2026-04-03T01:21:00Z", json!("vscode")),
            message_record("2026-04-03T01:21:01Z", "user", None, "state 실패 첫 질문"),
        ],
    );
    write_index(
        &config.codex_home,
        &[index_entry(
            session_id,
            "state failure recovery",
            "2026-04-03T01:21:02Z",
        )],
    );
    sync_once_write_with_options(&config, run_options()).unwrap();
    let old_state = fs::read(&config.state_file).unwrap();
    append_jsonl(
        &rollout,
        &message_record(
            "2026-04-03T01:21:02Z",
            "assistant",
            Some("final_answer"),
            "state 실패 후속 응답",
        ),
    );
    let blocked_state_target = root.join("blocked-state-target.json");
    fs::write(&blocked_state_target, &old_state).unwrap();
    fs::remove_file(&config.state_file).unwrap();
    symlink(&blocked_state_target, &config.state_file).unwrap();

    let error = sync_once_write_with_options(&config, run_options()).unwrap_err();

    assert_eq!(error.to_string(), "write error");
    let conversation_text = fs::read_to_string(single_conversation_note(&config.vault)).unwrap();
    assert!(conversation_text.contains("state 실패 후속 응답"));
    assert!(config.vault.join("Codex/Daily/2026-04-03.md").exists());
    assert!(config.vault.join("Codex/Projects/demo-project.md").exists());
    assert_eq!(fs::read(&blocked_state_target).unwrap(), old_state);

    fs::remove_file(&config.state_file).unwrap();
    fs::write(&config.state_file, old_state).unwrap();
    let summary = sync_once_write_with_options(&config, run_options()).unwrap();

    assert_eq!(summary.processed, 1);
    assert_eq!(
        read_json(&config.state_file)["files"][rollout.to_string_lossy().as_ref()]["included"],
        true
    );
}

#[test]
fn write_mode_appends_to_existing_real_note() {
    let root = temp_dir("write-append");
    let config = sync_config(&root, false);
    let session_id = "019d23a7-9258-7810-93cc-c6833b348309";
    let rollout = write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        session_id,
        &[
            session_meta(session_id, "2026-04-03T01:11:00Z", json!("vscode")),
            message_record("2026-04-03T01:11:01Z", "user", None, "첫 실제 질문"),
        ],
    );
    write_index(
        &config.codex_home,
        &[index_entry(
            session_id,
            "write append",
            "2026-04-03T01:11:01Z",
        )],
    );
    sync_once_write_with_options(&config, run_options()).unwrap();
    append_jsonl(
        &rollout,
        &message_record("2026-04-03T01:11:02Z", "user", None, "두 번째 실제 질문"),
    );

    let summary = sync_once_write_with_options(&config, run_options()).unwrap();

    assert_eq!(summary.processed, 1);
    assert_eq!(summary.appended, 1);
    assert_eq!(summary.rewritten, 0);
    let conversation_text = fs::read_to_string(single_conversation_note(&config.vault)).unwrap();
    assert!(conversation_text.contains("첫 실제 질문"));
    assert!(conversation_text.contains("두 번째 실제 질문"));
}

#[test]
fn write_mode_falls_back_to_full_rebuild_when_incremental_offset_is_stale() {
    let root = temp_dir("write-stale-offset");
    let config = sync_config(&root, false);
    let session_id = "019d23a7-9258-7810-93cc-c6833b348310";
    let rollout = write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        session_id,
        &[
            session_meta(session_id, "2026-04-03T01:12:00Z", json!("vscode")),
            message_record("2026-04-03T01:12:01Z", "user", None, "첫 실제 질문"),
        ],
    );
    write_index(
        &config.codex_home,
        &[index_entry(
            session_id,
            "write stale offset",
            "2026-04-03T01:12:01Z",
        )],
    );
    sync_once_write_with_options(&config, run_options()).unwrap();
    append_jsonl(
        &rollout,
        &message_record(
            "2026-04-03T01:12:02Z",
            "assistant",
            Some("final_answer"),
            "offset 복구 실제 응답",
        ),
    );
    bump_state_offset(&config.state_file, &rollout);

    let summary = sync_once_write_with_options(&config, run_options()).unwrap();

    assert_eq!(summary.processed, 1);
    assert_eq!(summary.skipped_invalid, 0);
    assert_eq!(summary.appended, 1);
    assert_eq!(summary.rewritten, 1);
    let conversation_text = fs::read_to_string(single_conversation_note(&config.vault)).unwrap();
    assert!(conversation_text.contains("offset 복구 실제 응답"));
}

#[test]
fn write_mode_quarantines_corrupt_state_before_writing_new_state() {
    let root = temp_dir("write-corrupt-state");
    let config = sync_config(&root, false);
    let session_id = "019d23a7-9258-7810-93cc-c6833b348311";
    write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        session_id,
        &[
            session_meta(session_id, "2026-04-03T01:12:00Z", json!("vscode")),
            message_record("2026-04-03T01:12:01Z", "user", None, "state 복구"),
        ],
    );
    write_index(
        &config.codex_home,
        &[index_entry(
            session_id,
            "corrupt state",
            "2026-04-03T01:12:01Z",
        )],
    );
    fs::write(&config.state_file, "{").unwrap();

    sync_once_write_with_options(&config, run_options()).unwrap();

    assert!(config.state_file.exists());
    assert!(read_json(&config.state_file)["files"].is_object());
    let quarantined = fs::read_dir(config.state_file.parent().unwrap())
        .unwrap()
        .filter_map(Result::ok)
        .any(|entry| {
            entry
                .file_name()
                .to_string_lossy()
                .starts_with("sync-state.json.corrupt-")
        });
    assert!(quarantined);
}

#[test]
fn cli_write_rejects_dry_run_output() {
    let root = temp_dir("write-dry-run-output");
    let config = sync_config(&root, false);

    Command::cargo_bin(BIN)
        .unwrap()
        .args([
            "sync-once",
            "--write",
            "--vault",
            config.vault.to_str().unwrap(),
            "--codex-home",
            config.codex_home.to_str().unwrap(),
            "--state-file",
            config.state_file.to_str().unwrap(),
            "--lock-file",
            config.lock_file.to_str().unwrap(),
            "--dry-run-output",
            root.join("output").to_str().unwrap(),
        ])
        .assert()
        .failure()
        .stderr(predicates::str::contains("config error"));
}

#[test]
fn cli_write_rejects_trace_read_paths() {
    let root = temp_dir("write-trace-read-paths");
    let config = sync_config(&root, false);

    Command::cargo_bin(BIN)
        .unwrap()
        .args([
            "sync-once",
            "--write",
            "--vault",
            config.vault.to_str().unwrap(),
            "--codex-home",
            config.codex_home.to_str().unwrap(),
            "--state-file",
            config.state_file.to_str().unwrap(),
            "--lock-file",
            config.lock_file.to_str().unwrap(),
            "--trace-read-paths",
            root.join("read-trace.jsonl").to_str().unwrap(),
        ])
        .assert()
        .failure()
        .stderr(predicates::str::contains("config error"));
}

#[test]
fn cli_write_outputs_python_shaped_summary() {
    let root = temp_dir("write-summary-shape");
    let config = sync_config(&root, false);
    let session_id = "019d23a7-9258-7810-93cc-c6833b348312";
    write_rollout(
        &config.codex_home,
        "2026",
        "04",
        "03",
        session_id,
        &[
            session_meta(session_id, "2026-04-03T01:13:00Z", json!("vscode")),
            message_record("2026-04-03T01:13:01Z", "user", None, "summary shape"),
        ],
    );
    write_index(
        &config.codex_home,
        &[index_entry(
            session_id,
            "summary shape",
            "2026-04-03T01:13:01Z",
        )],
    );

    let assert = Command::cargo_bin(BIN)
        .unwrap()
        .args([
            "sync-once",
            "--write",
            "--vault",
            config.vault.to_str().unwrap(),
            "--codex-home",
            config.codex_home.to_str().unwrap(),
            "--state-file",
            config.state_file.to_str().unwrap(),
            "--lock-file",
            config.lock_file.to_str().unwrap(),
        ])
        .assert()
        .success();
    let stdout = String::from_utf8(assert.get_output().stdout.clone()).unwrap();
    let summary: Value = serde_json::from_str(&stdout).unwrap();

    assert_eq!(summary["processed"], 1);
    assert!(summary.get("dry_run").is_none());
    assert!(summary.get("output_dir").is_none());
    assert!(summary.get("temp_state_file").is_none());
    assert!(summary.get("planned_writes").is_none());
    assert!(summary.get("lock_exists").is_none());
}

#[test]
fn cli_write_reports_lock_contention() {
    let root = temp_dir("write-lock-contention");
    let config = sync_config(&root, false);
    let _lock = ProcessLock::try_acquire(&config.lock_file).unwrap();

    Command::cargo_bin(BIN)
        .unwrap()
        .args([
            "sync-once",
            "--write",
            "--vault",
            config.vault.to_str().unwrap(),
            "--codex-home",
            config.codex_home.to_str().unwrap(),
            "--state-file",
            config.state_file.to_str().unwrap(),
            "--lock-file",
            config.lock_file.to_str().unwrap(),
        ])
        .assert()
        .failure()
        .stderr(predicates::str::contains(
            "Another codex-obsidian-sync process is already running",
        ));
}

#[test]
fn cli_config_failure_exits_nonzero() {
    let root = temp_dir("cli-config-failure");
    let config = sync_config(&root, false);
    let output = root.join("output");

    Command::cargo_bin(BIN)
        .unwrap()
        .args([
            "sync-once",
            "--vault",
            root.join("missing-vault").to_str().unwrap(),
            "--codex-home",
            config.codex_home.to_str().unwrap(),
            "--state-file",
            config.state_file.to_str().unwrap(),
            "--dry-run-output",
            output.to_str().unwrap(),
        ])
        .assert()
        .failure();
}

fn sync_config(root: &Path, include_subagents: bool) -> SyncConfig {
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
        include_subagents,
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
        read_trace_path: None,
    }
}

fn write_index(codex_home: &Path, entries: &[Value]) {
    write_jsonl(&codex_home.join("session_index.jsonl"), entries);
}

fn index_entry(session_id: &str, thread_name: &str, updated_at: &str) -> Value {
    json!({
        "id": session_id,
        "thread_name": thread_name,
        "updated_at": updated_at,
    })
}

fn write_rollout(
    codex_home: &Path,
    year: &str,
    month: &str,
    day: &str,
    session_id: &str,
    records: &[Value],
) -> PathBuf {
    let path = rollout_path(codex_home, year, month, day, session_id);
    write_jsonl(&path, records);
    path
}

fn rollout_path(
    codex_home: &Path,
    year: &str,
    month: &str,
    day: &str,
    session_id: &str,
) -> PathBuf {
    codex_home
        .join("sessions")
        .join(year)
        .join(month)
        .join(day)
        .join(format!("rollout-2026-04-03T10-00-00-{session_id}.jsonl"))
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

fn append_jsonl(path: &Path, record: &Value) {
    let mut file = fs::OpenOptions::new().append(true).open(path).unwrap();
    writeln!(file, "{}", serde_json::to_string(record).unwrap()).unwrap();
}

fn session_meta(session_id: &str, timestamp: &str, source: Value) -> Value {
    json!({
        "timestamp": timestamp,
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "timestamp": timestamp,
            "cwd": "/tmp/demo-project",
            "originator": "Codex Desktop",
            "source": source,
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

fn single_conversation_note(root: &Path) -> PathBuf {
    let conversation_dir = root.join("Codex").join("Conversations").join("2026");
    let notes = fs::read_dir(conversation_dir)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .collect::<Vec<_>>();
    assert_eq!(notes.len(), 1);
    notes[0].clone()
}

fn materialize_output_as_real_state(config: &SyncConfig, output: &Path) {
    let codex_output = output.join("Codex");
    if codex_output.exists() {
        fs::rename(codex_output, config.vault.join("Codex")).unwrap();
    }
    fs::create_dir_all(config.state_file.parent().unwrap()).unwrap();
    fs::copy(output.join("sync-state.json"), &config.state_file).unwrap();
}

fn bump_state_offset(state_file: &Path, rollout: &Path) {
    let mut state = read_json(state_file);
    state["files"][rollout.to_string_lossy().as_ref()]["offset"] = json!(
        state["files"][rollout.to_string_lossy().as_ref()]["offset"]
            .as_u64()
            .unwrap()
            + 1
    );
    fs::write(state_file, serde_json::to_string_pretty(&state).unwrap()).unwrap();
}

fn read_json(path: impl AsRef<Path>) -> Value {
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
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

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-sync-{name}-{}-{}",
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
