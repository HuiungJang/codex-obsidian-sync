use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use codex_obsidian_sync_rs::config::{load_toml_config, resolve_service_paths};
use codex_obsidian_sync_rs::status_snapshot::{build_status_snapshot, render_status_snapshot};
use serde_json::{Map, Value, json};
use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;

#[test]
fn status_snapshot_uses_cooldown_terms_and_ready_now() {
    let root = temp_dir("ready-now");
    let config_path = write_config(&root, 60);
    let config = load_toml_config(Some(&config_path)).unwrap();
    let paths = resolve_service_paths(Some(&config_path), &config).unwrap();
    let now = OffsetDateTime::parse("2026-05-16T00:02:00Z", &Rfc3339).unwrap();
    let service_state = service_state(json!({
        "pending": false,
        "last_run_finished_at": "2026-05-16T00:00:00+00:00",
        "last_success_at": "2026-05-16T00:00:00+00:00",
        "last_error_type": null,
        "last_error_summary": null,
        "last_summary": {"processed": 1},
    }));

    let snapshot = build_status_snapshot(&config, &paths, true, &service_state, now);
    let rendered = render_status_snapshot(&snapshot);

    assert_eq!(snapshot.cooldown, "1m (60s)");
    assert_eq!(snapshot.next_eligible_run, "ready now");
    assert!(rendered.contains("Cooldown: 1m (60s)"));
    assert!(rendered.contains("Next eligible run: ready now"));
}

#[test]
fn status_snapshot_preserves_distinct_error_types_and_launchd_state() {
    let root = temp_dir("errors");
    let config_path = write_config(&root, 10);
    let config = load_toml_config(Some(&config_path)).unwrap();
    let paths = resolve_service_paths(Some(&config_path), &config).unwrap();
    let now = OffsetDateTime::parse("2026-05-16T00:02:00Z", &Rfc3339).unwrap();

    for error_type in [
        "ConfigError",
        "LockContention",
        "LaunchdError",
        "ParseError",
        "WriteError",
    ] {
        let service_state = service_state(json!({
            "pending": false,
            "last_error_type": error_type,
            "last_error_summary": "phase 18 test",
        }));

        let snapshot = build_status_snapshot(&config, &paths, false, &service_state, now);
        let rendered = render_status_snapshot(&snapshot);
        let payload = serde_json::to_value(&snapshot).unwrap();

        assert_eq!(snapshot.last_error, format!("{error_type}: phase 18 test"));
        assert_eq!(
            payload["last_error"],
            format!("{error_type}: phase 18 test")
        );
        assert_eq!(payload["launchd_loaded"], false);
        assert!(!snapshot.launchd_loaded);
        assert!(rendered.contains("LaunchAgent: unloaded"));
    }
}

fn service_state(value: Value) -> Map<String, Value> {
    value.as_object().unwrap().clone()
}

fn write_config(root: &Path, interval_seconds: u64) -> PathBuf {
    let codex_home = root.join(".codex");
    let vault = root.join("vault");
    fs::create_dir_all(&codex_home).unwrap();
    fs::create_dir_all(&vault).unwrap();
    let config_path = root.join("config.toml");
    fs::write(
        &config_path,
        format!(
            "vault = \"{}\"\ncodex_home = \"{}\"\ninterval_seconds = {interval_seconds}\n",
            vault.display(),
            codex_home.display(),
        ),
    )
    .unwrap();
    config_path
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-status-snapshot-{name}-{}-{}",
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
