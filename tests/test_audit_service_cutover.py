from __future__ import annotations

import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class AuditServiceCutoverTests(unittest.TestCase):
    def test_reports_ready_from_python_stopped_and_rust_started_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"], report["no_go_reasons"])
        self.assertEqual(report["snapshots"]["pre"]["plist_label"], "com.codex.obsidian-sync")
        self.assertEqual(report["snapshots"]["pre"]["program_arg0"], python_binary)
        self.assertFalse(report["snapshots"]["stopped"]["launchd_loaded"])
        self.assertEqual(report["snapshots"]["post"]["program_arg0"], rust_binary)

    def test_fails_when_stopped_snapshot_is_still_loaded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(
                root,
                python_binary=python_binary,
                rust_binary=rust_binary,
                stopped_loaded=True,
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("stopped: status still reports launchd_loaded=true", report["no_go_reasons"])
        self.assertIn("stopped: launchctl print still looks loaded", report["no_go_reasons"])

    def test_fails_when_post_plist_still_points_to_python_binary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(
                root,
                python_binary=python_binary,
                rust_binary=rust_binary,
                post_program_arg0=python_binary,
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("post: ProgramArguments[0] does not match expected binary", report["no_go_reasons"])

    def test_fails_when_expected_rust_binary_is_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("expected Rust ProgramArguments[0] must be an absolute path", report["no_go_reasons"])
        self.assertIn("post: ProgramArguments[0] is not an absolute path", report["no_go_reasons"])

    def test_fails_when_plist_program_arguments_is_not_a_list(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            files["post_plist"].write_bytes(
                plistlib.dumps(
                    {
                        "Label": "com.codex.obsidian-sync",
                        "ProgramArguments": rust_binary,
                    },
                    fmt=plistlib.FMT_XML,
                    sort_keys=False,
                )
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("post: LaunchAgent ProgramArguments is not a list", report["no_go_reasons"])

    def test_fails_when_plist_program_arguments_contains_empty_value(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            write_plist(files["post_plist"], rust_binary, label="com.codex.obsidian-sync", extra_argument="")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn(
            "post: LaunchAgent ProgramArguments contains non-string or empty values",
            report["no_go_reasons"],
        )

    def test_fails_when_post_launchctl_does_not_find_service(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            files["post_launchctl"].write_text("could not find service\n", encoding="utf-8")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("post: launchctl print did not return a loaded service", report["no_go_reasons"])

    def test_fails_when_post_plist_label_is_unexpected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(
                root,
                python_binary=python_binary,
                rust_binary=rust_binary,
                post_plist_label="com.codex.obsidian-sync.other",
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("post: LaunchAgent Label does not match expected label", report["no_go_reasons"])

    def test_fails_when_stopped_status_label_is_unexpected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(
                root,
                python_binary=python_binary,
                rust_binary=rust_binary,
                stopped_status_label="com.codex.obsidian-sync.other",
            )

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertIn("stopped: status launchd_label does not match expected label", report["no_go_reasons"])

    def test_fails_with_report_when_status_json_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            files["post_status"].write_text("{not json\n", encoding="utf-8")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["snapshots"], {})
        self.assertTrue(
            any(reason.startswith("cutover evidence is invalid:") for reason in report["no_go_reasons"])
        )

    def test_fails_with_report_when_plist_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            files["post_plist"].write_text("not a plist\n", encoding="utf-8")

            result = run_audit(files, python_binary, rust_binary)
            report = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["snapshots"], {})
        self.assertTrue(
            any(reason.startswith("cutover evidence is invalid:") for reason in report["no_go_reasons"])
        )

    def test_refuses_symlinked_evidence_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-obsidian-sync-service-cutover-") as temp_dir:
            root = Path(temp_dir)
            python_binary = "/Users/test/.local/bin/codex-obsidian-sync"
            rust_binary = "/opt/homebrew/bin/codex-obsidian-sync"
            files = write_cutover_files(root, python_binary=python_binary, rust_binary=rust_binary)
            target = root / "external-post.plist"
            target.write_bytes(files["post_plist"].read_bytes())
            files["post_plist"].unlink()
            files["post_plist"].symlink_to(target)

            result = run_audit(files, python_binary, rust_binary)

        self.assertEqual(result.returncode, 1)
        self.assertIn("evidence file is a symlink", result.stderr)


def run_audit(files: dict[str, Path], python_binary: str, rust_binary: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/audit_service_cutover.py",
            "--pre-status-json-file",
            str(files["pre_status"]),
            "--pre-plist-file",
            str(files["pre_plist"]),
            "--pre-launchctl-print-file",
            str(files["pre_launchctl"]),
            "--stopped-status-json-file",
            str(files["stopped_status"]),
            "--stopped-launchctl-print-file",
            str(files["stopped_launchctl"]),
            "--post-status-json-file",
            str(files["post_status"]),
            "--post-plist-file",
            str(files["post_plist"]),
            "--post-launchctl-print-file",
            str(files["post_launchctl"]),
            "--expected-python-program-arg0",
            python_binary,
            "--expected-rust-program-arg0",
            rust_binary,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def write_cutover_files(
    root: Path,
    *,
    python_binary: str,
    rust_binary: str,
    stopped_loaded: bool = False,
    post_program_arg0: str | None = None,
    pre_status_label: str = "com.codex.obsidian-sync",
    stopped_status_label: str = "com.codex.obsidian-sync",
    post_status_label: str = "com.codex.obsidian-sync",
    pre_plist_label: str = "com.codex.obsidian-sync",
    post_plist_label: str = "com.codex.obsidian-sync",
) -> dict[str, Path]:
    post_program_arg0 = post_program_arg0 or rust_binary
    files = {
        "pre_status": root / "pre-status.json",
        "pre_plist": root / "pre.plist",
        "pre_launchctl": root / "pre-launchctl.txt",
        "stopped_status": root / "stopped-status.json",
        "stopped_launchctl": root / "stopped-launchctl.txt",
        "post_status": root / "post-status.json",
        "post_plist": root / "post.plist",
        "post_launchctl": root / "post-launchctl.txt",
    }
    write_status(files["pre_status"], launchd_loaded=True, label=pre_status_label)
    write_plist(files["pre_plist"], python_binary, label=pre_plist_label)
    files["pre_launchctl"].write_text("state = running\n", encoding="utf-8")
    write_status(files["stopped_status"], launchd_loaded=stopped_loaded, label=stopped_status_label)
    files["stopped_launchctl"].write_text(
        "state = running\n" if stopped_loaded else "could not find service\n",
        encoding="utf-8",
    )
    write_status(files["post_status"], launchd_loaded=True, label=post_status_label)
    write_plist(files["post_plist"], post_program_arg0, label=post_plist_label)
    files["post_launchctl"].write_text("state = running\n", encoding="utf-8")
    return files


def write_status(path: Path, *, launchd_loaded: bool, label: str) -> None:
    path.write_text(
        json.dumps(
            {
                "configured": True,
                "launchd_label": label,
                "launchd_loaded": launchd_loaded,
                "plist_path": "/tmp/com.codex.obsidian-sync.plist",
                "last_success": "2026-05-16T00:05:00+00:00",
                "last_error": "none",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def write_plist(path: Path, program_arg0: str, *, label: str, extra_argument: str | None = None) -> None:
    program_arguments = [
        program_arg0,
        "--config",
        "/tmp/config.toml",
        "service-run",
    ]
    if extra_argument is not None:
        program_arguments.append(extra_argument)
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
