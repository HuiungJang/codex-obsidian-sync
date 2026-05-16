use std::cell::Cell;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use codex_obsidian_sync_rs::config::{load_toml_config, resolve_service_paths};
use codex_obsidian_sync_rs::error::SyncError;
#[cfg(unix)]
use codex_obsidian_sync_rs::lock::ProcessLock;
use codex_obsidian_sync_rs::service_runner::{ServiceRunOptions, run_service_with_sync};
use codex_obsidian_sync_rs::service_state::{load_service_state, mutate_service_state};
use codex_obsidian_sync_rs::status_snapshot::build_status_snapshot;
use codex_obsidian_sync_rs::sync::SyncSummary;
use serde_json::json;
use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;

#[test]
fn run_service_records_summary_and_success() {
    let root = temp_dir("summary");
    let config_path = write_config(&root, true);

    run_service_with_sync(
        Some(&config_path),
        ServiceRunOptions::fast_for_tests(),
        |_| Ok(summary(1, 0)),
    )
    .unwrap();
    let state = load_service_state(&service_state_path(&config_path));

    assert_eq!(state["last_summary"]["processed"], 1);
    assert_eq!(state["pending"], false);
    assert!(state["last_success_at"].is_string());
}

#[test]
fn run_service_flushes_pending_follow_up_trigger() {
    let root = temp_dir("pending-follow-up");
    let config_path = write_config(&root, true);
    let config = load_toml_config(Some(&config_path)).unwrap();
    let paths = resolve_service_paths(Some(&config_path), &config).unwrap();
    let calls = Cell::new(0);

    run_service_with_sync(
        Some(&config_path),
        ServiceRunOptions::fast_for_tests(),
        |_| {
            calls.set(calls.get() + 1);
            if calls.get() == 1 {
                mutate_service_state(
                    &paths.service_state_file,
                    &paths.service_state_lock_file,
                    |state| {
                        state.insert("pending".to_owned(), json!(true));
                    },
                )
                .unwrap();
            }
            Ok(summary(1, 0))
        },
    )
    .unwrap();
    let state = load_service_state(&paths.service_state_file);

    assert_eq!(calls.get(), 2);
    assert_eq!(state["pending"], false);
}

#[test]
fn run_service_schedules_follow_up_when_source_changes_during_run() {
    let root = temp_dir("source-changed");
    let config_path = write_config(&root, true);
    let config = load_toml_config(Some(&config_path)).unwrap();
    let paths = resolve_service_paths(Some(&config_path), &config).unwrap();
    let calls = Cell::new(0);

    run_service_with_sync(
        Some(&config_path),
        ServiceRunOptions::fast_for_tests(),
        |_| {
            calls.set(calls.get() + 1);
            if calls.get() == 1 {
                let rollout = paths
                    .sessions_path
                    .join("2026/05/16/rollout-019e2f05-0000-7000-8000-000000000001.jsonl");
                fs::create_dir_all(rollout.parent().unwrap()).unwrap();
                fs::write(rollout, "{}\n").unwrap();
            }
            Ok(summary(1, 0))
        },
    )
    .unwrap();

    assert_eq!(calls.get(), 2);
}

#[test]
fn run_service_records_failed_sync_error_without_success() {
    let root = temp_dir("failed-sync");
    let config_path = write_config(&root, true);

    run_service_with_sync(
        Some(&config_path),
        ServiceRunOptions::fast_for_tests(),
        |_| Err(SyncError::Write),
    )
    .unwrap();
    let state = load_service_state(&service_state_path(&config_path));

    assert_eq!(state["last_error_type"], "WriteError");
    assert_eq!(state["last_error_summary"], "write error");
    assert!(state["last_success_at"].is_null());
}

#[test]
fn run_service_preserves_last_success_when_later_sync_fails() {
    let root = temp_dir("success-then-failure");
    let config_path = write_config(&root, true);

    run_service_with_sync(
        Some(&config_path),
        ServiceRunOptions::fast_for_tests(),
        |_| Ok(summary(1, 0)),
    )
    .unwrap();
    let previous = load_service_state(&service_state_path(&config_path));
    let previous_success = previous["last_success_at"].as_str().unwrap().to_owned();

    run_service_with_sync(
        Some(&config_path),
        ServiceRunOptions::fast_for_tests(),
        |_| Err(SyncError::Write),
    )
    .unwrap();
    let state = load_service_state(&service_state_path(&config_path));

    assert_eq!(state["last_success_at"], previous_success);
    assert_eq!(state["last_error_type"], "WriteError");
    assert_eq!(state["pending"], false);
    assert!(state["next_eligible_at"].is_string());
}

