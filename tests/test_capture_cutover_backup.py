from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CaptureCutoverBackupTests(unittest.TestCase):
    def test_captures_state_plist_status_and_launchctl_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-test-") as temp_dir:
            root = Path(temp_dir)
            state = root / "sync-state.json"
            plist = root / "agent.plist"
            status = root / "status.json"
            launchctl = root / "launchctl.txt"
            output_dir = Path(tempfile.gettempdir()) / f"codex-obsidian-sync-backup-{root.name}"
            state_content = '{"files": {}}\n'
            plist_content = "<plist><dict></dict></plist>\n"
            state.write_text(state_content, encoding="utf-8")
            plist.write_text(plist_content, encoding="utf-8")
            state_checksum = hashlib.sha256(state_content.encode("utf-8")).hexdigest()
            plist_checksum = hashlib.sha256(plist_content.encode("utf-8")).hexdigest()
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
                    str(output_dir),
                    "--status-json-file",
                    str(status),
                    "--launchctl-print-file",
                    str(launchctl),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            manifest = json.loads((output_dir / "backup-manifest.json").read_text(encoding="utf-8"))
            backed_up_state = (output_dir / "sync-state.json").read_text(encoding="utf-8")
            backed_up_plist = (output_dir / "launchagent.plist").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(manifest["ok"], manifest["no_go_reasons"])
        self.assertEqual(backed_up_state, state_content)
        self.assertEqual(backed_up_plist, plist_content)
        self.assertRecordChecksum(manifest, "sync-state.json", state_checksum)
        self.assertRecordChecksum(manifest, "launchagent.plist", plist_checksum)

    def test_fails_when_state_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-test-") as temp_dir:
            root = Path(temp_dir)
            plist = root / "agent.plist"
            status = root / "status.json"
            launchctl = root / "launchctl.txt"
            output_dir = Path(tempfile.gettempdir()) / f"codex-obsidian-sync-backup-{root.name}"
            plist.write_text("<plist><dict></dict></plist>\n", encoding="utf-8")
            status.write_text(
                json.dumps({"configured": True, "state_file": str(root / "missing.json"), "plist_path": str(plist)})
                + "\n",
                encoding="utf-8",
            )
            launchctl.write_text("state = running\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/capture_cutover_backup.py",
                    "--output-dir",
                    str(output_dir),
                    "--status-json-file",
                    str(status),
                    "--launchctl-print-file",
                    str(launchctl),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            manifest = json.loads((output_dir / "backup-manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 1)
        self.assertFalse(manifest["ok"])
        self.assertIn("required backup files are missing: ['sync-state.json']", manifest["no_go_reasons"])

    def test_refuses_non_empty_backup_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-backup-test-") as temp_dir:
            output_dir = Path(tempfile.gettempdir()) / f"codex-obsidian-sync-backup-{Path(temp_dir).name}"
            output_dir.mkdir(exist_ok=True)
            (output_dir / "existing").write_text("keep\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/capture_cutover_backup.py",
                    "--output-dir",
                    str(output_dir),
                    "--status-json-file",
                    str(Path(temp_dir) / "status.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("backup directory is not empty", result.stderr)

    def assertRecordChecksum(self, manifest: dict[str, object], name: str, checksum: str) -> None:
        files = manifest["files"]
        self.assertIsInstance(files, list)
        matches = [record for record in files if record["name"] == name]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["sha256"], checksum)


if __name__ == "__main__":
    unittest.main()
