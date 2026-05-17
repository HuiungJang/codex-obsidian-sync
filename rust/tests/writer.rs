use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use codex_obsidian_sync_rs::discovery::{build_session_envelope, load_session_index};
use codex_obsidian_sync_rs::models::TranscriptMessage;
use codex_obsidian_sync_rs::parser::build_message_key;
use codex_obsidian_sync_rs::render::{
    MANAGED_END, MANAGED_START, RenderOptions, TRANSCRIPT_MARKER,
    build_conversation_note_record_with_options, render_conversation_header,
    render_conversation_note, render_daily_managed_section, render_daily_note_header,
    render_project_managed_section, render_project_note_header,
};
use codex_obsidian_sync_rs::writer::{
    RealWriter, validate_vault_root, write_atomic, write_state_file,
};
use time::{OffsetDateTime, UtcOffset};

#[test]
fn validate_vault_root_requires_absolute_existing_directory() {
    let root = temp_dir("validate-root");
    assert_eq!(validate_vault_root(&root).unwrap(), root);

    assert!(validate_vault_root(Path::new("relative/path")).is_err());
    assert!(validate_vault_root(&root.join("missing")).is_err());
    fs::write(root.join("file.md"), "x").unwrap();
    assert!(validate_vault_root(&root.join("file.md")).is_err());
}

#[test]
fn resolve_note_path_rejects_traversal_target_symlink_and_parent_escape() {
    let root = temp_dir("path-safety");
    let writer = RealWriter::prepare(&root).unwrap();
    let notes_dir = root.join("Codex").join("Conversations");
    fs::create_dir_all(&notes_dir).unwrap();
    let target = notes_dir.join("linked.md");
    symlink_file(root.join("outside.md"), &target);

    assert!(writer.resolve_note_path("../escape.md").is_err());
    assert!(writer.resolve_note_path("/absolute.md").is_err());
    assert!(
        writer
            .resolve_note_path("Codex/Conversations/linked.md")
            .is_err()
    );

    let outside = temp_dir("outside-parent");
    let linked_parent = root.join("Linked");
    symlink_dir(&outside, &linked_parent);
    assert!(writer.resolve_note_path("Linked/new/escape.md").is_err());
    assert!(!outside.join("new").exists());
}

#[test]
fn write_atomic_replaces_content_and_leaves_no_temp_file() {
    let root = temp_dir("atomic");
    let target = root.join("note.md");
    write_atomic(&target, "first\n").unwrap();
    write_atomic(&target, "second\n").unwrap();

    assert_eq!(fs::read_to_string(&target).unwrap(), "second\n");
    let temp_files = fs::read_dir(&root)
        .unwrap()
        .filter_map(Result::ok)
        .filter(|entry| {
            entry
                .file_name()
                .to_string_lossy()
                .starts_with(".codex-obsidian-sync-rs-")
        })
        .count();
    assert_eq!(temp_files, 0);
}

#[test]
fn write_managed_file_writes_conversation_note_content() {
    let root = temp_dir("managed-file");
    let writer = RealWriter::prepare(&root).unwrap();

    let target = writer
        .write_managed_file("Codex/Conversations/2026/demo.md", "conversation\n")
        .unwrap();

    assert_eq!(fs::read_to_string(target).unwrap(), "conversation\n");
}

