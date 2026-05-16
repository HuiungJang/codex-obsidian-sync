from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import smoke_launchagent


class SmokeLaunchAgentTests(unittest.TestCase):
    def test_bootstraps_kickstarts_and_bootouts_isolated_label(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-launchagent-test-") as temp_dir:
            root = Path(temp_dir)
            binary = write_fake_binary(root)
            launchctl = write_fake_launchctl(root)
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-launchagent-smoke-{root.name}"
            self.addCleanup(shutil.rmtree, work_dir, ignore_errors=True)
            output = root / "summary.json"
            label = f"com.codex.obsidian-sync.smoke.{root.name.replace('_', '-')}"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_launchagent.py",
                    "--binary",
                    str(binary),
                    "--launchctl",
                    str(launchctl),
                    "--work-dir",
                    str(work_dir),
                    "--label",
                    label,
                    "--timeout-seconds",
                    "5",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            report = json.loads(output.read_text(encoding="utf-8"))
            command_report = json.loads((work_dir / "launchagent-smoke-commands.json").read_text(encoding="utf-8"))
            log = (root / "launchctl.log").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(report["ok"])
        self.assertEqual(report["label"], label)
        self.assertFalse(report["loaded_after_bootout"])
        self.assertEqual(report["note_files"], 1)
        self.assertEqual(report["last_summary"]["processed"], 1)
        self.assertEqual(command_report["commands"][-1]["command"][1:3], ["print", report["target"]])
        self.assertIn("bootstrap", log)
        self.assertIn("kickstart -k", log)
        self.assertIn("bootout", log)

    def test_refuses_symlinked_output_report_path(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-launchagent-test-") as temp_dir:
            root = Path(temp_dir)
            output_target = root / "target-summary.json"
            output = root / "summary.json"
            output_target.write_text("keep\n", encoding="utf-8")
            output.symlink_to(output_target)

            with self.assertRaisesRegex(RuntimeError, "Output path is a symlink"):
                smoke_launchagent.resolve_output_path(output)

            self.assertEqual(output_target.read_text(encoding="utf-8"), "keep\n")

    def test_rejects_symlinked_work_dir(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-launchagent-test-") as temp_dir:
            root = Path(temp_dir)
            binary = write_fake_binary(root)
            work_target = root / "work-target"
            work_target.mkdir()
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-launchagent-smoke-{root.name}"
            remove_path(work_dir)
            work_dir.symlink_to(work_target, target_is_directory=True)

            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/smoke_launchagent.py",
                        "--binary",
                        str(binary),
                        "--work-dir",
                        str(work_dir),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                remove_path(work_dir)

        self.assertEqual(result.returncode, 1)
        self.assertIn("Work dir is a symlink", result.stderr)

    def test_rejects_boolean_processed_summary(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-launchagent-test-") as temp_dir:
            root = Path(temp_dir)
            binary = write_fake_binary(root)
            binary.write_text(
                binary.read_text(encoding="utf-8").replace(
                    '"last_summary": {"processed": 1, "rewritten": 1}',
                    '"last_summary": {"processed": True, "rewritten": 1}',
                ),
                encoding="utf-8",
            )
            launchctl = write_fake_launchctl(root)
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-launchagent-smoke-{root.name}"
            self.addCleanup(shutil.rmtree, work_dir, ignore_errors=True)
            label = f"com.codex.obsidian-sync.smoke.{root.name.replace('_', '-')}"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_launchagent.py",
                    "--binary",
                    str(binary),
                    "--launchctl",
                    str(launchctl),
                    "--work-dir",
                    str(work_dir),
                    "--label",
                    label,
                    "--timeout-seconds",
                    "5",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("processed summary", result.stderr)

    def test_rejects_non_smoke_label(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-launchagent-test-") as temp_dir:
            root = Path(temp_dir)
            binary = write_fake_binary(root)
            launchctl = write_fake_launchctl(root)
            work_dir = Path(gettempdir()) / f"codex-obsidian-sync-launchagent-smoke-{root.name}"
            self.addCleanup(shutil.rmtree, work_dir, ignore_errors=True)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/smoke_launchagent.py",
                    "--binary",
                    str(binary),
                    "--launchctl",
                    str(launchctl),
                    "--work-dir",
                    str(work_dir),
                    "--label",
                    "com.codex.obsidian-sync",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("smoke label", result.stderr)


def write_fake_binary(root: Path) -> Path:
    binary = root / "codex-obsidian-sync"
    binary.write_text(
        """#!/usr/bin/env python3
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def parse_config(path):
    data = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"')
    return data


args = sys.argv[1:]
if len(args) == 3 and args[0] == "--config" and args[2] == "service-run":
    config = parse_config(args[1])
    vault = Path(config["vault"])
    state_file = Path(config["state_file"])
    service_state = Path(config["service_state_file"])
    now = datetime.now(UTC).isoformat().replace("+00:00", "+00:00")
    note = vault / "Codex" / "Conversations" / "2026" / "2026-05-16-0000-launchagent-smoke.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# LaunchAgent Smoke\\n", encoding="utf-8")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"files": {}}) + "\\n", encoding="utf-8")
    service_state.parent.mkdir(parents=True, exist_ok=True)
    service_state.write_text(json.dumps({
        "pending": False,
        "last_success_at": now,
        "last_error_type": None,
        "last_error_summary": None,
        "last_summary": {"processed": 1, "rewritten": 1},
        "last_run_finished_at": now,
        "next_eligible_at": (datetime.now(UTC) + timedelta(seconds=3600)).isoformat().replace("+00:00", "+00:00"),
    }) + "\\n", encoding="utf-8")
    raise SystemExit(0)
print("unexpected args: " + " ".join(args), file=sys.stderr)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def write_fake_launchctl(root: Path) -> Path:
    launchctl = root / "launchctl"
    launchctl.write_text(
        f"""#!/usr/bin/env python3
import plistlib
import subprocess
import sys
from pathlib import Path

root = Path({str(root)!r})
state_dir = root / "launchctl-state"
state_dir.mkdir(exist_ok=True)
log = root / "launchctl.log"
log.write_text(log.read_text(encoding="utf-8") + " ".join(sys.argv[1:]) + "\\n" if log.exists() else " ".join(sys.argv[1:]) + "\\n", encoding="utf-8")


def state_path(target):
    return state_dir / target.replace("/", "_")


args = sys.argv[1:]
if len(args) == 2 and args[0] == "print":
    state = state_path(args[1])
    if not state.exists():
        print("could not find service", file=sys.stderr)
        raise SystemExit(113)
    print("state = running")
    raise SystemExit(0)
if len(args) == 3 and args[0] == "bootstrap":
    plist = Path(args[2])
    payload = plistlib.loads(plist.read_bytes())
    label = payload["Label"]
    target = args[1] + "/" + label
    state_path(target).write_text(str(plist), encoding="utf-8")
    raise SystemExit(0)
if len(args) == 3 and args[0:2] == ["kickstart", "-k"]:
    state = state_path(args[2])
    if not state.exists():
        raise SystemExit(113)
    payload = plistlib.loads(Path(state.read_text(encoding="utf-8")).read_bytes())
    result = subprocess.run(payload["ProgramArguments"], capture_output=True, text=True, check=False)
    Path(payload["StandardOutPath"]).write_text(result.stdout, encoding="utf-8")
    Path(payload["StandardErrorPath"]).write_text(result.stderr, encoding="utf-8")
    raise SystemExit(result.returncode)
if len(args) == 2 and args[0] == "bootout":
    state = state_path(args[1])
    if state.exists():
        state.unlink()
    raise SystemExit(0)
print("unexpected launchctl args: " + " ".join(args), file=sys.stderr)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    launchctl.chmod(0o755)
    return launchctl


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


if __name__ == "__main__":
    unittest.main()
