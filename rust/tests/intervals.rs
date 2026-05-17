use codex_obsidian_sync_rs::intervals::parse_interval;

#[test]
fn parse_interval_supports_human_friendly_units() {
    assert_eq!(parse_interval("10s").unwrap(), 10);
    assert_eq!(parse_interval("1m").unwrap(), 60);
    assert_eq!(parse_interval("2h").unwrap(), 7200);
    assert_eq!(parse_interval("15").unwrap(), 15);
}

#[test]
fn parse_interval_rejects_invalid_values_and_clamps_zero() {
    assert_eq!(parse_interval("0").unwrap(), 1);
    assert!(parse_interval("soon").is_err());
    assert!(parse_interval("10d").is_err());
}