#[test]
fn real_writer_materializes_baseline_notes_byte_for_byte() {
    let repo_root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let baseline = repo_root.join("baseline");
    let fixture = baseline
        .join("fixtures")
        .join("synthetic")
        .join("codex-home");
    let session_index = load_session_index(&fixture.join("session_index.jsonl")).unwrap();
    let writer = RealWriter::prepare(&temp_dir("baseline-notes")).unwrap();
    let render_options = RenderOptions {
        local_offset_override: Some(UtcOffset::from_hms(9, 0, 0).unwrap()),
        now_utc: OffsetDateTime::UNIX_EPOCH,
    };
    let rollouts = [
        (
            "rollout-2026-05-14T09-00-00-019f5f16-0000-7000-8000-000000000001.jsonl",
            appended_message("2026-05-14T00:02:00Z", "Baseline appended response"),
        ),
        (
            "rollout-2026-05-14T09-30-00-019f5f16-0000-7000-8000-000000000004.jsonl",
            appended_message("2026-05-14T00:31:00Z", "Stale offset recovered response"),
        ),
    ];
    let mut notes = Vec::new();

    for (rollout_name, appended) in rollouts {
        let rollout = fixture
            .join("sessions")
            .join("2026")
            .join("05")
            .join("14")
            .join(rollout_name);
        let envelope = build_session_envelope(&rollout, &session_index).unwrap();
        let note = build_conversation_note_record_with_options(&envelope, &render_options).unwrap();
        writer
            .write_managed_file(
                &note.conversation_note_path,
                &render_conversation_note(&envelope, &note),
            )
            .unwrap();

        let mut final_envelope = envelope.clone();
        final_envelope.messages.push(appended.clone());
        let final_note =
            build_conversation_note_record_with_options(&final_envelope, &render_options).unwrap();
        writer
            .rewrite_conversation_header(
                &final_note.conversation_note_path,
                &render_conversation_header(&final_envelope, &final_note),
            )
            .unwrap();
        writer
            .append_conversation_transcript(&final_note.conversation_note_path, &[appended])
            .unwrap();
        notes.push(final_note);
    }

    let mut daily_notes = notes.clone();
    daily_notes.sort_by(|left, right| {
        (left.time.as_str(), left.title.as_str()).cmp(&(right.time.as_str(), right.title.as_str()))
    });
    writer
        .write_sectioned_note(
            "Codex/Daily/2026-05-14.md",
            &render_daily_note_header("2026-05-14"),
            &render_daily_managed_section(&daily_notes),
        )
        .unwrap();

    let mut project_notes = notes.clone();
    project_notes.sort_by(|left, right| {
        (
            right.date.as_str(),
            right.time.as_str(),
            right.title.as_str(),
        )
            .cmp(&(left.date.as_str(), left.time.as_str(), left.title.as_str()))
    });
    writer
        .write_sectioned_note(
            "Codex/Projects/baseline-project.md",
            &render_project_note_header("baseline-project", &project_notes).unwrap(),
            &render_project_managed_section(&project_notes),
        )
        .unwrap();

    for relative in [
        "Codex/Conversations/2026/2026-05-14-0900-baseline-first-question.md",
        "Codex/Conversations/2026/2026-05-14-0930-stale-offset-first-question.md",
        "Codex/Daily/2026-05-14.md",
        "Codex/Projects/baseline-project.md",
    ] {
        assert_eq!(
            fs::read_to_string(writer.resolve_note_path(relative).unwrap()).unwrap(),
            fs::read_to_string(baseline.join("notes").join(relative)).unwrap(),
            "baseline mismatch for {relative}"
        );
    }
}

fn appended_message(timestamp: &str, text: &str) -> TranscriptMessage {
    TranscriptMessage {
        message_key: build_message_key(timestamp, "assistant", "final_answer", text),
        role: "assistant".to_owned(),
        phase: "final_answer".to_owned(),
        timestamp: timestamp.to_owned(),
        text: text.to_owned(),
    }
}

#[test]
fn write_sectioned_note_preserves_user_content_and_replaces_managed_block() {
    let root = temp_dir("sectioned");
    let writer = RealWriter::prepare(&root).unwrap();
    let relative = "Codex/Daily/2026-05-16.md";
    let target = writer.resolve_note_path(relative).unwrap();
    fs::write(
        &target,
        format!("# Daily\n\nUser notes stay.\n\n{MANAGED_START}\nold\n{MANAGED_END}\n"),
    )
    .unwrap();

    writer
        .write_sectioned_note(relative, "# Daily", "new managed content")
        .unwrap();
    let content = fs::read_to_string(target).unwrap();

    assert!(content.contains("User notes stay."));
    assert!(content.contains("new managed content"));
    assert!(!content.contains("\nold\n"));
}

