use std::fs;
use std::io::{self, Cursor, Read, Write};
use std::path::PathBuf;

use codex_obsidian_sync_rs::parser::{
    RolloutParseError, load_rollout_records, load_rollout_records_from_offset,
    load_rollout_records_from_offset_with_limit, load_rollout_records_with_limit,
    read_rollout_records_with_limit,
};
use serde_json::json;

#[test]
fn loads_complete_jsonl_object_records() {
    let root = temp_dir("complete-records");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(
        &rollout_path,
        br#"
{"timestamp":"2026-05-15T00:00:00Z","type":"session_meta"}
{"timestamp":"2026-05-15T00:00:01Z","type":"response_item"}
"#,
    )
    .unwrap();

    let records = load_rollout_records(&rollout_path).unwrap();

    assert_eq!(records.len(), 2);
    assert_eq!(records[0].get("type"), Some(&json!("session_meta")));
    assert_eq!(records[1].get("type"), Some(&json!("response_item")));
}

#[test]
fn ignores_trailing_partial_json_record() {
    let root = temp_dir("trailing-partial");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(
        &rollout_path,
        br#"{"type":"complete","payload":{"ok":true}}
{"type":"partial""#,
    )
    .unwrap();

    let records = load_rollout_records(&rollout_path).unwrap();

    assert_eq!(records.len(), 1);
    assert_eq!(records[0].get("type"), Some(&json!("complete")));
}

#[test]
fn full_reader_parses_complete_final_record_without_trailing_newline() {
    let root = temp_dir("final-record-no-newline");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(&rollout_path, br#"{"type":"complete"}"#).unwrap();

    let records = load_rollout_records(&rollout_path).unwrap();

    assert_eq!(records.len(), 1);
    assert_eq!(records[0].get("type"), Some(&json!("complete")));
}

#[test]
fn offset_reader_waits_for_complete_line() {
    let root = temp_dir("offset-partial");
    let rollout_path = root.join("rollout.jsonl");
    let first_record =
        br#"{"timestamp":"2026-03-25T06:21:17Z","type":"event_msg","payload":{"type":"ok"}}"#;
    let partial_record = br#"{"timestamp":"2026-03-25T06:21:18Z","type":"event_msg""#;
    fs::write(
        &rollout_path,
        [first_record.as_slice(), b"\n", partial_record].concat(),
    )
    .unwrap();

    let (records, next_offset) = load_rollout_records_from_offset(&rollout_path, 0).unwrap();

    assert_eq!(records.len(), 1);
    assert_eq!(records[0]["payload"]["type"], json!("ok"));
    assert!(next_offset < fs::metadata(&rollout_path).unwrap().len());

    fs::OpenOptions::new()
        .append(true)
        .open(&rollout_path)
        .unwrap()
        .write_all(br#","payload":{"type":"done"}}"#)
        .unwrap();
    fs::OpenOptions::new()
        .append(true)
        .open(&rollout_path)
        .unwrap()
        .write_all(b"\n")
        .unwrap();

    let (delta_records, final_offset) =
        load_rollout_records_from_offset(&rollout_path, next_offset).unwrap();

    assert_eq!(delta_records.len(), 1);
    assert_eq!(delta_records[0]["payload"]["type"], json!("done"));
    assert_eq!(final_offset, fs::metadata(&rollout_path).unwrap().len());
}

#[test]
fn offset_reader_counts_blank_complete_lines() {
    let root = temp_dir("offset-blank-lines");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(&rollout_path, b"\n\r\n{\"type\":\"one\"}\n").unwrap();

    let (records, next_offset) = load_rollout_records_from_offset(&rollout_path, 0).unwrap();

    assert_eq!(records.len(), 1);
    assert_eq!(records[0].get("type"), Some(&json!("one")));
    assert_eq!(next_offset, fs::metadata(&rollout_path).unwrap().len());
}

#[test]
fn stale_offset_beyond_eof_returns_same_offset() {
    let root = temp_dir("stale-offset");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(&rollout_path, b"").unwrap();

    let (records, next_offset) = load_rollout_records_from_offset(&rollout_path, 999).unwrap();

    assert!(records.is_empty());
    assert_eq!(next_offset, 999);
}

#[test]
fn crlf_lines_are_accepted() {
    let root = temp_dir("crlf");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(
        &rollout_path,
        b"{\"type\":\"one\"}\r\n{\"type\":\"two\"}\r\n",
    )
    .unwrap();

    let records = load_rollout_records(&rollout_path).unwrap();

    assert_eq!(records.len(), 2);
    assert_eq!(records[1].get("type"), Some(&json!("two")));
}

#[test]
fn full_reader_reports_partial_multibyte_eof() {
    let root = temp_dir("partial-multibyte");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(
        &rollout_path,
        b"{\"type\":\"complete\"}\n{\"text\":\"\xF0\x9F",
    )
    .unwrap();

    let error = load_rollout_records(&rollout_path).unwrap_err();

    assert_eq!(error, RolloutParseError::Utf8);
}

#[test]
fn offset_reader_ignores_partial_multibyte_eof() {
    let root = temp_dir("offset-partial-multibyte");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(
        &rollout_path,
        b"{\"type\":\"complete\"}\n{\"text\":\"\xF0\x9F",
    )
    .unwrap();

    let (records, next_offset) = load_rollout_records_from_offset(&rollout_path, 0).unwrap();

    assert_eq!(records.len(), 1);
    assert_eq!(records[0].get("type"), Some(&json!("complete")));
    assert!(next_offset < fs::metadata(&rollout_path).unwrap().len());
}

#[test]
fn complete_invalid_utf8_line_is_error() {
    let root = temp_dir("invalid-utf8");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(&rollout_path, b"{\"text\":\"\xF0\x9F\"}\n").unwrap();

    let error = load_rollout_records(&rollout_path).unwrap_err();

    assert_eq!(error, RolloutParseError::Utf8);
    assert_eq!(error.to_string(), "jsonl utf-8 error");
}

#[test]
fn malformed_json_is_not_oversized() {
    let root = temp_dir("malformed-json");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(&rollout_path, b"{\"type\":\n").unwrap();

    let error = load_rollout_records_with_limit(&rollout_path, 128).unwrap_err();

    assert_eq!(error, RolloutParseError::MalformedJson);
    assert_eq!(error.to_string(), "malformed jsonl record");
}

#[test]
fn oversized_complete_line_has_distinct_error() {
    let root = temp_dir("oversized-line");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(&rollout_path, b"{\"type\":\"too-long\"}\n").unwrap();

    let error = load_rollout_records_with_limit(&rollout_path, 8).unwrap_err();

    assert_eq!(error, RolloutParseError::LineTooLarge);
    assert_eq!(error.to_string(), "oversized jsonl record");
}

#[test]
fn non_object_json_records_are_rejected() {
    let root = temp_dir("non-object");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(&rollout_path, b"[]\n").unwrap();

    let error = load_rollout_records(&rollout_path).unwrap_err();

    assert_eq!(error, RolloutParseError::NonObjectRecord);
    assert_eq!(error.to_string(), "non-object jsonl record");
}

#[test]
fn duplicate_json_keys_follow_python_last_value_behavior() {
    let root = temp_dir("duplicate-json-keys");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(&rollout_path, br#"{"type":"first","type":"second"}"#).unwrap();
    fs::OpenOptions::new()
        .append(true)
        .open(&rollout_path)
        .unwrap()
        .write_all(b"\n")
        .unwrap();

    let records = load_rollout_records(&rollout_path).unwrap();

    assert_eq!(records.len(), 1);
    assert_eq!(records[0].get("type"), Some(&json!("second")));
}

#[test]
fn offset_reader_uses_complete_line_limit() {
    let root = temp_dir("offset-limit");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(&rollout_path, b"{\"type\":\"too-long\"}\n").unwrap();

    let error = load_rollout_records_from_offset_with_limit(&rollout_path, 0, 8).unwrap_err();

    assert_eq!(error, RolloutParseError::LineTooLarge);
}

#[test]
fn reader_path_uses_bounded_line_reads() {
    let reader = SlowReader::new(b"{\"type\":\"one\"}\n{\"type\":\"two\"}\n", 3);

    let records = read_rollout_records_with_limit(io::BufReader::new(reader), 32).unwrap();

    assert_eq!(records.len(), 2);
    assert_eq!(records[0].get("type"), Some(&json!("one")));
    assert_eq!(records[1].get("type"), Some(&json!("two")));
}

#[test]
fn parser_errors_do_not_include_paths_or_payload_text() {
    let root = temp_dir("content-free-error");
    let rollout_path = root.join("rollout.jsonl");
    fs::write(&rollout_path, b"{\"text\":\"secret transcript\"\n").unwrap();

    let error = load_rollout_records(&rollout_path).unwrap_err();
    let display = error.to_string();

    assert!(!display.contains("secret transcript"));
    assert!(!display.contains(rollout_path.to_string_lossy().as_ref()));
}

struct SlowReader {
    inner: Cursor<Vec<u8>>,
    max_chunk_size: usize,
}

impl SlowReader {
    fn new(bytes: &[u8], max_chunk_size: usize) -> Self {
        Self {
            inner: Cursor::new(bytes.to_vec()),
            max_chunk_size,
        }
    }
}

impl Read for SlowReader {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let limit = buf.len().min(self.max_chunk_size);
        self.inner.read(&mut buf[..limit])
    }
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-parser-{name}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let path = std::env::temp_dir().join(unique);
    fs::create_dir_all(&path).unwrap();
    path
}
