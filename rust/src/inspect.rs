use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use crate::discovery::{build_session_envelope, load_session_index};
use crate::error::SyncError;
use crate::models::SessionEnvelope;
use crate::redaction::redact_text;

pub fn inspect_rollout(rollout_path: &Path, session_index_path: &Path) -> Result<Value, SyncError> {
    let session_index = load_session_index(session_index_path)?;
    let envelope = build_session_envelope(rollout_path, &session_index)?;
    Ok(envelope_to_value(&envelope))
}

pub fn inspect_recent(
    codex_home: &Path,
    limit: usize,
    include_subagents: bool,
) -> Result<Vec<Value>, SyncError> {
    let session_index = load_session_index(&codex_home.join("session_index.jsonl"))?;
    let mut summaries = Vec::new();
    for rollout_path in recent_rollout_files(codex_home, limit.saturating_mul(5))? {
        let Ok(envelope) = build_session_envelope(&rollout_path, &session_index) else {
            eprintln!("skip {}: discovery error", rollout_path.display());
            continue;
        };
        if envelope.is_subagent && !include_subagents {
            continue;
        }
        summaries.push(envelope_to_value(&envelope));
        if summaries.len() >= limit {
            break;
        }
    }
    Ok(summaries)
}

fn envelope_to_value(envelope: &SessionEnvelope) -> Value {
    json!({
        "canonical_session_id": envelope.canonical_session_id,
        "rollout_path": envelope.rollout_path.to_string_lossy(),
        "originator": envelope.originator,
        "source_kind": envelope.source_kind,
        "is_subagent": envelope.is_subagent,
        "parent_session_id": envelope.parent_session_id,
        "cwd": envelope.cwd,
        "project_slug": envelope.project_slug,
        "thread_name": envelope.thread_name.as_ref().map(|value| redact_text(value)),
        "title_seed": redact_text(&envelope.title_seed),
        "started_at": envelope.started_at,
        "updated_at": envelope.updated_at,
        "message_count": envelope.messages.len(),
        "messages": envelope.messages.iter().map(|message| {
            json!({
                "message_key": message.message_key,
                "role": message.role,
                "phase": message.phase,
                "timestamp": message.timestamp,
                "text_preview": preview(&redact_text(&message.text)),
            })
        }).collect::<Vec<_>>(),
    })
}

fn preview(value: &str) -> String {
    value.chars().take(120).collect()
}

fn recent_rollout_files(codex_home: &Path, limit: usize) -> Result<Vec<PathBuf>, SyncError> {
    let sessions_root = codex_home.join("sessions");
    let mut rollouts = list_rollouts(&sessions_root)?
        .into_iter()
        .map(|path| {
            let mtime = file_mtime_ns(&path).unwrap_or(0);
            (path, mtime)
        })
        .collect::<Vec<_>>();
    rollouts.sort_by(|(left_path, left_mtime), (right_path, right_mtime)| {
        right_mtime
            .cmp(left_mtime)
            .then_with(|| left_path.cmp(right_path))
    });
    Ok(rollouts
        .into_iter()
        .take(limit)
        .map(|(path, _)| path)
        .collect())
}

fn list_rollouts(root: &Path) -> Result<Vec<PathBuf>, SyncError> {
    if !root.exists() {
        return Ok(Vec::new());
    }
    let mut rollouts = Vec::new();
    for year in fs::read_dir(root).map_err(|_| SyncError::Discovery)? {
        let year = year.map_err(|_| SyncError::Discovery)?.path();
        if !year.is_dir() {
            continue;
        }
        for month in fs::read_dir(&year).map_err(|_| SyncError::Discovery)? {
            let month = month.map_err(|_| SyncError::Discovery)?.path();
            if !month.is_dir() {
                continue;
            }
            for day in fs::read_dir(&month).map_err(|_| SyncError::Discovery)? {
                let day = day.map_err(|_| SyncError::Discovery)?.path();
                if !day.is_dir() {
                    continue;
                }
                for file in fs::read_dir(&day).map_err(|_| SyncError::Discovery)? {
                    let path = file.map_err(|_| SyncError::Discovery)?.path();
                    if path.extension().and_then(|extension| extension.to_str()) == Some("jsonl") {
                        rollouts.push(path);
                    }
                }
            }
        }
    }
    Ok(rollouts)
}

fn file_mtime_ns(path: &Path) -> Option<u128> {
    let modified = path.metadata().ok()?.modified().ok()?;
    Some(
        modified
            .duration_since(std::time::UNIX_EPOCH)
            .ok()?
            .as_nanos(),
    )
}
