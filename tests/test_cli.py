from __future__ import annotations

import json
import plistlib
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codex_obsidian_sync.cli import main
from codex_obsidian_sync.config import load_toml_config, resolve_service_paths, save_toml_config
from codex_obsidian_sync.launchd import LaunchdStatus


class CliTests(unittest.TestCase):
    def test_setup_updates_existing_config_and_preserves_unknown_keys(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "vault"
            vault.mkdir()
            config_path = root / "config.toml"
            save_toml_config(
                config_path,
                {
                    "vault": str(vault),
                    "interval_seconds": 10,
                    "codex_home": str(root / ".codex"),
                    "launchd_plist_path": str(root / "agent.plist"),
                    "extra_flag": True,
                },
            )

            stdout = StringIO()
            with patch("codex_obsidian_sync.cli.write_launch_agent_plist") as write_plist_mock, patch(
                "sys.stdout",
                stdout,
            ):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "setup",
                        "--vault",
                        str(vault),
                        "--cooldown",
                        "1m",
                    ]
                )

            saved = load_toml_config(config_path)
            paths = resolve_service_paths(config_path=config_path, config_data=saved)

        self.assertEqual(exit_code, 0)
        self.assertEqual(saved["interval_seconds"], 60)
        self.assertTrue(saved["extra_flag"])
        self.assertEqual(str(saved["launchd_plist_path"]), str(root / "agent.plist"))
        self.assertEqual(paths.launchd_plist_path, root / "agent.plist")
        write_plist_mock.assert_called_once()
        plist_content = write_plist_mock.call_args.args[1]
        payload = plistlib.loads(plist_content.encode("utf-8"))
        self.assertEqual(payload["StartInterval"], 60)

    def test_start_prompts_for_missing_config_and_bootstraps_agent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "vault"
            vault.mkdir()
            config_path = root / "config.toml"
            stdout = StringIO()

            with patch("builtins.input", side_effect=[str(vault), "1m"]), patch(
                "codex_obsidian_sync.cli.query_launchd_status",
                return_value=LaunchdStatus(
                    label="com.codex.obsidian-sync",
                    loaded=False,
                    plist_path=root / "agent.plist",
                    target="gui/1/com.codex.obsidian-sync",
                ),
            ), patch("codex_obsidian_sync.cli.bootstrap_launch_agent") as bootstrap_mock, patch(
                "codex_obsidian_sync.cli.write_launch_agent_plist",
            ) as write_plist_mock, patch(
                "sys.stdout",
                stdout,
            ):
                exit_code = main(["--config", str(config_path), "start"])

            saved = load_toml_config(config_path)

        self.assertEqual(exit_code, 0)
        self.assertEqual(saved["vault"], str(vault.resolve()))
        self.assertEqual(saved["interval_seconds"], 60)
        write_plist_mock.assert_called_once()
        bootstrap_mock.assert_called_once()

    def test_start_reloads_loaded_agent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "vault"
            vault.mkdir()
            config_path = root / "config.toml"
            save_toml_config(
                config_path,
                {
                    "vault": str(vault),
                    "interval_seconds": 60,
                    "codex_home": str(root / ".codex"),
                    "launchd_plist_path": str(root / "agent.plist"),
                },
            )

            with patch(
                "codex_obsidian_sync.cli.query_launchd_status",
                return_value=LaunchdStatus(
                    label="com.codex.obsidian-sync",
                    loaded=True,
                    plist_path=root / "agent.plist",
                    target="gui/1/com.codex.obsidian-sync",
                ),
            ), patch("codex_obsidian_sync.cli.bootout_launch_agent") as bootout_mock, patch(
                "codex_obsidian_sync.cli.bootstrap_launch_agent"
            ) as bootstrap_mock, patch(
                "codex_obsidian_sync.cli.write_launch_agent_plist"
            ) as write_plist_mock:
                exit_code = main(["--config", str(config_path), "start"])

        self.assertEqual(exit_code, 0)
        write_plist_mock.assert_called_once()
        bootout_mock.assert_called_once()
        bootstrap_mock.assert_called_once()

    def test_inspect_rollout_redacts_message_previews(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481cc"
        github_token = "ghp_" + ("a" * 36)

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rollout_path = root / f"rollout-2026-03-25T15-21-17-{session_id}.jsonl"
            self._write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-03-25T06:21:17Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "timestamp": "2026-03-25T06:21:17Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": "vscode",
                        },
                    },
                    self._message_record(
                        timestamp="2026-03-25T06:21:18Z",
                        role="user",
                        text=f"deploy with {github_token}",
                    ),
                ],
            )

            stdout = StringIO()
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "--config",
                        str(root / "config.toml"),
                        "inspect-rollout",
                        str(rollout_path),
                        "--session-index",
                        str(root / "missing-session-index.jsonl"),
                    ]
                )

            output = stdout.getvalue()
            payload = json.loads(output)

        self.assertEqual(exit_code, 0)
        self.assertNotIn(github_token, output)
        self.assertEqual(
            payload["messages"][0]["text_preview"],
            "deploy with [REDACTED_GITHUB_TOKEN]",
        )

    def test_inspect_recent_redacts_message_previews(self) -> None:
        session_id = "019d23a7-9258-7810-93cc-c6833b3481cd"
        slack_token = "xox" + "b-" + "123456789012-123456789012-abcdefghijklmnopqrstuvwx"

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            rollout_path = (
                codex_home
                / "sessions"
                / "2026"
                / "03"
                / "25"
                / f"rollout-2026-03-25T15-21-17-{session_id}.jsonl"
            )
            self._write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-03-25T06:21:17Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "timestamp": "2026-03-25T06:21:17Z",
                            "cwd": "/tmp/demo-project",
                            "originator": "Codex Desktop",
                            "source": "vscode",
                        },
                    },
                    self._message_record(
                        timestamp="2026-03-25T06:21:18Z",
                        role="user",
                        text=f"deploy with {slack_token}",
                    ),
                ],
            )

            stdout = StringIO()
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "--config",
                        str(root / "config.toml"),
                        "inspect-recent",
                        "--codex-home",
                        str(codex_home),
                        "--limit",
                        "1",
                    ]
                )

            output = stdout.getvalue()
            payload = json.loads(output)

        self.assertEqual(exit_code, 0)
        self.assertNotIn(slack_token, output)
        self.assertEqual(
            payload[0]["messages"][0]["text_preview"],
            "deploy with [REDACTED_SLACK_TOKEN]",
        )

    def test_large_file_flags_are_not_exposed(self) -> None:
        for command in ("sync-once", "watch"):
            with self.subTest(command=command):
                stderr = StringIO()
                with patch("sys.stderr", stderr), self.assertRaises(SystemExit) as exit_context:
                    main([command, "--large-file-bytes", "1024"])

                self.assertEqual(exit_context.exception.code, 2)
                self.assertIn("unrecognized arguments: --large-file-bytes 1024", stderr.getvalue())

    @staticmethod
    def _message_record(*, timestamp: str, role: str, text: str) -> dict[str, object]:
        return {
            "timestamp": timestamp,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": role,
                "content": [
                    {"type": "output_text" if role == "assistant" else "input_text", "text": text}
                ],
            },
        }

    @staticmethod
    def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False))
                handle.write("\n")


if __name__ == "__main__":
    unittest.main()
