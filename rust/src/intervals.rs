use crate::error::SyncError;

pub fn parse_interval(value: &str) -> Result<u64, SyncError> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err(SyncError::Config);
    }
    let digit_count = trimmed
        .chars()
        .take_while(|character| character.is_ascii_digit())
        .count();
    if digit_count == 0 {
        return Err(SyncError::Config);
    }
    let (amount, unit) = trimmed.split_at(digit_count);
    let amount = amount.parse::<u64>().map_err(|_| SyncError::Config)?;
    let multiplier = match unit.trim().to_ascii_lowercase().as_str() {
        "" | "s" => 1,
        "m" => 60,
        "h" => 3600,
        _ => return Err(SyncError::Config),
    };
    Ok((amount * multiplier).max(1))
}
