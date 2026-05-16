use std::fs::File;
use std::io::{BufRead, BufReader, Seek, SeekFrom};
use std::path::Path;

use serde_json::{Map, Value};
use sha1::{Digest, Sha1};
use thiserror::Error;

use crate::models::{SessionMeta, SourceClassification, TranscriptMessage};

pub type RolloutRecord = Map<String, Value>;

pub const DEFAULT_MAX_JSONL_LINE_BYTES: usize = 16 * 1024 * 1024;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum RolloutParseError {
    #[error("jsonl io error")]
    Io,

    #[error("jsonl utf-8 error")]
    Utf8,

    #[error("malformed jsonl record")]
    MalformedJson,

    #[error("non-object jsonl record")]
    NonObjectRecord,

    #[error("oversized jsonl record")]
    LineTooLarge,
}

pub fn load_rollout_records(path: &Path) -> Result<Vec<RolloutRecord>, RolloutParseError> {
    load_rollout_records_with_limit(path, DEFAULT_MAX_JSONL_LINE_BYTES)
}

pub fn load_rollout_records_with_limit(
    path: &Path,
    max_line_bytes: usize,
) -> Result<Vec<RolloutRecord>, RolloutParseError> {
    let file = File::open(path).map_err(|_| RolloutParseError::Io)?;
    read_rollout_records_with_limit(BufReader::new(file), max_line_bytes)
}

pub fn read_rollout_records_with_limit<R: BufRead>(
    mut reader: R,
    max_line_bytes: usize,
) -> Result<Vec<RolloutRecord>, RolloutParseError> {
    let mut records = Vec::new();
    read_jsonl_lines(
        &mut reader,
        max_line_bytes,
        UnterminatedLine::ParseThenIgnoreMalformed,
        |_, trimmed_line, _| {
            if !trimmed_line.is_empty() {
                records.push(parse_record(trimmed_line)?);
            }
            Ok(())
        },
    )?;
    Ok(records)
}

pub fn load_rollout_records_from_offset(
    path: &Path,
    offset: u64,
) -> Result<(Vec<RolloutRecord>, u64), RolloutParseError> {
    load_rollout_records_from_offset_with_limit(path, offset, DEFAULT_MAX_JSONL_LINE_BYTES)
}

pub fn load_rollout_records_from_offset_with_limit(
    path: &Path,
    offset: u64,
    max_line_bytes: usize,
) -> Result<(Vec<RolloutRecord>, u64), RolloutParseError> {
    let mut file = File::open(path).map_err(|_| RolloutParseError::Io)?;
    file.seek(SeekFrom::Start(offset))
        .map_err(|_| RolloutParseError::Io)?;
    let mut reader = BufReader::new(file);
    let mut records = Vec::new();
    let mut next_offset = offset;

    read_jsonl_lines(
        &mut reader,
        max_line_bytes,
        UnterminatedLine::Ignore,
        |raw_line, trimmed_line, _| {
            next_offset += raw_line.len() as u64;
            if !trimmed_line.is_empty() {
                records.push(parse_record(trimmed_line)?);
            }
            Ok(())
        },
    )?;

    Ok((records, next_offset))
}

pub fn collect_session_metas(records: &[RolloutRecord]) -> Vec<SessionMeta> {
    records
        .iter()
        .filter_map(session_meta_from_record)
        .filter(|meta| !meta.session_id.is_empty())
        .collect()
}

pub fn extract_candidate_messages(records: &[RolloutRecord]) -> Vec<TranscriptMessage> {
    let mut messages = Vec::new();
    let mut seen_keys = std::collections::BTreeSet::new();

    for record in records {
        if let Some(message) = extract_candidate_message(record, &mut seen_keys) {
            messages.push(message);
        }
    }

    messages
}

