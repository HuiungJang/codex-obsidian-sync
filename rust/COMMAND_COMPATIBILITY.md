# Rust Command Compatibility

| Command | Python behavior | Rust migration behavior |
| --- | --- | --- |
| `sync-once` | Writes to the configured vault and state by default. | Dry-run by default; writes rendered notes and temp state only under `--dry-run-output` or a private temp output. |
| `inspect-rollout` | Read-only JSON inspection with redacted message previews. | Read-only JSON inspection with the same redacted preview shape. |
| `inspect-recent` | Read-only JSON inspection of recent rollout files; subagents excluded unless requested. | Read-only JSON inspection of recent rollout files; subagents excluded unless requested. |
| `status --json` | Read-only config, launchd, and service-state snapshot. | Read-only config, launchd, and service-state snapshot with Python-compatible field names. |

`--write`, service commands, and launchd cutover remain out of scope until later migration phases.
