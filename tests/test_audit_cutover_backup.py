from __future__ import annotations

import hashlib
import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_cutover_backup


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

    def test_refuses_symlinked_output_report_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            root = Path(temp_dir)
            output_target = root / "target-report.json"
            output = root / "backup-audit.json"
            output_target.write_text("keep\n", encoding="utf-8")
            output.symlink_to(output_target)

            with self.assertRaisesRegex(RuntimeError, "output path is a symlink"):
                audit_cutover_backup.resolve_output_path(output)

            self.assertEqual(output_target.read_text(encoding="utf-8"), "keep\n")

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

    def test_fails_when_required_file_exists_flag_is_false(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            manifest_path = backup_dir / "backup-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest["files"]:
                if entry["name"] == "sync-state.json":
                    entry["exists"] = False
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
        self.assertIn("required backup file exists flag is not true: sync-state.json", report["no_go_reasons"])

    def test_fails_when_manifest_generated_at_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            manifest_path = backup_dir / "backup-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            del manifest["generated_at"]
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
        self.assertIn("backup manifest generated_at is missing or invalid", report["no_go_reasons"])

    def test_fails_when_manifest_generated_at_is_timezone_naive(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            manifest_path = backup_dir / "backup-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["generated_at"] = "2026-05-17T00:00:00"
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
        self.assertIn("backup manifest generated_at is missing or invalid", report["no_go_reasons"])

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

    def test_fails_when_launchagent_backup_plist_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            plist = backup_dir / "launchagent.plist"
            plist.write_text("not a plist\n", encoding="utf-8")
            update_manifest_file_entry(backup_dir, "launchagent.plist")

            report = audit_cutover_backup.audit_backup_dir(backup_dir)

        self.assertFalse(report["ok"])
        self.assertTrue(
            any(reason.startswith("backup launchagent.plist is invalid:") for reason in report["no_go_reasons"])
        )

    def test_fails_when_launchagent_backup_label_is_unexpected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            write_launchagent_plist(
                backup_dir / "launchagent.plist",
                label="com.codex.obsidian-sync.other",
            )
            update_manifest_file_entry(backup_dir, "launchagent.plist")

            report = audit_cutover_backup.audit_backup_dir(backup_dir)

        self.assertFalse(report["ok"])
        self.assertIn(
            "backup LaunchAgent Label does not match expected label",
            report["no_go_reasons"],
        )

    def test_fails_when_launchagent_backup_config_path_is_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            write_launchagent_plist(backup_dir / "launchagent.plist", config_path="config.toml")
            update_manifest_file_entry(backup_dir, "launchagent.plist")

            report = audit_cutover_backup.audit_backup_dir(backup_dir)

        self.assertFalse(report["ok"])
        self.assertIn(
            "backup LaunchAgent --config path is not absolute",
            report["no_go_reasons"],
        )

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

    def test_fails_when_manifest_file_path_basename_does_not_match_entry_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            manifest_path = backup_dir / "backup-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest["files"]:
                if entry["name"] == "status.json":
                    entry["path"] = str(backup_dir / "sync-state.json")
                    source = backup_dir / "sync-state.json"
                    entry["size"] = source.stat().st_size
                    entry["sha256"] = checksum(source)
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
        self.assertIn(
            "backup file path basename does not match entry name: status.json",
            report["no_go_reasons"],
        )

    def test_fails_when_manifest_reuses_same_file_path_for_multiple_entries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            manifest_path = backup_dir / "backup-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest["files"]:
                if entry["name"] == "sync-state.json":
                    entry["path"] = str(backup_dir / "status.json")
                    source = backup_dir / "status.json"
                    entry["size"] = source.stat().st_size
                    entry["sha256"] = checksum(source)
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
        self.assertIn("duplicate backup file path: status.json", report["no_go_reasons"])

    def test_fails_when_manifest_source_disagrees_with_status_state_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            manifest_path = backup_dir / "backup-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest["files"]:
                if entry["name"] == "sync-state.json":
                    entry["source"] = str(backup_dir / "other-sync-state.json")
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
        self.assertIn(
            "backup file source does not match status state_file: sync-state.json",
            report["no_go_reasons"],
        )

    def test_fails_when_required_manifest_source_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            manifest_path = backup_dir / "backup-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest["files"]:
                if entry["name"] == "launchagent.plist":
                    del entry["source"]
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
        self.assertIn("backup file source is missing: launchagent.plist", report["no_go_reasons"])

    def test_fails_when_captured_status_source_path_is_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            status_path = backup_dir / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["plist_path"] = "Library/LaunchAgents/com.codex.obsidian-sync.plist"
            status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

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
        self.assertIn("backup status.json plist_path is not absolute", report["no_go_reasons"])

    def test_fails_when_captured_status_config_path_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            status_path = backup_dir / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            del status["config_path"]
            status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
            update_manifest_file_entry(backup_dir, "status.json")

            report = audit_cutover_backup.audit_backup_dir(backup_dir)

        self.assertFalse(report["ok"])
        self.assertIn("backup status.json config_path is missing", report["no_go_reasons"])

    def test_fails_when_captured_status_config_path_is_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            status_path = backup_dir / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["config_path"] = "config.toml"
            status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
            update_manifest_file_entry(backup_dir, "status.json")

            report = audit_cutover_backup.audit_backup_dir(backup_dir)

        self.assertFalse(report["ok"])
        self.assertIn("backup status.json config_path is not absolute", report["no_go_reasons"])

    def test_fails_when_captured_status_config_path_differs_from_launchagent_config(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-audit-") as temp_dir:
            backup_dir = create_backup(Path(temp_dir))
            status_path = backup_dir / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["config_path"] = "/tmp/other-config.toml"
            status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
            update_manifest_file_entry(backup_dir, "status.json")

            report = audit_cutover_backup.audit_backup_dir(backup_dir)

        self.assertFalse(report["ok"])
        self.assertIn(
            "backup LaunchAgent --config path does not match status config_path",
            report["no_go_reasons"],
        )

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
    write_launchagent_plist(plist)
    status.write_text(
        json.dumps(
            {
                "configured": True,
                "state_file": str(state),
                "plist_path": str(plist),
                "config_path": "/tmp/config.toml",
            }
        )
        + "\n",
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


def write_launchagent_plist(
    path: Path,
    *,
    label: str = "com.codex.obsidian-sync",
    config_path: str = "/tmp/config.toml",
) -> None:
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": [
                    "/tmp/codex-obsidian-sync-python",
                    "-m",
                    "codex_obsidian_sync.cli",
                    "--config",
                    config_path,
                    "service-run",
                ],
            },
            fmt=plistlib.FMT_XML,
            sort_keys=False,
        )
    )


def update_manifest_file_entry(backup_dir: Path, name: str) -> None:
    manifest_path = backup_dir / "backup-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = backup_dir / name
    for entry in manifest["files"]:
        if entry["name"] == name:
            entry["size"] = path.stat().st_size
            entry["sha256"] = checksum(path)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
