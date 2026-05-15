use codex_obsidian_sync_rs::cli::{Cli, Command};
use codex_obsidian_sync_rs::error::SyncError;

fn main() {
    let cli = Cli::parse();
    match cli.command {
        Command::SyncOnce(_) => {
            eprintln!("{}", SyncError::UnsupportedCommand);
            std::process::exit(1);
        }
    }
}
