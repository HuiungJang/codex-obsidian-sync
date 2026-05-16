use std::ffi::OsString;
use std::path::PathBuf;

use clap::parser::ValueSource;
use clap::{Args, CommandFactory, FromArgMatches, Parser, Subcommand};

use crate::BINARY_NAME;

#[derive(Debug)]
pub struct Cli {
    pub config: Option<PathBuf>,
    pub command: Command,
}

#[derive(Debug)]
pub enum Command {
    SyncOnce(SyncOnceArgs),
    InspectRollout(InspectRolloutArgs),
    InspectRecent(InspectRecentArgs),
    Status(StatusArgs),
}

#[derive(Debug)]
pub struct SyncOnceArgs {
    pub vault: Option<PathBuf>,
    pub codex_home: Option<PathBuf>,
    pub state_file: Option<PathBuf>,
    pub lock_file: Option<PathBuf>,
    pub dry_run_output: Option<PathBuf>,
    pub include_subagents: Option<bool>,
    pub log_level: Option<String>,
    pub recent_days: Option<u32>,
    pub candidate_file_limit: Option<u32>,
    pub candidate_bytes_limit: Option<u64>,
}

#[derive(Debug)]
pub struct InspectRolloutArgs {
    pub rollout_path: PathBuf,
    pub session_index: PathBuf,
}

#[derive(Debug)]
pub struct InspectRecentArgs {
    pub codex_home: PathBuf,
    pub limit: usize,
    pub include_subagents: bool,
}

#[derive(Debug)]
pub struct StatusArgs {
    pub as_json: bool,
}

impl Cli {
    pub fn parse() -> Self {
        Self::try_parse_from(std::env::args_os()).unwrap_or_else(|error| error.exit())
    }

    pub fn try_parse_from<I, T>(args: I) -> Result<Self, clap::Error>
    where
        I: IntoIterator<Item = T>,
        T: Into<OsString> + Clone,
    {
        let matches = RawCli::command().try_get_matches_from(args)?;
        let raw = RawCli::from_arg_matches(&matches)?;
        let command = match raw.command {
            RawCommand::InspectRollout(raw_args) => Command::InspectRollout(InspectRolloutArgs {
                rollout_path: raw_args.rollout_path,
                session_index: raw_args.session_index,
            }),
            RawCommand::InspectRecent(raw_args) => Command::InspectRecent(InspectRecentArgs {
                codex_home: raw_args.codex_home,
                limit: raw_args.limit as usize,
                include_subagents: raw_args.include_subagents,
            }),
            RawCommand::Status(raw_args) => Command::Status(StatusArgs {
                as_json: raw_args.as_json,
            }),
            RawCommand::SyncOnce(raw_args) => {
                let include_subagents = matches
                    .subcommand()
                    .and_then(|(_, subcommand)| subcommand.value_source("include_subagents"))
                    .filter(|source| *source == ValueSource::CommandLine)
                    .map(|_| true);
                Command::SyncOnce(SyncOnceArgs {
                    vault: raw_args.vault,
                    codex_home: raw_args.codex_home,
                    state_file: raw_args.state_file,
                    lock_file: raw_args.lock_file,
                    dry_run_output: raw_args.dry_run_output,
                    include_subagents,
                    log_level: raw_args.log_level,
                    recent_days: raw_args.recent_days,
                    candidate_file_limit: raw_args.candidate_file_limit,
                    candidate_bytes_limit: raw_args.candidate_bytes_limit,
                })
            }
        };

        Ok(Self {
            config: raw.config,
            command,
        })
    }
}

#[derive(Debug, Parser)]
#[command(name = BINARY_NAME)]
#[command(version)]
#[command(about = "Sync local Codex conversations into an Obsidian vault")]
#[command(disable_help_subcommand = true)]
struct RawCli {
    #[arg(long, value_name = "CONFIG", help = "Path to config.toml")]
    config: Option<PathBuf>,

    #[command(subcommand)]
    command: RawCommand,
}

#[derive(Debug, Subcommand)]
enum RawCommand {
    #[command(name = "inspect-rollout")]
    #[command(about = "Inspect one rollout file as redacted JSON")]
    InspectRollout(RawInspectRolloutArgs),

    #[command(name = "inspect-recent")]
    #[command(about = "Inspect recent rollout files as redacted JSON")]
    InspectRecent(RawInspectRecentArgs),

    #[command(name = "status")]
    #[command(about = "Show read-only service status")]
    Status(RawStatusArgs),

    #[command(name = "sync-once")]
    #[command(about = "Run one dry-run sync pass")]
    SyncOnce(RawSyncOnceArgs),
}

#[derive(Debug, Args)]
struct RawInspectRolloutArgs {
    #[arg(value_name = "ROLLOUT_PATH")]
    rollout_path: PathBuf,

    #[arg(
        long,
        value_name = "SESSION_INDEX",
        default_value = "~/.codex/session_index.jsonl",
        help = "Path to session_index.jsonl"
    )]
    session_index: PathBuf,
}

#[derive(Debug, Args)]
struct RawInspectRecentArgs {
    #[arg(
        long,
        value_name = "CODEX_HOME",
        default_value = "~/.codex",
        help = "Codex home directory"
    )]
    codex_home: PathBuf,

    #[arg(long, value_name = "LIMIT", default_value_t = 5, value_parser = clap::value_parser!(u32).range(1..), help = "Maximum rollout summaries")]
    limit: u32,

    #[arg(
        long,
        action = clap::ArgAction::SetTrue,
        help = "Include subagent conversations"
    )]
    include_subagents: bool,
}

#[derive(Debug, Args)]
struct RawStatusArgs {
    #[arg(long = "json", action = clap::ArgAction::SetTrue, help = "Render status as JSON")]
    as_json: bool,
}

#[derive(Debug, Args)]
struct RawSyncOnceArgs {
    #[arg(long, value_name = "VAULT", help = "Obsidian vault root")]
    vault: Option<PathBuf>,

    #[arg(long, value_name = "CODEX_HOME", help = "Codex home directory")]
    codex_home: Option<PathBuf>,

    #[arg(long, value_name = "STATE_FILE", help = "Sync state file")]
    state_file: Option<PathBuf>,

    #[arg(long, value_name = "LOCK_FILE", help = "Sync lock file")]
    lock_file: Option<PathBuf>,

    #[arg(long, value_name = "DIR", help = "Directory for dry-run output")]
    dry_run_output: Option<PathBuf>,

    #[arg(
        long,
        action = clap::ArgAction::SetTrue,
        help = "Include subagent conversations"
    )]
    include_subagents: bool,

    #[arg(long, value_name = "LOG_LEVEL", help = "Log level")]
    log_level: Option<String>,

    #[arg(long, value_name = "RECENT_DAYS", value_parser = clap::value_parser!(u32).range(1..), help = "Recent index window in days")]
    recent_days: Option<u32>,

    #[arg(long, value_name = "CANDIDATE_FILE_LIMIT", value_parser = clap::value_parser!(u32).range(1..), help = "Maximum candidate rollout files")]
    candidate_file_limit: Option<u32>,

    #[arg(long, value_name = "CANDIDATE_BYTES_LIMIT", value_parser = clap::value_parser!(u64).range(1..), help = "Maximum candidate bytes")]
    candidate_bytes_limit: Option<u64>,
}
