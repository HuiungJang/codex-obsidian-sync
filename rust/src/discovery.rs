use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use indexmap::IndexMap;
use regex::Regex;
use serde_json::Value;
use unicode_normalization::UnicodeNormalization;

use crate::error::SyncError;
use crate::models::{SessionEnvelope, SessionIndexEntry, SessionMeta, TranscriptMessage};
use crate::parser::{
    RolloutRecord, collect_session_metas, extract_candidate_messages, is_control_message,
    load_rollout_records,
};

pub type SessionIndex = IndexMap<String, SessionIndexEntry>;

pub fn load_session_index(path: &Path) -> Result<SessionIndex, SyncError> {
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(IndexMap::new());
        }
        Err(_) => return Err(SyncError::Discovery),
    };
    let reader = BufReader::new(file);
    let mut entries = IndexMap::new();

    for line in reader.lines() {
        let line = line.map_err(|_| SyncError::Discovery)?;
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let Ok(Value::Object(payload)) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        let Some(session_id) = payload.get("id").and_then(string_or_none) else {
            continue;
        };

        entries.insert(
            session_id.clone(),
            SessionIndexEntry {
                session_id,
                thread_name: payload.get("thread_name").and_then(string_or_none),
                updated_at: payload.get("updated_at").and_then(string_or_none),
            },
        );
    }

    Ok(entries)
}

pub fn parse_rollout_session_id(path: &Path) -> Result<String, SyncError> {
    let stem = path
        .file_stem()
        .and_then(|stem| stem.to_str())
        .ok_or(SyncError::Discovery)?;
    uuid_regex()
        .find_iter(stem)
        .last()
        .map(|matched| matched.as_str().to_owned())
        .ok_or(SyncError::Discovery)
}

pub fn build_session_envelope(
    rollout_path: &Path,
    session_index: &SessionIndex,
) -> Result<SessionEnvelope, SyncError> {
    let records = load_rollout_records(rollout_path).map_err(|_| SyncError::Parse)?;
    build_session_envelope_from_records(rollout_path, session_index, &records)
}

pub fn build_session_envelope_from_records(
    rollout_path: &Path,
    session_index: &SessionIndex,
    records: &[RolloutRecord],
) -> Result<SessionEnvelope, SyncError> {
    let metas = collect_session_metas(records);
    let messages = extract_candidate_messages(records);
    let canonical_meta = select_canonical_session(rollout_path, &metas)?;
    let index_entry = session_index.get(&canonical_meta.session_id);

    let title_source = first_title_candidate_text(&messages)
        .or_else(|| index_entry.and_then(|entry| entry.thread_name.clone()));
    let title_seed = slugify(
        title_source
            .as_deref()
            .unwrap_or(&canonical_meta.session_id),
        80,
    );
    let project_slug = slugify(&project_name_from_cwd(canonical_meta.cwd.as_deref()), 80);
    let updated_at = index_entry
        .and_then(|entry| entry.updated_at.clone())
        .or_else(|| messages.last().map(|message| message.timestamp.clone()))
        .or_else(|| canonical_meta.timestamp.clone());

    Ok(SessionEnvelope {
        canonical_session_id: canonical_meta.session_id.clone(),
        rollout_path: rollout_path.to_path_buf(),
        originator: canonical_meta.originator.clone(),
        source_kind: canonical_meta.source_classification.source_kind.clone(),
        is_subagent: canonical_meta.source_classification.is_subagent,
        parent_session_id: canonical_meta
            .source_classification
            .parent_session_id
            .clone(),
        cwd: canonical_meta.cwd.clone(),
        project_slug,
        thread_name: index_entry.and_then(|entry| entry.thread_name.clone()),
        title_seed,
        started_at: canonical_meta.timestamp.clone(),
        updated_at,
        messages,
    })
}

pub fn select_canonical_session<'a>(
    rollout_path: &Path,
    metas: &'a [SessionMeta],
) -> Result<&'a SessionMeta, SyncError> {
    if metas.is_empty() {
        return Err(SyncError::Discovery);
    }

    let rollout_session_id = parse_rollout_session_id(rollout_path)?;
    if let Some(meta) = metas
        .iter()
        .find(|meta| meta.session_id == rollout_session_id)
    {
        return Ok(meta);
    }

    let mut non_subagents = metas
        .iter()
        .filter(|meta| !meta.source_classification.is_subagent);
    if let Some(meta) = non_subagents.next() {
        if non_subagents.next().is_none() {
            return Ok(meta);
        }
    }

    if metas.len() == 1 {
        return Ok(&metas[0]);
    }

    Err(SyncError::Discovery)
}

pub fn first_user_text(messages: &[TranscriptMessage]) -> Option<String> {
    messages
        .iter()
        .find(|message| message.role == "user" && !message.text.trim().is_empty())
        .map(|message| message.text.clone())
}

pub fn first_title_candidate_text(messages: &[TranscriptMessage]) -> Option<String> {
    messages
        .iter()
        .filter(|message| message.role == "user")
        .find_map(|message| {
            let text = message.text.trim();
            if text.is_empty() || is_control_message(text) {
                None
            } else {
                Some(message.text.clone())
            }
        })
        .or_else(|| first_user_text(messages))
}

pub fn project_name_from_cwd(cwd: Option<&str>) -> String {
    let Some(cwd) = cwd else {
        return "unknown-project".to_owned();
    };
    let cwd = PathBuf::from(cwd);
    let name = cwd
        .file_name()
        .and_then(|name| name.to_str())
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .unwrap_or("unknown-project");
    name.to_owned()
}

pub fn slugify(value: &str, max_length: usize) -> String {
    let normalized = value.nfkc().collect::<String>();
    let normalized = normalized.replace(['/', '\\'], " ");
    let normalized = backticks_regex().replace_all(&normalized, " ");
    let normalized = disallowed_slug_regex().replace_all(&normalized, " ");
    let normalized = slug_separator_regex()
        .replace_all(normalized.trim().to_lowercase().as_str(), "-")
        .trim_matches(['-', '_'])
        .to_owned();

    if normalized.is_empty() {
        return "conversation".to_owned();
    }

    let truncated = normalized.chars().take(max_length).collect::<String>();
    truncated.trim_end_matches('-').to_owned()
}

fn string_or_none(value: &Value) -> Option<String> {
    value.as_str().and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_owned())
        }
    })
}

fn uuid_regex() -> &'static Regex {
    static UUID_REGEX: OnceLock<Regex> = OnceLock::new();
    UUID_REGEX.get_or_init(|| {
        Regex::new(r"(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
            .expect("valid rollout session id regex")
    })
}

fn backticks_regex() -> &'static Regex {
    static BACKTICKS_REGEX: OnceLock<Regex> = OnceLock::new();
    BACKTICKS_REGEX.get_or_init(|| Regex::new(r"`+").expect("valid backticks regex"))
}

fn disallowed_slug_regex() -> &'static Regex {
    static DISALLOWED_SLUG_REGEX: OnceLock<Regex> = OnceLock::new();
    DISALLOWED_SLUG_REGEX
        .get_or_init(|| Regex::new(r"[^\p{Letter}\p{Number}_\s-]").expect("valid slug regex"))
}

fn slug_separator_regex() -> &'static Regex {
    static SLUG_SEPARATOR_REGEX: OnceLock<Regex> = OnceLock::new();
    SLUG_SEPARATOR_REGEX.get_or_init(|| Regex::new(r"[-\s]+").expect("valid separator regex"))
}
