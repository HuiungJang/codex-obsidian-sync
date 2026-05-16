use std::process::Command;

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
fn config_is_root_only() {
    Command::cargo_bin(BIN)
        .unwrap()
        .args(["sync-once", "--config", "/tmp/config.toml"])
        .assert()
        .failure()
        .code(2);
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
