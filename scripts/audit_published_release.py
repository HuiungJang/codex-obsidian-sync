from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable

from audit_cutover_readiness import (
    DEFAULT_REPOSITORY,
    FORMULA_NAME,
    TARGETS,
    cargo_version,
    normalize_version,
    validate_repository,
    write_json,
)
from audit_release_evidence import audit_release_evidence


GITHUB_API_URL = "https://api.github.com"
RELEASE_EVIDENCE_SUMMARY = "release-evidence-summary.json"
UrlOpener = Callable[[urllib.request.Request], Any]


def main() -> int:
    parser = argparse.ArgumentParser(description="Download and audit published GitHub Release assets.")
    parser.add_argument(
        "--version",
        help="Release version or tag, for example 0.1.0 or v0.1.0. Defaults to rust/Cargo.toml.",
    )
    parser.add_argument(
        "--repository",
        default=DEFAULT_REPOSITORY,
        help=f"GitHub repository in owner/name form. Defaults to {DEFAULT_REPOSITORY}.",
    )
    parser.add_argument(
        "--download-dir",
        required=True,
        type=Path,
        help="Empty directory where release assets will be downloaded.",
    )
    parser.add_argument(
        "--github-api-url",
        default=GITHUB_API_URL,
        help=f"GitHub API base URL. Defaults to {GITHUB_API_URL}.",
    )
    parser.add_argument(
        "--github-token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub token for private releases or higher API limits. Defaults to GITHUB_TOKEN.",
    )
    parser.add_argument("--output", type=Path, help="Write the published-release audit report to this path.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    version = normalize_version(args.version or cargo_version(repo_root / "rust" / "Cargo.toml"))
    repository = validate_repository(args.repository)
    result = audit_published_release(
        version=version,
        repository=repository,
        download_dir=args.download_dir,
        github_api_url=args.github_api_url,
        github_token=args.github_token,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def audit_published_release(
    *,
    version: str,
    repository: str,
    download_dir: Path,
    github_api_url: str = GITHUB_API_URL,
    github_token: str | None = None,
    opener: UrlOpener | None = None,
) -> dict[str, Any]:
    tag = f"v{version}"
    no_go_reasons: list[str] = []
    release: dict[str, Any] | None = None
    downloaded_assets: list[dict[str, Any]] = []
    local_evidence_audit: dict[str, Any] | None = None
    uploaded_evidence_summary: dict[str, Any] | None = None

    try:
        destination = prepare_download_dir(download_dir)
        release = fetch_release(
            github_api_url=github_api_url,
            repository=repository,
            tag=tag,
            github_token=github_token,
            opener=opener,
        )
        no_go_reasons.extend(validate_release_metadata(release, tag))

        assets = release_assets_by_name(release)
        for asset_name in required_asset_names():
            asset = assets.get(asset_name)
            if asset is None:
                no_go_reasons.append(f"missing release asset: {asset_name}")
                continue
            downloaded_assets.append(
                download_asset(
                    asset=asset,
                    destination=destination / asset_name,
                    github_token=github_token,
                    opener=opener,
                )
            )

        local_evidence_audit = audit_release_evidence(
            release_dir=destination,
            formula_path=destination / f"{FORMULA_NAME}.rb",
            version=version,
            repository=repository,
        )
        no_go_reasons.extend(f"local release evidence: {reason}" for reason in local_evidence_audit["no_go_reasons"])

        uploaded_evidence_summary = read_uploaded_evidence_summary(destination / RELEASE_EVIDENCE_SUMMARY)
        no_go_reasons.extend(validate_uploaded_evidence_summary(uploaded_evidence_summary, tag, repository))
    except Exception as error:
        no_go_reasons.append(str(error))

    release_details = release_summary(release)
    return {
        "ok": not no_go_reasons,
        "version": version,
        "tag": tag,
        "repository": repository,
        "download_dir": str(download_dir.expanduser().resolve()),
        "release": release_details,
        "downloaded_assets": downloaded_assets,
        "missing_assets": sorted(set(required_asset_names()) - {asset["name"] for asset in downloaded_assets}),
        "uploaded_evidence_summary": uploaded_evidence_summary,
        "local_evidence_audit": local_evidence_audit,
        "no_go_reasons": no_go_reasons,
    }


def required_asset_names() -> tuple[str, ...]:
    names: list[str] = []
    for target in TARGETS:
        package_name = f"{FORMULA_NAME}-{target}.tar.gz"
        names.extend(
            [
                package_name,
                f"{package_name}.sha256",
                f"{FORMULA_NAME}-{target}.smoke-summary.json",
            ]
        )
    names.extend(
        [
            f"{FORMULA_NAME}.rb",
            "homebrew-smoke-summary.json",
            RELEASE_EVIDENCE_SUMMARY,
        ]
    )
    return tuple(names)


def prepare_download_dir(download_dir: Path) -> Path:
    destination = download_dir.expanduser().resolve()
    if destination.exists() and not destination.is_dir():
        raise RuntimeError(f"download path is not a directory: {destination}")
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError(f"download directory is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def fetch_release(
    *,
    github_api_url: str,
    repository: str,
    tag: str,
    github_token: str | None,
    opener: UrlOpener | None,
) -> dict[str, Any]:
    api_url = github_api_url.rstrip("/")
    url = f"{api_url}/repos/{repository}/releases/tags/{tag}"
    release = read_json_url(url, github_token, "application/vnd.github+json", opener)
    if not isinstance(release, dict):
        raise RuntimeError("GitHub release response is not an object")
    return release


def validate_release_metadata(release: dict[str, Any], tag: str) -> list[str]:
    reasons: list[str] = []
    if release.get("tag_name") != tag:
        reasons.append("GitHub release tag does not match requested tag")
    if release.get("draft") is True:
        reasons.append("GitHub release is still a draft")
    return reasons


def release_assets_by_name(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError("GitHub release assets are missing")

    by_name: dict[str, dict[str, Any]] = {}
    duplicate_names: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            continue
        name = asset["name"]
        if name in by_name:
            duplicate_names.add(name)
        by_name[name] = asset
    if duplicate_names:
        duplicates = ", ".join(sorted(duplicate_names))
        raise RuntimeError(f"GitHub release has duplicate asset names: {duplicates}")
    return by_name


def download_asset(
    *,
    asset: dict[str, Any],
    destination: Path,
    github_token: str | None,
    opener: UrlOpener | None,
) -> dict[str, Any]:
    asset_url = asset.get("url")
    name = asset.get("name")
    if not isinstance(asset_url, str) or not asset_url:
        raise RuntimeError(f"release asset URL is missing: {name}")
    if not isinstance(name, str) or not name:
        raise RuntimeError("release asset name is missing")

    payload = read_bytes_url(asset_url, github_token, "application/octet-stream", opener)
    destination.write_bytes(payload)
    expected_size = asset.get("size")
    size_matches = not isinstance(expected_size, int) or expected_size == len(payload)
    if not size_matches:
        raise RuntimeError(f"release asset size mismatch for {name}")
    return {
        "name": name,
        "path": str(destination),
        "size": len(payload),
        "expected_size": expected_size if isinstance(expected_size, int) else None,
        "size_matches": size_matches,
    }


def read_uploaded_evidence_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"ok": False, "no_go_reasons": ["release evidence summary asset is missing"]}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        return {"ok": False, "no_go_reasons": ["release evidence summary is not an object"]}
    return value


def validate_uploaded_evidence_summary(summary: dict[str, Any], tag: str, repository: str) -> list[str]:
    reasons: list[str] = []
    if summary.get("ok") is not True:
        reasons.append("uploaded release evidence summary ok is not true")
    if summary.get("tag") != tag:
        reasons.append("uploaded release evidence summary tag does not match requested tag")
    if summary.get("repository") != repository:
        reasons.append("uploaded release evidence summary repository does not match requested repository")
    no_go = summary.get("no_go_reasons")
    if no_go not in ([], None):
        reasons.append("uploaded release evidence summary contains no-go reasons")
    return reasons


def release_summary(release: dict[str, Any] | None) -> dict[str, Any] | None:
    if release is None:
        return None
    assets = release.get("assets")
    asset_count = len(assets) if isinstance(assets, list) else None
    return {
        "tag_name": release.get("tag_name"),
        "html_url": release.get("html_url"),
        "draft": release.get("draft"),
        "prerelease": release.get("prerelease"),
        "asset_count": asset_count,
    }


def read_json_url(
    url: str,
    github_token: str | None,
    accept: str,
    opener: UrlOpener | None,
) -> Any:
    return json.loads(read_bytes_url(url, github_token, accept, opener).decode("utf-8"))


def read_bytes_url(
    url: str,
    github_token: str | None,
    accept: str,
    opener: UrlOpener | None,
) -> bytes:
    request = urllib.request.Request(url, headers=github_headers(github_token, accept))
    open_url = opener or default_urlopen
    with open_url(request) as response:
        return response.read()


def default_urlopen(request: urllib.request.Request) -> Any:
    return urllib.request.urlopen(request, timeout=60)


def github_headers(github_token: str | None, accept: str) -> dict[str, str]:
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "codex-obsidian-sync-release-audit",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    return headers


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
