use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use codex_obsidian_sync_rs::config::SyncConfig;
use codex_obsidian_sync_rs::dry_run_output::DryRunOutput;
use serde_json::Value;

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

#[test]
fn prepare_rejects_output_inside_real_vault_codex_home_and_state_dir() {
    let root = temp_dir("dangerous-roots");
    let config = sync_config(&root);

    for path in [
        config.vault.join("dry-run"),
        config.codex_home.join("dry-run"),
        config.state_file.parent().unwrap().join("dry-run"),
    ] {
        assert!(DryRunOutput::prepare(&config, Some(&path)).is_err());
        assert!(!path.exists());
    }
}

#[cfg(unix)]
#[test]
fn prepare_rejects_output_inside_vault_through_symlink_ancestor_before_mutation() {
    let root = temp_dir("symlink-output-root");
    let config = sync_config(&root);
    let vault_link = root.join("vault-link");
    std::os::unix::fs::symlink(&config.vault, &vault_link).unwrap();
    let requested = vault_link.join("new").join("output");

    assert!(DryRunOutput::prepare(&config, Some(&requested)).is_err());
    assert!(!config.vault.join("new").exists());
}

#[test]
fn prepare_rejects_existing_non_empty_or_file_output() {
    let root = temp_dir("non-empty-output");
    let config = sync_config(&root);
    let output = root.join("output");
    fs::create_dir_all(&output).unwrap();
    fs::write(output.join("partial.md"), "interrupted").unwrap();

    assert!(DryRunOutput::prepare(&config, Some(&output)).is_err());

    let output_file = root.join("output-file");
    fs::write(&output_file, "not a dir").unwrap();
    assert!(DryRunOutput::prepare(&config, Some(&output_file)).is_err());
}

#[test]
fn prepare_accepts_empty_output_and_builds_summary() {
    let root = temp_dir("summary");
    let config = sync_config(&root);
    let output = root.join("output");
    fs::create_dir_all(&output).unwrap();

    let dry_run = DryRunOutput::prepare(&config, Some(&output)).unwrap();
    let summary = dry_run.summary(3);

    assert_eq!(dry_run.root(), output);
    assert_eq!(dry_run.temp_state_file(), output.join("sync-state.json"));
    assert!(summary.dry_run);
    assert_eq!(summary.output_dir, output.to_string_lossy());
    assert_eq!(
        summary.temp_state_file,
        output.join("sync-state.json").to_string_lossy()
    );
    assert_eq!(summary.planned_writes, 3);
}

#[test]
fn prepare_creates_preserved_default_temp_output() {
    let root = temp_dir("default-temp");
    let config = sync_config(&root);

    let dry_run = DryRunOutput::prepare(&config, None).unwrap();

    assert!(dry_run.root().exists());
    assert!(dry_run.root().starts_with(std::env::temp_dir()));
    assert!(
        dry_run
            .root()
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with("codex-obsidian-sync-rs-dry-run-")
    );
}

#[cfg(unix)]
#[test]
fn default_output_uses_private_permissions_for_directory_and_files() {
    let root = temp_dir("default-permissions");
    let config = sync_config(&root);

    let dry_run = DryRunOutput::prepare(&config, None).unwrap();
    dry_run
        .write_relative("Codex/private.md", "secret")
        .unwrap();

    assert_eq!(mode(dry_run.root()), 0o700);
    assert_eq!(mode(&dry_run.root().join("Codex")), 0o700);
    assert_eq!(mode(&dry_run.root().join("Codex/private.md")), 0o600);
}