#[test]
fn append_conversation_transcript_appends_without_replacing_file() {
    let root = temp_dir("append");
    let writer = RealWriter::prepare(&root).unwrap();
    let relative = "Codex/Conversations/2026/demo.md";
    let target = writer.resolve_note_path(relative).unwrap();
    fs::write(&target, format!("# Demo\n\n{TRANSCRIPT_MARKER}\n")).unwrap();
    let before_fingerprint = writer.fingerprint_relative(relative).unwrap().unwrap();

    let appended = writer
        .append_conversation_transcript(
            relative,
            &[TranscriptMessage {
                message_key: "abc".to_owned(),
                role: "user".to_owned(),
                phase: String::new(),
                timestamp: "2026-04-03T00:00:00Z".to_owned(),
                text: "append body".to_owned(),
            }],
        )
        .unwrap();

    let after = fs::read_to_string(&target).unwrap();
    let after_fingerprint = writer.fingerprint_relative(relative).unwrap().unwrap();
    assert!(appended);
    assert!(after.contains("### User"));
    assert!(after.contains("append body"));
    assert!(after_fingerprint.size > before_fingerprint.size);
}

#[test]
fn append_conversation_transcript_matches_python_append_create_behavior() {
    let root = temp_dir("append-create");
    let writer = RealWriter::prepare(&root).unwrap();
    let relative = "Codex/Conversations/2026/new.md";

    let appended = writer
        .append_conversation_transcript(
            relative,
            &[TranscriptMessage {
                message_key: "abc".to_owned(),
                role: "assistant".to_owned(),
                phase: String::new(),
                timestamp: "2026-04-03T00:00:00Z".to_owned(),
                text: "created by append".to_owned(),
            }],
        )
        .unwrap();

    let content = fs::read_to_string(root.join(relative)).unwrap();
    assert!(appended);
    assert!(content.contains("### Assistant"));
    assert!(content.contains("created by append"));
}

#[test]
fn rewrite_conversation_header_preserves_transcript_body() {
    let root = temp_dir("rewrite-header");
    let writer = RealWriter::prepare(&root).unwrap();
    let relative = "Codex/Conversations/2026/demo.md";
    let target = writer.resolve_note_path(relative).unwrap();
    fs::write(&target, format!("old header\n{TRANSCRIPT_MARKER}\nbody\n")).unwrap();

    writer
        .rewrite_conversation_header(relative, &format!("new header\n{TRANSCRIPT_MARKER}\n"))
        .unwrap();

    assert_eq!(
        fs::read_to_string(target).unwrap(),
        format!("new header\n{TRANSCRIPT_MARKER}\nbody\n")
    );
}

#[test]
fn write_state_file_creates_parent_and_rejects_relative_or_symlink_target() {
    let root = temp_dir("state");
    let state = root.join("state").join("sync-state.json");
    write_state_file(&state, "{}\n").unwrap();
    assert_eq!(fs::read_to_string(&state).unwrap(), "{}\n");

    assert!(write_state_file(Path::new("relative-state.json"), "{}\n").is_err());
    let linked = root.join("linked-state.json");
    symlink_file(root.join("outside-state.json"), &linked);
    assert!(write_state_file(&linked, "{}\n").is_err());

    let outside = temp_dir("outside-state-parent");
    let linked_parent = root.join("linked-state-parent");
    symlink_dir(&outside, &linked_parent);
    assert!(write_state_file(&linked_parent.join("sync-state.json"), "{}\n").is_err());
    assert!(!outside.join("sync-state.json").exists());
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-writer-{name}-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let path = std::env::temp_dir().join(unique);
    if path.exists() {
        fs::remove_dir_all(&path).unwrap();
    }
    fs::create_dir_all(&path).unwrap();
    fs::canonicalize(path).unwrap()
}

#[cfg(unix)]
fn symlink_file(target: impl AsRef<Path>, link: impl AsRef<Path>) {
    std::os::unix::fs::symlink(target, link).unwrap();
}

#[cfg(unix)]
fn symlink_dir(target: impl AsRef<Path>, link: impl AsRef<Path>) {
    std::os::unix::fs::symlink(target, link).unwrap();
}

#[cfg(not(unix))]
fn symlink_file(_target: impl AsRef<Path>, link: impl AsRef<Path>) {
    fs::write(link, "not a symlink").unwrap();
}

#[cfg(not(unix))]
fn symlink_dir(target: impl AsRef<Path>, link: impl AsRef<Path>) {
    let _ = target;
    fs::create_dir(link).unwrap();
}
