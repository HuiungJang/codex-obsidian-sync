use std::cmp::Ordering;

use serde::{Deserialize, Serialize};
use time::format_description::well_known::Rfc3339;
use time::{Date, Month, OffsetDateTime, PrimitiveDateTime, Time, UtcOffset};

use crate::error::SyncError;
use crate::models::{SessionEnvelope, TranscriptMessage};
use crate::redaction::redact_text;

pub const MANAGED_START: &str = "<!-- BEGIN CODEX MANAGED SECTION -->";
pub const MANAGED_END: &str = "<!-- END CODEX MANAGED SECTION -->";
pub const CONVERSATION_NOTE_MARKER: &str = "<!-- CODEX CONVERSATION NOTE v2 -->";
pub const TRANSCRIPT_MARKER: &str = "<!-- BEGIN CODEX TRANSCRIPT -->";

#[derive(Debug, Clone, Copy)]
pub struct RenderOptions {
    pub local_offset_override: Option<UtcOffset>,
    pub now_utc: OffsetDateTime,
}

impl RenderOptions {
    pub fn new() -> Self {
        Self {
            local_offset_override: None,
            now_utc: OffsetDateTime::now_utc(),
        }
    }
}

impl Default for RenderOptions {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DailyEntry {
    pub date: String,
    pub time: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConversationNoteRecord {
    pub conversation_note_path: String,
    pub daily_note_path: String,
    pub project_note_path: String,
    pub date: String,
    pub time: String,
    pub started_date: String,
    pub started_time: String,
    pub title: String,
    pub project_slug: String,
    pub canonical_session_id: String,
    pub started_at: String,
    pub updated_at: String,
    pub status: String,
    pub daily_entries: Vec<DailyEntry>,
}

pub fn build_conversation_note_record(
    envelope: &SessionEnvelope,
) -> Result<ConversationNoteRecord, SyncError> {
    build_conversation_note_record_with_options(envelope, &RenderOptions::new())
}

pub fn build_conversation_note_record_with_options(
    envelope: &SessionEnvelope,
    options: &RenderOptions,
) -> Result<ConversationNoteRecord, SyncError> {
    let (started_date_key, started_time_key, year_key) =
        conversation_start_parts(envelope, options)?;
    let daily_entries = render_daily_entries_with_options(envelope, options)?;
    let (last_activity_date_key, last_activity_time_label) =
        if let Some(latest_entry) = daily_entries.last() {
            (latest_entry.date.clone(), latest_entry.time.clone())
        } else {
            let (date_key, time_key, _) = activity_parts(envelope, options)?;
            (date_key, render_clock_label(&time_key))
        };
    let title = conversation_title(envelope);
    Ok(ConversationNoteRecord {
        conversation_note_path: format!(
            "Codex/Conversations/{year_key}/{started_date_key}-{started_time_key}-{}.md",
            envelope.title_seed
        ),
        daily_note_path: format!("Codex/Daily/{last_activity_date_key}.md"),
        project_note_path: format!("Codex/Projects/{}.md", envelope.project_slug),
        date: last_activity_date_key,
        time: last_activity_time_label,
        started_date: started_date_key,
        started_time: render_clock_label(&started_time_key),
        title,
        project_slug: envelope.project_slug.clone(),
        canonical_session_id: envelope.canonical_session_id.clone(),
        started_at: envelope.started_at.clone().unwrap_or_default(),
        updated_at: envelope.updated_at.clone().unwrap_or_default(),
        status: session_status(envelope).to_owned(),
        daily_entries,
    })
}

pub fn render_conversation_note(
    envelope: &SessionEnvelope,
    note: &ConversationNoteRecord,
) -> String {
    let header = render_conversation_header(envelope, note);
    let transcript = render_transcript_messages(&envelope.messages);
    format!("{}{}", header, transcript).trim_end().to_owned() + "\n"
}

pub fn render_conversation_header(
    envelope: &SessionEnvelope,
    note: &ConversationNoteRecord,
) -> String {
    let title = conversation_title(envelope);
    [
        "---".to_owned(),
        "type: codex-conversation".to_owned(),
        format!("date: {}", note.started_date),
        format!("session_id: {}", envelope.canonical_session_id),
        format!(
            "originator: {}",
            envelope.originator.as_deref().unwrap_or("unknown")
        ),
        format!("project_slug: {}", envelope.project_slug),
        format!("daily_note: {}", note.daily_note_path),
        format!("status: {}", session_status(envelope)),
        "tags:".to_owned(),
        "  - codex".to_owned(),
        "  - codex-conversation".to_owned(),
        format!("  - project/{}", envelope.project_slug),
        "---".to_owned(),
        String::new(),
        CONVERSATION_NOTE_MARKER.to_owned(),
        String::new(),
        format!("# {title}"),
        String::new(),
        format!("[[{}]]", obsidian_target(&note.daily_note_path)),
        format!("[[{}]]", obsidian_target(&note.project_note_path)),
        String::new(),
        "## Transcript".to_owned(),
        String::new(),
        TRANSCRIPT_MARKER.to_owned(),
        String::new(),
    ]
    .join("\n")
}

pub fn render_transcript_messages(messages: &[TranscriptMessage]) -> String {
    let mut body_sections = Vec::new();
    for message in messages {
        body_sections.extend(render_transcript_message(message));
        body_sections.push(String::new());
    }
    body_sections.join("\n")
}

pub fn render_transcript_append_text(messages: &[TranscriptMessage]) -> Option<String> {
    let content = render_transcript_messages(messages).trim_end().to_owned();
    if content.is_empty() {
        None
    } else {
        Some(format!("\n{content}\n"))
    }
}

pub fn render_transcript_message(message: &TranscriptMessage) -> Vec<String> {
    let heading = if message.role == "user" {
        "User"
    } else {
        "Assistant"
    };
    vec![format!("### {heading}"), redact_text(&message.text)]
}

pub fn render_daily_managed_section(conversations: &[ConversationNoteRecord]) -> String {
    let mut sections = Vec::new();
    for conversation in conversations {
        sections.extend([
            format!("## {} {}", conversation.time, conversation.title),
            "> [!note]- Conversation".to_owned(),
            format!(
                "> [[{}|{}]]",
                obsidian_target(&conversation.conversation_note_path),
                conversation.title
            ),
            format!(
                "> Project: [[{}|{}]]",
                obsidian_target(&conversation.project_note_path),
                conversation.project_slug
            ),
            format!("> Session: `{}`", conversation.canonical_session_id),
            format!("> Status: `{}`", conversation.status),
            String::new(),
        ]);
    }
    sections.join("\n").trim_end().to_owned()
}

pub fn render_project_managed_section(conversations: &[ConversationNoteRecord]) -> String {
    let mut lines = vec!["## Conversations".to_owned(), String::new()];
    for conversation in conversations {
        lines.push(format!(
            "- {} {} [[{}|{}]]",
            conversation.date,
            conversation.time,
            obsidian_target(&conversation.conversation_note_path),
            conversation.title
        ));
    }
    lines.join("\n").trim_end().to_owned()
}

pub fn render_daily_note_header(date_key: &str) -> String {
    format!("# Codex Daily Log - {date_key}")
}

pub fn render_project_note_header(
    project_slug: &str,
    conversations: &[ConversationNoteRecord],
) -> Option<String> {
    let first_seen = conversations
        .iter()
        .min_by(|left, right| compare_date_time(left, right))?;
    Some(format!(
        "---\ntype: codex-project\nproject_slug: {project_slug}\nfirst_seen_at: {}\n---\n\n# {project_slug}",
        first_seen.started_at
    ))
}

pub fn render_daily_entries(envelope: &SessionEnvelope) -> Result<Vec<DailyEntry>, SyncError> {
    render_daily_entries_with_options(envelope, &RenderOptions::new())
}

pub fn render_daily_entries_with_options(
    envelope: &SessionEnvelope,
    options: &RenderOptions,
) -> Result<Vec<DailyEntry>, SyncError> {
    let mut daily_last_activity = std::collections::BTreeMap::<String, String>::new();
    let mut timestamps = envelope
        .messages
        .iter()
        .filter_map(|message| {
            if message.timestamp.is_empty() {
                None
            } else {
                Some(message.timestamp.as_str())
            }
        })
        .collect::<Vec<_>>();
    if timestamps.is_empty() {
        timestamps.push(
            envelope
                .updated_at
                .as_deref()
                .or(envelope.started_at.as_deref())
                .unwrap_or(""),
        );
    }

    for timestamp in timestamps {
        if timestamp.is_empty() {
            continue;
        }
        let date_time = parse_iso_timestamp(timestamp, options)?;
        let date_key = date_key(date_time);
        let time_key = time_key(date_time);
        let previous = daily_last_activity.get(&date_key);
        if previous.is_none_or(|previous| time_key > *previous) {
            daily_last_activity.insert(date_key, time_key);
        }
    }

    Ok(daily_last_activity
        .into_iter()
        .map(|(date, time)| DailyEntry {
            date,
            time: render_clock_label(&time),
        })
        .collect())
}

pub fn session_status(envelope: &SessionEnvelope) -> &'static str {
    if envelope
        .messages
        .iter()
        .any(|message| message.role == "assistant")
    {
        "completed"
    } else {
        "in_progress"
    }
}

pub fn merge_managed_section(existing: &str, managed_content: &str) -> String {
    let managed_block = format!(
        "{MANAGED_START}\n{}\n{MANAGED_END}",
        managed_content.trim_end()
    );
    if existing.contains(MANAGED_START) && existing.contains(MANAGED_END) {
        let start = existing.find(MANAGED_START).expect("managed start exists");
        let end = existing.find(MANAGED_END).expect("managed end exists") + MANAGED_END.len();
        let merged = format!(
            "{}\n\n{managed_block}\n{}",
            existing[..start].trim_end(),
            existing[end..].trim_start()
        );
        return merged.trim_end().to_owned() + "\n";
    }

    let existing = existing.trim_end();
    if existing.is_empty() {
        format!("{managed_block}\n")
    } else {
        format!("{existing}\n\n{managed_block}\n")
    }
}

pub fn extract_transcript_body(existing: &str) -> Option<String> {
    let marker = format!("{TRANSCRIPT_MARKER}\n");
    existing
        .find(&marker)
        .map(|index| existing[index + marker.len()..].to_owned())
}

pub fn obsidian_target(relative_path: &str) -> String {
    relative_path
        .strip_suffix(".md")
        .unwrap_or(relative_path)
        .to_owned()
}

fn conversation_title(envelope: &SessionEnvelope) -> String {
    envelope
        .thread_name
        .clone()
        .unwrap_or_else(|| envelope.title_seed.replace('-', " "))
}

fn conversation_start_parts(
    envelope: &SessionEnvelope,
    options: &RenderOptions,
) -> Result<(String, String, String), SyncError> {
    let timestamp = envelope
        .started_at
        .as_deref()
        .or(envelope.updated_at.as_deref())
        .unwrap_or("");
    timestamp_parts(timestamp, options)
}

fn activity_parts(
    envelope: &SessionEnvelope,
    options: &RenderOptions,
) -> Result<(String, String, String), SyncError> {
    let timestamp = envelope
        .updated_at
        .as_deref()
        .or(envelope.started_at.as_deref())
        .unwrap_or("");
    timestamp_parts(timestamp, options)
}

fn timestamp_parts(
    timestamp: &str,
    options: &RenderOptions,
) -> Result<(String, String, String), SyncError> {
    let date_time = parse_iso_timestamp(timestamp, options)?;
    Ok((
        date_key(date_time),
        time_key(date_time),
        year_key(date_time),
    ))
}

fn parse_iso_timestamp(value: &str, options: &RenderOptions) -> Result<OffsetDateTime, SyncError> {
    if value.is_empty() {
        return Ok(options.now_utc.to_offset(local_offset_for_naive(options)));
    }

    let date_time = match OffsetDateTime::parse(value, &Rfc3339) {
        Ok(date_time) => date_time,
        Err(_) => return parse_naive_iso_timestamp(value, options),
    };
    let local_offset = options
        .local_offset_override
        .or_else(|| UtcOffset::local_offset_at(date_time).ok())
        .unwrap_or(UtcOffset::UTC);
    Ok(date_time.to_offset(local_offset))
}

fn parse_naive_iso_timestamp(
    value: &str,
    options: &RenderOptions,
) -> Result<OffsetDateTime, SyncError> {
    let (date_part, time_part) = value
        .split_once('T')
        .or_else(|| value.split_once(' '))
        .ok_or(SyncError::Render)?;
    let mut date_parts = date_part.split('-');
    let year = parse_i32_component(date_parts.next())?;
    let month = parse_u8_component(date_parts.next())?;
    let day = parse_u8_component(date_parts.next())?;
    if date_parts.next().is_some() {
        return Err(SyncError::Render);
    }

    let time_part = time_part
        .split_once('.')
        .map_or(time_part, |(head, _)| head);
    let mut time_parts = time_part.split(':');
    let hour = parse_u8_component(time_parts.next())?;
    let minute = parse_u8_component(time_parts.next())?;
    let second = match time_parts.next() {
        Some(value) => parse_u8_component(Some(value))?,
        None => 0,
    };
    if time_parts.next().is_some() {
        return Err(SyncError::Render);
    }

    let month = Month::try_from(month).map_err(|_| SyncError::Render)?;
    let date = Date::from_calendar_date(year, month, day).map_err(|_| SyncError::Render)?;
    let time = Time::from_hms(hour, minute, second).map_err(|_| SyncError::Render)?;
    Ok(PrimitiveDateTime::new(date, time).assume_offset(local_offset_for_naive(options)))
}

fn local_offset_for_naive(options: &RenderOptions) -> UtcOffset {
    options
        .local_offset_override
        .or_else(|| UtcOffset::current_local_offset().ok())
        .unwrap_or(UtcOffset::UTC)
}

fn parse_i32_component(value: Option<&str>) -> Result<i32, SyncError> {
    value
        .ok_or(SyncError::Render)?
        .parse()
        .map_err(|_| SyncError::Render)
}

fn parse_u8_component(value: Option<&str>) -> Result<u8, SyncError> {
    value
        .ok_or(SyncError::Render)?
        .parse()
        .map_err(|_| SyncError::Render)
}

fn date_key(date_time: OffsetDateTime) -> String {
    format!(
        "{:04}-{:02}-{:02}",
        date_time.year(),
        u8::from(date_time.month()),
        date_time.day()
    )
}

fn time_key(date_time: OffsetDateTime) -> String {
    format!("{:02}{:02}", date_time.hour(), date_time.minute())
}

fn year_key(date_time: OffsetDateTime) -> String {
    format!("{:04}", date_time.year())
}

fn render_clock_label(time_key: &str) -> String {
    format!("{}:{}", &time_key[..2], &time_key[2..])
}

fn compare_date_time(left: &ConversationNoteRecord, right: &ConversationNoteRecord) -> Ordering {
    left.date
        .cmp(&right.date)
        .then_with(|| left.time.cmp(&right.time))
}
