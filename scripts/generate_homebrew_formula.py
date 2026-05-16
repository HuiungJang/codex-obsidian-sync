from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path


DEFAULT_REPOSITORY = "HuiungJang/codex-obsidian-sync"
FORMULA_CLASS = "CodexObsidianSync"
FORMULA_NAME = "codex-obsidian-sync"
TARGETS = {
    "aarch64-apple-darwin": "arm",
    "x86_64-apple-darwin": "intel",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Homebrew formula for a Rust binary release.")
    parser.add_argument(
        "--version",
        help="Release version or tag, for example 0.1.0 or v0.1.0. Defaults to rust/Cargo.toml.",
    )
    parser.add_argument(
        "--repository",
        default=DEFAULT_REPOSITORY,
        help=f"GitHub repository in owner/name form. Defaults to {DEFAULT_REPOSITORY}.",
    )
    parser.add_argument("--aarch64-checksum", required=True, type=Path, help="aarch64 tarball .sha256 file.")
    parser.add_argument("--x86-64-checksum", required=True, type=Path, help="x86_64 tarball .sha256 file.")
    parser.add_argument("--output", type=Path, help="Write formula to this path instead of stdout.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    version = normalize_version(args.version or cargo_version(repo_root / "rust" / "Cargo.toml"))
    repository = validate_repository(args.repository)
    checksums = {
        "aarch64-apple-darwin": read_checksum(args.aarch64_checksum, "aarch64-apple-darwin"),
        "x86_64-apple-darwin": read_checksum(args.x86_64_checksum, "x86_64-apple-darwin"),
    }
    formula = render_formula(version=version, repository=repository, checksums=checksums)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(formula, encoding="utf-8")
    else:
        print(formula, end="")
    return 0


def cargo_version(cargo_toml: Path) -> str:
    with cargo_toml.open("rb") as handle:
        data = tomllib.load(handle)
    return str(data["package"]["version"])


def normalize_version(value: str) -> str:
    version = value.strip()
    if version.startswith("v"):
        version = version[1:]
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError(f"Invalid release version: {value}")
    return version


def validate_repository(value: str) -> str:
    repository = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError(f"Invalid repository: {value}")
    return repository


def read_checksum(path: Path, target: str) -> str:
    expected_name = f"{FORMULA_NAME}-{target}.tar.gz"
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        checksum = parts[0]
        if len(parts) == 1 or Path(parts[-1]).name != expected_name:
            continue
        if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
            raise ValueError(f"Invalid SHA-256 checksum in {path}")
        return checksum.lower()
    raise ValueError(f"Checksum file {path} does not reference {expected_name}")


def render_formula(*, version: str, repository: str, checksums: dict[str, str]) -> str:
    tag = f"v{version}"
    base_url = f"https://github.com/{repository}/releases/download/{tag}"
    lines = [
        f"class {FORMULA_CLASS} < Formula",
        '  desc "Sync local Codex conversations into an Obsidian vault"',
        f'  homepage "https://github.com/{repository}"',
        '  license "Apache-2.0"',
        f'  version "{version}"',
        "",
        "  depends_on :macos",
        "",
        "  on_macos do",
    ]

    for target, arch in TARGETS.items():
        package = f"{FORMULA_NAME}-{target}.tar.gz"
        lines.extend(
            [
                f"    on_{arch} do",
                f'      url "{base_url}/{package}"',
                f'      sha256 "{checksums[target]}"',
                "    end",
                "",
            ]
        )

    lines.extend(
        [
            "  end",
            "",
            "  def install",
            f'    bin.install "{FORMULA_NAME}"',
            "  end",
            "",
            "  test do",
            f'    assert_match "{FORMULA_NAME} #{{version}}", shell_output("#{{bin}}/{FORMULA_NAME} --version")',
            "  end",
            "end",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
