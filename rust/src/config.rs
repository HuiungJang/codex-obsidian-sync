use std::fs;
use std::path::{Path, PathBuf};

use toml::{Table, Value};
#[cfg(unix)]
use uzers::os::unix::UserExt;

use crate::cli::SyncOnceArgs;
use crate::error::SyncError;

const DEFAULT_STATE_DIRNAME: &str = "obsidian-sync";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SyncConfig {
    pub codex_home: PathBuf,
    pub vault: PathBuf,
    pub state_file: PathBuf,
    pub lock_file: PathBuf,
    pub include_subagents: bool,
    pub interval_seconds: u64,
    pub recent_days: u32,
    pub candidate_file_limit: u32,
    pub candidate_bytes_limit: u64,
    pub log_level: String,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct TomlConfig {
    raw: Table,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SyncConfigOverrides {
    pub vault: Option<PathBuf>,
    pub codex_home: Option<PathBuf>,
    pub state_file: Option<PathBuf>,
    pub lock_file: Option<PathBuf>,
    pub include_subagents: Option<bool>,
    pub interval_seconds: Option<u64>,
    pub recent_days: Option<u32>,
    pub candidate_file_limit: Option<u32>,
    pub candidate_bytes_limit: Option<u64>,
    pub log_level: Option<String>,
}

impl TomlConfig {
    pub fn empty() -> Self {
        Self::default()
    }

    pub fn raw(&self) -> &Table {
        &self.raw
    }
}

impl From<&SyncOnceArgs> for SyncConfigOverrides {
    fn from(args: &SyncOnceArgs) -> Self {
        Self {
            vault: args.vault.clone(),
            codex_home: args.codex_home.clone(),
            state_file: args.state_file.clone(),
            lock_file: args.lock_file.clone(),
            include_subagents: args.include_subagents,
            interval_seconds: None,
            recent_days: args.recent_days,
            candidate_file_limit: args.candidate_file_limit,
            candidate_bytes_limit: args.candidate_bytes_limit,
            log_level: args.log_level.clone(),
        }
    }
}

pub fn default_codex_home() -> Result<PathBuf, SyncError> {
    Ok(home_dir()?.join(".codex"))
}

pub fn default_config_path() -> Result<PathBuf, SyncError> {
    Ok(default_codex_home()?
        .join(DEFAULT_STATE_DIRNAME)
        .join("config.toml"))
}

pub fn load_toml_config(path: Option<&Path>) -> Result<TomlConfig, SyncError> {
    let config_path = match path {
        Some(path) => expand_user_path(path)?,
        None => default_config_path()?,
    };
    if !config_path.exists() {
        return Ok(TomlConfig::empty());
    }

    let content = fs::read_to_string(config_path).map_err(|_| SyncError::Config)?;
    let raw = content.parse::<Table>().map_err(|_| SyncError::Config)?;
    Ok(TomlConfig { raw })
}

pub fn resolve_sync_config(
    config: &TomlConfig,
    overrides: &SyncConfigOverrides,
) -> Result<SyncConfig, SyncError> {
    let service_codex_home = resolve_path(
        None,
        string_value(config.raw(), "codex_home"),
        Some(default_codex_home()?),
        "codex_home",
    )?;
    let state_dir = service_codex_home.join(DEFAULT_STATE_DIRNAME);
    let codex_home = resolve_path(
        overrides.codex_home.as_deref(),
        string_value(config.raw(), "codex_home"),
        Some(service_codex_home),
        "codex_home",
    )?;
    let state_file = resolve_path(
        overrides.state_file.as_deref(),
        string_value(config.raw(), "state_file"),
        Some(state_dir.join("sync-state.json")),
        "state_file",
    )?;
    let lock_file = resolve_path(
        overrides.lock_file.as_deref(),
        string_value(config.raw(), "lock_file"),
        Some(state_file.with_extension("lock")),
        "lock_file",
    )?;
    let vault = resolve_path(
        overrides.vault.as_deref(),
        string_value(config.raw(), "vault"),
        None,
        "vault",
    )?;

    Ok(SyncConfig {
        codex_home,
        vault,
        state_file,
        lock_file,
        include_subagents: overrides
            .include_subagents
            .or_else(|| bool_value(config.raw(), "include_subagents"))
            .unwrap_or(false),
        interval_seconds: clamp_min_u64(
            overrides
                .interval_seconds
                .or_else(|| u64_value(config.raw(), "interval_seconds"))
                .unwrap_or(10),
        ),
        recent_days: clamp_min_u32(
            overrides
                .recent_days
                .or_else(|| u32_value(config.raw(), "recent_days"))
                .unwrap_or(30),
        ),
        candidate_file_limit: clamp_min_u32(
            overrides
                .candidate_file_limit
                .or_else(|| u32_value(config.raw(), "candidate_file_limit"))
                .unwrap_or(100),
        ),
        candidate_bytes_limit: overrides
            .candidate_bytes_limit
            .or_else(|| u64_value(config.raw(), "candidate_bytes_limit"))
            .unwrap_or(500 * 1024 * 1024),
        log_level: string_override(
            overrides.log_level.as_deref(),
            string_value(config.raw(), "log_level"),
            "INFO",
        )
        .to_ascii_uppercase(),
    })
}

fn resolve_path(
    override_value: Option<&Path>,
    config_value: Option<&str>,
    default: Option<PathBuf>,
    _field_name: &str,
) -> Result<PathBuf, SyncError> {
    let path = if let Some(path) = override_value {
        expand_user_path(path)?
    } else if let Some(value) = config_value {
        expand_user_path(Path::new(value))?
    } else if let Some(default) = default {
        default
    } else {
        return Err(SyncError::Config);
    };

    if !path.is_absolute() {
        return Err(SyncError::Config);
    }
    Ok(path)
}

fn expand_user_path(path: &Path) -> Result<PathBuf, SyncError> {
    let text = path.to_string_lossy();
    if text == "~" {
        return home_dir();
    }
    if let Some(rest) = text.strip_prefix("~/") {
        return Ok(home_dir()?.join(rest));
    }
    if let Some(rest) = text.strip_prefix('~') {
        if let Some((username, suffix)) = rest.split_once('/') {
            if !username.is_empty() {
                return Ok(home_dir_for_user(username)?.join(suffix));
            }
        } else if !rest.is_empty() {
            return home_dir_for_user(rest);
        }
    }
    Ok(path.to_path_buf())
}

fn home_dir() -> Result<PathBuf, SyncError> {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
        .ok_or(SyncError::Config)
}

#[cfg(unix)]
fn home_dir_for_user(username: &str) -> Result<PathBuf, SyncError> {
    uzers::get_user_by_name(username)
        .map(|user| user.home_dir().to_path_buf())
        .filter(|path| path.is_absolute())
        .ok_or(SyncError::Config)
}

#[cfg(not(unix))]
fn home_dir_for_user(_username: &str) -> Result<PathBuf, SyncError> {
    Err(SyncError::Config)
}

fn string_value<'a>(table: &'a Table, key: &str) -> Option<&'a str> {
    table
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn bool_value(table: &Table, key: &str) -> Option<bool> {
    table.get(key).and_then(Value::as_bool)
}

fn u32_value(table: &Table, key: &str) -> Option<u32> {
    table
        .get(key)
        .and_then(Value::as_integer)
        .and_then(|value| u32::try_from(value).ok())
}

fn u64_value(table: &Table, key: &str) -> Option<u64> {
    table
        .get(key)
        .and_then(Value::as_integer)
        .and_then(|value| u64::try_from(value).ok())
}

fn string_override<'a>(
    override_value: Option<&'a str>,
    config_value: Option<&'a str>,
    default: &'a str,
) -> &'a str {
    override_value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .or(config_value)
        .unwrap_or(default)
}

fn clamp_min_u32(value: u32) -> u32 {
    value.max(1)
}

fn clamp_min_u64(value: u64) -> u64 {
    value.max(1)
}
