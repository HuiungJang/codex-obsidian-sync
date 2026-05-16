use std::sync::OnceLock;

use regex::{Captures, Regex};

pub fn redact_text(text: &str) -> String {
    let redacted = private_key_pattern()
        .replace_all(text, "[REDACTED_PRIVATE_KEY]")
        .into_owned();
    let redacted = bearer_pattern()
        .replace_all(&redacted, |captures: &Captures<'_>| {
            let trail = captures.name("trail").map_or("", |value| value.as_str());
            format!("[REDACTED_BEARER_TOKEN]{trail}")
        })
        .into_owned();
    let redacted = openai_key_pattern()
        .replace_all(&redacted, "[REDACTED_API_KEY]")
        .into_owned();
    let redacted = github_token_pattern()
        .replace_all(&redacted, "[REDACTED_GITHUB_TOKEN]")
        .into_owned();
    let redacted = aws_access_key_pattern()
        .replace_all(&redacted, "[REDACTED_AWS_ACCESS_KEY]")
        .into_owned();
    let redacted = slack_token_pattern()
        .replace_all(&redacted, "[REDACTED_SLACK_TOKEN]")
        .into_owned();
    let redacted = npm_token_pattern()
        .replace_all(&redacted, "[REDACTED_NPM_TOKEN]")
        .into_owned();
    let redacted = google_api_key_pattern()
        .replace_all(&redacted, "[REDACTED_GOOGLE_API_KEY]")
        .into_owned();
    let redacted = stripe_secret_key_pattern()
        .replace_all(&redacted, "[REDACTED_STRIPE_SECRET_KEY]")
        .into_owned();
    let redacted = jwt_pattern()
        .replace_all(&redacted, "[REDACTED_JWT]")
        .into_owned();
    assignment_secret_pattern()
        .replace_all(&redacted, |captures: &Captures<'_>| {
            let matched = captures.get(0).map_or("", |value| value.as_str());
            let key = captures.name("key").map_or("", |value| value.as_str());
            let separator = captures
                .name("separator")
                .map_or("", |value| value.as_str());
            let quote = captures.name("quote").map_or("", |value| value.as_str());
            let trailing_quote = captures
                .name("trailing_quote")
                .map_or("", |value| value.as_str());
            if !quote.is_empty() && quote != trailing_quote {
                matched.to_owned()
            } else {
                format!("{key}{separator}{quote}[REDACTED_SECRET]{trailing_quote}")
            }
        })
        .into_owned()
}

fn private_key_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----")
            .expect("valid private key redaction regex")
    })
}

fn bearer_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(r#"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+(?P<trail>$|[\s,;\)\]\}"'`])"#)
            .expect("valid bearer redaction regex")
    })
}

fn openai_key_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"\bsk-[A-Za-z0-9_-]{20,}\b").expect("valid regex"))
}

fn github_token_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
            .expect("valid regex")
    })
}

fn aws_access_key_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b").expect("valid regex"))
}

fn slack_token_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b").expect("valid regex"))
}

fn npm_token_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"\bnpm_[A-Za-z0-9_]{20,}\b").expect("valid regex"))
}

fn google_api_key_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"\bAIza[0-9A-Za-z_-]{35}\b").expect("valid regex"))
}

fn stripe_secret_key_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b").expect("valid regex")
    })
}

fn jwt_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
            .expect("valid regex")
    })
}

fn assignment_secret_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(
            r#"(?ix)
            \b
            (?P<key>[A-Z0-9_-]*
                (?:api[_-]?key|access[_-]?key|secret[_-]?access[_-]?key|secret[_-]?key|
                   client[_-]?secret|
                   access[_-]?token|refresh[_-]?token|auth[_-]?token|id[_-]?token|github[_-]?token|
                   slack[_-]?(?:bot[_-]?)?token|npm[_-]?token|password|passwd|secret)
                [A-Z0-9_-]*)
            \b
            (?P<separator>\s*[:=]\s*)
            (?P<quote>['"]?)
            (?P<value>[^\s,'"\[\]]{6,})
            (?P<trailing_quote>['"]?)
            "#,
        )
        .expect("valid assignment redaction regex")
    })
}
