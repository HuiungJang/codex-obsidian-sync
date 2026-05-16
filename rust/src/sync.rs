use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::Serialize;
use serde_json::{Value, json};
use sha1::{Digest, Sha1};
use time::format_description::well_known::Rfc3339;
use time::{OffsetDateTime, UtcOffset};

use crate::config::SyncConfig;
use crate::discovery::{
    DiscoveryOptions, SessionIndex, build_session_envelope, discover_rollout_candidates,
    load_session_index, state_entry_needs_processing,
};
use crate::dry_run_output::DryRunOutput;
use crate::error::SyncError;
use crate::models::{SessionEnvelope, TranscriptMessage};
use crate::parser::load_rollout_records_from_offset;
use crate::render::{
    ConversationNoteRecord, DailyEntry, RenderOptions, build_conversation_note_record_with_options,
    extract_transcript_body, merge_managed_section, render_conversation_header,
    render_conversation_note, render_daily_managed_section, render_daily_note_header,
    render_project_managed_section, render_project_note_header, render_transcript_append_text,
};
use crate::state_store::{
    FileFingerprint, StateEntry, SyncState, file_fingerprint, load_state_read_only,
    render_temp_state,
};

#[derive(Debug, Clone, Copy)]
pub struct SyncRunOptions {
    pub now_utc: OffsetDateTime,
    pub local_offset_override: Option<UtcOffset>,
}

impl SyncRunOptions {
    pub fn new() -> Self {
        Self {
            now_utc: OffsetDateTime::now_utc(),
            local_offset_override: None,
        }
    }
}

impl Default for SyncRunOptions {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SyncSummary {
    pub processed: usize,
    pub appended: usize,
    pub rewritten: usize,
    pub skipped_subagents: usize,
    pub skipped_invalid: usize,
    pub unchanged: usize,
    pub total_rollouts: usize,
    pub paused: usize,
    pub fast_path: usize,
    pub duration_ms: u64,
    pub dry_run: bool,
    pub output_dir: String,
    pub temp_state_file: String,
    pub planned_writes: usize,
    pub lock_exists: bool,
}

impl SyncSummary {
    fn new(lock_exists: bool) -> Self {
        Self {
            processed: 0,
            appended: 0,
            rewritten: 0,
            skipped_subagents: 0,
            skipped_invalid: 0,
            unchanged: 0,
            total_rollouts: 0,
            paused: 0,
            fast_path: 0,
            duration_ms: 0,
            dry_run: true,
            output_dir: String::new(),
            temp_state_file: String::new(),
            planned_writes: 0,
            lock_exists,
        }
    }
}

pub fn sync_once_dry_run(
    config: &SyncConfig,
    requested_output: Option<&Path>,
) -> Result<SyncSummary, SyncError> {
    sync_once_dry_run_with_options(config, requested_output, SyncRunOptions::new())
}

pub fn sync_once_dry_run_with_options(
    config: &SyncConfig,
    requested_output: Option<&Path>,
    options: SyncRunOptions,
) -> Result<SyncSummary, SyncError> {
    let started = Instant::now();
    let mut summary = SyncSummary::new(config.lock_file.exists());
    let vault_root = validate_vault_root(&config.vault)?;
    let dry_run = DryRunOutput::prepare(config, requested_output)?;
    summary.output_dir = dry_run.root().to_string_lossy().into_owned();
    summary.temp_state_file = dry_run.temp_state_file().to_string_lossy().into_owned();

    let session_index_path = config.codex_home.join("session_index.jsonl");
    let mut state = load_state_read_only(&config.state_file)?;
    prune_missing_rollouts(&mut state);
    let previous_conversations = included_conversations(&state);

    if can_fast_skip_sync(
        &state,
        &session_index_path,
        &vault_root,
        config.include_subagents,
    ) {
        update_runtime_state(&mut state, &session_index_path, None, options.now_utc)?;
        summary.fast_path = 1;
        summary.unchanged = state.files.len();
        write_temp_state(&dry_run, &state, &mut summary)?;
        summary.duration_ms = elapsed_ms(started);
        return Ok(summary);
    }

    let session_index = load_session_index(&session_index_path)?;
    let mut discovery_options = DiscoveryOptions::new(&vault_root);
    discovery_options.include_subagents = config.include_subagents;
    discovery_options.recent_days = i64::from(config.recent_days);
    discovery_options.max_files = config.candidate_file_limit as usize;
    discovery_options.max_bytes = config.candidate_bytes_limit;
    discovery_options.now_utc = options.now_utc;
    discovery_options.local_offset_override = options.local_offset_override;
    let discovery = discover_rollout_candidates(
        &config.codex_home,
        &session_index,
        &state,
        &discovery_options,
    )?;
    summary.total_rollouts = discovery.candidates.len();

    let render_options = RenderOptions {
        local_offset_override: options.local_offset_override,
        now_utc: options.now_utc,
    };
    let process_context = ProcessContext {
        session_index: &session_index,
        config,
        dry_run: &dry_run,
        render_options: &render_options,
        now_utc: options.now_utc,
    };
    for rollout_path in &discovery.candidates {
        process_rollout(rollout_path, &process_context, &mut state, &mut summary)?;
    }

    let active_conversations = included_conversations(&state);
    write_daily_notes(&dry_run, &active_conversations, &mut summary)?;
    write_project_notes(&dry_run, &active_conversations, &mut summary)?;
    clear_stale_index_notes(
        &dry_run,
        &previous_conversations,
        &active_conversations,
        &mut summary,
    )?;
    update_runtime_state(
        &mut state,
        &session_index_path,
        Some(!discovery.hit_file_cap && !discovery.hit_byte_cap),
        options.now_utc,
    )?;
    write_temp_state(&dry_run, &state, &mut summary)?;
    summary.duration_ms = elapsed_ms(started);
    Ok(summary)
}

struct ProcessContext<'a> {
    session_index: &'a SessionIndex,
    config: &'a SyncConfig,
    dry_run: &'a DryRunOutput,
    render_options: &'a RenderOptions,
    now_utc: OffsetDateTime,
}

