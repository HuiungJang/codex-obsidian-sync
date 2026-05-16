from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class AuditCutoverBackupTests(unittest.TestCase):
    def test_reports_ready_for_intact_backup_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_backup.py",
                    "--backup-dir",
                    str(backup_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertEqual(report["no_go_reasons"], [])

    def test_allows_live_capture_stderr_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            (backup_dir / "status.stderr").write_text("", encoding="utf-8")
            (backup_dir / "launchctl-print.stderr").write_text("", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_backup.py",
                    "--backup-dir",
                    str(backup_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])

    def test_fails_when_required_file_checksum_changes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            (backup_dir / "sync-state.json").write_text('{"files": {"changed": true}}\n', encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_backup.py",
                    "--backup-dir",
                    str(backup_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("backup file checksum mismatch: sync-state.json", report["no_go_reasons"])

    def test_fails_when_required_file_size_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            manifest_path = backup_dir / "backup-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest["files"]:
                if entry["name"] == "sync-state.json":
                    del entry["size"]
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_backup.py",
                    "--backup-dir",
                    str(backup_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("required backup file size is missing or invalid: sync-state.json", report["no_go_reasons"])

    def test_fails_when_required_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            (backup_dir / "launchagent.plist").unlink()

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_backup.py",
                    "--backup-dir",
                    str(backup_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("backup file is missing: launchagent.plist", report["no_go_reasons"])

    def test_fails_when_backup_directory_contains_unexpected_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            (backup_dir / "untracked.json").write_text("{}\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_backup.py",
                    "--backup-dir",
                    str(backup_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("unexpected backup directory entry: untracked.json", report["no_go_reasons"])

    def test_fails_with_report_when_manifest_json_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            (backup_dir / "backup-manifest.json").write_text("{not json\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_backup.py",
                    "--backup-dir",
                    str(backup_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(reason.startswith("backup manifest is invalid:") for reason in report["no_go_reasons"])
        )

    def test_fails_with_report_when_manifest_json_is_not_object(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            (backup_dir / "backup-manifest.json").write_text("[]\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_backup.py",
                    "--backup-dir",
                    str(backup_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertTrue(
            any(reason.startswith("backup manifest is invalid:") for reason in report["no_go_reasons"])
        )

    def test_fails_when_manifest_file_path_points_outside_backup_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            manifest_path = backup_dir / "backup-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest["files"]:
                if entry["name"] == "sync-state.json":
                    entry["path"] = entry["source"]
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_backup.py",
                    "--backup-dir",
                    str(backup_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("backup file path is outside backup directory: sync-state.json", report["no_go_reasons"])

    def test_fails_when_backup_directory_is_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            root = Path(temp_dir)
            backup_dir = create_backup(root)
            symlink_dir = root / "backup-link"
            symlink_dir.symlink_to(backup_dir, target_is_directory=True)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_backup.py",
                    "--backup-dir",
                    str(symlink_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("backup directory is a symlink: " + str(symlink_dir), report["no_go_reasons"])


def create_backup(root: Path) -> Path:
    state = root / "sync-state-source.json"
    plist = root / "agent-source.plist"
    status = root / "status.json"
    launchctl = root / "launchctl.txt"
    backup_dir = Path(tempfile.gettempdir()) / f"codex-obsidian-sync-backup-{root.name}"
    state.write_text('{"files": {}}\n', encoding="utf-8")
    plist.write_text("<plist><dict></dict></plist>\n", encoding="utf-8")
    status.write_text(
        json.dumps({"configured": True, "state_file": str(state), "plist_path": str(plist)}) + "\n",
        encoding="utf-8",
    )
    launchctl.write_text("state = running\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/capture_cutover_backup.py",
            "--output-dir",
            str(backup_dir),
            "--status-json-file",
            str(status),
            "--launchctl-print-file",
            str(launchctl),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return backup_dir


if __name__ == "__main__":
    unittest.main()
