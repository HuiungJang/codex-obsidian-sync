from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir


class SmokeReleaseArtifactTests(unittest.TestCase):
    def test_installs_runs_and_uninstalls_tarball_binary(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-artifact-") as temp_dir:
            root = Path(temp_dir)
            tarball = write_release_tarball(root)
            checksum = write_checksum(tarball)
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-release-smoke-{root.name}"
            shutil.rmtree(work_dir, ignore_errors=True)

            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/smoke_release_artifact.py",
                        "--tarball",
                        str(tarball),
                        "--checksum",
                        str(checksum),
                        "--expected-version",
                        "0.1.0",
                        "--work-dir",
                        str(work_dir),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                report = json.loads(result.stdout)
                installed_binary = Path(report["installed_binary"])

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(report["ok"])
                self.assertTrue(report["uninstalled"])
                self.assertFalse(report["installed_after"])
                self.assertFalse(installed_binary.exists())
                self.assertEqual(report["version"], "codex-obsidian-sync 0.1.0")
                version_commands = [
                    command
                    for command in report["commands"]
                    if command["command"][-1:] == ["--version"]
                ]
                status_commands = [
                    command
                    for command in report["commands"]
                    if command["command"][-2:] == ["status", "--json"]
                ]
                inspect_commands = [
                    command
                    for command in report["commands"]
                    if "inspect-recent" in command["command"]
                ]
                sync_commands = [
                    command
                    for command in report["commands"]
                    if "sync-once" in command["command"]
                ]
                self.assertEqual(version_commands[0]["stdout"].strip(), "codex-obsidian-sync 0.1.0")
                self.assertEqual(status_commands[0]["returncode"], 0)
                self.assertEqual(inspect_commands[0]["returncode"], 0)
                self.assertEqual(sync_commands[0]["returncode"], 0)
                self.assertTrue(report["vault_unchanged"])
                self.assertEqual(report["note_files"], 1)
                self.assertEqual(Path(report["work_dir"]), work_dir.resolve())
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)


def write_release_tarball(root: Path) -> Path:
    source_dir = root / "source" / "codex-obsidian-sync-aarch64-apple-darwin"
    source_dir.mkdir(parents=True)
    binary = source_dir / "codex-obsidian-sync"
    binary.write_text(fake_binary_script(), encoding="utf-8")
    binary.chmod(0o755)

    tarball = root / "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
    with tarfile.open(tarball, "w:gz") as archive:
        archive.add(binary, arcname="codex-obsidian-sync-aarch64-apple-darwin/codex-obsidian-sync")
    return tarball


def write_checksum(tarball: Path) -> Path:
    checksum = hashlib.sha256(tarball.read_bytes()).hexdigest()
    path = Path(f"{tarball}.sha256")
    path.write_text(f"{checksum}  {tarball.name}\n", encoding="utf-8")
    return path


def fake_binary_script() -> str:
    return """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("codex-obsidian-sync 0.1.0")
    raise SystemExit(0)
if len(args) == 4 and args[0] == "--config" and args[2:] == ["status", "--json"]:
    print(json.dumps({"configured": True}))
    raise SystemExit(0)
if len(args) == 5 and args[0] == "inspect-recent" and args[1] == "--codex-home" and args[3:] == ["--limit", "3"]:
    print(json.dumps([{"id": "release-smoke"}]))
    raise SystemExit(0)
if len(args) == 5 and args[0] == "--config" and args[2] == "sync-once" and args[3] == "--dry-run-output":
    output = Path(args[4])
    note = output / "Codex" / "Conversations" / "2026" / "2026-05-16-0000-release-smoke.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Release Smoke\\n", encoding="utf-8")
    (output / "sync-state.json").write_text(json.dumps({"files": {}}) + "\\n", encoding="utf-8")
    print(json.dumps({"dry_run": True, "processed": 1}))
    raise SystemExit(0)
print("unexpected args: " + " ".join(args), file=sys.stderr)
raise SystemExit(2)
"""


if __name__ == "__main__":
    unittest.main()
