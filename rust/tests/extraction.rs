use codex_obsidian_sync_rs::parser::{
    build_message_key, classify_source, collect_session_metas, extract_candidate_messages,
    extract_text, is_control_message, normalize_for_hash,
};
use serde_json::{Map, Value, json};

#[test]
fn collects_session_metas_and_classifies_sources() {
    let records = vec![
        object(json!({
            "type": "session_meta",
            "payload": {
                "id": " child-session ",
                "timestamp": "2026-03-25T06:21:17Z",
                "cwd": "/tmp/demo-project",
                "originator": "Codex Desktop",
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": " parent-session "
                        }
                    }
                },
                "forked_from_id": " fork-source "
            }
        })),
        object(json!({
            "type": "session_meta",
            "payload": { "id": "" }
        })),
        object(json!({ "type": "response_item" })),
    ];

    let metas = collect_session_metas(&records);

    assert_eq!(metas.len(), 1);
    assert_eq!(metas[0].session_id, " child-session ");
    assert_eq!(metas[0].timestamp.as_deref(), Some("2026-03-25T06:21:17Z"));
    assert_eq!(metas[0].cwd.as_deref(), Some("/tmp/demo-project"));
    assert_eq!(metas[0].originator.as_deref(), Some("Codex Desktop"));
    assert_eq!(metas[0].forked_from_id.as_deref(), Some("fork-source"));
    assert_eq!(
        metas[0].source_classification.source_kind,
        "subagent.thread_spawn"
    );
    assert!(metas[0].source_classification.is_subagent);
    assert_eq!(
        metas[0].source_classification.parent_session_id.as_deref(),
        Some("parent-session")
    );
}

#[test]
fn classify_source_matches_python_fallbacks() {
    let string_source = classify_source(Some(&json!(" vscode ")));
    assert_eq!(string_source.source_kind, " vscode ");
    assert!(!string_source.is_subagent);

    let unknown_source = classify_source(Some(&json!({ "subagent": {} })));
    assert_eq!(unknown_source.source_kind, "unknown");
    assert!(!unknown_source.is_subagent);

    let empty_thread_spawn = classify_source(Some(&json!({
        "subagent": { "thread_spawn": {} }
    })));
    assert_eq!(empty_thread_spawn.source_kind, "unknown");
    assert!(!empty_thread_spawn.is_subagent);
}

#[test]
fn extracts_user_and_assistant_messages_with_filters_and_dedupe() {
    let records = vec![
        message_record(
            "2026-03-25T06:21:20Z",
            "developer",
            None,
            "ignore developer",
        ),
        message_record(
            "2026-03-25T06:21:21Z",
            "user",
            None,
            "Keep only this user question",
        ),
        message_record(
            "2026-03-25T06:21:22Z",
            "assistant",
            Some("commentary"),
            "ignore commentary",
        ),
        message_record(
            "2026-03-25T06:21:23Z",
            "assistant",
            Some("final_answer"),
            "Keep this final answer",
        ),
        message_record(
            "2026-03-25T06:21:23Z",
            "assistant",
            Some("final_answer"),
            "Keep this final answer",
        ),
        message_record(
            "2026-03-25T06:21:24Z",
            "assistant",
            None,
            "Keep this empty phase answer too",
        ),
        message_record(
            "2026-03-25T06:21:25Z",
            "user",
            None,
            "# AGENTS.md instructions for /tmp/demo",
        ),
    ];

    let messages = extract_candidate_messages(&records);

    assert_eq!(
        messages
            .iter()
            .map(|message| message.role.as_str())
            .collect::<Vec<_>>(),
        vec!["user", "assistant", "assistant"]
    );
    assert_eq!(
        messages
            .iter()
            .map(|message| message.phase.as_str())
            .collect::<Vec<_>>(),
        vec!["", "final_answer", ""]
    );
    assert_eq!(
        messages
            .iter()
            .map(|message| message.text.as_str())
            .collect::<Vec<_>>(),
        vec![
            "Keep only this user question",
            "Keep this final answer",
            "Keep this empty phase answer too"
        ]
    );
}

#[test]
fn extract_text_normalizes_blocks_like_python() {
    let text = extract_text(Some(&json!([
        { "type": "input_text", "text": " first line \r\n\r\n second line " },
        { "type": "ignored", "text": "hidden" },
        { "type": "output_text", "text": " third line\n  fourth line\r fifth line\u{2028} sixth line " },
        "not-an-object"
    ])));

    assert_eq!(
        text,
        "first line\nsecond line\n\nthird line\nfourth line\nfifth line\nsixth line"
    );
}

#[test]
fn message_key_matches_python_sha1_contract() {
    let key = build_message_key(
        "2026-03-25T06:21:23Z",
        "assistant",
        "final_answer",
        " Keep   this\nfinal\tanswer ",
    );

    assert_eq!(
        normalize_for_hash(" Keep   this\nfinal\tanswer "),
        "Keep this final answer"
    );
    assert_eq!(key, "a43746fbd7f6d77b12ec90610b30e92cac273153");
}

#[test]
fn control_message_prefixes_match_python() {
    assert!(is_control_message("# AGENTS.md instructions for /tmp/demo"));
    assert!(is_control_message(
        "<environment_context>\n<cwd>/tmp/demo</cwd>"
    ));
    assert!(is_control_message("<subagent_notification>{}"));
    assert!(is_control_message("<turn_aborted>"));
    assert!(!is_control_message("/workflows:brainstorm codex"));
}

fn message_record(
    timestamp: &str,
    role: &str,
    phase: Option<&str>,
    text: &str,
) -> Map<String, Value> {
    let mut payload = json!({
        "type": "message",
        "role": role,
        "content": [
            {
                "type": if role == "assistant" { "output_text" } else { "input_text" },
                "text": text
            }
        ]
    });
    if let Some(phase) = phase {
        payload
            .as_object_mut()
            .unwrap()
            .insert("phase".to_owned(), json!(phase));
    }

    object(json!({
        "timestamp": timestamp,
        "type": "response_item",
        "payload": payload
    }))
}

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(object) => object,
        _ => panic!("test value must be an object"),
    }
}
