from __future__ import annotations

import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RecordCutoverMonitorTests(unittest.TestCase):
    def test_records_post_cutover_checkpoint_from_offline_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            launchctl_path = root / "launchctl.txt"
            note_path = root / "vault" / "Codex" / "Daily" / "2026-05-16.md"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            note_path.parent.mkdir(parents=True)
            note_path.write_text("fresh note\n", encoding="utf-8")
            write_status(status_path, skipped_invalid=0)
            write_plist(plist_path, expected_binary)
            launchctl_path.write_text("state = running\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+5m",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--launchctl-print-file",
                    str(launchctl_path),
                    "--expected-program-arg0",
                    expected_binary,
                    "--note-path",
                    str(note_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            record = json.loads((root / "plus5m.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(record["ok"])
        self.assertEqual(record["checkpoint"], "+5m")
        self.assertEqual(record["status_launchd_label"], "com.codex.obsidian-sync")
        self.assertEqual(record["plist_label"], "com.codex.obsidian-sync")
        self.assertEqual(record["program_arg0"], expected_binary)
        self.assertEqual(record["service_command"], "service-run")
        self.assertEqual(record["skipped_invalid"], 0)
        self.assertEqual(record["processed"], 12)
        self.assertTrue(record["notes"][0]["exists"])

    def test_fails_when_skipped_invalid_increases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            launchctl_path = root / "launchctl.txt"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_status(status_path, skipped_invalid=1)
            write_plist(plist_path, expected_binary)
            launchctl_path.write_text("state = running\n", encoding="utf-8")
            (root / "plus5m.json").write_text(
                json.dumps({"checkpoint": "+5m", "skipped_invalid": 0}) + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+1h",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--launchctl-print-file",
                    str(launchctl_path),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            record = json.loads((root / "plus1h.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 1)
        self.assertFalse(record["ok"])
        self.assertIn("skipped_invalid increased from previous monitor record", record["no_go_reasons"])

    def test_fails_when_last_success_regresses(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            launchctl_path = root / "launchctl.txt"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_status(
                status_path,
                skipped_invalid=0,
                last_success="2026-05-16T00:05:00+00:00",
            )
            write_plist(plist_path, expected_binary)
            launchctl_path.write_text("state = running\n", encoding="utf-8")
            (root / "plus5m.json").write_text(
                json.dumps(
                    {
                        "checkpoint": "+5m",
                        "skipped_invalid": 0,
                        "last_success": "2026-05-16T01:05:00+00:00",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+1h",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--launchctl-print-file",
                    str(launchctl_path),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            record = json.loads((root / "plus1h.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 1)
        self.assertFalse(record["ok"])
        self.assertIn("last_success regressed from previous monitor record", record["no_go_reasons"])

    def test_fails_when_last_success_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            launchctl_path = root / "launchctl.txt"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_status(status_path, skipped_invalid=0, last_success="never run")
            write_plist(plist_path, expected_binary)
            launchctl_path.write_text("state = running\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+5m",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--launchctl-print-file",
                    str(launchctl_path),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            record = json.loads((root / "plus5m.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 1)
        self.assertFalse(record["ok"])
        self.assertIn("last_success is missing or invalid", record["no_go_reasons"])

    def test_records_no_go_when_status_counter_is_malformed_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            launchctl_path = root / "launchctl.txt"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_status(status_path, skipped_invalid="1")
            write_plist(plist_path, expected_binary)
            launchctl_path.write_text("state = running\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+5m",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--launchctl-print-file",
                    str(launchctl_path),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            record = json.loads((root / "plus5m.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 1)
        self.assertFalse(record["ok"])
        self.assertIsNone(record["skipped_invalid"])
        self.assertIn(
            "last_summary skipped_invalid is missing or not a non-negative integer",
            record["no_go_reasons"],
        )

    def test_ignores_non_checkpoint_json_when_loading_previous_records(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            launchctl_path = root / "launchctl.txt"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_status(
                status_path,
                skipped_invalid=0,
                last_success="2026-05-16T01:00:00+00:00",
            )
            write_plist(plist_path, expected_binary)
            launchctl_path.write_text("state = running\n", encoding="utf-8")
            (root / "plus5m.json").write_text(
                json.dumps(
                    {
                        "checkpoint": "+5m",
                        "skipped_invalid": 0,
                        "last_success": "2026-05-16T00:05:00+00:00",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "monitor-audit.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "last_success": "2026-05-16T04:00:00+00:00",
                        "skipped_invalid": 99,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+1h",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--launchctl-print-file",
                    str(launchctl_path),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            record = json.loads((root / "plus1h.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(record["ok"], record["no_go_reasons"])

    def test_fails_when_status_label_is_unexpected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            launchctl_path = root / "launchctl.txt"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_status(status_path, skipped_invalid=0, label="com.codex.obsidian-sync.other")
            write_plist(plist_path, expected_binary)
            launchctl_path.write_text("state = running\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+5m",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--launchctl-print-file",
                    str(launchctl_path),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            record = json.loads((root / "plus5m.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 1)
        self.assertFalse(record["ok"])
        self.assertIn("status launchd_label does not match expected label", record["no_go_reasons"])

    def test_fails_when_status_loaded_flag_is_not_boolean(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            launchctl_path = root / "launchctl.txt"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_status(status_path, skipped_invalid=0, launchd_loaded="true")
            write_plist(plist_path, expected_binary)
            launchctl_path.write_text("state = running\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+5m",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--launchctl-print-file",
                    str(launchctl_path),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            record = json.loads((root / "plus5m.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 1)
        self.assertFalse(record["ok"])
        self.assertEqual(record["launchd_loaded"], "true")
        self.assertIn("status launchd_loaded is not boolean true", record["no_go_reasons"])

    def test_fails_when_launchctl_print_reports_unloaded_service(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            launchctl_path = root / "launchctl.txt"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_status(status_path, skipped_invalid=0)
            write_plist(plist_path, expected_binary)
            launchctl_path.write_text("could not find service\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+5m",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--launchctl-print-file",
                    str(launchctl_path),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            record = json.loads((root / "plus5m.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 1)
        self.assertFalse(record["ok"])
        self.assertFalse(record["launchctl_loaded"])
        self.assertIn("launchctl print did not return a loaded service", record["no_go_reasons"])

    def test_fails_when_plist_label_is_unexpected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            launchctl_path = root / "launchctl.txt"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_status(status_path, skipped_invalid=0)
            write_plist(plist_path, expected_binary, label="com.codex.obsidian-sync.other")
            launchctl_path.write_text("state = running\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+5m",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--launchctl-print-file",
                    str(launchctl_path),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            record = json.loads((root / "plus5m.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 1)
        self.assertFalse(record["ok"])
        self.assertIn("LaunchAgent Label does not match expected label", record["no_go_reasons"])

    def test_refuses_symlinked_monitor_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            (root / ".codex-obsidian-sync-cutover-monitor").symlink_to(root / "marker-target")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+5m",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(root / "status.json"),
                    "--plist-file",
                    str(root / "agent.plist"),
                    "--launchctl-print-file",
                    str(root / "launchctl.txt"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("Refusing symlinked monitor directory marker", result.stderr)

    def test_refuses_symlinked_evidence_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            launchctl_path = root / "launchctl.txt"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            target = root / "status-target.json"
            write_status(target, skipped_invalid=0)
            status_path.symlink_to(target)
            write_plist(plist_path, expected_binary)
            launchctl_path.write_text("state = running\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+5m",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--launchctl-print-file",
                    str(launchctl_path),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("evidence file is a symlink", result.stderr)

    def test_refuses_symlinked_previous_monitor_record(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            launchctl_path = root / "launchctl.txt"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_status(status_path, skipped_invalid=0)
            write_plist(plist_path, expected_binary)
            launchctl_path.write_text("state = running\n", encoding="utf-8")
            target = root / "previous-target.json"
            target.write_text(json.dumps({"checkpoint": "+5m", "skipped_invalid": 0}) + "\n", encoding="utf-8")
            (root / "plus5m.json").symlink_to(target)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_cutover_monitor.py",
                    "--checkpoint",
                    "+1h",
                    "--output-dir",
                    str(root),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--launchctl-print-file",
                    str(launchctl_path),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("monitor record is a symlink: plus5m.json", result.stderr)


def write_status(
    path: Path,
    *,
    skipped_invalid: object,
    last_success: str = "2026-05-16T00:05:00+00:00",
    label: str = "com.codex.obsidian-sync",
    launchd_loaded: object = True,
) -> None:
    path.write_text(
        json.dumps(
            {
                "configured": True,
                "config_path": "/tmp/config.toml",
                "vault": "/tmp/vault",
                "cooldown": "1m (60s)",
                "launchd_label": label,
                "launchd_loaded": launchd_loaded,
                "plist_path": "/tmp/agent.plist",
                "pending": "no",
                "next_eligible_run": "ready now",
                "last_run": "2026-05-16T00:05:00+00:00",
                "last_success": last_success,
                "last_error": "none",
                "last_summary": {
                    "processed": 12,
                    "appended": 1,
                    "rewritten": 11,
                    "skipped_invalid": skipped_invalid,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def write_plist(path: Path, program_arg0: str, *, label: str = "com.codex.obsidian-sync") -> None:
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": [
                    program_arg0,
                    "--config",
                    "/tmp/config.toml",
                    "service-run",
                ],
                "StartInterval": 60,
                "RunAtLoad": False,
                "KeepAlive": False,
            },
            fmt=plistlib.FMT_XML,
            sort_keys=False,
        )
    )


if __name__ == "__main__":
    unittest.main()