fn process_rollout(
    rollout_path: &Path,
    context: &ProcessContext<'_>,
    state: &mut SyncState,
    summary: &mut SyncSummary,
) -> Result<(), SyncError> {
    let state_key = rollout_path.to_string_lossy().into_owned();
    let previous_entry = state.files.get(&state_key).cloned();
    let envelope = match load_session_envelope_for_sync(
        rollout_path,
        context.session_index,
        previous_entry.as_ref(),
    ) {
        Ok(Some(envelope)) => envelope,
        Ok(None) => {
            summary.unchanged += 1;
            return Ok(());
        }
        Err(_) => {
            summary.skipped_invalid += 1;
            return Ok(());
        }
    };

    if envelope.is_subagent && !context.config.include_subagents {
        upsert_skipped_subagent(state, rollout_path, &envelope, previous_entry.as_ref())?;
        summary.skipped_subagents += 1;
        return Ok(());
    }

    let conversation =
        build_conversation_note_record_with_options(&envelope, context.render_options)?;
    let note_relative = conversation.conversation_note_path.as_str();
    let note_exists = context.dry_run.exists_relative(note_relative)?;
    let header = render_conversation_header(&envelope, &conversation);
    let header_hash = build_content_hash(&header);
    let render_hash = build_render_hash(&envelope);
    let previous_render_hash = previous_entry
        .as_ref()
        .and_then(|entry| entry.string_field("render_hash"));
    let previous_header_hash = previous_entry
        .as_ref()
        .and_then(|entry| entry.string_field("header_hash"));
    let previous_note_fingerprint = previous_entry
        .as_ref()
        .and_then(|entry| entry.conversation_note_fingerprint.as_ref());

    let mut wrote_note = false;
    let previous_note_matches = note_exists
        && note_fingerprint_matches(context.dry_run, note_relative, previous_note_fingerprint)?;
    if note_exists && previous_render_hash == Some(render_hash.as_str()) && previous_note_matches {
        summary.unchanged += 1;
    } else {
        let append_start =
            append_start_index(&envelope, previous_entry.as_ref(), previous_note_matches);
        let header_changed = previous_header_hash != Some(header_hash.as_str());

        if previous_note_matches && header_changed {
            if let Some(content) =
                rewrite_conversation_header_content(context.dry_run, note_relative, &header)?
            {
                write_relative(context.dry_run, note_relative, &content, summary)?;
                wrote_note = true;
                summary.rewritten += 1;
            }
        }

        if let Some(start_index) = append_start {
            if let Some(append_text) =
                render_transcript_append_text(&envelope.messages[start_index..])
            {
                let existing = context
                    .dry_run
                    .read_relative(note_relative)?
                    .ok_or(SyncError::DryRunOutput)?;
                let content = format!("{existing}{append_text}");
                write_relative(context.dry_run, note_relative, &content, summary)?;
                wrote_note = true;
                summary.appended += 1;
            }
        }

        if !wrote_note {
            let content = render_conversation_note(&envelope, &conversation);
            write_relative(context.dry_run, note_relative, &content, summary)?;
            wrote_note = true;
            summary.rewritten += 1;
        }
        summary.processed += 1;
    }

    upsert_included_conversation(
        state,
        IncludedConversationUpdate {
            rollout_path,
            envelope: &envelope,
            conversation: &conversation,
            render_hash: &render_hash,
            header_hash: &header_hash,
            note_fingerprint: context
                .dry_run
                .fingerprint_relative(note_relative)?
                .filter(|_| note_exists || wrote_note),
            previous_entry: previous_entry.as_ref(),
            wrote_note,
            now_utc: context.now_utc,
        },
    )?;
    Ok(())
}

