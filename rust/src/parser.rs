use std::fs::File;
use std::io::{BufRead, BufReader, Seek, SeekFrom};
use std::path::Path;

use serde_json::{Map, Value};
use thiserror::Error;

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
