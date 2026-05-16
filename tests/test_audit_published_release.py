from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_published_release
from audit_release_evidence import audit_release_evidence


REPOSITORY = "HuiungJang/codex-obsidian-sync"
API_URL = "https://api.example.test"


class AuditPublishedReleaseTests(unittest.TestCase):
    def test_downloads_release_assets_and_runs_evidence_audit(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            assets["published-release-audit.json"] = b'{"ok": true}\n'
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertTrue(result["ok"], result["no_go_reasons"])
        self.assertEqual(result["tag"], "v0.1.0")
        self.assertEqual(result["missing_assets"], [])
        self.assertEqual(result["extra_assets"], [])
        self.assertTrue(result["local_evidence_audit"]["ok"])
        self.assertTrue(result["uploaded_evidence_summary"]["ok"])
        self.assertEqual(len(result["downloaded_assets"]), len(audit_published_release.required_asset_names()))

    def test_fails_when_required_release_asset_is_missing(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            del assets["homebrew-smoke-summary.json"]
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn("missing release asset: homebrew-smoke-summary.json", result["no_go_reasons"])
        self.assertIn("homebrew-smoke-summary.json", result["missing_assets"])

    def test_fails_when_release_is_draft_or_prerelease(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            opener = FakeGitHubOpener(assets, draft=True, prerelease=True)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertIn("GitHub release is still a draft", result["no_go_reasons"])
        self.assertIn("GitHub release is marked as a prerelease", result["no_go_reasons"])

    def test_fails_when_release_contains_unexpected_asset(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "assets"
            download_dir = root / "downloaded"
            assets = write_release_asset_set(source_dir)
            assets["debug.log"] = b"unexpected\n"
            opener = FakeGitHubOpener(assets)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=opener,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["extra_assets"], ["debug.log"])
        self.assertIn("unexpected release asset: debug.log", result["no_go_reasons"])

    def test_refuses_non_empty_download_directory(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            download_dir = root / "downloaded"
            download_dir.mkdir()
            (download_dir / "existing").write_text("keep\n", encoding="utf-8")

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=FakeGitHubOpener({}),
            )

        self.assertFalse(result["ok"])
        self.assertIn("download directory is not empty", result["no_go_reasons"][0])

    def test_refuses_symlinked_download_directory(self) -> None:
        with TemporaryDirectory(prefix="codex-obsidian-sync-published-release-") as temp_dir:
            root = Path(temp_dir)
            target_dir = root / "target"
            download_dir = root / "downloaded"
            target_dir.mkdir()
            download_dir.symlink_to(target_dir, target_is_directory=True)

            result = audit_published_release.audit_published_release(
                version="0.1.0",
                repository=REPOSITORY,
                download_dir=download_dir,
                github_api_url=API_URL,
                opener=FakeGitHubOpener({}),
            )

        self.assertFalse(result["ok"])
        self.assertIn("download path is a symlink", result["no_go_reasons"][0])


class FakeGitHubOpener:
    def __init__(self, assets: dict[str, bytes], *, draft: bool = False, prerelease: bool = False) -> None:
        self.assets = assets
        self.draft = draft
        self.prerelease = prerelease
        self.release_url = f"{API_URL}/repos/{REPOSITORY}/releases/tags/v0.1.0"

    def __call__(self, request: Any) -> "FakeResponse":
        if request.full_url == self.release_url:
            return FakeResponse(json.dumps(self.release_json()).encode("utf-8"))
        prefix = f"{API_URL}/assets/"
        if request.full_url.startswith(prefix):
            name = request.full_url.removeprefix(prefix)
            if name in self.assets:
                return FakeResponse(self.assets[name])
        raise RuntimeError(f"unexpected URL: {request.full_url}")

    def release_json(self) -> dict[str, Any]:
        return {
            "tag_name": "v0.1.0",
            "html_url": "https://github.example.test/HuiungJang/codex-obsidian-sync/releases/tag/v0.1.0",
            "draft": self.draft,
            "prerelease": self.prerelease,
            "assets": [
                {
                    "name": name,
                    "url": f"{API_URL}/assets/{name}",
                    "size": len(payload),
                }
                for name, payload in sorted(self.assets.items())
            ],
        }


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def write_release_asset_set(release_dir: Path) -> dict[str, bytes]:
    release_dir.mkdir(parents=True)
    checksums = write_release_artifacts(release_dir)
    formula = write_formula(release_dir, checksums)
    write_release_smoke_summaries(release_dir)
    write_homebrew_smoke_summary(release_dir, formula)
    evidence = audit_release_evidence(
        release_dir=release_dir,
        formula_path=formula,
        version="0.1.0",
        repository=REPOSITORY,
    )
    (release_dir / "release-evidence-summary.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        asset_name: (release_dir / asset_name).read_bytes()
        for asset_name in audit_published_release.required_asset_names()
    }


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
        checksum = hashlib.sha256(package.read_bytes()).hexdigest()
        checksums[target] = checksum
        (release_dir / f"{package_name}.sha256").write_text(f"{checksum}  {package_name}\n", encoding="utf-8")
    return checksums


def write_formula(release_dir: Path, checksums: dict[str, str]) -> Path:
    path = release_dir / "codex-obsidian-sync.rb"
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
                "end",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def write_release_smoke_summaries(release_dir: Path) -> None:
    for target in ("aarch64-apple-darwin", "x86_64-apple-darwin"):
        installed_binary = "/tmp/codex-obsidian-sync/bin/codex-obsidian-sync"
        (release_dir / f"codex-obsidian-sync-{target}.smoke-summary.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "tarball": str(release_dir / f"codex-obsidian-sync-{target}.tar.gz"),
                    "checksum": str(release_dir / f"codex-obsidian-sync-{target}.tar.gz.sha256"),
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


def write_homebrew_smoke_summary(release_dir: Path, formula: Path) -> None:
    installed_binary = "/tmp/codex-obsidian-sync/bin/codex-obsidian-sync"
    (release_dir / "homebrew-smoke-summary.json").write_text(
        json.dumps(
            {
                "ok": True,
                "formula": str(formula.resolve()),
                "formula_sha256": hashlib.sha256(formula.resolve().read_bytes()).hexdigest(),
                "expected_version": "0.1.0",
                "version": "codex-obsidian-sync 0.1.0",
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


if __name__ == "__main__":
    unittest.main()