fn load_session_envelope_for_sync(
    rollout_path: &Path,
    session_index: &SessionIndex,
    state_entry: Option<&StateEntry>,
) -> Result<Option<SessionEnvelope>, SyncError> {
    if append_only_change(state_entry, rollout_path)? {
        let offset = state_entry
            .and_then(|entry| entry.offset)
            .unwrap_or_default();
        match load_rollout_records_from_offset(rollout_path, offset) {
            Ok((records, next_offset)) if records.is_empty() && next_offset == offset => {
                return Ok(None);
            }
            Ok(_) | Err(_) => {}
        }
    }

    build_session_envelope(rollout_path, session_index).map(Some)
}

fn append_only_change(state_entry: Option<&StateEntry>, path: &Path) -> Result<bool, SyncError> {
    let Some(state_entry) = state_entry else {
        return Ok(false);
    };
    let (Some(previous_size), Some(previous_offset)) = (state_entry.size, state_entry.offset)
    else {
        return Ok(false);
    };
    let fingerprint = file_fingerprint(path)?;
    Ok(fingerprint.size >= previous_size && previous_offset < fingerprint.size)
}

fn append_start_index(
    envelope: &SessionEnvelope,
    state_entry: Option<&StateEntry>,
    previous_note_matches: bool,
) -> Option<usize> {
    let state_entry = state_entry?;
    if !previous_note_matches {
        return None;
    }

    let previous_count = state_entry
        .extra
        .get("last_written_message_count")
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok());
    let previous_key = state_entry
        .extra
        .get("last_written_message_key")
        .and_then(Value::as_str);
    let previous_count = previous_count?;
    if previous_count > envelope.messages.len() {
        return None;
    }
    if previous_count > 0 {
        let expected_key = envelope.messages[previous_count - 1].message_key.as_str();
        if previous_key != Some(expected_key) {
            return None;
        }
    }
    Some(previous_count)
}

fn rewrite_conversation_header_content(
    dry_run: &DryRunOutput,
    note_relative: &str,
    header: &str,
) -> Result<Option<String>, SyncError> {
    let Some(existing) = dry_run.read_relative(note_relative)? else {
        return Ok(None);
    };
    let Some(transcript_body) = extract_transcript_body(&existing) else {
        return Ok(None);
    };
    Ok(Some(
        format!("{header}{transcript_body}").trim_end().to_owned() + "\n",
    ))
}

