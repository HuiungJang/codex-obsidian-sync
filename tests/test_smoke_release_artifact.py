from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import unittest
from datetime import datetime
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
                self.assertIsNotNone(datetime.fromisoformat(report["generated_at"]).tzinfo)
                self.assertEqual(report["tarball_sha256"], hashlib.sha256(tarball.read_bytes()).hexdigest())
                self.assertEqual(report["checksum_sha256"], hashlib.sha256(checksum.read_bytes()).hexdigest())
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

    def test_rejects_checksum_without_tarball_filename(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-artifact-") as temp_dir:
            root = Path(temp_dir)
            tarball = write_release_tarball(root)
            checksum = write_checksum(tarball)
            checksum.write_text(hashlib.sha256(tarball.read_bytes()).hexdigest() + "\n", encoding="utf-8")
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

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"Checksum file does not reference {tarball.name}", result.stderr)
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)

    def test_rejects_tarball_member_path_traversal(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-artifact-") as temp_dir:
            root = Path(temp_dir)
            tarball = write_tarball_with_file(root, "../escape")
            checksum = write_checksum(tarball)
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-release-smoke-{root.name}"
            shutil.rmtree(work_dir, ignore_errors=True)

            try:
                result = run_smoke(tarball=tarball, checksum=checksum, work_dir=work_dir)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unsafe tar member path", result.stderr)
                self.assertFalse((work_dir / "escape").exists())
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)

    def test_rejects_tarball_symlink_member(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-artifact-") as temp_dir:
            root = Path(temp_dir)
            tarball = write_tarball_with_symlink(root)
            checksum = write_checksum(tarball)
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-release-smoke-{root.name}"
            shutil.rmtree(work_dir, ignore_errors=True)

            try:
                result = run_smoke(tarball=tarball, checksum=checksum, work_dir=work_dir)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unsupported tar member type", result.stderr)
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)

    def test_rejects_duplicate_tarball_member_path(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-artifact-") as temp_dir:
            root = Path(temp_dir)
            member_name = "codex-obsidian-sync-aarch64-apple-darwin/codex-obsidian-sync"
            tarball = write_tarball_with_files(root, [member_name, member_name])
            checksum = write_checksum(tarball)
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-release-smoke-{root.name}"
            shutil.rmtree(work_dir, ignore_errors=True)

            try:
                result = run_smoke(tarball=tarball, checksum=checksum, work_dir=work_dir)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Duplicate tar member path", result.stderr)
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)

    def test_rejects_symlinked_tarball(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-artifact-") as temp_dir:
            root = Path(temp_dir)
            tarball = write_release_tarball(root)
            checksum = write_checksum(tarball)
            tarball_link = root / "linked-release.tar.gz"
            tarball_link.symlink_to(tarball)
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-release-smoke-{root.name}"
            shutil.rmtree(work_dir, ignore_errors=True)

            try:
                result = run_smoke(tarball=tarball_link, checksum=checksum, work_dir=work_dir)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Release tarball is a symlink", result.stderr)
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)

    def test_rejects_symlinked_checksum(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-artifact-") as temp_dir:
            root = Path(temp_dir)
            tarball = write_release_tarball(root)
            checksum = write_checksum(tarball)
            checksum_link = root / "linked-release.tar.gz.sha256"
            checksum_link.symlink_to(checksum)
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-release-smoke-{root.name}"
            shutil.rmtree(work_dir, ignore_errors=True)

            try:
                result = run_smoke(tarball=tarball, checksum=checksum_link, work_dir=work_dir)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Checksum file is a symlink", result.stderr)
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)

    def test_rejects_symlinked_work_dir(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-artifact-") as temp_dir:
            root = Path(temp_dir)
            tarball = write_release_tarball(root)
            checksum = write_checksum(tarball)
            work_target = root / "work-target"
            work_target.mkdir()
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-release-smoke-{root.name}"
            remove_path(work_dir)
            work_dir.symlink_to(work_target, target_is_directory=True)

            try:
                result = run_smoke(tarball=tarball, checksum=checksum, work_dir=work_dir)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Work dir is a symlink", result.stderr)
            finally:
                remove_path(work_dir)

    def test_records_expected_codesign_identifier(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-artifact-") as temp_dir:
            root = Path(temp_dir)
            tarball = write_release_tarball(root)
            checksum = write_checksum(tarball)
            codesign = write_fake_codesign(root, identifier="com.codex.obsidian-sync")
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-release-smoke-{root.name}"
            shutil.rmtree(work_dir, ignore_errors=True)

            try:
                result = run_smoke(
                    tarball=tarball,
                    checksum=checksum,
                    work_dir=work_dir,
                    codesign=codesign,
                    expected_signing_identifier="com.codex.obsidian-sync",
                )
                report = json.loads(result.stdout)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(report["codesign"]["checked"])
                self.assertEqual(report["codesign"]["identifier"], "com.codex.obsidian-sync")
                self.assertEqual(report["codesign"]["signature"], "adhoc")
                self.assertEqual(report["codesign"]["team_identifier"], "not set")
                self.assertEqual(report["codesign"]["cdhash"], "abc123")
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)

    def test_rejects_unexpected_codesign_identifier(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-artifact-") as temp_dir:
            root = Path(temp_dir)
            tarball = write_release_tarball(root)
            checksum = write_checksum(tarball)
            codesign = write_fake_codesign(root, identifier="codex_obsidian_sync_rs-random")
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-release-smoke-{root.name}"
            shutil.rmtree(work_dir, ignore_errors=True)

            try:
                result = run_smoke(
                    tarball=tarball,
                    checksum=checksum,
                    work_dir=work_dir,
                    codesign=codesign,
                    expected_signing_identifier="com.codex.obsidian-sync",
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unexpected codesign identifier", result.stderr)
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


def write_tarball_with_file(root: Path, arcname: str) -> Path:
    return write_tarball_with_files(root, [arcname])


def write_tarball_with_files(root: Path, arcnames: list[str]) -> Path:
    tarball = root / "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
    payload = b"unsafe"
    with tarfile.open(tarball, "w:gz") as archive:
        for arcname in arcnames:
            info = tarfile.TarInfo(arcname)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return tarball


def write_tarball_with_symlink(root: Path) -> Path:
    tarball = root / "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
    with tarfile.open(tarball, "w:gz") as archive:
        info = tarfile.TarInfo("codex-obsidian-sync-aarch64-apple-darwin/codex-obsidian-sync")
        info.type = tarfile.SYMTYPE
        info.linkname = "/tmp/codex-obsidian-sync"
        archive.addfile(info)
    return tarball


def write_checksum(tarball: Path) -> Path:
    checksum = hashlib.sha256(tarball.read_bytes()).hexdigest()
    path = Path(f"{tarball}.sha256")
    path.write_text(f"{checksum}  {tarball.name}\n", encoding="utf-8")
    return path


def write_fake_codesign(root: Path, *, identifier: str) -> Path:
    codesign = root / "fake-codesign"
    codesign.write_text(
        f"""#!/usr/bin/env python3
import sys

print("Executable=" + sys.argv[-1], file=sys.stderr)
print("Identifier={identifier}", file=sys.stderr)
print("Format=Mach-O thin (arm64)", file=sys.stderr)
print("CDHash=abc123", file=sys.stderr)
print("Signature=adhoc", file=sys.stderr)
print("TeamIdentifier=not set", file=sys.stderr)
""",
        encoding="utf-8",
    )
    codesign.chmod(0o755)
    return codesign


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def run_smoke(
    *,
    tarball: Path,
    checksum: Path,
    work_dir: Path,
    codesign: Path | None = None,
    expected_signing_identifier: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
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
    ]
    if codesign is not None:
        command.extend(["--codesign", str(codesign)])
    if expected_signing_identifier is not None:
        command.extend(["--expected-signing-identifier", expected_signing_identifier])
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )


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
