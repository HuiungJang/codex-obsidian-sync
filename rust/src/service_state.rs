use std::fs;
use std::path::Path;

use serde_json::{Map, Value, json};
use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;

use crate::error::SyncError;
use crate::lock::ProcessLock;
use crate::writer::write_state_file;

const SERVICE_STATE_SCHEMA_VERSION: i64 = 1;

pub fn default_service_state() -> Map<String, Value> {
    let mut state = Map::new();
    state.insert(
        "schema_version".to_owned(),
        json!(SERVICE_STATE_SCHEMA_VERSION),
    );
    state.insert("pending".to_owned(), json!(false));
    state.insert("last_trigger_at".to_owned(), Value::Null);
    state.insert("next_eligible_at".to_owned(), Value::Null);
    state.insert("last_run_started_at".to_owned(), Value::Null);
    state.insert("last_run_finished_at".to_owned(), Value::Null);
    state.insert("last_success_at".to_owned(), Value::Null);
    state.insert("last_error_type".to_owned(), Value::Null);
    state.insert("last_error_summary".to_owned(), Value::Null);
    state.insert("last_summary".to_owned(), json!({}));
    state.insert("updated_at".to_owned(), Value::Null);
    state
}

pub fn load_service_state(path: &Path) -> Map<String, Value> {
    let Ok(content) = fs::read_to_string(path) else {
        return default_service_state();
    };
    let Ok(Value::Object(data)) = serde_json::from_str::<Value>(&content) else {
        return default_service_state();
    };
    let mut state = default_service_state();
    for (key, value) in data {
        if state.contains_key(&key) {
            state.insert(key, value);
        }
    }
    state
}

pub fn save_service_state(path: &Path, state: &Map<String, Value>) -> Result<(), SyncError> {
    let content = serde_json::to_string_pretty(&Value::Object(state.clone()))
        .map_err(|_| SyncError::Render)?;
    write_state_file(path, &format!("{content}\n"))
}

pub fn mutate_service_state<T>(
    state_path: &Path,
    lock_path: &Path,
    mutator: impl FnOnce(&mut Map<String, Value>) -> T,
) -> Result<T, SyncError> {
    let _lock = ProcessLock::acquire(lock_path)?;
    let mut state = load_service_state(state_path);
    let result = mutator(&mut state);
    state.insert(
        "schema_version".to_owned(),
        json!(SERVICE_STATE_SCHEMA_VERSION),
    );
    state.insert("updated_at".to_owned(), json!(utc_now_iso()));
    save_service_state(state_path, &state)?;
    Ok(result)
}

pub fn utc_now_iso() -> String {
    let formatted = OffsetDateTime::now_utc()
        .format(&Rfc3339)
        .unwrap_or_else(|_| OffsetDateTime::now_utc().to_string());
    formatted
        .strip_suffix('Z')
        .map(|prefix| format!("{prefix}+00:00"))
        .unwrap_or(formatted)
}
