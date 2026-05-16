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
        self.assertEqual(report["unexpected_entries"], [])
        self.assertEqual(report["present_checkpoints"], ["+5m", "+1h", "+4h", "+24h"])
        self.assertEqual(report["records"][0]["status_launchd_label"], "com.codex.obsidian-sync")
        self.assertEqual(report["records"][0]["plist_label"], "com.codex.obsidian-sync")
        self.assertEqual(report["records"][0]["config_path"], "/tmp/config.toml")

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

    def test_fails_when_record_config_path_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary, config_paths={"+1h": None})

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
        self.assertIn("+1h: config_path is missing", report["no_go_reasons"])

    def test_fails_when_record_config_path_is_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary, config_paths={"+1h": "relative-config.toml"})

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
        self.assertIn("+1h: config_path is not absolute", report["no_go_reasons"])

    def test_fails_when_record_config_path_changes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary, config_paths={"+4h": "/tmp/other-config.toml"})

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
        self.assertIn("LaunchAgent --config path changed across monitor records", report["no_go_reasons"])

    def test_fails_when_record_program_arguments_count_is_not_rust_shape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary, program_arguments_counts={"+4h": 5})

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
        self.assertIn("+4h: ProgramArguments count is not the Rust launchd shape", report["no_go_reasons"])

    def test_fails_when_recorded_at_regresses(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(
                root,
                expected_binary=expected_binary,
                recorded_ats={"+4h": "2026-05-16T00:30:00+00:00"},
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
        self.assertIn("+4h: recorded_at regressed from an earlier checkpoint", report["no_go_reasons"])

    def test_fails_when_last_success_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary, last_successes={"+1h": "never run"})

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
        self.assertIn("+1h: last_success is missing or invalid", report["no_go_reasons"])

    def test_fails_when_counter_field_is_malformed_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(
                root,
                expected_binary=expected_binary,
                counter_overrides={"+1h": {"skipped_invalid": "1"}},
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
        self.assertIn(
            "+1h: skipped_invalid is missing or not a non-negative integer",
            report["no_go_reasons"],
        )

    def test_allows_latest_capture_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary)
            for name in (
                "latest-status.stdout",
                "latest-status.stderr",
                "latest-launchagent.plist",
                "latest-launchctl-print.txt",
                "latest-launchctl-print.stderr",
            ):
                (root / name).write_text("", encoding="utf-8")

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

    def test_fails_when_unexpected_non_json_entry_is_present(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary)
            (root / "notes.diff").write_text("", encoding="utf-8")

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
        self.assertEqual(report["unexpected_entries"], ["notes.diff"])
        self.assertIn("unexpected monitor directory entry: notes.diff", report["no_go_reasons"])

    def test_fails_when_checkpoint_record_is_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            write_marker(root)
            write_records(root, expected_binary=expected_binary)
            original = root / "plus5m.json"
            target = root / "plus5m-target.json"
            target.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
            original.unlink()
            original.symlink_to(target)

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
        self.assertIn("monitor record is a symlink for +5m", report["no_go_reasons"])

    def test_fails_when_marker_is_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            marker_target = root / "marker-target"
            marker_target.write_text("managed by record_cutover_monitor.py\n", encoding="utf-8")
            (root / ".codex-obsidian-sync-cutover-monitor").symlink_to(marker_target)
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

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("monitor directory marker is a symlink", report["no_go_reasons"])

    def test_fails_when_monitor_directory_is_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-monitor-") as temp_dir:
            root = Path(temp_dir)
            target = root / "target"
            monitor_dir = root / "monitor-link"
            expected_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            target.mkdir()
            write_marker(target)
            write_records(target, expected_binary=expected_binary)
            monitor_dir.symlink_to(target, target_is_directory=True)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_monitor.py",
                    "--monitor-dir",
                    str(monitor_dir),
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
        self.assertIn("monitor directory is a symlink: " + str(monitor_dir), report["no_go_reasons"])


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
    recorded_ats: dict[str, str] | None = None,
    last_successes: dict[str, str] | None = None,
    counter_overrides: dict[str, dict[str, object]] | None = None,
    config_paths: dict[str, str | None] | None = None,
    program_arguments_counts: dict[str, int] | None = None,
) -> None:
    omit = omit or set()
    failed = failed or set()
    status_labels = status_labels or {}
    plist_labels = plist_labels or {}
    recorded_ats = recorded_ats or {}
    last_successes = last_successes or {}
    counter_overrides = counter_overrides or {}
    config_paths = config_paths or {}
    program_arguments_counts = program_arguments_counts or {}
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
        counters = {"skipped_invalid": 0, "processed": 12, "appended": 1, "rewritten": 11}
        counters.update(counter_overrides.get(checkpoint, {}))
        config_path = config_paths.get(checkpoint, "/tmp/config.toml")
        record = {
            "ok": not is_failed,
            "checkpoint": checkpoint,
            "recorded_at": recorded_ats.get(checkpoint, last_success),
            "no_go_reasons": ["last_error is boom"] if is_failed else [],
            "configured": True,
            "status_launchd_label": status_labels.get(checkpoint, "com.codex.obsidian-sync"),
            "plist_label": plist_labels.get(checkpoint, "com.codex.obsidian-sync"),
            "launchd_loaded": True,
            "launchctl_loaded": True,
            "program_arg0": expected_binary,
            "program_arguments_count": program_arguments_counts.get(checkpoint, 4),
            "service_command": "service-run",
            "last_success": last_successes.get(checkpoint, last_success),
            "last_error": "boom" if is_failed else "none",
            **counters,
        }
        if config_path is not None:
            record["config_path"] = config_path
        (root / filename).write_text(
            json.dumps(record) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
