use codex_obsidian_sync_rs::cli::{Cli, Command};
use codex_obsidian_sync_rs::config::{
    SyncConfigOverrides, expand_user_path, load_toml_config, resolve_service_paths,
    resolve_sync_config,
};
use codex_obsidian_sync_rs::error::SyncError;
use codex_obsidian_sync_rs::inspect::{inspect_recent, inspect_rollout};
use codex_obsidian_sync_rs::service_state::load_service_state;
use codex_obsidian_sync_rs::status_snapshot::{
    build_status_snapshot, query_launchd_loaded, render_status_snapshot,
};
use codex_obsidian_sync_rs::sync::{sync_once_dry_run, sync_once_write};
use time::OffsetDateTime;

fn main() {
    let cli = Cli::parse();
    if let Err(error) = run(cli) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

fn run(cli: Cli) -> Result<(), SyncError> {
    match cli.command {
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
    }
}
