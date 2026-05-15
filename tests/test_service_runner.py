from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codex_obsidian_sync.config import resolve_service_paths, save_toml_config
from codex_obsidian_sync.service_runner import run_service
from codex_obsidian_sync.service_state import load_service_state, mutate_service_state


class ServiceRunnerTests(unittest.TestCase):
    def test_run_service_records_summary_and_success(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "vault"
            vault.mkdir()
            config_path = root / "config.toml"
            save_toml_config(
                config_path,
                {
                    "vault": str(vault),
                    "codex_home": str(root / ".codex"),
                    "interval_seconds": 10,
                },
            )

            with patch(
                "codex_obsidian_sync.service_runner.sync_once",
                return_value={"processed": 1, "unchanged": 0, "paused": 0},
            ):
                run_service(config_path=config_path)

            paths = resolve_service_paths(config_path=config_path, config_data={"codex_home": str(root / ".codex")})
            state = load_service_state(paths.service_state_file)

        self.assertEqual(state["last_summary"]["processed"], 1)
        self.assertFalse(state["pending"])
        self.assertIsNotNone(state["last_success_at"])

    def test_run_service_flushes_pending_follow_up_trigger(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "vault"
            vault.mkdir()
            config_path = root / "config.toml"
            config_data = {
                "vault": str(vault),
                "codex_home": str(root / ".codex"),
                "interval_seconds": 1,
            }
            save_toml_config(config_path, config_data)
            paths = resolve_service_paths(config_path=config_path, config_data=config_data)

            calls = {"count": 0}

            def fake_sync_once(*, config, logger, allow_pause):  # type: ignore[no-untyped-def]
                calls["count"] += 1
                if calls["count"] == 1:
                    mutate_service_state(
                        state_path=paths.service_state_file,
                        lock_path=paths.service_state_lock_file,
                        mutator=lambda state: state.update({"pending": True}),
                    )
                return {"processed": 1, "unchanged": 0, "paused": 0}

            with patch("codex_obsidian_sync.service_runner.sync_once", side_effect=fake_sync_once), patch(
                "codex_obsidian_sync.service_runner._shift_iso",
                side_effect=lambda value, seconds: value,
            ):
                run_service(config_path=config_path)

            state = load_service_state(paths.service_state_file)

        self.assertEqual(calls["count"], 2)
        self.assertFalse(state["pending"])

    def test_run_service_schedules_follow_up_when_source_changes_during_run(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "vault"
            vault.mkdir()
            config_path = root / "config.toml"
            save_toml_config(
                config_path,
                {
                    "vault": str(vault),
                    "codex_home": str(root / ".codex"),
                    "interval_seconds": 1,
                },
            )

            with patch(
                "codex_obsidian_sync.service_runner.sync_once",
                return_value={"processed": 1, "unchanged": 0, "paused": 0},
            ) as sync_mock, patch(
                "codex_obsidian_sync.service_runner._wait_for_source_settle",
                side_effect=[("before", 1, 1), ("after", 2, 2)],
            ), patch(
                "codex_obsidian_sync.service_runner._capture_source_fingerprint",
                side_effect=[("after", 2, 2), ("after", 2, 2)],
            ), patch(
                "codex_obsidian_sync.service_runner._shift_iso",
                side_effect=lambda value, seconds: value,
            ):
                run_service(config_path=config_path)

        self.assertEqual(sync_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
