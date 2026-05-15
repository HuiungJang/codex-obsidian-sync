use std::path::PathBuf;

use serde::{Deserialize, Deserializer, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionIndexEntry {
    #[serde(rename = "id")]
    pub session_id: String,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub thread_name: Option<String>,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub updated_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceClassification {
    #[serde(default = "default_source_kind")]
    pub source_kind: String,

    #[serde(default)]
    pub is_subagent: bool,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub parent_session_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionMeta {
    pub session_id: String,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub timestamp: Option<String>,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub cwd: Option<String>,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub originator: Option<String>,

    #[serde(default)]
    pub source_classification: SourceClassification,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub forked_from_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TranscriptMessage {
    pub message_key: String,
    pub role: String,

    #[serde(default)]
    pub phase: String,

    pub timestamp: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionEnvelope {
    pub canonical_session_id: String,
    pub rollout_path: PathBuf,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub originator: Option<String>,

    #[serde(default = "default_source_kind")]
    pub source_kind: String,

    #[serde(default)]
    pub is_subagent: bool,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub parent_session_id: Option<String>,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub cwd: Option<String>,

    #[serde(default = "default_project_slug")]
    pub project_slug: String,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub thread_name: Option<String>,

    #[serde(default = "default_title_seed")]
    pub title_seed: String,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub started_at: Option<String>,

    #[serde(default, deserialize_with = "deserialize_optional_string")]
    pub updated_at: Option<String>,

    #[serde(default)]
    pub messages: Vec<TranscriptMessage>,
}

impl Default for SourceClassification {
    fn default() -> Self {
        Self {
            source_kind: default_source_kind(),
            is_subagent: false,
            parent_session_id: None,
        }
    }
}

fn default_source_kind() -> String {
    "unknown".to_owned()
}

fn default_project_slug() -> String {
    "unknown-project".to_owned()
}

fn default_title_seed() -> String {
    "conversation".to_owned()
}

fn deserialize_optional_string<'de, D>(deserializer: D) -> Result<Option<String>, D::Error>
where
    D: Deserializer<'de>,
{
    let value = Option::<String>::deserialize(deserializer)?;
    Ok(value.and_then(|item| {
        let trimmed = item.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_owned())
        }
    }))
}