#[test]
fn service_status_fields_survive_state_reload_after_restart() {
    let root = temp_dir("restart-state");
    let config_path = write_config(&root, true);
    let config = load_toml_config(Some(&config_path)).unwrap();
    let paths = resolve_service_paths(Some(&config_path), &config).unwrap();

    mutate_service_state(
        &paths.service_state_file,
        &paths.service_state_lock_file,
        |state| {
            state.insert("pending".to_owned(), json!(true));
            state.insert(
                "last_run_finished_at".to_owned(),
                json!("2026-05-16T00:00:00+00:00"),
            );
            state.insert(
                "next_eligible_at".to_owned(),
                json!("2026-05-16T00:01:00+00:00"),
            );
            state.insert(
                "last_success_at".to_owned(),
                json!("2026-05-15T23:59:00+00:00"),
            );
            state.insert("last_error_type".to_owned(), json!("WriteError"));
            state.insert("last_error_summary".to_owned(), json!("write error"));
            state.insert("last_summary".to_owned(), json!({"processed": 2}));
        },
    )
    .unwrap();

    let reloaded = load_service_state(&paths.service_state_file);
    let now = OffsetDateTime::parse("2026-05-16T00:00:30Z", &Rfc3339).unwrap();
    let snapshot = build_status_snapshot(&config, &paths, false, &reloaded, now);
    let reloaded_again = load_service_state(&paths.service_state_file);

    assert_eq!(snapshot.cooldown, "1s");
    assert_eq!(snapshot.pending, "yes");
    assert_eq!(snapshot.next_eligible_run, "2026-05-16T00:01:00+00:00");
    assert_eq!(snapshot.last_success, "2026-05-15T23:59:00+00:00");
    assert_eq!(snapshot.last_error, "WriteError: write error");
    assert_eq!(snapshot.last_summary["processed"], 2);
    assert_eq!(reloaded_again["pending"], true);
    assert_eq!(
        reloaded_again["next_eligible_at"],
        "2026-05-16T00:01:00+00:00"
    );
    assert_eq!(
        reloaded_again["last_success_at"],
        "2026-05-15T23:59:00+00:00"
    );
    assert_eq!(reloaded_again["last_error_type"], "WriteError");
}

#[cfg(unix)]
#[test]
fn run_service_keeps_pending_trigger_when_runner_lock_is_busy() {
    let root = temp_dir("runner-lock-pending");
    let config_path = write_config(&root, true);
    let config = load_toml_config(Some(&config_path)).unwrap();
    let paths = resolve_service_paths(Some(&config_path), &config).unwrap();
    let runner_lock = ProcessLock::try_acquire(&paths.service_runner_lock_file).unwrap();
    let called = Cell::new(false);

    let exit_code = run_service_with_sync(
        Some(&config_path),
        ServiceRunOptions::fast_for_tests(),
        |_| {
            called.set(true);
            Ok(summary(1, 0))
        },
    )
    .unwrap();
    let pending_state = load_service_state(&paths.service_state_file);

    assert_eq!(exit_code, 0);
    assert!(!called.get());
    assert_eq!(pending_state["pending"], true);

    drop(runner_lock);
    run_service_with_sync(
        Some(&config_path),
        ServiceRunOptions::fast_for_tests(),
        |_| {
            called.set(true);
            Ok(summary(1, 0))
        },
    )
    .unwrap();
    let finished_state = load_service_state(&paths.service_state_file);

    assert!(called.get());
    assert_eq!(finished_state["pending"], false);
    assert!(finished_state["last_success_at"].is_string());
}

#[test]
fn run_service_requires_explicit_write_gate() {
    let root = temp_dir("write-gate");
    let config_path = write_config(&root, false);
    let called = Cell::new(false);

    let error = run_service_with_sync(
        Some(&config_path),
        ServiceRunOptions::fast_for_tests(),
        |_| {
            called.set(true);
            Ok(summary(1, 0))
        },
    )
    .unwrap_err();

    assert_eq!(error.to_string(), "config error");
    assert!(!called.get());
}

fn summary(processed: usize, paused: usize) -> SyncSummary {
    SyncSummary {
        processed,
        appended: 0,
        rewritten: processed,
        skipped_subagents: 0,
        skipped_invalid: 0,
        unchanged: 0,
        total_rollouts: processed,
        paused,
        fast_path: 0,
        duration_ms: 1,
        dry_run: false,
        output_dir: String::new(),
        temp_state_file: String::new(),
        planned_writes: 0,
        lock_exists: false,
    }
}

fn service_state_path(config_path: &Path) -> PathBuf {
    let config = load_toml_config(Some(config_path)).unwrap();
    resolve_service_paths(Some(config_path), &config)
        .unwrap()
        .service_state_file
}

fn write_config(root: &Path, write_enabled: bool) -> PathBuf {
    let vault = root.join("vault");
    let codex_home = root.join(".codex");
    fs::create_dir_all(&vault).unwrap();
    fs::create_dir_all(&codex_home).unwrap();
    let config_path = root.join("config.toml");
    fs::write(
        &config_path,
        format!(
            "vault = \"{}\"\ncodex_home = \"{}\"\ninterval_seconds = 1\nrust_service_write_enabled = {write_enabled}\n",
            vault.display(),
            codex_home.display(),
        ),
    )
    .unwrap();
    config_path
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-service-runner-{name}-{}-{}",
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
