use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::Path;

#[cfg(unix)]
use std::os::unix::fs::MetadataExt;

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use time::OffsetDateTime;

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
    #[serde(skip_serializing_if = "Option::is_none")]
    pub included: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mtime_ns: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub offset: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
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
    Ok(file_fingerprint_from_metadata(&metadata))
}

pub fn file_fingerprint_from_metadata(metadata: &fs::Metadata) -> FileFingerprint {
    FileFingerprint {
        size: metadata.len(),
        mtime_ns: metadata_mtime_ns(metadata),
    }
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

pub fn load_state_for_write(path: &Path) -> Result<SyncState, SyncError> {
    match load_state_read_only(path) {
        Ok(state) => Ok(state),
        Err(SyncError::Parse) => {
            quarantine_corrupt_state(path)?;
            Ok(SyncState::default())
        }
        Err(error) => Err(error),
    }
}

pub fn render_temp_state(state: &SyncState) -> Result<String, SyncError> {
    let content = serde_json::to_string_pretty(state).map_err(|_| SyncError::Render)?;
    Ok(format!("{content}\n"))
}

fn quarantine_corrupt_state(path: &Path) -> Result<(), SyncError> {
    if !path.exists() {
        return Ok(());
    }
    let timestamp = compact_utc_timestamp(OffsetDateTime::now_utc());
    quarantine_corrupt_state_at(path, &timestamp)
}

fn quarantine_corrupt_state_at(path: &Path, timestamp: &str) -> Result<(), SyncError> {
    let state_file_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or(SyncError::Parse)?;
    for suffix in 0..1000 {
        let candidate_name = if suffix == 0 {
            format!("{state_file_name}.corrupt-{timestamp}")
        } else {
            format!("{state_file_name}.corrupt-{timestamp}-{suffix}")
        };
        let candidate = path.with_file_name(candidate_name);
        if !candidate.exists() {
            fs::rename(path, candidate).map_err(|_| SyncError::Parse)?;
            return Ok(());
        }
    }
    Err(SyncError::Parse)
}

fn compact_utc_timestamp(value: OffsetDateTime) -> String {
    format!(
        "{:04}{:02}{:02}T{:02}{:02}{:02}Z",
        value.year(),
        u8::from(value.month()),
        value.day(),
        value.hour(),
        value.minute(),
        value.second()
    )
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

#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::*;

    #[test]
    fn corrupt_state_quarantine_uses_collision_suffix() {
        let root = temp_dir("quarantine-suffix");
        let state_path = root.join("sync-state.json");
        let timestamp = "20260516T120000Z";
        let first_candidate = root.join("sync-state.json.corrupt-20260516T120000Z");
        let second_candidate = root.join("sync-state.json.corrupt-20260516T120000Z-1");
        fs::write(&state_path, b"{").unwrap();
        fs::write(&first_candidate, b"existing").unwrap();

        quarantine_corrupt_state_at(&state_path, timestamp).unwrap();

        assert!(!state_path.exists());
        assert_eq!(fs::read(first_candidate).unwrap(), b"existing");
        assert_eq!(fs::read(second_candidate).unwrap(), b"{");
    }

    #[test]
    fn corrupt_state_quarantine_fails_after_all_suffixes_collide() {
        let root = temp_dir("quarantine-collision-failure");
        let state_path = root.join("sync-state.json");
        let timestamp = "20260516T120001Z";
        fs::write(&state_path, b"{").unwrap();
        for suffix in 0..1000 {
            let name = if suffix == 0 {
                format!("sync-state.json.corrupt-{timestamp}")
            } else {
                format!("sync-state.json.corrupt-{timestamp}-{suffix}")
            };
            fs::write(root.join(name), b"existing").unwrap();
        }

        let error = quarantine_corrupt_state_at(&state_path, timestamp).unwrap_err();

        assert_eq!(error.to_string(), "parse error");
        assert_eq!(fs::read(state_path).unwrap(), b"{");
    }

    fn temp_dir(name: &str) -> PathBuf {
        let unique = format!(
            "codex-obsidian-sync-rs-state-store-unit-{name}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let path = std::env::temp_dir().join(unique);
        fs::create_dir_all(&path).unwrap();
        path
    }
}
