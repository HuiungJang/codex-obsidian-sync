use std::fs;
use std::path::Path;
use std::thread;
use std::time::{Duration as StdDuration, UNIX_EPOCH};

use serde_json::{Map, Value, json};
use time::format_description::well_known::Rfc3339;
use time::{Duration, OffsetDateTime};

use crate::config::{
    SyncConfig, SyncConfigOverrides, load_toml_config, resolve_service_paths, resolve_sync_config,
};
use crate::error::SyncError;
use crate::lock::ProcessLock;
use crate::service_state::{mutate_service_state, utc_now_iso};
use crate::sync::{SyncSummary, sync_once_write};

const SETTLE_POLL_SECONDS: f64 = 0.25;
const SETTLE_STABLE_POLLS: usize = 2;
const SETTLE_MAX_WAIT_SECONDS: f64 = 2.0;

#[derive(Debug, Clone, Copy)]
pub struct ServiceRunOptions {
    pub settle_poll: StdDuration,
    pub settle_stable_polls: usize,
    pub settle_max_wait: StdDuration,
    pub sleep_on_cooldown: bool,
}

impl ServiceRunOptions {
    pub fn new() -> Self {
        Self {
            settle_poll: StdDuration::from_secs_f64(SETTLE_POLL_SECONDS),
            settle_stable_polls: SETTLE_STABLE_POLLS,
            settle_max_wait: StdDuration::from_secs_f64(SETTLE_MAX_WAIT_SECONDS),
            sleep_on_cooldown: true,
        }
    }

    pub fn fast_for_tests() -> Self {
        Self {
            settle_poll: StdDuration::ZERO,
            settle_stable_polls: 0,
            settle_max_wait: StdDuration::ZERO,
            sleep_on_cooldown: false,
        }
    }
}

