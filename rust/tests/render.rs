use std::path::PathBuf;

use codex_obsidian_sync_rs::models::{SessionEnvelope, TranscriptMessage};
use codex_obsidian_sync_rs::redaction::redact_text;
use codex_obsidian_sync_rs::render::{
    CONVERSATION_NOTE_MARKER, DailyEntry, MANAGED_END, MANAGED_START, RenderOptions,
    TRANSCRIPT_MARKER, build_conversation_note_record_with_options, extract_transcript_body,
    merge_managed_section, obsidian_target, render_conversation_header, render_conversation_note,
    render_daily_managed_section, render_daily_note_header, render_project_managed_section,
    render_project_note_header, render_transcript_append_text,
};
use time::{OffsetDateTime, UtcOffset};

#[test]
fn build_conversation_note_record_uses_local_time_daily_entries_and_status() {
    let envelope = sample_envelope();
    let options = render_options();

    let note = build_conversation_note_record_with_options(&envelope, &options).unwrap();

    assert_eq!(
        note.conversation_note_path,
        "Codex/Conversations/2026/2026-04-03-0915-demo-thread.md"
    );
    assert_eq!(note.daily_note_path, "Codex/Daily/2026-04-04.md");
    assert_eq!(note.project_note_path, "Codex/Projects/demo-project.md");
    assert_eq!(note.date, "2026-04-04");
    assert_eq!(note.time, "09:05");
    assert_eq!(note.started_date, "2026-04-03");
    assert_eq!(note.started_time, "09:15");
    assert_eq!(note.title, "Demo Thread");
    assert_eq!(note.status, "completed");
    assert_eq!(
        note.daily_entries,
        vec![
            DailyEntry {
                date: "2026-04-03".to_owned(),
                time: "10:45".to_owned(),
            },
            DailyEntry {
                date: "2026-04-04".to_owned(),
                time: "09:05".to_owned(),
            },
        ]
    );
}

#[test]
fn build_conversation_note_record_accepts_timezone_less_local_timestamps() {
    let mut envelope = sample_envelope();
    envelope.started_at = Some("2026-04-03T00:15:00".to_owned());
    envelope.updated_at = Some("2026-04-03T23:05:00".to_owned());
    envelope.messages = vec![TranscriptMessage {
        message_key: "m1".to_owned(),
        role: "user".to_owned(),
        phase: String::new(),
        timestamp: "2026-04-03T23:05:00".to_owned(),
        text: "local naive".to_owned(),
    }];

    let note = build_conversation_note_record_with_options(&envelope, &render_options()).unwrap();

    assert_eq!(
        note.conversation_note_path,
        "Codex/Conversations/2026/2026-04-03-0015-demo-thread.md"
    );
    assert_eq!(note.daily_note_path, "Codex/Daily/2026-04-03.md");
    assert_eq!(note.date, "2026-04-03");
    assert_eq!(note.time, "23:05");
    assert_eq!(
        note.daily_entries,
        vec![DailyEntry {
            date: "2026-04-03".to_owned(),
            time: "23:05".to_owned(),
        }]
    );
}

#[test]
fn render_conversation_header_matches_python_frontmatter() {
    let envelope = sample_envelope();
    let note = build_conversation_note_record_with_options(&envelope, &render_options()).unwrap();

    let header = render_conversation_header(&envelope, &note);

    assert_eq!(
        header,
        [
            "---",
            "type: codex-conversation",
            "date: 2026-04-03",
            "session_id: 019d23a7-9258-7810-93cc-c6833b348201",
            "originator: Codex Desktop",
            "project_slug: demo-project",
            "daily_note: Codex/Daily/2026-04-04.md",
            "status: completed",
            "tags:",
            "  - codex",
            "  - codex-conversation",
            "  - project/demo-project",
            "---",
            "",
            CONVERSATION_NOTE_MARKER,
            "",
            "# Demo Thread",
            "",
            "[[Codex/Daily/2026-04-04]]",
            "[[Codex/Projects/demo-project]]",
            "",
            "## Transcript",
            "",
            TRANSCRIPT_MARKER,
            "",
        ]
        .join("\n")
    );
}

#[test]
fn render_conversation_note_redacts_transcript_text() {
    let envelope = sample_envelope();
    let note = build_conversation_note_record_with_options(&envelope, &render_options()).unwrap();

    let content = render_conversation_note(&envelope, &note);

    assert!(content.contains("### User\nUse Authorization: [REDACTED_BEARER_TOKEN]"));
    assert!(content.contains("### Assistant\nStored AWS_SECRET_ACCESS_KEY=\"[REDACTED_SECRET]\""));
    assert!(content.ends_with('\n'));
    assert!(!content.contains("abc.def+/ghi=="));
    assert!(!content.contains("wJalrXUtnFEMI"));
}

