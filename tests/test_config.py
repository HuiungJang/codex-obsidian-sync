from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codex_obsidian_sync.config import (
    load_toml_config,
    resolve_service_paths,
    resolve_sync_config,
    save_toml_config,
)


class ConfigTests(unittest.TestCase):
    def test_load_toml_config_and_resolve_sync_config(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    vault = "/tmp/obsidian"
                    codex_home = "/tmp/codex"
                    state_file = "/tmp/state.json"
                    lock_file = "/tmp/state.lock"
                    include_subagents = true
                    interval_seconds = 15
                    recent_days = 7
                    candidate_file_limit = 12
                    candidate_bytes_limit = 4096
                    log_level = "debug"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            config_data = load_toml_config(config_path)
            config = resolve_sync_config(
                vault=None,
                codex_home=None,
                state_file=None,
                lock_file=None,
                include_subagents=None,
                interval_seconds=None,
                recent_days=None,
                candidate_file_limit=None,
                candidate_bytes_limit=None,
                log_level=None,
                config_data=config_data,
            )

        self.assertEqual(config.vault, Path("/tmp/obsidian"))
        self.assertEqual(config.codex_home, Path("/tmp/codex"))
        self.assertTrue(config.include_subagents)
        self.assertEqual(config.interval_seconds, 15)
        self.assertEqual(config.recent_days, 7)
        self.assertEqual(config.candidate_file_limit, 12)
        self.assertEqual(config.candidate_bytes_limit, 4096)
        self.assertFalse(hasattr(config, "large_file_bytes"))
        self.assertFalse(hasattr(config, "large_file_lines"))
        self.assertEqual(config.log_level, "DEBUG")

    def test_resolve_sync_config_clamps_minimums(self) -> None:
        config = resolve_sync_config(
            vault=Path("/tmp/obsidian"),
            codex_home=Path("/tmp/codex"),
            state_file=Path("/tmp/state.json"),
            lock_file=Path("/tmp/state.lock"),
            include_subagents=False,
            interval_seconds=0,
            recent_days=0,
            candidate_file_limit=0,
            candidate_bytes_limit=4096,
            log_level="info",
            config_data={},
        )

        self.assertEqual(config.interval_seconds, 1)
        self.assertEqual(config.recent_days, 1)
        self.assertEqual(config.candidate_file_limit, 1)

    def test_save_toml_config_preserves_unknown_keys(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    vault = "/tmp/old-vault"
                    interval_seconds = 10
                    extra_flag = true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            config_data = load_toml_config(config_path)
            config_data["vault"] = "/tmp/new-vault"
            config_data["interval_seconds"] = 60
            save_toml_config(config_path, config_data)
            reloaded = load_toml_config(config_path)

        self.assertEqual(reloaded["vault"], "/tmp/new-vault")
        self.assertEqual(reloaded["interval_seconds"], 60)
        self.assertTrue(reloaded["extra_flag"])

    def test_resolve_service_paths_uses_obsidian_sync_defaults(self) -> None:
        config_data = {
            "codex_home": "/tmp/codex-home",
            "vault": "/tmp/vault",
        }

        paths = resolve_service_paths(config_path=Path("/tmp/custom-config.toml"), config_data=config_data)

        self.assertEqual(paths.config_path, Path("/tmp/custom-config.toml"))
        self.assertEqual(paths.session_index_path, Path("/tmp/codex-home/session_index.jsonl"))
        self.assertEqual(paths.sessions_path, Path("/tmp/codex-home/sessions"))
        self.assertEqual(paths.service_state_file, Path("/tmp/codex-home/obsidian-sync/service-state.json"))
        self.assertEqual(paths.launchd_plist_path.name, "com.codex.obsidian-sync.plist")

    def test_save_toml_config_uses_shared_atomic_write_helper(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"

            with patch("codex_obsidian_sync.config.write_atomic") as write_atomic:
                saved_path = save_toml_config(config_path, {"vault": "/tmp/vault"})

        self.assertEqual(saved_path, config_path)
        write_atomic.assert_called_once_with(config_path, 'vault = "/tmp/vault"\n')


if __name__ == "__main__":
    unittest.main()