impl Default for ServiceRunOptions {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
struct SourceFingerprint {
    latest_path: String,
    latest_size: u64,
    latest_mtime_ns: u128,
}

pub fn run_service(config_path: Option<&Path>) -> Result<i32, SyncError> {
    run_service_with_sync(config_path, ServiceRunOptions::new(), sync_once_write)
}

pub fn run_service_with_sync(
    config_path: Option<&Path>,
    options: ServiceRunOptions,
    mut sync_runner: impl FnMut(&SyncConfig) -> Result<SyncSummary, SyncError>,
) -> Result<i32, SyncError> {
    let config_data = load_toml_config(config_path)?;
    if config_data.raw().is_empty() || !service_write_enabled(config_data.raw()) {
        return Err(SyncError::Config);
    }
    let sync_config = resolve_sync_config(&config_data, &SyncConfigOverrides::default())?;
    let paths = resolve_service_paths(config_path, &config_data)?;

    mutate_service_state(
        &paths.service_state_file,
        &paths.service_state_lock_file,
        |state| {
            mark_triggered(state);
        },
    )?;

    let _runner_lock = match ProcessLock::try_acquire(&paths.service_runner_lock_file) {
        Ok(lock) => lock,
        Err(SyncError::LockContention) => return Ok(0),
        Err(error) => return Err(error),
    };

    drain_pending(
        &sync_config,
        &paths.sessions_path,
        &paths.service_state_file,
        &paths.service_state_lock_file,
        options,
        &mut sync_runner,
    )
}

fn drain_pending(
    sync_config: &SyncConfig,
    sessions_path: &Path,
    service_state_file: &Path,
    service_state_lock_file: &Path,
    options: ServiceRunOptions,
    sync_runner: &mut impl FnMut(&SyncConfig) -> Result<SyncSummary, SyncError>,
) -> Result<i32, SyncError> {
    loop {
        let snapshot =
            mutate_service_state(service_state_file, service_state_lock_file, |state| {
                mark_previous_abnormal_exit(state);
                state.clone()
            })?;

        if !bool_field(snapshot.get("pending")) {
            return Ok(0);
        }

        if let Some(next_eligible_at) =
            string_field(snapshot.get("next_eligible_at")).and_then(parse_iso)
        {
            if let Some(sleep_seconds) =
                cooldown_sleep_seconds(next_eligible_at, OffsetDateTime::now_utc())
                && options.sleep_on_cooldown
            {
                thread::sleep(StdDuration::from_secs(sleep_seconds as u64));
                continue;
            }
        }

        let source_before = wait_for_source_settle(sessions_path, options);
        let started_at = utc_now_iso();
        mutate_service_state(service_state_file, service_state_lock_file, |state| {
            mark_run_started(state, &started_at);
        })?;

        let summary = sync_runner(sync_config);
        let source_after = capture_source_fingerprint(sessions_path);
        let finished_at = utc_now_iso();
        mutate_service_state(service_state_file, service_state_lock_file, |state| {
            mark_run_finished(
                state,
                &finished_at,
                sync_config.interval_seconds,
                summary.as_ref().ok(),
                summary.as_ref().err(),
                source_after != source_before,
            );
        })?;
    }
}

fn mark_triggered(state: &mut Map<String, Value>) {
    state.insert("pending".to_owned(), Value::Bool(true));
    state.insert("last_trigger_at".to_owned(), json!(utc_now_iso()));
}

fn mark_previous_abnormal_exit(state: &mut Map<String, Value>) {
    let started_at = string_field(state.get("last_run_started_at")).and_then(parse_iso);
    let finished_at = string_field(state.get("last_run_finished_at")).and_then(parse_iso);
    if let Some(started_at) = started_at
        && finished_at.is_none_or(|finished_at| finished_at < started_at)
    {
        state.insert("last_error_type".to_owned(), json!("AbnormalExit"));
        state.insert(
            "last_error_summary".to_owned(),
            json!("previous service-run did not finish cleanly"),
        );
    }
}

fn mark_run_started(state: &mut Map<String, Value>, started_at: &str) {
    state.insert("pending".to_owned(), Value::Bool(false));
    state.insert("last_run_started_at".to_owned(), json!(started_at));
}

fn mark_run_finished(
    state: &mut Map<String, Value>,
    finished_at: &str,
    interval_seconds: u64,
    summary: Option<&SyncSummary>,
    error: Option<&SyncError>,
    source_changed_during_run: bool,
) {
    state.insert("last_run_finished_at".to_owned(), json!(finished_at));
    state.insert(
        "next_eligible_at".to_owned(),
        json!(shift_iso(finished_at, interval_seconds)),
    );
    state.insert(
        "pending".to_owned(),
        Value::Bool(bool_field(state.get("pending")) || source_changed_during_run),
    );
    if let Some(error) = error {
        state.insert("last_error_type".to_owned(), json!(error_type(error)));
        state.insert(
            "last_error_summary".to_owned(),
            json!(one_line(&error.to_string())),
        );
        return;
    }

    state.insert(
        "last_summary".to_owned(),
        Value::Object(numeric_summary(summary)),
    );
    state.insert("last_error_type".to_owned(), Value::Null);
    state.insert("last_error_summary".to_owned(), Value::Null);
    if summary.is_none_or(|summary| summary.paused == 0) {
        state.insert("last_success_at".to_owned(), json!(finished_at));
    }
}

fn wait_for_source_settle(sessions_path: &Path, options: ServiceRunOptions) -> SourceFingerprint {
    let mut last = capture_source_fingerprint(sessions_path);
    if options.settle_max_wait.is_zero() {
        return last;
    }
    let mut stable_polls = 0;
    let deadline = std::time::Instant::now() + options.settle_max_wait;
    while std::time::Instant::now() < deadline {
        thread::sleep(options.settle_poll);
        let current = capture_source_fingerprint(sessions_path);
        if current == last {
            stable_polls += 1;
            if stable_polls >= options.settle_stable_polls {
                return current;
            }
        } else {
            last = current;
            stable_polls = 0;
        }
    }
    last
}

fn capture_source_fingerprint(sessions_path: &Path) -> SourceFingerprint {
    let mut latest = SourceFingerprint::default();
    collect_source_fingerprint(sessions_path, &mut latest);
    latest
}

fn collect_source_fingerprint(path: &Path, latest: &mut SourceFingerprint) {
    let Ok(entries) = fs::read_dir(path) else {
        return;
    };
    for entry in entries.filter_map(Result::ok) {
        let path = entry.path();
        let Ok(file_type) = entry.file_type() else {
            continue;
        };
        if file_type.is_dir() {
            collect_source_fingerprint(&path, latest);
            continue;
        }
        if !is_rollout_jsonl(&path) {
            continue;
        }
        let Ok(metadata) = entry.metadata() else {
            continue;
        };
        let mtime_ns = metadata_mtime_ns(&metadata);
        if mtime_ns > latest.latest_mtime_ns {
            latest.latest_path = path.to_string_lossy().into_owned();
            latest.latest_size = metadata.len();
            latest.latest_mtime_ns = mtime_ns;
        }
    }
}

fn is_rollout_jsonl(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.starts_with("rollout-") && name.ends_with(".jsonl"))
}

