use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::SyncError;

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct SyncState {
    #[serde(default)]
    pub files: BTreeMap<String, StateEntry>,

    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct StateEntry {
    pub included: Option<bool>,
    pub size: Option<u64>,
    pub mtime_ns: Option<u64>,
    pub offset: Option<u64>,
    pub conversation_note_fingerprint: Option<FileFingerprint>,

    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileFingerprint {
    pub size: u64,
    pub mtime_ns: u64,
}

pub fn load_state_read_only(path: &Path) -> Result<SyncState, SyncError> {
    let content = match fs::read_to_string(path) {
        Ok(content) => content,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(SyncState::default()),
        Err(_) => return Err(SyncError::Parse),
    };
    let state: SyncState = serde_json::from_str(&content).map_err(|_| SyncError::Parse)?;
    validate_state(&state)?;
    Ok(state)
}

pub fn render_temp_state(state: &SyncState) -> Result<String, SyncError> {
    let content = serde_json::to_string_pretty(state).map_err(|_| SyncError::Render)?;
    Ok(format!("{content}\n"))
}

fn validate_state(state: &SyncState) -> Result<(), SyncError> {
    for path in state.files.keys() {
        if !Path::new(path).is_absolute() {
            return Err(SyncError::Parse);
        }
    }
    Ok(())
}
