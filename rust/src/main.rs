use codex_obsidian_sync_rs::cli::{Cli, Command};
use codex_obsidian_sync_rs::config::{SyncConfigOverrides, load_toml_config, resolve_sync_config};
use codex_obsidian_sync_rs::error::SyncError;
use codex_obsidian_sync_rs::sync::sync_once_dry_run;

fn main() {
    let cli = Cli::parse();
    if let Err(error) = run(cli) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

fn run(cli: Cli) -> Result<(), SyncError> {
    match cli.command {
        Command::SyncOnce(args) => {
            let config_data = load_toml_config(cli.config.as_deref())?;
            let config = resolve_sync_config(&config_data, &SyncConfigOverrides::from(&args))?;
            let summary = sync_once_dry_run(&config, args.dry_run_output.as_deref())?;
            let content = serde_json::to_string_pretty(&summary).map_err(|_| SyncError::Render)?;
            println!("{content}");
            Ok(())
        }
    }
}
