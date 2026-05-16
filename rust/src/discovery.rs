use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use indexmap::IndexMap;
use regex::Regex;
use serde_json::Value;
use time::format_description::well_known::Rfc3339;
use time::{Duration, OffsetDateTime, UtcOffset};
use unicode_normalization::UnicodeNormalization;

use crate::error::SyncError;
use crate::models::{SessionEnvelope, SessionIndexEntry, SessionMeta, TranscriptMessage};
use crate::parser::{
    RolloutRecord, collect_session_metas, extract_candidate_messages, is_control_message,
    load_rollout_records,
};
use crate::state_store::{FileFingerprint, StateEntry, SyncState, file_fingerprint};

pub type SessionIndex = IndexMap<String, SessionIndexEntry>;

#[derive(Debug, Clone)]
pub struct DiscoveryOptions<'a> {
    pub vault_root: &'a Path,
    pub include_subagents: bool,
    pub recent_days: i64,
    pub max_files: usize,
    pub max_bytes: u64,
    pub now_utc: OffsetDateTime,
    pub local_offset_override: Option<UtcOffset>,
}

impl<'a> DiscoveryOptions<'a> {
    pub fn new(vault_root: &'a Path) -> Self {
        Self {
            vault_root,
            include_subagents: false,
            recent_days: 30,
            max_files: 100,
            max_bytes: 500 * 1024 * 1024,
            now_utc: OffsetDateTime::now_utc(),
            local_offset_override: None,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DiscoveryResult {
    pub candidates: Vec<PathBuf>,
    pub total_bytes: u64,
    pub hit_file_cap: bool,
    pub hit_byte_cap: bool,
}

#[derive(Debug, Clone, Copy)]
enum CurrentThreadName<'a> {
    Missing,
    Present(Option<&'a str>),
}

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

pub fn discover_rollout_candidates(
    codex_home: &Path,
    session_index: &SessionIndex,
    state: &SyncState,
    options: &DiscoveryOptions<'_>,
) -> Result<DiscoveryResult, SyncError> {
    let sessions_root = codex_home.join("sessions");
    let mut latest_by_session_id = IndexMap::new();
    let mut ordered_index_entries = session_index.values().collect::<Vec<_>>();
    ordered_index_entries.sort_by(|left, right| {
        parse_updated_at(right.updated_at.as_deref())
            .cmp(&parse_updated_at(left.updated_at.as_deref()))
    });

    let recent_cutoff = options.now_utc - Duration::days(options.recent_days);
    let mut recent_entries = Vec::new();
    let mut older_entries = Vec::new();
    for entry in ordered_index_entries {
        match parse_updated_at(entry.updated_at.as_deref()) {
            Some(updated_at) if updated_at >= recent_cutoff => recent_entries.push(entry),
            _ => older_entries.push(entry),
        }
    }

    let mut result = DiscoveryResult::default();
    let mut seen = std::collections::BTreeSet::new();

    for entry in recent_entries {
        let rollout_path = latest_rollout_for_entry(
            &sessions_root,
            entry,
            &mut latest_by_session_id,
            options.local_offset_override,
        )?;
        try_add_candidate(
            rollout_path.as_deref(),
            &mut result,
            &mut seen,
            Some(entry),
            state,
            options,
        )?;
        if discovery_should_stop(&result, options) {
            return Ok(result);
        }
    }

    for (tracked_path, state_entry) in state.files.iter() {
        let rollout_path = PathBuf::from(tracked_path);
        let tracked_index_entry = state_entry
            .string_field("canonical_session_id")
            .and_then(|session_id| session_index.get(session_id));
        try_add_candidate(
            rollout_path.exists().then_some(rollout_path.as_path()),
            &mut result,
            &mut seen,
            tracked_index_entry,
            state,
            options,
        )?;
        if discovery_should_stop(&result, options) {
            return Ok(result);
        }
    }

    for entry in older_entries {
        let rollout_path = latest_rollout_for_entry(
            &sessions_root,
            entry,
            &mut latest_by_session_id,
            options.local_offset_override,
        )?;
        try_add_candidate(
            rollout_path.as_deref(),
            &mut result,
            &mut seen,
            Some(entry),
            state,
            options,
        )?;
        if discovery_should_stop(&result, options) {
            return Ok(result);
        }
    }

    if options.include_subagents {
        let mut all_rollouts = list_all_rollouts(&sessions_root)?;
        all_rollouts.sort_by(|left, right| {
            file_mtime_ns(right)
                .unwrap_or(0)
                .cmp(&file_mtime_ns(left).unwrap_or(0))
        });
        for rollout_path in all_rollouts {
            let subagent_index_entry = parse_rollout_session_id(&rollout_path)
                .ok()
                .and_then(|session_id| session_index.get(&session_id));
            try_add_candidate(
                Some(&rollout_path),
                &mut result,
                &mut seen,
                subagent_index_entry,
                state,
                options,
            )?;
            if discovery_should_stop(&result, options) {
                return Ok(result);
            }
        }
    }

    Ok(result)
}

fn discovery_should_stop(result: &DiscoveryResult, options: &DiscoveryOptions<'_>) -> bool {
    result.hit_file_cap || result.total_bytes >= options.max_bytes
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

pub fn parse_updated_at(value: Option<&str>) -> Option<OffsetDateTime> {
    let value = value?;
    OffsetDateTime::parse(value, &Rfc3339).ok()
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

fn latest_rollout_for_entry(
    sessions_root: &Path,
    entry: &SessionIndexEntry,
    cache: &mut IndexMap<String, PathBuf>,
    local_offset_override: Option<UtcOffset>,
) -> Result<Option<PathBuf>, SyncError> {
    if let Some(cached) = cache.get(&entry.session_id) {
        return Ok(Some(cached.clone()));
    }

    let mut candidates = Vec::new();
    for session_dir in candidate_session_dirs(
        sessions_root,
        entry.updated_at.as_deref(),
        local_offset_override,
    ) {
        candidates.extend(session_dir_files_for_session(
            &session_dir,
            &entry.session_id,
        )?);
    }
    if candidates.is_empty() {
        candidates.extend(
            list_all_rollouts(sessions_root)?
                .into_iter()
                .filter(|path| {
                    path.file_name()
                        .and_then(|name| name.to_str())
                        .is_some_and(|name| name.ends_with(&format!("-{}.jsonl", entry.session_id)))
                }),
        );
    }
    let latest = latest_by_mtime_preserving_first(candidates);
    if let Some(latest) = latest {
        cache.insert(entry.session_id.clone(), latest.clone());
        Ok(Some(latest))
    } else {
        Ok(None)
    }
}

fn latest_by_mtime_preserving_first(paths: Vec<PathBuf>) -> Option<PathBuf> {
    let mut latest = None;
    let mut latest_mtime = 0;
    for path in paths {
        let mtime = file_mtime_ns(&path).unwrap_or(0);
        if latest.is_none() || mtime > latest_mtime {
            latest = Some(path);
            latest_mtime = mtime;
        }
    }
    latest
}

fn candidate_session_dirs(
    sessions_root: &Path,
    updated_at: Option<&str>,
    local_offset_override: Option<UtcOffset>,
) -> Vec<PathBuf> {
    let Some(parsed) = parse_updated_at(updated_at) else {
        return Vec::new();
    };
    let local_offset = local_offset_override
        .or_else(|| UtcOffset::local_offset_at(parsed).ok())
        .unwrap_or(UtcOffset::UTC);
    let mut dates = vec![
        parsed.to_offset(UtcOffset::UTC).date(),
        parsed.to_offset(local_offset).date(),
    ];
    dates.sort_by(|left, right| right.cmp(left));
    dates.dedup();
    dates
        .into_iter()
        .map(|date| {
            sessions_root
                .join(format!("{:04}", date.year()))
                .join(format!("{:02}", u8::from(date.month())))
                .join(format!("{:02}", date.day()))
        })
        .filter(|path| path.is_dir())
        .collect()
}

fn session_dir_files_for_session(
    session_dir: &Path,
    session_id: &str,
) -> Result<Vec<PathBuf>, SyncError> {
    let mut paths = Vec::new();
    for entry in std::fs::read_dir(session_dir).map_err(|_| SyncError::Discovery)? {
        let path = entry.map_err(|_| SyncError::Discovery)?.path();
        if path
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.ends_with(&format!("-{session_id}.jsonl")))
        {
            paths.push(path);
        }
    }
    Ok(paths)
}

fn list_all_rollouts(sessions_root: &Path) -> Result<Vec<PathBuf>, SyncError> {
    let mut rollouts = Vec::new();
    if !sessions_root.exists() {
        return Ok(rollouts);
    }
    for year in std::fs::read_dir(sessions_root).map_err(|_| SyncError::Discovery)? {
        let year = year.map_err(|_| SyncError::Discovery)?.path();
        if !year.is_dir() {
            continue;
        }
        for month in std::fs::read_dir(&year).map_err(|_| SyncError::Discovery)? {
            let month = month.map_err(|_| SyncError::Discovery)?.path();
            if !month.is_dir() {
                continue;
            }
            for day in std::fs::read_dir(&month).map_err(|_| SyncError::Discovery)? {
                let day = day.map_err(|_| SyncError::Discovery)?.path();
                if !day.is_dir() {
                    continue;
                }
                for file in std::fs::read_dir(&day).map_err(|_| SyncError::Discovery)? {
                    let path = file.map_err(|_| SyncError::Discovery)?.path();
                    if path.extension().and_then(|extension| extension.to_str()) == Some("jsonl")
                        && parse_rollout_session_id(&path).is_ok()
                    {
                        rollouts.push(path);
                    }
                }
            }
        }
    }
    Ok(rollouts)
}

fn try_add_candidate(
    rollout_path: Option<&Path>,
    result: &mut DiscoveryResult,
    seen: &mut std::collections::BTreeSet<PathBuf>,
    session_index_entry: Option<&SessionIndexEntry>,
    state: &SyncState,
    options: &DiscoveryOptions<'_>,
) -> Result<(), SyncError> {
    let Some(rollout_path) = rollout_path else {
        return Ok(());
    };
    let rollout_path = rollout_path.to_path_buf();
    if seen.contains(&rollout_path) {
        return Ok(());
    }

    let state_entry = state.files.get(rollout_path.to_string_lossy().as_ref());
    if state_entry.is_some_and(|entry| {
        let current_thread_name = match session_index_entry {
            Some(entry) => CurrentThreadName::Present(entry.thread_name.as_deref()),
            None => CurrentThreadName::Missing,
        };
        !needs_processing(
            entry,
            &rollout_path,
            options.vault_root,
            options.include_subagents,
            current_thread_name,
        )
    }) {
        seen.insert(rollout_path);
        return Ok(());
    }

    if result.candidates.len() >= options.max_files {
        result.hit_file_cap = true;
        return Ok(());
    }

    let file_size = std::fs::metadata(&rollout_path)
        .map_err(|_| SyncError::Discovery)?
        .len();
    if !result.candidates.is_empty() && result.total_bytes + file_size > options.max_bytes {
        result.hit_byte_cap = true;
        return Ok(());
    }

    result.total_bytes += file_size;
    result.candidates.push(rollout_path.clone());
    seen.insert(rollout_path);
    if result.candidates.len() >= options.max_files {
        result.hit_file_cap = true;
    }
    if result.total_bytes >= options.max_bytes {
        result.hit_byte_cap = true;
    }
    Ok(())
}

fn needs_processing(
    state_entry: &StateEntry,
    rollout_path: &Path,
    vault_root: &Path,
    include_subagents: bool,
    current_thread_name: CurrentThreadName<'_>,
) -> bool {
    let Ok(fingerprint) = file_fingerprint(rollout_path) else {
        return true;
    };
    if state_entry.size != Some(fingerprint.size)
        || state_entry.mtime_ns != Some(fingerprint.mtime_ns)
    {
        return true;
    }

    if include_subagents && !state_entry.included.unwrap_or(false) {
        return true;
    }
    if !include_subagents
        && state_entry.included == Some(true)
        && state_entry.bool_field("is_subagent") == Some(true)
    {
        return true;
    }

    if let CurrentThreadName::Present(expected_thread_name) = current_thread_name {
        if !thread_name_matches(state_entry, expected_thread_name) {
            return true;
        }
    }

    if state_entry.included == Some(true) {
        if let Some(relative_note_path) = state_entry.string_field("conversation_note_path") {
            let Some(note_path) = resolve_note_path_read_only(vault_root, relative_note_path)
            else {
                return true;
            };
            if !note_path.exists() {
                return true;
            }
            if let Some(expected) = &state_entry.conversation_note_fingerprint {
                if !fingerprint_matches(&note_path, expected) {
                    return true;
                }
            }
        }
    }

    false
}

fn thread_name_matches(state_entry: &StateEntry, expected: Option<&str>) -> bool {
    match (state_entry.extra.get("thread_name"), expected) {
        (Some(Value::String(actual)), Some(expected)) => actual == expected,
        (Some(Value::Null) | None, None) => true,
        (Some(_), None) | (None, Some(_)) => false,
        (Some(_), Some(_)) => false,
    }
}

fn fingerprint_matches(path: &Path, expected: &FileFingerprint) -> bool {
    file_fingerprint(path).is_ok_and(|actual| &actual == expected)
}

fn resolve_note_path_read_only(vault_root: &Path, relative_path: &str) -> Option<PathBuf> {
    let relative = Path::new(relative_path);
    if relative.is_absolute()
        || relative
            .components()
            .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        return None;
    }

    let target = vault_root.join(relative);
    if std::fs::symlink_metadata(&target).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return None;
    }

    let resolved_root = std::fs::canonicalize(vault_root).ok()?;
    let resolved_parent = std::fs::canonicalize(target.parent()?).ok()?;
    if resolved_parent != resolved_root && !resolved_parent.starts_with(&resolved_root) {
        return None;
    }
    Some(target)
}

fn file_mtime_ns(path: &Path) -> Result<u64, SyncError> {
    Ok(file_fingerprint(path)?.mtime_ns)
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
