from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_cutover_readiness


class AuditCutoverReadinessTests(unittest.TestCase):
    def test_reports_ready_from_offline_release_and_launchagent_inputs(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "v0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

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
        self.assertCheckOk(report, "launchagent current state")

    def test_refuses_symlinked_output_report_path(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            output_target = root / "target-report.json"
            output = root / "cutover-readiness.json"
            output_target.write_text("keep\n", encoding="utf-8")
            output.symlink_to(output_target)

            with self.assertRaisesRegex(RuntimeError, "output path is a symlink"):
                audit_cutover_readiness.resolve_output_path(output)

            self.assertEqual(output_target.read_text(encoding="utf-8"), "keep\n")

    def test_fails_when_formula_checksum_does_not_match_release_checksum(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            checksums["x86_64-apple-darwin"] = "f" * 64
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "homebrew formula: formula checksum mismatch for x86_64-apple-darwin",
            report["no_go_reasons"],
        )

    def test_fails_when_expected_rust_binary_reports_wrong_version(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.2.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "expected Rust binary: expected Rust binary version does not match release version",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_summary_is_missing(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: summary file is missing",
            report["no_go_reasons"],
        )

    def test_fails_when_release_smoke_counter_is_boolean(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))
            summary_path = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.smoke-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["note_files"] = True
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "release smoke summary:aarch64-apple-darwin: release smoke note_files is not positive",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_version_does_not_match_release(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula, version="codex-obsidian-sync 0.2.0")
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke version does not match release version",
            report["no_go_reasons"],
        )

    def test_fails_when_homebrew_smoke_version_command_output_does_not_match_release(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(
                release_dir,
                formula,
                version_command_stdout="codex-obsidian-sync 0.2.0\n",
            )
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "homebrew smoke summary: Homebrew smoke did not record expected installed binary version output",
            report["no_go_reasons"],
        )

    def test_fails_when_monitor_dir_contains_stale_records_before_cutover(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            with TemporaryDirectory(prefix="codex-obsidian-sync-monitor-", dir="/tmp") as monitor_temp:
                root = Path(temp_dir)
                release_dir = root / "dist"
                formula = root / "Formula" / "codex-obsidian-sync.rb"
                rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
                rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
                status_path = root / "status.json"
                plist_path = root / "agent.plist"
                monitor_dir = Path(monitor_temp)
                checksums = write_release_artifacts(release_dir)
                write_formula(formula, checksums)
                write_release_smoke_summaries(release_dir)
                write_homebrew_smoke_summary(release_dir, formula)
                write_status(status_path, plist_path)
                write_plist(plist_path, str(rollback_binary))
                (monitor_dir / "plus5m.json").write_text(
                    json.dumps({"ok": True, "checkpoint": "+5m"}) + "\n",
                    encoding="utf-8",
                )

                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/audit_cutover_readiness.py",
                        "--version",
                        "0.1.0",
                        "--release-dir",
                        str(release_dir),
                        "--homebrew-formula",
                        str(formula),
                        "--expected-rust-binary",
                        str(rust_binary),
                        "--rollback-binary",
                        str(rollback_binary),
                        "--status-json-file",
                        str(status_path),
                        "--plist-file",
                        str(plist_path),
                        "--expected-current-program-arg0",
                        str(rollback_binary),
                        "--monitor-dir",
                        str(monitor_dir),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "monitor prerequisites: monitor dir already contains JSON records: ['plus5m.json']",
            report["no_go_reasons"],
        )

    def test_fails_when_monitor_dir_contains_stale_non_json_entry_before_cutover(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            with TemporaryDirectory(prefix="codex-obsidian-sync-monitor-", dir="/tmp") as monitor_temp:
                root = Path(temp_dir)
                release_dir = root / "dist"
                formula = root / "Formula" / "codex-obsidian-sync.rb"
                rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
                rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
                status_path = root / "status.json"
                plist_path = root / "agent.plist"
                monitor_dir = Path(monitor_temp)
                checksums = write_release_artifacts(release_dir)
                write_formula(formula, checksums)
                write_release_smoke_summaries(release_dir)
                write_homebrew_smoke_summary(release_dir, formula)
                write_status(status_path, plist_path)
                write_plist(plist_path, str(rollback_binary))
                (monitor_dir / "latest-status.stdout").write_text("{}\n", encoding="utf-8")

                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/audit_cutover_readiness.py",
                        "--version",
                        "0.1.0",
                        "--release-dir",
                        str(release_dir),
                        "--homebrew-formula",
                        str(formula),
                        "--expected-rust-binary",
                        str(rust_binary),
                        "--rollback-binary",
                        str(rollback_binary),
                        "--status-json-file",
                        str(status_path),
                        "--plist-file",
                        str(plist_path),
                        "--expected-current-program-arg0",
                        str(rollback_binary),
                        "--monitor-dir",
                        str(monitor_dir),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "monitor prerequisites: monitor dir contains unexpected entries: ['latest-status.stdout']",
            report["no_go_reasons"],
        )

    def test_fails_when_monitor_dir_marker_is_symlink_before_cutover(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            with TemporaryDirectory(prefix="codex-obsidian-sync-monitor-", dir="/tmp") as monitor_temp:
                root = Path(temp_dir)
                release_dir = root / "dist"
                formula = root / "Formula" / "codex-obsidian-sync.rb"
                rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
                rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
                status_path = root / "status.json"
                plist_path = root / "agent.plist"
                monitor_dir = Path(monitor_temp)
                checksums = write_release_artifacts(release_dir)
                write_formula(formula, checksums)
                write_release_smoke_summaries(release_dir)
                write_homebrew_smoke_summary(release_dir, formula)
                write_status(status_path, plist_path)
                write_plist(plist_path, str(rollback_binary))
                marker_target = monitor_dir / "marker-target"
                marker_target.write_text("managed by record_cutover_monitor.py\n", encoding="utf-8")
                (monitor_dir / ".codex-obsidian-sync-cutover-monitor").symlink_to(marker_target)

                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/audit_cutover_readiness.py",
                        "--version",
                        "0.1.0",
                        "--release-dir",
                        str(release_dir),
                        "--homebrew-formula",
                        str(formula),
                        "--expected-rust-binary",
                        str(rust_binary),
                        "--rollback-binary",
                        str(rollback_binary),
                        "--status-json-file",
                        str(status_path),
                        "--plist-file",
                        str(plist_path),
                        "--expected-current-program-arg0",
                        str(rollback_binary),
                        "--monitor-dir",
                        str(monitor_dir),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("monitor prerequisites: monitor dir marker is a symlink", report["no_go_reasons"])

    def test_fails_when_monitor_dir_is_symlink_before_cutover(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            with TemporaryDirectory(prefix="codex-obsidian-sync-monitor-target-", dir="/tmp") as monitor_temp:
                root = Path(temp_dir)
                release_dir = root / "dist"
                formula = root / "Formula" / "codex-obsidian-sync.rb"
                rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
                rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
                status_path = root / "status.json"
                plist_path = root / "agent.plist"
                monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
                target_dir = Path(monitor_temp)
                checksums = write_release_artifacts(release_dir)
                write_formula(formula, checksums)
                write_release_smoke_summaries(release_dir)
                write_homebrew_smoke_summary(release_dir, formula)
                write_status(status_path, plist_path)
                write_plist(plist_path, str(rollback_binary))
                monitor_dir.symlink_to(target_dir, target_is_directory=True)

                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/audit_cutover_readiness.py",
                        "--version",
                        "0.1.0",
                        "--release-dir",
                        str(release_dir),
                        "--homebrew-formula",
                        str(formula),
                        "--expected-rust-binary",
                        str(rust_binary),
                        "--rollback-binary",
                        str(rollback_binary),
                        "--status-json-file",
                        str(status_path),
                        "--plist-file",
                        str(plist_path),
                        "--expected-current-program-arg0",
                        str(rollback_binary),
                        "--monitor-dir",
                        str(monitor_dir),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                report = json.loads(result.stdout)
                monitor_dir.unlink()

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("monitor prerequisites: monitor dir is a symlink", report["no_go_reasons"])

    def test_fails_when_release_dir_contains_unexpected_file(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))
            (release_dir / "debug.log").write_text("unexpected\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "release directory contents: release directory contains unexpected files: ['debug.log']",
            report["no_go_reasons"],
        )

    def test_fails_when_release_dir_contains_unexpected_directory(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))
            (release_dir / "scratch").mkdir()

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "release directory contents: release directory contains unexpected directories: ['scratch']",
            report["no_go_reasons"],
        )

    def test_fails_when_release_dir_contains_symlinked_artifact(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))
            package = release_dir / "codex-obsidian-sync-aarch64-apple-darwin.tar.gz"
            target = root / "outside-aarch64.tar.gz"
            target.write_bytes(package.read_bytes())
            package.unlink()
            package.symlink_to(target)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "release directory contents: release directory contains symlinks: "
            "['codex-obsidian-sync-aarch64-apple-darwin.tar.gz']",
            report["no_go_reasons"],
        )

    def test_fails_when_release_dir_is_symlink(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_target = root / "dist-target"
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_target)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_target)
            write_homebrew_smoke_summary(release_target, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))
            release_dir.symlink_to(release_target, target_is_directory=True)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("release artifact:aarch64-apple-darwin: release directory is a symlink", report["no_go_reasons"])
        self.assertIn("release directory contents: release directory is a symlink", report["no_go_reasons"])

    def test_fails_when_homebrew_formula_is_symlink(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula_target = root / "FormulaTarget" / "codex-obsidian-sync.rb"
            formula = root / "FormulaLink" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula_target, checksums)
            formula.parent.mkdir(parents=True)
            formula.symlink_to(formula_target)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("homebrew formula: Homebrew formula is a symlink", report["no_go_reasons"])

    def test_fails_when_status_snapshot_file_is_symlink(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            status_target = root / "status-target.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_target, plist_path)
            status_path.symlink_to(status_target)
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("status snapshot: evidence file is a symlink: " + str(status_path), report["no_go_reasons"])

    def test_fails_when_plist_file_is_symlink(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            plist_target = root / "agent-target.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_target, str(rollback_binary))
            plist_path.symlink_to(plist_target)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("launchagent plist: evidence file is a symlink: " + str(plist_path), report["no_go_reasons"])

    def test_fails_when_status_loaded_flag_is_not_boolean(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["launchd_loaded"] = "true"
            status_path.write_text(json.dumps(status) + "\n", encoding="utf-8")
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("status snapshot: status launchd_loaded is not boolean", report["no_go_reasons"])
        self.assertIn("launchagent current state: status launchd_loaded is not boolean true", report["no_go_reasons"])

    def test_fails_when_current_status_label_is_unexpected(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path, label="com.codex.obsidian-sync.other")
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "launchagent current state: status launchd_label does not match expected label",
            report["no_go_reasons"],
        )

    def test_fails_when_current_plist_label_is_unexpected(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary), label="com.codex.obsidian-sync.other")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "launchagent current state: LaunchAgent Label does not match expected label",
            report["no_go_reasons"],
        )

    def test_fails_when_current_plist_missing_config_argument(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary), include_config=False)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "launchagent current state: LaunchAgent ProgramArguments must include --config before service-run",
            report["no_go_reasons"],
        )

    def test_fails_when_current_program_arg0_is_relative(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, "codex-obsidian-sync")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "launchagent current state: ProgramArguments[0] is not an absolute path",
            report["no_go_reasons"],
        )

    def test_fails_when_current_status_plist_path_is_relative(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, Path("Library/LaunchAgents/com.codex.obsidian-sync.plist"))
            write_plist(plist_path, str(rollback_binary))

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "launchagent current state: status plist_path is not absolute",
            report["no_go_reasons"],
        )

    def test_fails_when_current_config_path_is_relative(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-audit-") as temp_dir:
            root = Path(temp_dir)
            release_dir = root / "dist"
            formula = root / "Formula" / "codex-obsidian-sync.rb"
            rust_binary = write_fake_binary(root / "bin" / "codex-obsidian-sync", "0.1.0")
            rollback_binary = write_fake_binary(root / "rollback" / "codex-obsidian-sync", "0.1.0")
            status_path = root / "status.json"
            plist_path = root / "agent.plist"
            monitor_dir = Path("/tmp") / f"codex-obsidian-sync-monitor-{os.getpid()}-{root.name}"
            checksums = write_release_artifacts(release_dir)
            write_formula(formula, checksums)
            write_release_smoke_summaries(release_dir)
            write_homebrew_smoke_summary(release_dir, formula)
            write_status(status_path, plist_path)
            write_plist(plist_path, str(rollback_binary), config_path="relative-config.toml")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_cutover_readiness.py",
                    "--version",
                    "0.1.0",
                    "--release-dir",
                    str(release_dir),
                    "--homebrew-formula",
                    str(formula),
                    "--expected-rust-binary",
                    str(rust_binary),
                    "--rollback-binary",
                    str(rollback_binary),
                    "--status-json-file",
                    str(status_path),
                    "--plist-file",
                    str(plist_path),
                    "--expected-current-program-arg0",
                    str(rollback_binary),
                    "--monitor-dir",
                    str(monitor_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "launchagent current state: LaunchAgent --config path is not absolute",
            report["no_go_reasons"],
        )

    def assertCheckOk(self, report: dict[str, object], name: str) -> None:
        checks = report["checks"]
        self.assertIsInstance(checks, list)
        matches = [item for item in checks if item["name"] == name]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["ok"], matches[0]["no_go_reasons"])