fn note_fingerprint_matches(
    dry_run: &DryRunOutput,
    note_relative: &str,
    expected: Option<&FileFingerprint>,
) -> Result<bool, SyncError> {
    let Some(expected) = expected else {
        return Ok(false);
    };
    Ok(dry_run.fingerprint_relative(note_relative)?.as_ref() == Some(expected))
}

struct IncludedConversationUpdate<'a> {
    rollout_path: &'a Path,
    envelope: &'a SessionEnvelope,
    conversation: &'a ConversationNoteRecord,
    render_hash: &'a str,
    header_hash: &'a str,
    note_fingerprint: Option<FileFingerprint>,
    previous_entry: Option<&'a StateEntry>,
    wrote_note: bool,
    now_utc: OffsetDateTime,
}

fn upsert_included_conversation(
    state: &mut SyncState,
    update: IncludedConversationUpdate<'_>,
) -> Result<(), SyncError> {
    let rollout_fingerprint = file_fingerprint(update.rollout_path)?;
    let mut extra = conversation_extra(update.conversation)?;
    extra.insert("is_subagent".to_owned(), json!(update.envelope.is_subagent));
    extra.insert(
        "originator".to_owned(),
        option_string_value(update.envelope.originator.as_deref()),
    );
    extra.insert(
        "thread_name".to_owned(),
        option_string_value(update.envelope.thread_name.as_deref()),
    );
    extra.insert(
        "transcript_hash".to_owned(),
        json!(build_transcript_hash(&update.envelope.messages)),
    );
    extra.insert("render_hash".to_owned(), json!(update.render_hash));
    extra.insert("header_hash".to_owned(), json!(update.header_hash));
    extra.insert(
        "last_written_message_count".to_owned(),
        json!(update.envelope.messages.len()),
    );
    extra.insert(
        "last_written_message_key".to_owned(),
        json!(last_message_key(update.envelope)),
    );
    extra.insert(
        "last_note_write_at".to_owned(),
        json!(if update.wrote_note {
            utc_now_iso(update.now_utc)
        } else {
            update
                .previous_entry
                .and_then(|entry| entry.string_field("last_note_write_at"))
                .unwrap_or("")
                .to_owned()
        }),
    );

    upsert_state_entry(
        state,
        update.rollout_path,
        StateEntry {
            included: Some(true),
            size: Some(rollout_fingerprint.size),
            mtime_ns: Some(rollout_fingerprint.mtime_ns),
            offset: Some(rollout_fingerprint.size),
            conversation_note_fingerprint: update.note_fingerprint,
            extra,
        },
    );
    Ok(())
}

fn upsert_skipped_subagent(
    state: &mut SyncState,
    rollout_path: &Path,
    envelope: &SessionEnvelope,
    previous_entry: Option<&StateEntry>,
) -> Result<(), SyncError> {
    let rollout_fingerprint = file_fingerprint(rollout_path)?;
    let mut extra = preserved_note_paths(previous_entry);
    extra.insert(
        "canonical_session_id".to_owned(),
        json!(envelope.canonical_session_id),
    );
    extra.insert("is_subagent".to_owned(), json!(true));
    extra.insert("project_slug".to_owned(), json!(envelope.project_slug));
    extra.insert(
        "updated_at".to_owned(),
        option_string_value(envelope.updated_at.as_deref()),
    );

    upsert_state_entry(
        state,
        rollout_path,
        StateEntry {
            included: Some(false),
            size: Some(rollout_fingerprint.size),
            mtime_ns: Some(rollout_fingerprint.mtime_ns),
            offset: Some(rollout_fingerprint.size),
            extra,
            ..StateEntry::default()
        },
    );
    Ok(())
}

fn upsert_state_entry(state: &mut SyncState, rollout_path: &Path, entry: StateEntry) {
    state
        .files
        .insert(rollout_path.to_string_lossy().into_owned(), entry);
}