pub fn classify_source(source: Option<&Value>) -> SourceClassification {
    if let Some(Value::String(source_kind)) = source {
        if !source_kind.trim().is_empty() {
            return SourceClassification {
                source_kind: source_kind.clone(),
                is_subagent: false,
                parent_session_id: None,
            };
        }
    }

    if let Some(source) = source.and_then(Value::as_object) {
        let thread_spawn = source
            .get("subagent")
            .and_then(Value::as_object)
            .and_then(|subagent| subagent.get("thread_spawn"))
            .and_then(Value::as_object);
        if let Some(thread_spawn) = thread_spawn.filter(|thread_spawn| !thread_spawn.is_empty()) {
            return SourceClassification {
                source_kind: "subagent.thread_spawn".to_owned(),
                is_subagent: true,
                parent_session_id: thread_spawn
                    .get("parent_thread_id")
                    .and_then(string_or_none),
            };
        }
    }

    SourceClassification::default()
}

pub fn extract_text(content: Option<&Value>) -> String {
    let Some(items) = content.and_then(Value::as_array) else {
        return String::new();
    };

    items
        .iter()
        .filter_map(Value::as_object)
        .filter(|item| {
            item.get("type")
                .and_then(Value::as_str)
                .is_some_and(|item_type| matches!(item_type, "input_text" | "output_text"))
        })
        .filter_map(|item| item.get("text").and_then(string_or_none))
        .map(|text| normalize_text_block(&text))
        .filter(|text| !text.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}

pub fn build_message_key(timestamp: &str, role: &str, phase: &str, text: &str) -> String {
    let normalized = normalize_for_hash(text);
    let mut hasher = Sha1::new();
    hasher.update(format!("{timestamp}\n{role}\n{phase}\n{normalized}").as_bytes());
    hex_lower(&hasher.finalize())
}

pub fn normalize_for_hash(text: &str) -> String {
    let mut normalized = String::new();
    let mut previous_was_whitespace = false;

    for character in text.trim().chars() {
        if character.is_whitespace() {
            if !previous_was_whitespace {
                normalized.push(' ');
                previous_was_whitespace = true;
            }
        } else {
            normalized.push(character);
            previous_was_whitespace = false;
        }
    }

    normalized
}

pub fn is_control_message(text: &str) -> bool {
    let stripped = text.trim();
    stripped.starts_with("# AGENTS.md instructions")
        || stripped.starts_with("<environment_context>")
        || stripped.starts_with("<subagent_notification>")
        || stripped.starts_with("<turn_aborted>")
}

fn session_meta_from_record(record: &RolloutRecord) -> Option<SessionMeta> {
    if record.get("type")?.as_str()? != "session_meta" {
        return None;
    }
    let payload = record.get("payload").and_then(Value::as_object)?;
    Some(SessionMeta {
        session_id: payload
            .get("id")
            .map(string_value)
            .unwrap_or_default()
            .to_owned(),
        timestamp: payload.get("timestamp").and_then(string_or_none),
        cwd: payload.get("cwd").and_then(string_or_none),
        originator: payload.get("originator").and_then(string_or_none),
        source_classification: classify_source(payload.get("source")),
        forked_from_id: payload.get("forked_from_id").and_then(string_or_none),
    })
}

fn extract_candidate_message(
    record: &RolloutRecord,
    seen_keys: &mut std::collections::BTreeSet<String>,
) -> Option<TranscriptMessage> {
    if record.get("type")?.as_str()? != "response_item" {
        return None;
    }

    let payload = record.get("payload").and_then(Value::as_object)?;
    if payload.get("type")?.as_str()? != "message" {
        return None;
    }

    let role = payload.get("role").and_then(string_or_none)?;
    if !matches!(role.as_str(), "user" | "assistant") {
        return None;
    }

    let phase = payload
        .get("phase")
        .and_then(string_or_none)
        .unwrap_or_default();
    if role == "assistant" && !matches!(phase.as_str(), "" | "final_answer") {
        return None;
    }

    let text = extract_text(payload.get("content"));
    if text.is_empty() {
        return None;
    }
    if role == "user" && is_control_message(&text) {
        return None;
    }

    let timestamp = record
        .get("timestamp")
        .and_then(string_or_none)
        .unwrap_or_default();
    let message_key = build_message_key(&timestamp, &role, &phase, &text);
    if !seen_keys.insert(message_key.clone()) {
        return None;
    }

    Some(TranscriptMessage {
        message_key,
        role,
        phase,
        timestamp,
        text,
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum UnterminatedLine {
    Ignore,
    ParseThenIgnoreMalformed,
}

fn read_jsonl_lines<R, F>(
    reader: &mut R,
    max_line_bytes: usize,
    unterminated_line: UnterminatedLine,
    mut on_line: F,
) -> Result<(), RolloutParseError>
where
    R: BufRead,
    F: FnMut(&[u8], &[u8], bool) -> Result<(), RolloutParseError>,
{
    let mut line = Vec::new();

    loop {
        let bytes_read = read_line_limited(reader, &mut line, max_line_bytes)?;
        if bytes_read == 0 {
            return Ok(());
        }

        let is_complete = line.ends_with(b"\n");
        if !is_complete && unterminated_line == UnterminatedLine::Ignore {
            return Ok(());
        }

        let trimmed = trim_jsonl_whitespace(&line);
        let line_result = on_line(&line, trimmed, is_complete);
        match line_result {
            Ok(()) => {}
            Err(RolloutParseError::MalformedJson)
                if !is_complete
                    && unterminated_line == UnterminatedLine::ParseThenIgnoreMalformed =>
            {
                return Ok(());
            }
            Err(error) => return Err(error),
        }

        if !is_complete {
            return Ok(());
        }
    }
}

fn read_line_limited<R: BufRead>(
    reader: &mut R,
    line: &mut Vec<u8>,
    max_line_bytes: usize,
) -> Result<usize, RolloutParseError> {
    line.clear();
    let mut total = 0;

    loop {
        let (take, finished) = {
            let available = reader.fill_buf().map_err(|_| RolloutParseError::Io)?;
            if available.is_empty() {
                return Ok(total);
            }

            let take = match available.iter().position(|byte| *byte == b'\n') {
                Some(position) => position + 1,
                None => available.len(),
            };

            if total + take > max_line_bytes {
                return Err(RolloutParseError::LineTooLarge);
            }

            line.extend_from_slice(&available[..take]);
            (take, available.get(take - 1) == Some(&b'\n'))
        };

        reader.consume(take);
        total += take;

        if finished {
            return Ok(total);
        }
    }
}

fn parse_record(line: &[u8]) -> Result<RolloutRecord, RolloutParseError> {
    let line = std::str::from_utf8(line).map_err(|_| RolloutParseError::Utf8)?;
    let value =
        serde_json::from_str::<Value>(line).map_err(|_| RolloutParseError::MalformedJson)?;
    match value {
        Value::Object(record) => Ok(record),
        _ => Err(RolloutParseError::NonObjectRecord),
    }
}

fn normalize_text_block(text: &str) -> String {
    python_splitlines(text)
        .into_iter()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

fn python_splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut characters = text.char_indices().peekable();

    while let Some((index, character)) = characters.next() {
        if !is_python_line_boundary(character) {
            continue;
        }

        lines.push(&text[start..index]);
        start = index + character.len_utf8();
        if character == '\r' {
            if let Some(&(next_index, '\n')) = characters.peek() {
                characters.next();
                start = next_index + '\n'.len_utf8();
            }
        }
    }

    lines.push(&text[start..]);
    lines
}

fn is_python_line_boundary(character: char) -> bool {
    matches!(
        character,
        '\n' | '\r'
            | '\u{000B}'
            | '\u{000C}'
            | '\u{001C}'
            | '\u{001D}'
            | '\u{001E}'
            | '\u{0085}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

fn string_value(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        _ => value.to_string(),
    }
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

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn trim_jsonl_whitespace(line: &[u8]) -> &[u8] {
    let start = line
        .iter()
        .position(|byte| !byte.is_ascii_whitespace())
        .unwrap_or(line.len());
    let end = line
        .iter()
        .rposition(|byte| !byte.is_ascii_whitespace())
        .map(|position| position + 1)
        .unwrap_or(start);
    &line[start..end]
}
