use std::fs;
use std::path::{Path, PathBuf};

use codex_obsidian_sync_rs::config::{
    SyncConfigOverrides, TomlConfig, default_codex_home, load_toml_config, resolve_sync_config,
};

#[test]
fn load_toml_config_and_resolve_sync_config() {
    let root = temp_dir("resolve-sync-config");
    let config_path = root.join("config.toml");
    fs::write(
        &config_path,
        r#"
vault = "/tmp/obsidian"
codex_home = "/tmp/codex"
state_file = "/tmp/state.json"
lock_file = "/tmp/state.lock"
include_subagents = true
interval_seconds = 15
recent_days = 7
candidate_file_limit = 12
candidate_bytes_limit = 4096
log_level = "debug"
extra_flag = true
"#,
    )
    .unwrap();

    let config = load_toml_config(Some(&config_path)).unwrap();
    let resolved = resolve_sync_config(&config, &SyncConfigOverrides::default()).unwrap();

    assert_eq!(resolved.vault, Path::new("/tmp/obsidian"));
    assert_eq!(resolved.codex_home, Path::new("/tmp/codex"));
    assert_eq!(resolved.state_file, Path::new("/tmp/state.json"));
    assert_eq!(resolved.lock_file, Path::new("/tmp/state.lock"));
    assert!(resolved.include_subagents);
    assert_eq!(resolved.interval_seconds, 15);
    assert_eq!(resolved.recent_days, 7);
    assert_eq!(resolved.candidate_file_limit, 12);
    assert_eq!(resolved.candidate_bytes_limit, 4096);
    assert_eq!(resolved.log_level, "DEBUG");
    assert_eq!(
        config
            .raw()
            .get("extra_flag")
            .and_then(|value| value.as_bool()),
        Some(true)
    );
}

#[test]
fn cli_overrides_config_values_without_mutating_unknown_keys() {
    let root = temp_dir("cli-overrides");
    let config_path = root.join("config.toml");
    fs::write(
        &config_path,
        r#"
vault = "/tmp/config-vault"
codex_home = "/tmp/config-codex"
include_subagents = false
recent_days = 2
candidate_file_limit = 3
candidate_bytes_limit = 4
log_level = "warning"
unknown_table = { keep = "yes" }
"#,
    )
    .unwrap();

    let config = load_toml_config(Some(&config_path)).unwrap();
    let resolved = resolve_sync_config(
        &config,
        &SyncConfigOverrides {
            vault: Some(PathBuf::from("/tmp/cli-vault")),
            include_subagents: Some(true),
            recent_days: Some(8),
            log_level: Some("debug".to_owned()),
            ..SyncConfigOverrides::default()
        },
    )
    .unwrap();

    assert_eq!(resolved.vault, Path::new("/tmp/cli-vault"));
    assert!(resolved.include_subagents);
    assert_eq!(resolved.recent_days, 8);
    assert_eq!(resolved.log_level, "DEBUG");
    assert!(config.raw().contains_key("unknown_table"));
}

#[test]
fn cli_codex_home_does_not_move_default_state_or_lock_paths() {
    let root = temp_dir("cli-codex-home-state-defaults");
    let config_path = root.join("config.toml");
    fs::write(
        &config_path,
        r#"
vault = "/tmp/config-vault"
codex_home = "/tmp/config-codex"
"#,
    )
    .unwrap();

    let config = load_toml_config(Some(&config_path)).unwrap();
    let resolved = resolve_sync_config(
        &config,
        &SyncConfigOverrides {
            codex_home: Some(PathBuf::from("/tmp/cli-codex")),
            ..SyncConfigOverrides::default()
        },
    )
    .unwrap();

    assert_eq!(resolved.codex_home, Path::new("/tmp/cli-codex"));
    assert_eq!(
        resolved.state_file,
        Path::new("/tmp/config-codex/obsidian-sync/sync-state.json")
    );
    assert_eq!(
        resolved.lock_file,
        Path::new("/tmp/config-codex/obsidian-sync/sync-state.lock")
    );
}

