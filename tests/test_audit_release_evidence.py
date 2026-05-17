from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_release_evidence


class AuditReleaseEvidenceTests(unittest.TestCase):
    def test_reports_ready_from_complete_release_evidence(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            output = release_dir / "release-evidence-summary.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "v0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertEqual(report["tag"], "v0.1.0")
        self.assertEqual(report["no_go_reasons"], [])
        self.assertCheckOk(report, "release artifact:aarch64-apple-darwin")
        self.assertCheckOk(report, "release artifact:x86_64-apple-darwin")
        self.assertCheckOk(report, "release directory contents")
        self.assertCheckOk(report, "homebrew formula")
        self.assertCheckOk(report, "release smoke summary:aarch64-apple-darwin")
        self.assertCheckOk(report, "release smoke summary:x86_64-apple-darwin")
        self.assertCheckOk(report, "homebrew smoke summary")

    def test_accepts_homebrew_smoke_installed_from_temporary_tap(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            formula_sha256 = hashlib.sha256(formula.read_bytes()).hexdigest()
            install_formula = "codex-smoke/codex-obsidian-sync-smoke-123456789abc/codex-obsidian-sync"
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(
                release_dir,
                formula,
                install_formula=install_formula,
                summary_install_formula=install_formula,
                summary_install_formula_path=str((release_dir / "tap" / "Formula" / formula.name).resolve()),
                summary_install_formula_sha256=formula_sha256,
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertCheckOk(report, "homebrew smoke summary")
        smoke_matches = [item for item in report["checks"] if item["name"] == "homebrew smoke summary"]
        self.assertEqual(len(smoke_matches), 1)
        smoke_check = smoke_matches[0]
        self.assertEqual(smoke_check["details"]["install_formula"], install_formula)

    def test_refuses_symlinked_output_report_path(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            root = Path(temp_dir)
            output_target = root / "target-report.json"
            output = root / "release-evidence-summary.json"
            output_target.write_text("keep\n", encoding="utf-8")
            output.symlink_to(output_target)

            with self.assertRaisesRegex(RuntimeError, "output path is a symlink"):
                audit_release_evidence.resolve_output_path(output)

            self.assertEqual(output_target.read_text(encoding="utf-8"), "keep\n")

    def test_fails_when_release_smoke_summary_is_not_ok(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir, failed_target="x86_64-apple-darwin")
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:x86_64-apple-darwin: release smoke summary ok is not true",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_generated_at_is_missing(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            del summary["generated_at"]
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke generated_at is missing or invalid",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_did_not_uninstall_artifact(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir, installed_after=True)
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke did not prove the artifact install was removed",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_processed_is_not_positive(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir, processed=0)
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke processed is not positive",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_processed_differs_from_sync_stdout(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            for command in summary["commands"]:
                if "sync-once" in command["command"]:
                    command["stdout"] = json.dumps({"dry_run": True, "processed": 2}) + "\n"
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke processed does not match sync stdout",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_version_command_output_does_not_match(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(
                release_dir,
                version_command_stdout="codex-obsidian-sync 0.2.0\n",
            )
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke did not record expected installed binary version output",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_tarball_checksum_does_not_match(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir, tarball_sha256="0" * 64)
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke tarball checksum does not match target artifact",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_tarball_path_is_relative(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["tarball"] = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke tarball path is not absolute",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_tarball_path_points_elsewhere(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            release_dir.mkdir()
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            original = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            copied = root / "other" / original.name
            copied.parent.mkdir()
            shutil.copy2(original, copied)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["tarball"] = str(copied)
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke tarball path does not match target artifact",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_checksum_file_digest_does_not_match(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir, checksum_sha256="0" * 64)
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke checksum file digest does not match target artifact",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_checksum_path_is_relative(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["checksum"] = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz.sha256"
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke checksum path is not absolute",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_checksum_path_points_elsewhere(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            release_dir.mkdir()
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            original = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.tar.gz.sha256"
            copied = root / "other" / original.name
            copied.parent.mkdir()
            shutil.copy2(original, copied)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["checksum"] = str(copied)
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke checksum path does not match target artifact",
            report["no_go_reasons"],
        )

    def test_fails_when_checksum_file_does_not_reference_artifact_name(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            checksum_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.tar.gz.sha256"
            checksum_path.write_text(f"{checksums['aarch64-apple-darwin']}\n", encoding="utf-8")
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release artifact:aarch64-apple-darwin: checksum file does not reference "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz",
            report["no_go_reasons"],
        )

    def test_fails_when_checksum_file_contains_duplicate_artifact_entries(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            package_name = "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            checksum_path = release_dir / f"{package_name}.sha256"
            checksum_path.write_text(
                "\n".join(
                    [
                        f"{checksums['aarch64-apple-darwin']}  {package_name}",
                        f"{'0' * 64}  {package_name}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release artifact:aarch64-apple-darwin: checksum file contains duplicate entries for "
            "codex-obsidian-sync-aarch64-apple-darwin.tar.gz",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_command_does_not_use_installed_binary(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(
                release_dir,
                command_binary="/tmp/other/bin/codex-obsidian-sync",
            )
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke did not record successful installed binary version",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_status_command_omits_config(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            for command in summary["commands"]:
                if command["command"][-2:] == ["status", "--json"]:
                    command["command"] = [summary["installed_binary"], "status", "--json"]
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: "
            "release smoke did not record successful installed binary status with --config",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_status_configured_differs_from_status_stdout(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            for command in summary["commands"]:
                if command["command"][-2:] == ["status", "--json"]:
                    command["stdout"] = json.dumps({"configured": False}) + "\n"
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: "
            "release smoke status_configured does not match status stdout",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_inspect_command_omits_codex_home(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            for command in summary["commands"]:
                if "inspect-recent" in command["command"]:
                    command["command"] = [summary["installed_binary"], "inspect-recent"]
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: "
            "release smoke did not record successful installed binary inspect",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_inspect_count_differs_from_inspect_stdout(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            for command in summary["commands"]:
                if "inspect-recent" in command["command"]:
                    command["stdout"] = json.dumps([{"id": "one"}, {"id": "two"}]) + "\n"
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: "
            "release smoke inspect_count does not match inspect stdout",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_sync_command_uses_relative_config(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            for command in summary["commands"]:
                if "sync-once" in command["command"]:
                    command["command"][2] = "relative-config.toml"
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: "
            "release smoke did not record parseable installed binary sync summary",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_installed_binary_is_relative(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(
                release_dir,
                command_binary="bin/codex-obsidian-sync",
                installed_binary="bin/codex-obsidian-sync",
            )
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke installed_binary is not absolute",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_counter_is_boolean(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["inspect_count"] = True
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke inspect_count is not positive",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_summary_contains_no_go_reasons(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["no_go_reasons"] = ["stale smoke failure"]
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke summary contains no-go reasons",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_did_not_uninstall(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, installed_after=True)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not prove the formula was uninstalled",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_does_not_confirm_uninstall_with_brew_list(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "homebrew-smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["commands"] = summary["commands"][:-1]
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not record failed brew list after uninstall",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_dry_run_mutated_vault(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, vault_unchanged=False)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke vault_unchanged is not true",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_processed_is_not_positive(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, processed=0)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke processed is not positive",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_processed_differs_from_sync_stdout(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "homebrew-smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            for command in summary["commands"]:
                if "sync-once" in command["command"]:
                    command["stdout"] = json.dumps({"dry_run": True, "processed": 2}) + "\n"
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke processed does not match sync stdout",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_version_does_not_match(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, version="codex-obsidian-sync 0.2.0")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke version does not match release version",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_uninstall_command_failed(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, uninstall_returncode=1)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not record successful brew uninstall",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_formula_checksum_does_not_match(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, formula_sha256="0" * 64)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke formula checksum does not match generated formula",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_formula_path_is_relative(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "homebrew-smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["formula"] = formula.name
            for command in summary["commands"]:
                if command["command"][:3] == ["brew", "install", "--formula"]:
                    command["command"][3] = formula.name
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke formula path is not absolute",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_formula_path_points_elsewhere(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            release_dir.mkdir()
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            formula_copy = root / "other" / formula.name
            formula_copy.parent.mkdir()
            shutil.copy2(formula, formula_copy)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "homebrew-smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["formula"] = str(formula_copy)
            for command in summary["commands"]:
                if command["command"][:3] == ["brew", "install", "--formula"]:
                    command["command"][3] = str(formula_copy)
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke formula path does not match generated formula",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_note_files_is_boolean(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "homebrew-smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["note_files"] = True
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke note_files is not positive",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_summary_contains_no_go_reasons(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "homebrew-smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["no_go_reasons"] = ["stale Homebrew failure"]
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke summary contains no-go reasons",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_generated_at_is_invalid(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "homebrew-smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["generated_at"] = "2026-05-16 12:00:00"
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke generated_at is missing or invalid",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_install_command_uses_different_formula(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            release_dir.mkdir()
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(
                release_dir,
                formula,
                install_formula=str(root / "other" / "codex-obsidian-sync.rb"),
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not record successful brew install for recorded formula",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_install_command_is_not_brew(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, brew_command="not-brew")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not record successful brew install for recorded formula",
            report["no_go_reasons"],
        )
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not record successful brew test",
            report["no_go_reasons"],
        )
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not record successful brew uninstall",
            report["no_go_reasons"],
        )

    def test_accepts_homebrew_smoke_commands_with_absolute_brew_path(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(
                release_dir,
                formula,
                brew_command="/opt/homebrew/bin/brew",
                recorded_brew="/opt/homebrew/bin/brew",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, report["no_go_reasons"])
        self.assertTrue(report["ok"], report["no_go_reasons"])

    def test_fails_when_homebrew_smoke_installed_binary_does_not_match_brew_prefix(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(
                release_dir,
                formula,
                prefix_stdout="/tmp/other-codex-obsidian-sync\n",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke installed_binary does not match brew prefix",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_command_uses_different_brew_than_recorded(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(
                release_dir,
                formula,
                brew_command="/tmp/brew",
                recorded_brew="/opt/homebrew/bin/brew",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not record successful brew install for recorded formula",
            report["no_go_reasons"],
        )
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not record successful brew test",
            report["no_go_reasons"],
        )
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not record successful brew uninstall",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_installed_binary_is_relative(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(
                release_dir,
                formula,
                command_binary="bin/codex-obsidian-sync",
                installed_binary="bin/codex-obsidian-sync",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke installed_binary is not absolute",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_status_command_omits_config(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "homebrew-smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            for command in summary["commands"]:
                if command["command"][-2:] == ["status", "--json"]:
                    command["command"] = [summary["installed_binary"], "status", "--json"]
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not record successful installed binary status with --config",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_status_configured_differs_from_status_stdout(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            summary_path = release_dir / "homebrew-smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            for command in summary["commands"]:
                if command["command"][-2:] == ["status", "--json"]:
                    command["stdout"] = json.dumps({"configured": False}) + "\n"
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke status_configured does not match status stdout",
            report["no_go_reasons"],
        )

    def test_fails_when_release_dir_contains_unexpected_file(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            (release_dir / "debug.log").write_text("unexpected\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release directory contents: release directory contains unexpected files: ['debug.log']",
            report["no_go_reasons"],
        )

    def test_fails_when_release_dir_contains_unexpected_directory(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            (release_dir / "scratch").mkdir()

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release directory contents: release directory contains unexpected directories: ['scratch']",
            report["no_go_reasons"],
        )

    def test_fails_when_release_dir_contains_symlinked_artifact(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            release_dir.mkdir()
            checksums = write_release_artifacts(release_dir)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            package = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            target = root / "outside-aarch64.tar.gz"
            target.write_bytes(package.read_bytes())
            package.unlink()
            package.symlink_to(target)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release directory contents: release directory contains symlinks: "
            "['codex-obsidian-sync-aarch64-apple-darwin.tar.gz']",
            report["no_go_reasons"],
        )

    def test_fails_when_release_tarball_contains_duplicate_member_path(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            rewrite_tarball_with_duplicate_member(release_dir, "aarch64-apple-darwin", checksums)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release artifact:aarch64-apple-darwin: duplicate tar member path: "
            "codex-obsidian-sync-aarch64-apple-darwin/codex-obsidian-sync",
            report["no_go_reasons"],
        )

    def test_fails_when_release_tarball_binary_is_not_executable(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            rewrite_tarball_with_non_executable_binary(release_dir, "aarch64-apple-darwin", checksums)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release artifact:aarch64-apple-darwin: release tarball binary is not executable: "
            "codex-obsidian-sync-aarch64-apple-darwin/codex-obsidian-sync",
            report["no_go_reasons"],
        )

    def test_fails_when_release_tarball_binary_path_is_unexpected(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            rewrite_tarball_with_unexpected_binary_path(release_dir, "aarch64-apple-darwin", checksums)
            formula = write_formula(release_dir, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release artifact:aarch64-apple-darwin: release tarball binary path is unexpected: "
            "unexpected/codex-obsidian-sync",
            report["no_go_reasons"],
        )

    def test_fails_when_release_dir_is_symlink(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            root = Path(temp_dir)
            release_target = root / "dist-target"
            release_target.mkdir()
            release_dir = root / "dist"
            checksums = write_release_artifacts(release_target)
            formula = write_formula(release_target, checksums)
            write_release_smoke_summaries(release_target)
            write_homebrew_smoke_summary(release_target, formula)
            release_dir.symlink_to(release_target, target_is_directory=True)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(release_dir / formula.name),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn("release artifact:aarch64-apple-darwin: release directory is a symlink", report["no_go_reasons"])
        self.assertIn("release directory contents: release directory is a symlink", report["no_go_reasons"])

    def test_fails_when_homebrew_formula_is_symlink(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            release_dir.mkdir()
            checksums = write_release_artifacts(release_dir)
            formula_target_dir = root / "FormulaTarget"
            formula_link_dir = root / "FormulaLink"
            formula_target_dir.mkdir()
            formula_link_dir.mkdir()
            formula_target = write_formula(formula_target_dir, checksums)
            formula = formula_link_dir / formula_target.name
            formula.symlink_to(formula_target)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn("homebrew formula: Homebrew formula is a symlink", report["no_go_reasons"])

    def test_fails_when_homebrew_formula_test_block_is_missing(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            write_release_smoke_summaries(release_dir)
            formula = write_formula(release_dir, checksums, include_test=False)
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn("homebrew formula: formula test block is missing", report["no_go_reasons"])
        self.assertIn(
            "homebrew formula: formula test does not run installed binary --version",
            report["no_go_reasons"],
        )
        self.assertIn(
            "homebrew formula: formula test does not assert the installed binary version",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_formula_url_is_only_present_in_comment(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            write_release_smoke_summaries(release_dir)
            formula = write_formula(release_dir, checksums)
            expected_url = (
                "https://github.com/HuiungJang/codex-obsidian-sync/releases/download/v0.1.0/"
                "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            )
            wrong_url = expected_url.replace("v0.1.0", "v0.2.0")
            formula.write_text(
                formula.read_text(encoding="utf-8").replace(
                    f'      url "{expected_url}"',
                    f'      # url "{expected_url}"\n      url "{wrong_url}"',
                ),
                encoding="utf-8",
            )
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew formula: formula URL is missing for aarch64-apple-darwin",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_formula_checksum_is_only_present_in_comment(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-release-evidence-") as temp_dir:
            release_dir = Path(temp_dir)
            checksums = write_release_artifacts(release_dir)
            write_release_smoke_summaries(release_dir)
            formula = write_formula(release_dir, checksums)
            expected_checksum = checksums["x86_64-apple-darwin"]
            wrong_checksum = "0" * 64
            formula.write_text(
                formula.read_text(encoding="utf-8").replace(
                    f'      sha256 "{expected_checksum}"',
                    f'      # sha256 "{expected_checksum}"\n      sha256 "{wrong_checksum}"',
                ),
                encoding="utf-8",
            )
            write_homebrew_smoke_summary(release_dir, formula)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_release_evidence.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "homebrew formula: formula checksum mismatch for x86_64-apple-darwin",
            report["no_go_reasons"],
        )

    def assertCheckOk(self, report: dict[str, object], name: str) -> None:
        checks = report["checks"]
        self.assertIsInstance(checks, list)
        matches = [item for item in checks if item["name"] == name]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["ok"], matches[0]["no_go_reasons"])


def write_release_artifacts(release_dir: Path) -> dict[str, str]:
    checksums = {}
    for target in ("aarch64-apple-darwin", "x86_64-apple-darwin"):
        package_name = f"codex-obsidian-sync-{target}.tar.gz"
        package = release_dir / package_name
        source_dir = release_dir / f"source-{target}"
        source_dir.mkdir()
        binary = source_dir / "codex-obsidian-sync"
        binary.write_text("#!/bin/sh\necho codex-obsidian-sync 0.1.0\n", encoding="utf-8")
        binary.chmod(0o755)
        with tarfile.open(package, "w:gz") as archive:
            archive.add(binary, arcname=f"codex-obsidian-sync-{target}/codex-obsidian-sync")
        shutil.rmtree(source_dir)
        checksum = hashlib.sha256(package.read_bytes()).hexdigest()
        checksums[target] = checksum
        (release_dir / f"{package_name}.sha256").write_text(f"{checksum}  {package_name}\n", encoding="utf-8")
    return checksums


def rewrite_tarball_with_duplicate_member(release_dir: Path, target: str, checksums: dict[str, str]) -> None:
    package_name = f"codex-obsidian-sync-{target}.tar.gz"
    package = release_dir / package_name
    scratch = release_dir / f"duplicate-source-{target}"
    scratch.mkdir()
    binary = scratch / "codex-obsidian-sync"
    binary.write_text("#!/bin/sh\necho codex-obsidian-sync 0.1.0\n", encoding="utf-8")
    binary.chmod(0o755)
    arcname = f"codex-obsidian-sync-{target}/codex-obsidian-sync"
    with tarfile.open(package, "w:gz") as archive:
        archive.add(binary, arcname=arcname)
        archive.add(binary, arcname=arcname)
    shutil.rmtree(scratch)
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    checksums[target] = checksum
    (release_dir / f"{package_name}.sha256").write_text(f"{checksum}  {package_name}\n", encoding="utf-8")


def rewrite_tarball_with_non_executable_binary(release_dir: Path, target: str, checksums: dict[str, str]) -> None:
    package_name = f"codex-obsidian-sync-{target}.tar.gz"
    package = release_dir / package_name
    scratch = release_dir / f"non-executable-source-{target}"
    scratch.mkdir()
    binary = scratch / "codex-obsidian-sync"
    binary.write_text("#!/bin/sh\necho codex-obsidian-sync 0.1.0\n", encoding="utf-8")
    binary.chmod(0o644)
    with tarfile.open(package, "w:gz") as archive:
        archive.add(binary, arcname=f"codex-obsidian-sync-{target}/codex-obsidian-sync")
    shutil.rmtree(scratch)
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    checksums[target] = checksum
    (release_dir / f"{package_name}.sha256").write_text(f"{checksum}  {package_name}\n", encoding="utf-8")


def rewrite_tarball_with_unexpected_binary_path(release_dir: Path, target: str, checksums: dict[str, str]) -> None:
    package_name = f"codex-obsidian-sync-{target}.tar.gz"
    package = release_dir / package_name
    scratch = release_dir / f"unexpected-path-source-{target}"
    scratch.mkdir()
    binary = scratch / "codex-obsidian-sync"
    binary.write_text("#!/bin/sh\necho codex-obsidian-sync 0.1.0\n", encoding="utf-8")
    binary.chmod(0o755)
    with tarfile.open(package, "w:gz") as archive:
        archive.add(binary, arcname="unexpected/codex-obsidian-sync")
    shutil.rmtree(scratch)
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    checksums[target] = checksum
    (release_dir / f"{package_name}.sha256").write_text(f"{checksum}  {package_name}\n", encoding="utf-8")


def write_formula(release_dir: Path, checksums: dict[str, str], *, include_test: bool = True) -> Path:
    path = release_dir / "codex-obsidian-sync.rb"
    lines = [
        "class CodexObsidianSync < Formula",
        '  desc "Sync local Codex conversations into an Obsidian vault"',
        '  homepage "https://github.com/HuiungJang/codex-obsidian-sync"',
        '  license "Apache-2.0"',
        '  version "0.1.0"',
        "",
        "  depends_on :macos",
        "",
        "  on_macos do",
        "    on_arm do",
        '      url "https://github.com/HuiungJang/codex-obsidian-sync/releases/download/v0.1.0/codex-obsidian-sync-aarch64-apple-darwin.tar.gz"',
        f'      sha256 "{checksums["aarch64-apple-darwin"]}"',
        "    end",
        "",
        "    on_intel do",
        '      url "https://github.com/HuiungJang/codex-obsidian-sync/releases/download/v0.1.0/codex-obsidian-sync-x86_64-apple-darwin.tar.gz"',
        f'      sha256 "{checksums["x86_64-apple-darwin"]}"',
        "    end",
        "  end",
        "",
        "  def install",
        '    bin.install "codex-obsidian-sync"',
        "  end",
    ]
    if include_test:
        lines.extend(
            [
                "",
                "  test do",
                '    assert_match "codex-obsidian-sync #{version}", shell_output("#{bin}/codex-obsidian-sync --version")',
                "  end",
            ]
        )
    lines.extend(["end", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_release_smoke_summaries(
    release_dir: Path,
    failed_target: str | None = None,
    installed_after: bool = False,
    version_command_stdout: str = "codex-obsidian-sync 0.1.0\n",
    command_binary: str = "/tmp/codex-obsidian-sync/bin/codex-obsidian-sync",
    installed_binary: str = "/tmp/codex-obsidian-sync/bin/codex-obsidian-sync",
    tarball_sha256: str | None = None,
    checksum_sha256: str | None = None,
    processed: int = 1,
) -> None:
    for target in ("aarch64-apple-darwin", "x86_64-apple-darwin"):
        tarball = release_dir / f"codex-obsidian-sync-{target}.tar.gz"
        checksum = release_dir / f"codex-obsidian-sync-{target}.tar.gz.sha256"
        (release_dir / f"codex-obsidian-sync-{target}.smoke-summary.json").write_text(
            json.dumps(
                {
                    "ok": target != failed_target,
                    "generated_at": "2026-05-16T00:00:00+00:00",
                    "tarball": str(tarball),
                    "tarball_sha256": tarball_sha256 or hashlib.sha256(tarball.read_bytes()).hexdigest(),
                    "checksum": str(checksum),
                    "checksum_sha256": checksum_sha256 or hashlib.sha256(checksum.read_bytes()).hexdigest(),
                    "installed_binary": installed_binary,
                    "version": "codex-obsidian-sync 0.1.0",
                    "status_configured": True,
                    "status_json_parsed": True,
                    "inspect_count": 1,
                    "dry_run": True,
                    "processed": processed,
                    "vault_unchanged": True,
                    "uninstalled": not installed_after,
                    "installed_after": installed_after,
                    "note_files": 1,
                    "commands": [
                        {
                            "command": [command_binary, "--version"],
                            "returncode": 0,
                            "stdout": version_command_stdout,
                        },
                        {
                            "command": [
                                command_binary,
                                "--config",
                                "/tmp/config.toml",
                                "status",
                                "--json",
                            ],
                            "returncode": 0,
                            "stdout": json.dumps({"configured": True}) + "\n",
                        },
                        {
                            "command": [
                                command_binary,
                                "inspect-recent",
                                "--codex-home",
                                "/tmp/.codex",
                                "--limit",
                                "3",
                            ],
                            "returncode": 0,
                            "stdout": json.dumps([{"id": "session"}]) + "\n",
                        },
                        {
                            "command": [
                                command_binary,
                                "--config",
                                "/tmp/config.toml",
                                "sync-once",
                                "--dry-run-output",
                                "/tmp/output",
                            ],
                            "returncode": 0,
                            "stdout": json.dumps({"dry_run": True, "processed": processed}) + "\n",
                        },
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )


def write_homebrew_smoke_summary(
    release_dir: Path,
    formula: Path,
    installed_after: bool = False,
    uninstall_returncode: int = 0,
    vault_unchanged: bool = True,
    version: str = "codex-obsidian-sync 0.1.0",
    formula_sha256: str | None = None,
    install_formula: str | None = None,
    command_binary: str = "/tmp/codex-obsidian-sync/bin/codex-obsidian-sync",
    installed_binary: str = "/tmp/codex-obsidian-sync/bin/codex-obsidian-sync",
    brew_command: str = "brew",
    recorded_brew: str | None = None,
    prefix_stdout: str = "/tmp/codex-obsidian-sync\n",
    processed: int = 1,
    summary_install_formula: str | None = None,
    summary_install_formula_path: str | None = None,
    summary_install_formula_sha256: str | None = None,
) -> None:
    formula_path = str(formula.resolve())
    install_formula_path = install_formula or formula_path
    summary = {
        "ok": True,
        "generated_at": "2026-05-16T00:00:00+00:00",
        "formula": formula_path,
        "formula_sha256": formula_sha256 or hashlib.sha256(formula.resolve().read_bytes()).hexdigest(),
        "expected_version": "0.1.0",
        "version": version,
        "installed_binary": installed_binary,
        "installed_after": installed_after,
        "status_configured": True,
        "status_json_parsed": True,
        "dry_run": True,
        "processed": processed,
        "vault_unchanged": vault_unchanged,
        "note_files": 1,
        "commands": [
            {"command": [brew_command, "list", "--formula", "codex-obsidian-sync"], "returncode": 1},
            {"command": [brew_command, "install", "--formula", install_formula_path], "returncode": 0},
            {
                "command": [brew_command, "--prefix", "codex-obsidian-sync"],
                "returncode": 0,
                "stdout": prefix_stdout,
            },
            {
                "command": [command_binary, "--version"],
                "returncode": 0,
                "stdout": f"{version}\n",
            },
            {
                "command": [
                    command_binary,
                    "--config",
                    "/tmp/config.toml",
                    "status",
                    "--json",
                ],
                "returncode": 0,
                "stdout": json.dumps({"configured": True}) + "\n",
            },
            {
                "command": [
                    command_binary,
                    "--config",
                    "/tmp/config.toml",
                    "sync-once",
                    "--dry-run-output",
                    "/tmp/output",
                ],
                "returncode": 0,
                "stdout": json.dumps({"dry_run": True, "processed": processed}) + "\n",
            },
            {"command": [brew_command, "test", "codex-obsidian-sync"], "returncode": 0},
            {
                "command": [brew_command, "uninstall", "--formula", "codex-obsidian-sync"],
                "returncode": uninstall_returncode,
            },
            {"command": [brew_command, "list", "--formula", "codex-obsidian-sync"], "returncode": 1},
        ],
    }
    if recorded_brew is not None:
        summary["brew"] = recorded_brew
    if summary_install_formula is not None:
        summary["install_formula"] = summary_install_formula
    if summary_install_formula_path is not None:
        summary["install_formula_path"] = summary_install_formula_path
    if summary_install_formula_sha256 is not None:
        summary["install_formula_sha256"] = summary_install_formula_sha256
    (release_dir / "homebrew-smoke-summary.json").write_text(
        json.dumps(summary) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