#[test]
fn render_transcript_append_text_wraps_non_empty_content() {
    let message = TranscriptMessage {
        message_key: "m1".to_owned(),
        role: "user".to_owned(),
        phase: String::new(),
        timestamp: "2026-04-03T00:20:00Z".to_owned(),
        text: "append body".to_owned(),
    };

    let content = render_transcript_append_text(&[message]).unwrap();

    assert_eq!(content, "\n### User\nappend body\n");
    assert_eq!(render_transcript_append_text(&[]), None);
}

#[test]
fn render_daily_and_project_managed_sections_are_byte_identical() {
    let records = sample_records();

    assert_eq!(
        render_daily_managed_section(&records),
        [
            "## 09:05 Demo Thread",
            "> [!note]- Conversation",
            "> [[Codex/Conversations/2026/2026-04-03-0915-demo-thread|Demo Thread]]",
            "> Project: [[Codex/Projects/demo-project|demo-project]]",
            "> Session: `019d23a7-9258-7810-93cc-c6833b348201`",
            "> Status: `completed`",
        ]
        .join("\n")
    );
    assert_eq!(
        render_project_managed_section(&records),
        [
            "## Conversations",
            "",
            "- 2026-04-04 09:05 [[Codex/Conversations/2026/2026-04-03-0915-demo-thread|Demo Thread]]",
        ]
        .join("\n")
    );
}

#[test]
fn render_daily_and_project_headers_match_python_shape() {
    let records = sample_records();

    assert_eq!(
        render_daily_note_header("2026-04-04"),
        "# Codex Daily Log - 2026-04-04"
    );
    assert_eq!(
        render_project_note_header("demo-project", &records).unwrap(),
        [
            "---",
            "type: codex-project",
            "project_slug: demo-project",
            "first_seen_at: 2026-04-03T00:15:00Z",
            "---",
            "",
            "# demo-project",
        ]
        .join("\n")
    );
}

#[test]
fn obsidian_target_strips_markdown_suffix_only() {
    assert_eq!(
        obsidian_target("Codex/Daily/2026-04-04.md"),
        "Codex/Daily/2026-04-04"
    );
    assert_eq!(obsidian_target("Codex/Daily/README"), "Codex/Daily/README");
}

#[test]
fn merge_managed_section_matches_marker_replacement_behavior() {
    let existing =
        format!("# Daily\n\nUser notes stay.\n\n{MANAGED_START}\nold\n{MANAGED_END}\n\nTail\n");
    let merged = merge_managed_section(&existing, "new managed content");

    assert_eq!(
        merged,
        format!(
            "# Daily\n\nUser notes stay.\n\n{MANAGED_START}\nnew managed content\n{MANAGED_END}\nTail\n"
        )
    );
}

#[test]
fn merge_managed_section_keeps_malformed_and_duplicate_marker_scenarios() {
    let malformed = format!("# Daily\n\n{MANAGED_START}\nold\n");
    assert_eq!(
        merge_managed_section(&malformed, "new"),
        format!("# Daily\n\n{MANAGED_START}\nold\n\n{MANAGED_START}\nnew\n{MANAGED_END}\n")
    );

    let duplicate = format!(
        "{MANAGED_START}\nold one\n{MANAGED_END}\n\n{MANAGED_START}\nold two\n{MANAGED_END}\n"
    );
    assert_eq!(
        merge_managed_section(&duplicate, "new"),
        format!(
            "\n\n{MANAGED_START}\nnew\n{MANAGED_END}\n{MANAGED_START}\nold two\n{MANAGED_END}\n"
        )
    );
}

#[test]
fn extract_transcript_body_matches_marker_behavior() {
    assert_eq!(
        extract_transcript_body(&format!("# Note\n\n{TRANSCRIPT_MARKER}\nbody\n")),
        Some("body\n".to_owned())
    );
    assert_eq!(extract_transcript_body("# Note\n\nmissing\n"), None);
}