fn numeric_summary(summary: Option<&SyncSummary>) -> Map<String, Value> {
    let Some(summary) = summary else {
        return Map::new();
    };
    let Ok(Value::Object(data)) = serde_json::to_value(summary) else {
        return Map::new();
    };
    data.into_iter()
        .filter(|(_, value)| value.as_i64().is_some() || value.as_u64().is_some())
        .collect()
}

fn service_write_enabled(table: &toml::Table) -> bool {
    table
        .get("rust_service_write_enabled")
        .and_then(toml::Value::as_bool)
        .unwrap_or(false)
}

fn bool_field(value: Option<&Value>) -> bool {
    value.and_then(Value::as_bool).unwrap_or(false)
}

fn string_field(value: Option<&Value>) -> Option<&str> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn parse_iso(value: &str) -> Option<OffsetDateTime> {
    OffsetDateTime::parse(value, &Rfc3339).ok()
}

fn cooldown_sleep_seconds(next_eligible_at: OffsetDateTime, now: OffsetDateTime) -> Option<i64> {
    let seconds = (next_eligible_at - now).whole_seconds();
    (seconds > 0).then_some(seconds)
}

fn shift_iso(value: &str, seconds: u64) -> String {
    let base = parse_iso(value).unwrap_or_else(OffsetDateTime::now_utc);
    format_iso(base + Duration::seconds(seconds.max(1) as i64))
}

fn format_iso(value: OffsetDateTime) -> String {
    let formatted = value.format(&Rfc3339).unwrap_or_else(|_| value.to_string());
    formatted
        .strip_suffix('Z')
        .map(|prefix| format!("{prefix}+00:00"))
        .unwrap_or(formatted)
}

fn error_type(error: &SyncError) -> &'static str {
    match error {
        SyncError::Config => "ConfigError",
        SyncError::Parse => "ParseError",
        SyncError::Discovery => "DiscoveryError",
        SyncError::Write => "WriteError",
        SyncError::LockContention => "LockContention",
        SyncError::Lock => "LockError",
        SyncError::Launchd => "LaunchdError",
        SyncError::Render => "RenderError",
        SyncError::DryRunOutput => "DryRunOutputError",
        SyncError::UnsupportedCommand => "UnsupportedCommand",
    }
}

fn one_line(value: &str) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(200)
        .collect()
}

fn metadata_mtime_ns(metadata: &fs::Metadata) -> u128 {
    metadata
        .modified()
        .ok()
        .and_then(|modified| modified.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos())
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn previous_abnormal_exit_sets_error_fields() {
        let mut state = Map::new();
        state.insert(
            "last_run_started_at".to_owned(),
            json!("2026-05-16T00:00:01+00:00"),
        );
        state.insert("last_run_finished_at".to_owned(), Value::Null);

        mark_previous_abnormal_exit(&mut state);

        assert_eq!(state["last_error_type"], "AbnormalExit");
        assert_eq!(
            state["last_error_summary"],
            "previous service-run did not finish cleanly"
        );
    }

    #[test]
    fn cooldown_sleep_seconds_handles_clock_jumps() {
        let now = parse_iso("2026-05-16T00:00:00+00:00").unwrap();

        assert_eq!(
            cooldown_sleep_seconds(parse_iso("2026-05-16T00:00:30+00:00").unwrap(), now),
            Some(30)
        );
        assert_eq!(
            cooldown_sleep_seconds(parse_iso("2026-05-15T23:59:30+00:00").unwrap(), now),
            None
        );
        assert_eq!(
            cooldown_sleep_seconds(parse_iso("2026-05-16T00:00:00+00:00").unwrap(), now),
            None
        );
    }
}