#[test]
fn tilde_paths_expand_to_home_directory() {
    let root = temp_dir("tilde-paths");
    let config_path = root.join("config.toml");
    fs::write(
        &config_path,
        r#"
vault = "~/obsidian-vault"
codex_home = "~/.codex-test"
"#,
    )
    .unwrap();

    let home = home_dir();
    let config = load_toml_config(Some(&config_path)).unwrap();
    let resolved = resolve_sync_config(&config, &SyncConfigOverrides::default()).unwrap();

    assert_eq!(resolved.vault, home.join("obsidian-vault"));
    assert_eq!(resolved.codex_home, home.join(".codex-test"));
}

#[cfg(unix)]
#[test]
fn user_tilde_paths_expand_like_python_expanduser() {
    let root = temp_dir("user-tilde-paths");
    let config_path = root.join("config.toml");
    let username = std::env::var("USER").unwrap();
    fs::write(
        &config_path,
        format!(
            r#"
vault = "~{username}/obsidian-vault"
codex_home = "~{username}/.codex-test"
"#
        ),
    )
    .unwrap();

    let home = home_dir();
    let config = load_toml_config(Some(&config_path)).unwrap();
    let resolved = resolve_sync_config(&config, &SyncConfigOverrides::default()).unwrap();

    assert_eq!(resolved.vault, home.join("obsidian-vault"));
    assert_eq!(resolved.codex_home, home.join(".codex-test"));
}

#[test]
fn missing_config_file_loads_empty_config() {
    let config = load_toml_config(Some(Path::new(
        "/tmp/codex-obsidian-sync-missing-config.toml",
    )))
    .unwrap();
    assert!(config.raw().is_empty());
}

#[test]
fn invalid_toml_is_a_config_error() {
    let root = temp_dir("invalid-toml");
    let config_path = root.join("config.toml");
    fs::write(&config_path, "vault = ").unwrap();

    let error = load_toml_config(Some(&config_path)).unwrap_err();
    assert_eq!(error.to_string(), "config error");
}

#[test]
fn missing_vault_is_rejected_for_sync_config() {
    let config = TomlConfig::empty();
    let error = resolve_sync_config(
        &config,
        &SyncConfigOverrides {
            codex_home: Some(PathBuf::from("/tmp/codex")),
            ..SyncConfigOverrides::default()
        },
    )
    .unwrap_err();

    assert_eq!(error.to_string(), "config error");
}

#[test]
fn relative_paths_are_rejected() {
    let root = temp_dir("relative-paths");
    let config_path = root.join("config.toml");
    fs::write(
        &config_path,
        r#"
vault = "relative-vault"
"#,
    )
    .unwrap();

    let config = load_toml_config(Some(&config_path)).unwrap();
    assert!(resolve_sync_config(&config, &SyncConfigOverrides::default()).is_err());
}

#[test]
fn default_values_match_python_sync_config_defaults() {
    let config = TomlConfig::empty();
    let resolved = resolve_sync_config(
        &config,
        &SyncConfigOverrides {
            vault: Some(PathBuf::from("/tmp/vault")),
            ..SyncConfigOverrides::default()
        },
    )
    .unwrap();

    let expected_codex_home = default_codex_home().unwrap();
    assert_eq!(resolved.codex_home, expected_codex_home);
    assert_eq!(
        resolved.state_file,
        expected_codex_home
            .join("obsidian-sync")
            .join("sync-state.json")
    );
    assert_eq!(
        resolved.lock_file,
        expected_codex_home
            .join("obsidian-sync")
            .join("sync-state.lock")
    );
    assert!(!resolved.include_subagents);
    assert_eq!(resolved.interval_seconds, 10);
    assert_eq!(resolved.recent_days, 30);
    assert_eq!(resolved.candidate_file_limit, 100);
    assert_eq!(resolved.candidate_bytes_limit, 500 * 1024 * 1024);
    assert_eq!(resolved.log_level, "INFO");
}

#[test]
fn config_example_is_readable() {
    let config = load_toml_config(Some(Path::new("../config.example.toml"))).unwrap();
    let resolved = resolve_sync_config(&config, &SyncConfigOverrides::default()).unwrap();

    assert_eq!(
        resolved.vault,
        Path::new("/absolute/path/to/your/obsidian-vault")
    );
    assert_eq!(resolved.interval_seconds, 60);
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-{name}-{}-{}",
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

fn home_dir() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap()
}
