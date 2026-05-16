use std::fs;
use std::path::Path;

use serde_json::{Map, Value, json};

pub fn default_service_state() -> Map<String, Value> {
    let mut state = Map::new();
    state.insert("schema_version".to_owned(), json!(1));
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
