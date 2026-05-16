use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use assert_cmd::prelude::*;
use codex_obsidian_sync_rs::cli::{Cli, Command as CliCommand};
use insta_cmd::{assert_cmd_snapshot, get_cargo_bin};

const BIN: &str = "codex-obsidian-sync-rs";

#[test]
fn root_help_is_snapshotted() {
    assert_cmd_snapshot!(Command::new(get_cargo_bin(BIN)).arg("--help"), @r###"
success: true
exit_code: 0
----- stdout -----
Sync local Codex conversations into an Obsidian vault

Usage: codex-obsidian-sync-rs [OPTIONS] <COMMAND>

Commands:
  setup            Write config and LaunchAgent plist
  start            Start the LaunchAgent
  stop             Stop the LaunchAgent
  inspect-rollout  Inspect one rollout file as redacted JSON
  inspect-recent   Inspect recent rollout files as redacted JSON
  status           Show read-only service status
  sync-once        Run one dry-run sync pass

Options:
      --config <CONFIG>  Path to config.toml
  -h, --help             Print help
  -V, --version          Print version

----- stderr -----

"###);
}

#[test]
fn sync_once_help_is_snapshotted() {
    assert_cmd_snapshot!(Command::new(get_cargo_bin(BIN)).args(["sync-once", "--help"]), @r###"
success: true
exit_code: 0
----- stdout -----
Run one dry-run sync pass

Usage: codex-obsidian-sync-rs sync-once [OPTIONS]

Options:
      --vault <VAULT>                                  Obsidian vault root
      --codex-home <CODEX_HOME>                        Codex home directory
      --state-file <STATE_FILE>                        Sync state file
      --lock-file <LOCK_FILE>                          Sync lock file
      --dry-run-output <DIR>                           Directory for dry-run output
      --write                                          Write to the configured vault and state
      --include-subagents                              Include subagent conversations
      --log-level <LOG_LEVEL>                          Log level
      --recent-days <RECENT_DAYS>                      Recent index window in days
      --candidate-file-limit <CANDIDATE_FILE_LIMIT>    Maximum candidate rollout files
      --candidate-bytes-limit <CANDIDATE_BYTES_LIMIT>  Maximum candidate bytes
  -h, --help                                           Print help

----- stderr -----

"###);
}

#[test]
fn version_reports_package_version() {
    Command::cargo_bin(BIN)
        .unwrap()
        .arg("--version")
        .assert()
        .success()
        .stdout(format!("{BIN} {}\n", env!("CARGO_PKG_VERSION")));
}

#[test]
fn unknown_flag_exits_nonzero() {
    Command::cargo_bin(BIN)
        .unwrap()
        .arg("--unknown")
        .assert()
        .failure()
        .code(2);
}

#[test]
fn missing_config_value_exits_nonzero() {
    Command::cargo_bin(BIN)
        .unwrap()
        .arg("--config")
        .assert()
        .failure()
        .code(2);
}

#[test]
fn invalid_recent_days_exits_nonzero() {
    Command::cargo_bin(BIN)
        .unwrap()
        .args(["sync-once", "--recent-days", "0"])
        .assert()
        .failure()
        .code(2);
}

#[test]
fn sync_once_command_shape_defaults_to_dry_run_contract() {
    let cli = Cli::try_parse_from([BIN, "sync-once"]).unwrap();
    let CliCommand::SyncOnce(args) = cli.command else {
        panic!("expected sync-once command");
    };

    assert!(args.dry_run_output.is_none());
    assert!(!args.write);
    assert_eq!(args.include_subagents, None);
}

#[test]
fn include_subagents_records_command_line_override() {
    let cli = Cli::try_parse_from([BIN, "sync-once", "--include-subagents"]).unwrap();
    let CliCommand::SyncOnce(args) = cli.command else {
        panic!("expected sync-once command");
    };

    assert_eq!(args.include_subagents, Some(true));
}

#[test]
fn write_flag_is_opt_in() {
    let cli = Cli::try_parse_from([BIN, "sync-once", "--write"]).unwrap();
    let CliCommand::SyncOnce(args) = cli.command else {
        panic!("expected sync-once command");
    };

    assert!(args.write);
}

#[test]
fn setup_writes_config_and_plist_preserving_unknown_keys() {
    let root = temp_dir("setup");
    let vault = root.join("vault");
    let codex_home = root.join(".codex");
    let plist_path = root.join("LaunchAgents").join("agent.plist");
    let stdout_path = root.join("logs").join("stdout.log");
    let stderr_path = root.join("logs").join("stderr.log");
    let config_path = root.join("config.toml");
    fs::create_dir_all(&vault).unwrap();
    fs::create_dir_all(&codex_home).unwrap();
    fs::write(
        &config_path,
        format!(
            "codex_home = \"{}\"\nlaunchd_plist_path = \"{}\"\nlaunchd_stdout_path = \"{}\"\nlaunchd_stderr_path = \"{}\"\nextra_flag = true\n",
            codex_home.display(),
            plist_path.display(),
            stdout_path.display(),
            stderr_path.display(),
        ),
    )
    .unwrap();

    Command::cargo_bin(BIN)
        .unwrap()
        .args([
            "--config",
            config_path.to_str().unwrap(),
            "setup",
            "--vault",
            vault.to_str().unwrap(),
            "--cooldown",
            "1m",
        ])
        .assert()
        .success();

    let config = fs::read_to_string(&config_path).unwrap();
    let plist = fs::read_to_string(&plist_path).unwrap();

    assert!(config.contains(&format!(
        "vault = \"{}\"",
        fs::canonicalize(&vault).unwrap().display()
    )));
    assert!(config.contains("interval_seconds = 60"));
    assert!(config.contains("extra_flag = true"));
    assert!(plist.contains("service-run"));
    assert!(plist.contains("--config"));
    assert!(plist.contains(config_path.to_str().unwrap()));
}

#[test]
fn config_is_root_only() {
    Command::cargo_bin(BIN)
        .unwrap()
        .args(["sync-once", "--config", "/tmp/config.toml"])
        .assert()
        .failure()
        .code(2);
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-cli-{name}-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let path = std::env::temp_dir().join(unique);
    fs::create_dir_all(&path).unwrap();
    fs::canonicalize(path).unwrap()
}

#[test]
fn generated_help_subcommand_is_disabled() {
    Command::cargo_bin(BIN)
        .unwrap()
        .arg("help")
        .assert()
        .failure()
        .code(2);
}
