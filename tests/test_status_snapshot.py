from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest
from pathlib import Path

from codex_obsidian_sync.config import resolve_service_paths
from codex_obsidian_sync.status_snapshot import build_status_snapshot, render_status_snapshot


class StatusSnapshotTests(unittest.TestCase):
    def test_status_snapshot_uses_cooldown_terms_and_ready_now(self) -> None:
        paths = resolve_service_paths(
            config_path=Path("/tmp/config.toml"),
            config_data={"codex_home": "/tmp/codex-home", "vault": "/tmp/vault", "interval_seconds": 60},
        )
        last_run = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()

        snapshot = build_status_snapshot(
            config_path=Path("/tmp/config.toml"),
            config_data={"vault": "/tmp/vault", "interval_seconds": 60},
            paths=paths,
            launchd_loaded=True,
            service_state={
                "pending": False,
                "last_run_finished_at": last_run,
                "last_success_at": last_run,
                "last_error_type": None,
                "last_error_summary": None,
                "last_summary": {"processed": 1},
            },
        )

        rendered = render_status_snapshot(snapshot)

        self.assertEqual(snapshot.cooldown, "1m (60s)")
        self.assertEqual(snapshot.next_eligible_run, "ready now")
        self.assertIn("Cooldown: 1m (60s)", rendered)
        self.assertIn("Next eligible run: ready now", rendered)


if __name__ == "__main__":
    unittest.main()