#[test]
fn redaction_matches_common_provider_and_assignment_shapes() {
    let github_token = format!("ghp_{}", "a".repeat(36));
    let github_fine_grained_token = format!("github_pat_{}_{}", "A".repeat(22), "B".repeat(59));
    let aws_access_key = format!("AKIA{}", "A".repeat(16));
    let slack_token = concat!(
        "xox",
        "b-123456789012-123456789012-abcdefghijklmnopqrstuvwx"
    );
    let npm_token = format!("npm_{}", "b".repeat(36));
    let google_api_key = format!("AIza{}", "C".repeat(35));
    let stripe_key = format!("sk_live_{}", "d".repeat(24));
    let jwt_token = format!(
        "eyJ{}.eyJ{}.{}",
        "e".repeat(20),
        "f".repeat(20),
        "g".repeat(20)
    );
    let redacted = redact_text(
        &[
            github_token.as_str(),
            github_fine_grained_token.as_str(),
            aws_access_key.as_str(),
            slack_token,
            npm_token.as_str(),
            google_api_key.as_str(),
            stripe_key.as_str(),
            jwt_token.as_str(),
        ]
        .join("\n"),
    );

    for token in [
        github_token.as_str(),
        github_fine_grained_token.as_str(),
        aws_access_key.as_str(),
        slack_token,
        npm_token.as_str(),
        google_api_key.as_str(),
        stripe_key.as_str(),
        jwt_token.as_str(),
    ] {
        assert!(!redacted.contains(token));
    }
    assert!(redacted.contains("[REDACTED_GITHUB_TOKEN]"));
    assert!(redacted.contains("[REDACTED_AWS_ACCESS_KEY]"));
    assert!(redacted.contains("[REDACTED_SLACK_TOKEN]"));
    assert!(redacted.contains("[REDACTED_NPM_TOKEN]"));
    assert!(redacted.contains("[REDACTED_GOOGLE_API_KEY]"));
    assert!(redacted.contains("[REDACTED_STRIPE_SECRET_KEY]"));
    assert!(redacted.contains("[REDACTED_JWT]"));

    assert_eq!(
        redact_text("Authorization: Bearer abc.def+/ghi=="),
        "Authorization: [REDACTED_BEARER_TOKEN]"
    );
    assert_eq!(
        redact_text("`Authorization: Bearer abc.def+/ghi==`"),
        "`Authorization: [REDACTED_BEARER_TOKEN]`"
    );
    assert_eq!(
        redact_text("AWS_SECRET_ACCESS_KEY=\"wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY\""),
        "AWS_SECRET_ACCESS_KEY=\"[REDACTED_SECRET]\""
    );
    assert_eq!(redact_text("SECRET_KEY='abcdef\""), "SECRET_KEY='abcdef\"");
}

fn sample_records() -> Vec<codex_obsidian_sync_rs::render::ConversationNoteRecord> {
    let envelope = sample_envelope();
    vec![build_conversation_note_record_with_options(&envelope, &render_options()).unwrap()]
}

fn sample_envelope() -> SessionEnvelope {
    SessionEnvelope {
        canonical_session_id: "019d23a7-9258-7810-93cc-c6833b348201".to_owned(),
        rollout_path: PathBuf::from(
            "/tmp/rollout-2026-04-03T00-15-00-019d23a7-9258-7810-93cc-c6833b348201.jsonl",
        ),
        originator: Some("Codex Desktop".to_owned()),
        source_kind: "desktop".to_owned(),
        is_subagent: false,
        parent_session_id: None,
        cwd: Some("/tmp/demo-project".to_owned()),
        project_slug: "demo-project".to_owned(),
        thread_name: Some("Demo Thread".to_owned()),
        title_seed: "demo-thread".to_owned(),
        started_at: Some("2026-04-03T00:15:00Z".to_owned()),
        updated_at: Some("2026-04-04T00:05:00Z".to_owned()),
        messages: vec![
            TranscriptMessage {
                message_key: "m1".to_owned(),
                role: "user".to_owned(),
                phase: String::new(),
                timestamp: "2026-04-03T00:20:00Z".to_owned(),
                text: "Use Authorization: Bearer abc.def+/ghi==".to_owned(),
            },
            TranscriptMessage {
                message_key: "m2".to_owned(),
                role: "assistant".to_owned(),
                phase: String::new(),
                timestamp: "2026-04-03T01:45:00Z".to_owned(),
                text: "Stored AWS_SECRET_ACCESS_KEY=\"wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY\""
                    .to_owned(),
            },
            TranscriptMessage {
                message_key: "m3".to_owned(),
                role: "user".to_owned(),
                phase: String::new(),
                timestamp: "2026-04-04T00:05:00Z".to_owned(),
                text: "Next day".to_owned(),
            },
        ],
    }
}

fn render_options() -> RenderOptions {
    RenderOptions {
        local_offset_override: Some(UtcOffset::from_hms(9, 0, 0).unwrap()),
        now_utc: OffsetDateTime::parse(
            "2026-04-05T00:00:00Z",
            &time::format_description::well_known::Rfc3339,
        )
        .unwrap(),
    }
}
