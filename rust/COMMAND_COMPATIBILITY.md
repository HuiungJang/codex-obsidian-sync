# Rust Command Compatibility

| Command | Python behavior | Rust migration behavior |
| --- | --- | --- |
| `sync-once` | Writes to the configured vault and state by default. | Dry-run by default; writes rendered notes and temp state only under `--dry-run-output` or a private temp output. |
| `inspect-rollout` | Read-only JSON inspection with redacted message previews. | Read-only JSON inspection with the same redacted preview shape. |
| `inspect-recent` | Read-only JSON inspection of recent rollout files; subagents excluded unless requested. | Read-only JSON inspection of recent rollout files; subagents excluded unless requested. |
| `status --json` | Read-only config, launchd, and service-state snapshot. | Read-only config, launchd, and service-state snapshot with Python-compatible field names. |
| `setup` | Writes config and a LaunchAgent plist that runs `python -m codex_obsidian_sync.cli --config ... service-run`. | Writes config and a LaunchAgent plist that runs the canonical Rust binary path with `--config ... service-run`. |
| `start` | Bootstraps the LaunchAgent after refreshing setup. | Bootstraps the LaunchAgent after refreshing setup; Rust background write remains gated by `rust_service_write_enabled = true`. |
| `stop` | Boots out the LaunchAgent and keeps config/state/log files. | Boots out the same LaunchAgent label and keeps config/state/log files. |
| `service-run` | Internal launchd entry point that writes to the configured vault/state. | Internal launchd entry point that writes only when `rust_service_write_enabled = true`; otherwise it fails closed with a config error. |
| `sync-once --write` | Not needed because Python `sync-once` writes by default. | Explicit opt-in for real vault/state writes; rejected with `--dry-run-output`. |

The migration keeps Python/pipx as the rollback path until release artifacts, Homebrew install, and macOS LaunchAgent smoke tests pass.
