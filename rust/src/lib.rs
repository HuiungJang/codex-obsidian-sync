pub mod cli;
pub mod config;
pub mod discovery;
pub mod dry_run_output;
pub mod error;
pub mod models;
pub mod parser;
pub mod redaction;
pub mod render;
pub mod state_store;

pub const BINARY_NAME: &str = "codex-obsidian-sync-rs";
