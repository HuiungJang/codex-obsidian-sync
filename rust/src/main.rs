use codex_obsidian_sync_rs::cli::{Cli, Command};

fn main() {
    let cli = Cli::parse();
    match cli.command {
        Command::SyncOnce(_) => {
            eprintln!("sync-once is not implemented yet");
            std::process::exit(1);
        }
    }
}