def write_release_artifacts(release_dir: Path) -> dict[str, str]:
    release_dir.mkdir(parents=True)
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


def write_formula(path: Path, checksums: dict[str, str]) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
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
                "",
                "  test do",
                '    assert_match "codex-obsidian-sync #{version}", shell_output("#{bin}/codex-obsidian-sync --version")',
                "  end",
                "end",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_fake_binary(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                'if [ "$1" = "--version" ]; then',
                f"  echo codex-obsidian-sync {version}",
                'elif [ "$1" = "--help" ]; then',
                "  echo usage",
                "else",
                "  echo unexpected >&2",
                "  exit 1",
                "fi",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def write_release_smoke_summaries(release_dir: Path) -> None:
    for target in ("aarch64-apple-darwin", "x86_64-apple-darwin"):
        installed_binary = "/tmp/codex-obsidian-sync/bin/codex-obsidian-sync"
        tarball = release_dir / f"codex-obsidian-sync-{target}.tar.gz"
        checksum = release_dir / f"codex-obsidian-sync-{target}.tar.gz.sha256"
        (release_dir / f"codex-obsidian-sync-{target}.smoke-summary.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "tarball": str(tarball),
                    "tarball_sha256": hashlib.sha256(tarball.read_bytes()).hexdigest(),
                    "checksum": str(checksum),
                    "checksum_sha256": hashlib.sha256(checksum.read_bytes()).hexdigest(),
                    "installed_binary": installed_binary,
                    "version": "codex-obsidian-sync 0.1.0",
                    "status_configured": True,
                    "status_json_parsed": True,
                    "inspect_count": 1,
                    "dry_run": True,
                    "processed": 1,
                    "vault_unchanged": True,
                    "uninstalled": True,
                    "installed_after": False,
                    "note_files": 1,
                    "commands": [
                        {
                            "command": [installed_binary, "--version"],
                            "returncode": 0,
                            "stdout": "codex-obsidian-sync 0.1.0\n",
                        },
                        {
                            "command": [
                                installed_binary,
                                "--config",
                                "/tmp/config.toml",
                                "status",
                                "--json",
                            ],
                            "returncode": 0,
                        },
                        {
                            "command": [
                                installed_binary,
                                "inspect-recent",
                                "--codex-home",
                                "/tmp/.codex",
                                "--limit",
                                "3",
                            ],
                            "returncode": 0,
                        },
                        {
                            "command": [
                                installed_binary,
                                "--config",
                                "/tmp/config.toml",
                                "sync-once",
                                "--dry-run-output",
                                "/tmp/output",
                            ],
                            "returncode": 0,
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
    *,
    version: str = "codex-obsidian-sync 0.1.0",
    version_command_stdout: str = "codex-obsidian-sync 0.1.0\n",
) -> None:
    installed_binary = "/tmp/codex-obsidian-sync/bin/codex-obsidian-sync"
    (release_dir / "homebrew-smoke-summary.json").write_text(
        json.dumps(
            {
                "ok": True,
                "formula": str(formula.resolve()),
                "formula_sha256": hashlib.sha256(formula.resolve().read_bytes()).hexdigest(),
                "expected_version": "0.1.0",
                "version": version,
                "installed_binary": installed_binary,
                "installed_after": False,
                "status_configured": True,
                "status_json_parsed": True,
                "dry_run": True,
                "vault_unchanged": True,
                "note_files": 1,
                "commands": [
                    {"command": ["brew", "list", "--formula", "codex-obsidian-sync"], "returncode": 1},
                    {"command": ["brew", "install", "--formula", str(formula.resolve())], "returncode": 0},
                    {"command": ["brew", "--prefix", "codex-obsidian-sync"], "returncode": 0},
                    {
                        "command": [installed_binary, "--version"],
                        "returncode": 0,
                        "stdout": version_command_stdout,
                    },
                    {
                        "command": [
                            installed_binary,
                            "--config",
                            "/tmp/config.toml",
                            "status",
                            "--json",
                        ],
                        "returncode": 0,
                    },
                    {
                        "command": [
                            installed_binary,
                            "--config",
                            "/tmp/config.toml",
                            "sync-once",
                            "--dry-run-output",
                            "/tmp/output",
                        ],
                        "returncode": 0,
                    },
                    {"command": ["brew", "test", "codex-obsidian-sync"], "returncode": 0},
                    {"command": ["brew", "uninstall", "--formula", "codex-obsidian-sync"], "returncode": 0},
                    {"command": ["brew", "list", "--formula", "codex-obsidian-sync"], "returncode": 1},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def write_status(path: Path, plist_path: Path, *, label: str = "com.codex.obsidian-sync") -> None:
    path.write_text(
        json.dumps(
            {
                "configured": True,
                "launchd_label": label,
                "launchd_loaded": True,
                "plist_path": str(plist_path),
                "last_error": "none",
                "last_summary": {"processed": 12, "skipped_invalid": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def write_plist(
    path: Path,
    program_arg0: str,
    *,
    label: str = "com.codex.obsidian-sync",
    config_path: str = "/tmp/config.toml",
    include_config: bool = True,
) -> None:
    program_arguments = [program_arg0]
    if include_config:
        program_arguments.extend(["--config", config_path])
    program_arguments.append("service-run")
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": program_arguments,
            },
            fmt=plistlib.FMT_XML,
            sort_keys=False,
        )
    )


if __name__ == "__main__":
    unittest.main()