#[test]
fn overlay_read_prefers_output_then_real_vault_without_mutating_inputs() {
    let root = temp_dir("overlay");
    let config = sync_config(&root);
    let output = root.join("output");
    let real_note = config
        .vault
        .join("Codex")
        .join("Daily")
        .join("2026-04-04.md");
    fs::create_dir_all(real_note.parent().unwrap()).unwrap();
    fs::write(&real_note, "real vault").unwrap();
    let before_hashes = input_hashes(&config, &root);

    let dry_run = DryRunOutput::prepare(&config, Some(&output)).unwrap();

    assert_eq!(
        dry_run.read_relative("Codex/Daily/2026-04-04.md").unwrap(),
        Some("real vault".to_owned())
    );
    dry_run
        .write_relative("Codex/Daily/2026-04-04.md", "dry output")
        .unwrap();
    assert_eq!(
        dry_run.read_relative("Codex/Daily/2026-04-04.md").unwrap(),
        Some("dry output".to_owned())
    );
    assert_eq!(fs::read_to_string(real_note).unwrap(), "real vault");
    assert_eq!(input_hashes(&config, &root), before_hashes);
}

#[test]
fn overlay_read_trace_records_read_attempt_paths_when_requested() {
    let root = temp_dir("read-trace");
    let config = sync_config(&root);
    let output = root.join("output");
    let trace = root.join("read-trace.jsonl");
    let real_note = config
        .vault
        .join("Codex")
        .join("Daily")
        .join("2026-04-04.md");
    fs::create_dir_all(real_note.parent().unwrap()).unwrap();
    fs::write(&real_note, "real vault").unwrap();

    let dry_run =
        DryRunOutput::prepare_with_read_trace(&config, Some(&output), Some(&trace)).unwrap();
    assert!(trace.exists());
    assert_eq!(fs::read_to_string(&trace).unwrap(), "");

    assert_eq!(
        dry_run.read_relative("Codex/Daily/2026-04-04.md").unwrap(),
        Some("real vault".to_owned())
    );
    dry_run
        .write_relative("Codex/Daily/2026-04-04.md", "dry output")
        .unwrap();
    assert_eq!(
        dry_run.read_relative("Codex/Daily/2026-04-04.md").unwrap(),
        Some("dry output".to_owned())
    );

    let events: Vec<Value> = fs::read_to_string(&trace)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(events.len(), 2);
    assert_eq!(events[0]["event"], "read_attempt");
    assert_eq!(events[0]["source"], "vault");
    assert_eq!(events[0]["relative_path"], "Codex/Daily/2026-04-04.md");
    assert_eq!(events[1]["event"], "read_attempt");
    assert_eq!(events[1]["source"], "dry_run_output");
    assert_eq!(events[1]["relative_path"], "Codex/Daily/2026-04-04.md");
}

#[test]
fn read_trace_rejects_forbidden_roots_and_output_root() {
    let root = temp_dir("read-trace-forbidden-roots");
    let config = sync_config(&root);

    for trace in [
        config.vault.join("read-trace.jsonl"),
        config.codex_home.join("read-trace.jsonl"),
        config.state_file.parent().unwrap().join("read-trace.jsonl"),
        root.join("output").join("read-trace.jsonl"),
    ] {
        assert!(
            DryRunOutput::prepare_with_read_trace(
                &config,
                Some(&root.join("output")),
                Some(&trace)
            )
            .is_err()
        );
        let _ = fs::remove_dir_all(root.join("output"));
    }
}

#[cfg(unix)]
#[test]
fn read_trace_rejects_symlinked_trace_file() {
    let root = temp_dir("read-trace-symlink");
    let config = sync_config(&root);
    let trace = root.join("read-trace.jsonl");
    let target = root.join("trace-target.jsonl");
    fs::write(&target, "keep\n").unwrap();
    std::os::unix::fs::symlink(&target, &trace).unwrap();

    assert!(
        DryRunOutput::prepare_with_read_trace(&config, Some(&root.join("output")), Some(&trace))
            .is_err()
    );
    assert_eq!(fs::read_to_string(target).unwrap(), "keep\n");
}

