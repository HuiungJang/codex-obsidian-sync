from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class AuditCutoverMonitorTests(unittest.TestCase):
    def test_reports_ready_when_all_required_checkpoints_are_ok(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_monitor.py",
                    "--monitor-dir",
                    str(root),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertEqual(report["missing_checkpoints"], [])
        self.assertEqual(report["extra_records"], [])
        self.assertEqual(report["present_checkpoints"], ["+5m", "+1h", "+4h", "+24h"])
        self.assertEqual(report["records"][0]["status_launchd_label"], "com.codex.obsidian-sync")
        self.assertEqual(report["records"][0]["plist_label"], "com.codex.obsidian-sync")

    def test_fails_when_required_checkpoint_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary, omit={"+24h"})

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_monitor.py",
                    "--monitor-dir",
                    str(root),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing_checkpoints"], ["+24h"])
        self.assertIn("monitor record is missing for +24h", report["no_go_reasons"])

    def test_fails_when_record_contains_no_go_reasons(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary, failed={"+1h"})

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_monitor.py",
                    "--monitor-dir",
                    str(root),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("+1h: record ok is not true", report["no_go_reasons"])
        self.assertIn("+1h: record contains no-go reasons", report["no_go_reasons"])

    def test_fails_when_unexpected_json_record_is_present(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary)
            (root / "monitor-audit.json").write_text(
                json.dumps({"ok": True, "checkpoint": "+5m"}) + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_monitor.py",
                    "--monitor-dir",
                    str(root),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["extra_records"], ["monitor-audit.json"])
        self.assertIn("unexpected monitor record: monitor-audit.json", report["no_go_reasons"])

    def test_fails_when_record_label_is_unexpected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary, plist_labels={"+4h": "com.codex.obsidian-sync.other"})

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_monitor.py",
                    "--monitor-dir",
                    str(root),
                    "--expected-program-arg0",
                    expected_binary,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("+4h: LaunchAgent Label does not match expected label", report["no_go_reasons"])
        self.assertIn("LaunchAgent label changed across monitor records", report["no_go_reasons"])


def write_marker(root: Path) -> None:
    (root / ".codex-obsidian-sync-cutover-monitor").write_text(
        "managed by record_cutover_monitor.py\n",
        encoding="utf-8",
    )


def write_records(
    root: Path,
    *,
    expected_binary: str,
    omit: set[str] | None = None,
    failed: set[str] | None = None,
    status_labels: dict[str, str] | None = None,
    plist_labels: dict[str, str] | None = None,
) -> None:
    omit = omit or set()
    failed = failed or set()
    status_labels = status_labels or {}
    plist_labels = plist_labels or {}
    checkpoints = {
        "+5m": ("plus5m.json", "2026-05-16T00:05:00+00:00"),
        "+1h": ("plus1h.json", "2026-05-16T01:00:00+00:00"),
        "+4h": ("plus4h.json", "2026-05-16T04:00:00+00:00"),
        "+24h": ("plus24h.json", "2026-05-17T00:00:00+00:00"),
    }
    for checkpoint, (filename, last_success) in checkpoints.items():
        if checkpoint in omit:
            continue
        is_failed = checkpoint in failed
        (root / filename).write_text(
            json.dumps(
                {
                    "ok": not is_failed,
                    "checkpoint": checkpoint,
                    "recorded_at": last_success,
                    "no_go_reasons": ["last_error is boom"] if is_failed else [],
                    "configured": True,
                    "status_launchd_label": status_labels.get(checkpoint, "com.codex.obsidian-sync"),
                    "plist_label": plist_labels.get(checkpoint, "com.codex.obsidian-sync"),
                    "launchd_loaded": True,
                    "launchctl_loaded": True,
                    "program_arg0": expected_binary,
                    "service_command": "service-run",
                    "last_success": last_success,
                    "last_error": "boom" if is_failed else "none",
                    "skipped_invalid": 0,
                    "processed": 12,
                    "appended": 1,
                    "rewritten": 11,
                }
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
