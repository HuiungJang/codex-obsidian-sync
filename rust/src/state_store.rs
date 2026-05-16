use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::Path;

#[cfg(unix)]
use std::os::unix::fs::MetadataExt;

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::SyncError;

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct SyncState {
    #[serde(default)]
    pub files: IndexMap<String, StateEntry>,

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

impl StateEntry {
    pub fn bool_field(&self, key: &str) -> Option<bool> {
        self.extra.get(key).and_then(Value::as_bool)
    }

    pub fn string_field(&self, key: &str) -> Option<&str> {
        self.extra.get(key).and_then(Value::as_str)
    }
}

pub fn file_fingerprint(path: &Path) -> Result<FileFingerprint, SyncError> {
    let metadata = fs::metadata(path).map_err(|_| SyncError::Discovery)?;
    Ok(FileFingerprint {
        size: metadata.len(),
        mtime_ns: metadata_mtime_ns(&metadata),
    })
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

#[cfg(unix)]
fn metadata_mtime_ns(metadata: &fs::Metadata) -> u64 {
    (metadata.mtime() as u64)
        .saturating_mul(1_000_000_000)
        .saturating_add(metadata.mtime_nsec() as u64)
}

#[cfg(not(unix))]
fn metadata_mtime_ns(metadata: &fs::Metadata) -> u64 {
    metadata
        .modified()
        .ok()
        .and_then(|modified| modified.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos().min(u128::from(u64::MAX)) as u64)
        .unwrap_or(0)
}

fn validate_state(state: &SyncState) -> Result<(), SyncError> {
    for path in state.files.keys() {
        if !Path::new(path).is_absolute() {
            return Err(SyncError::Parse);
        }
    }
    Ok(())
}