#[test]
fn write_rejects_absolute_traversal_and_existing_target_symlink() {
    let root = temp_dir("unsafe-writes");
    let config = sync_config(&root);
    let output = root.join("output");
    let dry_run = DryRunOutput::prepare(&config, Some(&output)).unwrap();

    assert!(dry_run.write_relative("/absolute.md", "x").is_err());
    assert!(dry_run.write_relative("../escape.md", "x").is_err());

    #[cfg(unix)]
    {
        let target = output.join("Codex").join("linked.md");
        fs::create_dir_all(target.parent().unwrap()).unwrap();
        std::os::unix::fs::symlink(root.join("outside.md"), &target).unwrap();
        assert!(dry_run.write_relative("Codex/linked.md", "x").is_err());
    }
}

#[cfg(unix)]
#[test]
fn write_rejects_symlink_parent_escape() {
    let root = temp_dir("symlink-parent");
    let config = sync_config(&root);
    let output = root.join("output");
    let dry_run = DryRunOutput::prepare(&config, Some(&output)).unwrap();
    let outside = root.join("outside");
    fs::create_dir_all(&outside).unwrap();
    std::os::unix::fs::symlink(&outside, output.join("linked-parent")).unwrap();

    assert!(
        dry_run
            .write_relative("linked-parent/new-dir/escape.md", "x")
            .is_err()
    );
    assert!(!outside.join("new-dir").exists());
}

#[cfg(unix)]
#[test]
fn write_rejects_root_symlink_swap_before_creating_parent() {
    let root = temp_dir("root-symlink-swap");
    let config = sync_config(&root);
    let output = root.join("output");
    let dry_run = DryRunOutput::prepare(&config, Some(&output)).unwrap();
    fs::remove_dir(&output).unwrap();
    std::os::unix::fs::symlink(&config.vault, &output).unwrap();

    assert!(dry_run.write_relative("Codex/escape.md", "x").is_err());
    assert!(!config.vault.join("Codex").exists());
}

#[test]
fn write_reports_output_failure_without_mutating_real_inputs() {
    let root = temp_dir("write-failure");
    let config = sync_config(&root);
    let output = root.join("output");
    let dry_run = DryRunOutput::prepare(&config, Some(&output)).unwrap();
    let before_hashes = input_hashes(&config, &root);
    fs::write(output.join("blocked"), "not a directory").unwrap();

    let result = dry_run.write_relative("blocked/file.md", "x");

    assert!(result.is_err());
    assert_eq!(input_hashes(&config, &root), before_hashes);
}

fn sync_config(root: &Path) -> SyncConfig {
    let vault = root.join("vault");
    let codex_home = root.join(".codex");
    let state_dir = root.join("state");
    fs::create_dir_all(&vault).unwrap();
    fs::create_dir_all(&codex_home).unwrap();
    fs::create_dir_all(&state_dir).unwrap();
    fs::write(codex_home.join("session_index.jsonl"), "{}\n").unwrap();
    fs::write(state_dir.join("sync-state.json"), "{\"files\":{}}\n").unwrap();
    fs::write(state_dir.join("sync-state.lock"), "lock").unwrap();
    fs::write(root.join("config.toml"), "vault = \"/tmp/example\"\n").unwrap();

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

fn input_hashes(config: &SyncConfig, root: &Path) -> BTreeMap<String, u64> {
    [
        (
            "vault",
            config
                .vault
                .join("Codex")
                .join("Daily")
                .join("2026-04-04.md"),
        ),
        (
            "session_index",
            config.codex_home.join("session_index.jsonl"),
        ),
        ("state", config.state_file.clone()),
        ("lock", config.lock_file.clone()),
        ("config", root.join("config.toml")),
    ]
    .into_iter()
    .map(|(label, path)| (label.to_owned(), file_hash(&path)))
    .collect()
}

fn file_hash(path: &Path) -> u64 {
    fs::read(path)
        .unwrap_or_default()
        .into_iter()
        .fold(0xcbf2_9ce4_8422_2325, |hash, byte| {
            hash.wrapping_mul(0x100_0000_01b3) ^ u64::from(byte)
        })
}

#[cfg(unix)]
fn mode(path: &Path) -> u32 {
    fs::metadata(path).unwrap().permissions().mode() & 0o777
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-dry-run-output-{name}-{}-{}",
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
