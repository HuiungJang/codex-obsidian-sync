use std::path::{Path, PathBuf};

use codex_obsidian_sync_rs::cli::{Cli, Command, SetupArgs};
use codex_obsidian_sync_rs::config::{
    SyncConfigOverrides, TomlConfig, expand_user_path, load_toml_config, resolve_service_paths,
    resolve_sync_config, save_toml_config,
};
use codex_obsidian_sync_rs::error::SyncError;
use codex_obsidian_sync_rs::inspect::{inspect_recent, inspect_rollout};
use codex_obsidian_sync_rs::intervals::parse_interval;
use codex_obsidian_sync_rs::launchd::{
    bootout_launch_agent, bootstrap_launch_agent, ensure_launch_agent_dirs, query_launchd_status,
    render_launch_agent_plist, write_launch_agent_plist,
};
use codex_obsidian_sync_rs::service_runner::run_service;
use codex_obsidian_sync_rs::service_state::load_service_state;
use codex_obsidian_sync_rs::status_snapshot::{
    build_status_snapshot, query_launchd_loaded, render_status_snapshot,
};
use codex_obsidian_sync_rs::sync::{sync_once_dry_run, sync_once_write};
use codex_obsidian_sync_rs::writer::validate_vault_root;
use time::OffsetDateTime;
use toml::{Table, Value};

fn main() {
    let cli = Cli::parse();
    if let Err(error) = run(cli) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

fn run(cli: Cli) -> Result<(), SyncError> {
    match cli.command {
        Command::Setup(args) => {
            let config_data = load_toml_config(cli.config.as_deref())?;
            let saved_path = save_configuration(cli.config.as_deref(), &config_data, &args)?;
            println!("Saved config: {}", saved_path.display());
            Ok(())
        }
        Command::Start => {
            let config_data = load_toml_config(cli.config.as_deref())?;
            let saved_path = save_configuration(
                cli.config.as_deref(),
                &config_data,
                &SetupArgs {
                    vault: None,
                    cooldown: None,
                },
            )?;
            let saved_config = load_toml_config(Some(&saved_path))?;
            let paths = resolve_service_paths(Some(&saved_path), &saved_config)?;
            let launchd_status = query_launchd_status(Some(&paths.launchd_plist_path));
            if launchd_status.loaded {
                bootout_launch_agent()?;
                bootstrap_launch_agent(&paths.launchd_plist_path)?;
                println!("LaunchAgent reloaded.");
            } else {
                bootstrap_launch_agent(&paths.launchd_plist_path)?;
                println!("LaunchAgent started.");
            }
            Ok(())
        }
        Command::Stop => {
            let config_data = load_toml_config(cli.config.as_deref())?;
            let paths = resolve_service_paths(cli.config.as_deref(), &config_data)?;
            let launchd_status = query_launchd_status(Some(&paths.launchd_plist_path));
            if launchd_status.loaded {
                bootout_launch_agent()?;
                println!("LaunchAgent stopped.");
            } else {
                println!("LaunchAgent already stopped.");
            }
            Ok(())
        }
        Command::InspectRollout(args) => {
            let rollout_path = expand_user_path(&args.rollout_path)?;
            let session_index = expand_user_path(&args.session_index)?;
            let content =
                serde_json::to_string_pretty(&inspect_rollout(&rollout_path, &session_index)?)
                    .map_err(|_| SyncError::Render)?;
            println!("{content}");
            Ok(())
        }
        Command::InspectRecent(args) => {
            let codex_home = expand_user_path(&args.codex_home)?;
            let content = serde_json::to_string_pretty(&inspect_recent(
                &codex_home,
                args.limit,
                args.include_subagents,
            )?)
            .map_err(|_| SyncError::Render)?;
            println!("{content}");
            Ok(())
        }
        Command::Status(args) => {
            let config_data = load_toml_config(cli.config.as_deref())?;
            let paths = resolve_service_paths(cli.config.as_deref(), &config_data)?;
            let service_state = load_service_state(&paths.service_state_file);
            let snapshot = build_status_snapshot(
                &config_data,
                &paths,
                query_launchd_loaded(),
                &service_state,
                OffsetDateTime::now_utc(),
            );
            if args.as_json {
                let content =
                    serde_json::to_string_pretty(&snapshot).map_err(|_| SyncError::Render)?;
                println!("{content}");
            } else {
                println!("{}", render_status_snapshot(&snapshot));
            }
            Ok(())
        }
        Command::SyncOnce(args) => {
            let config_data = load_toml_config(cli.config.as_deref())?;
            let config = resolve_sync_config(&config_data, &SyncConfigOverrides::from(&args))?;
            let summary = if args.write {
                if args.dry_run_output.is_some() {
                    return Err(SyncError::Config);
                }
                sync_once_write(&config)?
            } else {
                sync_once_dry_run(&config, args.dry_run_output.as_deref())?
            };
            let content = serde_json::to_string_pretty(&summary).map_err(|_| SyncError::Render)?;
            println!("{content}");
            Ok(())
        }
        Command::ServiceRun => {
            run_service(cli.config.as_deref())?;
            Ok(())
        }
    }
}

fn save_configuration(
    config_path: Option<&Path>,
    config_data: &TomlConfig,
    args: &SetupArgs,
) -> Result<PathBuf, SyncError> {
    let mut updated = config_data.raw().clone();
    let vault = resolve_setup_vault(&updated, args.vault.as_deref())?;
    let interval_seconds = resolve_setup_interval(&updated, args.cooldown.as_deref())?;
    updated.insert(
        "vault".to_owned(),
        Value::String(vault.to_string_lossy().into_owned()),
    );
    updated.insert(
        "interval_seconds".to_owned(),
        Value::Integer(interval_seconds as i64),
    );

    let saved_path = save_toml_config(config_path, &updated)?;
    let saved_config = load_toml_config(Some(&saved_path))?;
    let paths = resolve_service_paths(Some(&saved_path), &saved_config)?;
    ensure_launch_agent_dirs(&paths)?;
    let binary_path = current_binary_path()?;
    let plist_content =
        render_launch_agent_plist(&saved_path, &paths, interval_seconds, &binary_path)?;
    write_launch_agent_plist(&paths.launchd_plist_path, &plist_content)?;
    Ok(saved_path)
}

fn resolve_setup_vault(table: &Table, vault_arg: Option<&Path>) -> Result<PathBuf, SyncError> {
    let vault = if let Some(vault_arg) = vault_arg {
        expand_user_path(vault_arg)?
    } else {
        let Some(value) = table.get("vault").and_then(Value::as_str) else {
            return Err(SyncError::Config);
        };
        expand_user_path(Path::new(value))?
    };
    validate_vault_root(&vault).map_err(|_| SyncError::Config)
}

fn resolve_setup_interval(table: &Table, cooldown_arg: Option<&str>) -> Result<u64, SyncError> {
    if let Some(cooldown_arg) = cooldown_arg {
        return parse_interval(cooldown_arg);
    }
    Ok(table
        .get("interval_seconds")
        .and_then(Value::as_integer)
        .and_then(|value| u64::try_from(value).ok())
        .unwrap_or(10)
        .max(1))
}

fn current_binary_path() -> Result<PathBuf, SyncError> {
    std::env::current_exe()
        .map_err(|_| SyncError::Config)?
        .canonicalize()
        .map_err(|_| SyncError::Config)
}
