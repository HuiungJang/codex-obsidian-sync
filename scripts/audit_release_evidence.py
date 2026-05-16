from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from audit_cutover_readiness import (
    DEFAULT_REPOSITORY,
    FORMULA_NAME,
    audit_homebrew_formula,
    audit_homebrew_smoke_summary,
    audit_release_dir,
    audit_release_smoke_summaries,
    build_result,
    cargo_version,
    normalize_version,
    validate_repository,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit release artifacts and smoke evidence before cutover.")
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
        "--release-dir",
        default=Path("dist"),
        type=Path,
        help="Directory containing release artifacts, formula, and smoke summaries. Defaults to dist.",
    )
    parser.add_argument("--homebrew-formula", type=Path, help="Generated Homebrew formula path.")
    parser.add_argument("--output", type=Path, help="Write the evidence report to this path.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    version = normalize_version(args.version or cargo_version(repo_root / "rust" / "Cargo.toml"))
    repository = validate_repository(args.repository)
    release_dir = args.release_dir.expanduser().resolve()
    formula_path = (args.homebrew_formula or release_dir / f"{FORMULA_NAME}.rb").expanduser().resolve()
    result = audit_release_evidence(
        release_dir=release_dir,
        formula_path=formula_path,
        version=version,
        repository=repository,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def audit_release_evidence(
    *,
    release_dir: Path,
    formula_path: Path,
    version: str,
    repository: str,
) -> dict[str, object]:
    release_checks = audit_release_dir(release_dir)
    checksums = {
        release_check["details"]["target"]: release_check["details"]["checksum"]
        for release_check in release_checks
        if release_check["ok"] and release_check["details"].get("checksum")
    }
    checks = [
        *release_checks,
        audit_homebrew_formula(formula_path, version, repository, checksums),
        *audit_release_smoke_summaries(release_dir, version),
        audit_homebrew_smoke_summary(release_dir, formula_path, version),
    ]
    return build_result(version=version, repository=repository, checks=checks)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