fn conversation_extra(
    conversation: &ConversationNoteRecord,
) -> Result<BTreeMap<String, Value>, SyncError> {
    let value = serde_json::to_value(conversation).map_err(|_| SyncError::Render)?;
    let Value::Object(map) = value else {
        return Err(SyncError::Render);
    };
    Ok(map.into_iter().collect())
}

fn preserved_note_paths(entry: Option<&StateEntry>) -> BTreeMap<String, Value> {
    let Some(entry) = entry else {
        return BTreeMap::new();
    };
    [
        "conversation_note_path",
        "daily_note_path",
        "project_note_path",
        "daily_entries",
    ]
    .into_iter()
    .filter_map(|key| {
        let value = entry.extra.get(key)?;
        is_truthy_json(value).then(|| (key.to_owned(), value.clone()))
    })
    .collect()
}

fn is_truthy_json(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_i64().is_some_and(|item| item != 0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn included_conversations(state: &SyncState) -> Vec<ConversationNoteRecord> {
    state
        .files
        .values()
        .filter(|entry| entry.included == Some(true))
        .filter_map(conversation_from_state_entry)
        .collect()
}

fn conversation_from_state_entry(entry: &StateEntry) -> Option<ConversationNoteRecord> {
    let date = string_extra(entry, "date")?;
    let time = string_extra(entry, "time")?;
    let daily_entries = entry
        .extra
        .get("daily_entries")
        .cloned()
        .and_then(|value| serde_json::from_value::<Vec<DailyEntry>>(value).ok())
        .unwrap_or_default();

    Some(ConversationNoteRecord {
        conversation_note_path: string_extra(entry, "conversation_note_path")?,
        daily_note_path: string_extra(entry, "daily_note_path")?,
        project_note_path: string_extra(entry, "project_note_path")?,
        date: date.clone(),
        time: time.clone(),
        started_date: string_extra(entry, "started_date").unwrap_or_else(|| date.clone()),
        started_time: string_extra(entry, "started_time").unwrap_or_else(|| time.clone()),
        title: string_extra(entry, "title")?,
        project_slug: string_extra(entry, "project_slug")?,
        canonical_session_id: string_extra(entry, "canonical_session_id")?,
        started_at: string_extra(entry, "started_at").unwrap_or_default(),
        updated_at: string_extra(entry, "updated_at").unwrap_or_default(),
        status: string_extra(entry, "status").unwrap_or_default(),
        daily_entries,
    })
}

fn string_extra(entry: &StateEntry, key: &str) -> Option<String> {
    entry
        .extra
        .get(key)
        .and_then(Value::as_str)
        .map(str::to_owned)
}

fn write_daily_notes(
    dry_run: &DryRunOutput,
    conversations: &[ConversationNoteRecord],
    summary: &mut SyncSummary,
) -> Result<(), SyncError> {
    let mut grouped = BTreeMap::<String, Vec<ConversationNoteRecord>>::new();
    for conversation in conversations {
        if !conversation.daily_entries.is_empty() {
            for daily_entry in &conversation.daily_entries {
                if daily_entry.date.trim().is_empty() || daily_entry.time.trim().is_empty() {
                    continue;
                }
                let mut item = conversation.clone();
                item.date = daily_entry.date.clone();
                item.time = daily_entry.time.clone();
                grouped
                    .entry(daily_entry.date.clone())
                    .or_default()
                    .push(item);
            }
        } else {
            grouped
                .entry(conversation.date.clone())
                .or_default()
                .push(conversation.clone());
        }
    }

    for (date_key, items) in grouped.iter_mut() {
        items.sort_by(|left, right| {
            left.time
                .cmp(&right.time)
                .then(left.title.cmp(&right.title))
        });
        let managed_content = render_daily_managed_section(items);
        write_sectioned_note(
            dry_run,
            &format!("Codex/Daily/{date_key}.md"),
            &render_daily_note_header(date_key),
            &managed_content,
            summary,
        )?;
    }
    Ok(())
}

fn write_project_notes(
    dry_run: &DryRunOutput,
    conversations: &[ConversationNoteRecord],
    summary: &mut SyncSummary,
) -> Result<(), SyncError> {
    let mut grouped = BTreeMap::<String, Vec<ConversationNoteRecord>>::new();
    for conversation in conversations {
        grouped
            .entry(conversation.project_slug.clone())
            .or_default()
            .push(conversation.clone());
    }

    for (project_slug, items) in grouped.iter_mut() {
        items.sort_by(|left, right| {
            right
                .date
                .cmp(&left.date)
                .then(right.time.cmp(&left.time))
                .then(right.title.cmp(&left.title))
        });
        let Some(header) = render_project_note_header(project_slug, items) else {
            continue;
        };
        let managed_content = render_project_managed_section(items);
        write_sectioned_note(
            dry_run,
            &format!("Codex/Projects/{project_slug}.md"),
            &header,
            &managed_content,
            summary,
        )?;
    }
    Ok(())
}

fn clear_stale_index_notes(
    dry_run: &DryRunOutput,
    previous_conversations: &[ConversationNoteRecord],
    active_conversations: &[ConversationNoteRecord],
    summary: &mut SyncSummary,
) -> Result<(), SyncError> {
    let (previous_daily, previous_projects) = index_note_paths(previous_conversations);
    let (active_daily, active_projects) = index_note_paths(active_conversations);

    for daily_path in previous_daily.difference(&active_daily) {
        if !dry_run.exists_relative(daily_path)? {
            continue;
        }
        let date_key = Path::new(daily_path)
            .file_stem()
            .and_then(|stem| stem.to_str())
            .ok_or(SyncError::Render)?;
        write_sectioned_note(
            dry_run,
            daily_path,
            &render_daily_note_header(date_key),
            "",
            summary,
        )?;
    }

    for project_path in previous_projects.difference(&active_projects) {
        if !dry_run.exists_relative(project_path)? {
            continue;
        }
        let project_slug = Path::new(project_path)
            .file_stem()
            .and_then(|stem| stem.to_str())
            .ok_or(SyncError::Render)?;
        write_sectioned_note(
            dry_run,
            project_path,
            &format!("# {project_slug}"),
            "",
            summary,
        )?;
    }
    Ok(())
}

fn index_note_paths(
    conversations: &[ConversationNoteRecord],
) -> (BTreeSet<String>, BTreeSet<String>) {
    let mut daily_paths = BTreeSet::new();
    let mut project_paths = BTreeSet::new();

    for conversation in conversations {
        if !conversation.daily_entries.is_empty() {
            for entry in &conversation.daily_entries {
                let date_key = entry.date.trim();
                if !date_key.is_empty() {
                    daily_paths.insert(format!("Codex/Daily/{date_key}.md"));
                }
            }
        } else if !conversation.daily_note_path.is_empty() {
            daily_paths.insert(conversation.daily_note_path.clone());
        }

        if !conversation.project_note_path.is_empty() {
            project_paths.insert(conversation.project_note_path.clone());
        }
    }

    (daily_paths, project_paths)
}

fn write_sectioned_note(
    dry_run: &DryRunOutput,
    relative_path: &str,
    header: &str,
    managed_content: &str,
    summary: &mut SyncSummary,
) -> Result<(), SyncError> {
    let existing = dry_run.read_relative(relative_path)?.unwrap_or_default();
    let base = if existing.is_empty() {
        format!("{header}\n")
    } else {
        existing
    };
    let merged = merge_managed_section(&base, managed_content);
    write_relative(dry_run, relative_path, &merged, summary)?;
    Ok(())
}

fn write_relative(
    dry_run: &DryRunOutput,
    relative_path: &str,
    content: &str,
    summary: &mut SyncSummary,
) -> Result<PathBuf, SyncError> {
    let path = dry_run.write_relative(relative_path, content)?;
    summary.planned_writes += 1;
    Ok(path)
}

fn write_temp_state(
    dry_run: &DryRunOutput,
    state: &SyncState,
    summary: &mut SyncSummary,
) -> Result<(), SyncError> {
    let content = render_temp_state(state)?;
    dry_run.write_relative("sync-state.json", &content)?;
    summary.planned_writes += 1;
    Ok(())
}

fn can_fast_skip_sync(
    state: &SyncState,
    session_index_path: &Path,
    vault_root: &Path,
    include_subagents: bool,
) -> bool {
    if state.extra.get("discovery_complete") != Some(&json!(true)) {
        return false;
    }
    if !session_index_path.exists() {
        return false;
    }
    let Ok(index_fingerprint) = file_fingerprint(session_index_path) else {
        return false;
    };
    if state.extra.get("session_index") != Some(&json!(index_fingerprint)) {
        return false;
    }

    for (path, entry) in &state.files {
        let rollout_path = Path::new(path);
        if !rollout_path.exists()
            || state_entry_needs_processing(entry, rollout_path, vault_root, include_subagents)
        {
            return false;
        }
    }
    true
}

fn update_runtime_state(
    state: &mut SyncState,
    session_index_path: &Path,
    discovery_complete: Option<bool>,
    now_utc: OffsetDateTime,
) -> Result<(), SyncError> {
    state
        .extra
        .insert("last_poll_at".to_owned(), json!(utc_now_iso(now_utc)));
    let session_index = if session_index_path.exists() {
        json!(file_fingerprint(session_index_path)?)
    } else {
        json!({})
    };
    state
        .extra
        .insert("session_index".to_owned(), session_index);
    if let Some(discovery_complete) = discovery_complete {
        state
            .extra
            .insert("discovery_complete".to_owned(), json!(discovery_complete));
    }
    Ok(())
}

fn prune_missing_rollouts(state: &mut SyncState) {
    state.files.retain(|path, _| Path::new(path).exists());
}

fn validate_vault_root(vault_root: &Path) -> Result<PathBuf, SyncError> {
    if vault_root.as_os_str().is_empty() || !vault_root.is_absolute() {
        return Err(SyncError::Config);
    }
    let resolved = std::fs::canonicalize(vault_root).map_err(|_| SyncError::Config)?;
    if !resolved.is_dir() {
        return Err(SyncError::Config);
    }
    Ok(resolved)
}

fn build_render_hash(envelope: &SessionEnvelope) -> String {
    let mut fingerprint = vec![
        envelope.canonical_session_id.as_str(),
        envelope.originator.as_deref().unwrap_or(""),
        envelope.project_slug.as_str(),
        envelope.thread_name.as_deref().unwrap_or(""),
        envelope.title_seed.as_str(),
        envelope.started_at.as_deref().unwrap_or(""),
        crate::render::session_status(envelope),
    ]
    .into_iter()
    .map(str::to_owned)
    .collect::<Vec<_>>();
    fingerprint.extend(
        envelope
            .messages
            .iter()
            .map(|message| message.message_key.clone()),
    );
    build_content_hash(&fingerprint.join("\n"))
}

fn build_transcript_hash(messages: &[TranscriptMessage]) -> String {
    build_content_hash(
        &messages
            .iter()
            .map(|message| message.message_key.as_str())
            .collect::<Vec<_>>()
            .join("\n"),
    )
}

fn build_content_hash(content: &str) -> String {
    let mut hasher = Sha1::new();
    hasher.update(content.as_bytes());
    hex_lower(&hasher.finalize())
}

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn last_message_key(envelope: &SessionEnvelope) -> &str {
    envelope
        .messages
        .last()
        .map(|message| message.message_key.as_str())
        .unwrap_or("")
}

fn option_string_value(value: Option<&str>) -> Value {
    value.map_or(Value::Null, |value| json!(value))
}

fn elapsed_ms(started: Instant) -> u64 {
    started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64
}

fn utc_now_iso(now_utc: OffsetDateTime) -> String {
    now_utc
        .to_offset(UtcOffset::UTC)
        .format(&Rfc3339)
        .unwrap_or_else(|_| "1970-01-01T00:00:00Z".to_owned())
}
