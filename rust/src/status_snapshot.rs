use std::collections::BTreeMap;
use std::process::Command;

use serde::Serialize;
use serde_json::{Map, Value};
use time::format_description::well_known::Rfc3339;
use time::{Duration, OffsetDateTime};

use crate::config::{ServicePaths, TomlConfig, default_launchd_label};

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct StatusSnapshot {
    pub configured: bool,
    pub config_path: String,
    pub vault: String,
    pub cooldown: String,
    pub launchd_label: String,
    pub launchd_loaded: bool,
    pub plist_path: String,
    pub pending: String,
    pub next_eligible_run: String,
    pub last_run: String,
    pub last_success: String,
    pub last_error: String,
    pub last_summary: BTreeMap<String, i64>,
}

pub fn build_status_snapshot(
    config: &TomlConfig,
    paths: &ServicePaths,
    launchd_loaded: bool,
    service_state: &Map<String, Value>,
    now_utc: OffsetDateTime,
) -> StatusSnapshot {
    if config.raw().is_empty() {
        return StatusSnapshot {
            configured: false,
            config_path: paths.config_path.to_string_lossy().into_owned(),
            vault: "not configured".to_owned(),
            cooldown: "not configured".to_owned(),
            launchd_label: default_launchd_label().to_owned(),
            launchd_loaded,
            plist_path: paths.launchd_plist_path.to_string_lossy().into_owned(),
            pending: "not configured".to_owned(),
            next_eligible_run: "not configured".to_owned(),
            last_run: "not configured".to_owned(),
            last_success: "not configured".to_owned(),
            last_error: "not configured".to_owned(),
            last_summary: BTreeMap::new(),
        };
    }

    let interval_seconds = int_field(config.raw().get("interval_seconds"))
        .unwrap_or(10)
        .max(1);
    let last_error = match (
        string_field(service_state.get("last_error_type")),
        string_field(service_state.get("last_error_summary")),
    ) {
        (Some(error_type), Some(error_summary)) => format!("{error_type}: {error_summary}"),
        _ => "none".to_owned(),
    };

    StatusSnapshot {
        configured: true,
        config_path: paths.config_path.to_string_lossy().into_owned(),
        vault: toml_string_field(config.raw().get("vault"))
            .unwrap_or("not configured")
            .to_owned(),
        cooldown: format_interval(interval_seconds),
        launchd_label: default_launchd_label().to_owned(),
        launchd_loaded,
        plist_path: paths.launchd_plist_path.to_string_lossy().into_owned(),
        pending: if bool_field(service_state.get("pending")).unwrap_or(false) {
            "yes".to_owned()
        } else {
            "no".to_owned()
        },
        next_eligible_run: next_eligible_run(service_state, interval_seconds, now_utc),
        last_run: string_field(service_state.get("last_run_finished_at"))
            .unwrap_or("never run")
            .to_owned(),
        last_success: string_field(service_state.get("last_success_at"))
            .unwrap_or("never run")
            .to_owned(),
        last_error,
        last_summary: normalize_summary(service_state.get("last_summary")),
    }
}

pub fn render_status_snapshot(snapshot: &StatusSnapshot) -> String {
    let launchd_state = if snapshot.launchd_loaded {
        "loaded"
    } else {
        "unloaded"
    };
    let summary_text = if snapshot.last_summary.is_empty() {
        "none".to_owned()
    } else {
        snapshot
            .last_summary
            .iter()
            .map(|(key, value)| format!("{key}={value}"))
            .collect::<Vec<_>>()
            .join(", ")
    };
    [
        format!("Config: {}", snapshot.config_path),
        format!("Vault: {}", snapshot.vault),
        format!("Cooldown: {}", snapshot.cooldown),
        format!("LaunchAgent: {launchd_state}"),
        format!("Pending: {}", snapshot.pending),
        format!("Next eligible run: {}", snapshot.next_eligible_run),
        format!("Last run: {}", snapshot.last_run),
        format!("Last success: {}", snapshot.last_success),
        format!("Last error: {}", snapshot.last_error),
        format!("Last summary: {summary_text}"),
        format!("Plist: {}", snapshot.plist_path),
    ]
    .join("\n")
}

pub fn query_launchd_loaded() -> bool {
    Command::new("launchctl")
        .args(["print", &launchd_target()])
        .output()
        .is_ok_and(|output| output.status.success())
}

fn launchd_target() -> String {
    format!("gui/{}/{}", current_uid(), default_launchd_label())
}

#[cfg(unix)]
fn current_uid() -> u32 {
    uzers::get_current_uid()
}

#[cfg(not(unix))]
fn current_uid() -> u32 {
    0
}

fn next_eligible_run(
    service_state: &Map<String, Value>,
    cooldown_seconds: i64,
    now_utc: OffsetDateTime,
) -> String {
    let pending = bool_field(service_state.get("pending")).unwrap_or(false);
    if pending && string_field(service_state.get("next_eligible_at")).is_none() {
        return "pending".to_owned();
    }

    let stored_next = string_field(service_state.get("next_eligible_at")).and_then(parse_iso);
    if pending && let Some(stored_next) = stored_next {
        return format_iso(stored_next);
    }

    let Some(last_run) =
        string_field(service_state.get("last_run_finished_at")).and_then(parse_iso)
    else {
        return "never run".to_owned();
    };
    let eligible_at = last_run + Duration::seconds(cooldown_seconds.max(1));
    if eligible_at <= now_utc {
        "ready now".to_owned()
    } else {
        format_iso(eligible_at)
    }
}

fn normalize_summary(value: Option<&Value>) -> BTreeMap<String, i64> {
    let mut summary = BTreeMap::new();
    let Some(Value::Object(data)) = value else {
        return summary;
    };
    for (key, value) in data {
        if let Some(item) = value.as_i64() {
            summary.insert(key.clone(), item);
        }
    }
    summary
}

fn format_interval(seconds: i64) -> String {
    let seconds = seconds.max(1);
    if seconds % 3600 == 0 {
        format!("{}h ({seconds}s)", seconds / 3600)
    } else if seconds % 60 == 0 {
        format!("{}m ({seconds}s)", seconds / 60)
    } else {
        format!("{seconds}s")
    }
}

fn parse_iso(value: &str) -> Option<OffsetDateTime> {
    OffsetDateTime::parse(value, &Rfc3339).ok()
}

fn format_iso(value: OffsetDateTime) -> String {
    let formatted = value.format(&Rfc3339).unwrap_or_else(|_| value.to_string());
    formatted
        .strip_suffix('Z')
        .map(|prefix| format!("{prefix}+00:00"))
        .unwrap_or(formatted)
}

fn string_field(value: Option<&Value>) -> Option<&str> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn toml_string_field(value: Option<&toml::Value>) -> Option<&str> {
    value
        .and_then(toml::Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn bool_field(value: Option<&Value>) -> Option<bool> {
    value.and_then(Value::as_bool)
}

fn int_field(value: Option<&toml::Value>) -> Option<i64> {
    value.and_then(toml::Value::as_integer)
}
