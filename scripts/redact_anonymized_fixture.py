from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from fixture_redaction import apply_redactions, dynamic_redactions, is_binary
OUTPUT_MARKER = ".codex-obsidian-sync-redacted-fixture"
OUTPUT_MARKER_CONTENT = "managed by redact_anonymized_fixture.py\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Redact a local Codex/Obsidian fixture before parity comparison.")
    parser.add_argument("--input-dir", type=Path, required=True, help="Raw local fixture root.")
    parser.add_argument("--output-dir", type=Path, help="Destination anonymized fixture root.")
    parser.add_argument("--force", action="store_true", help="Replace an existing output dir.")
    parser.add_argument("--check-only", action="store_true", help="Only fail if leaks remain in input-dir.")
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    if not input_dir.is_dir():
        raise RuntimeError(f"input-dir does not exist: {input_dir}")
    ensure_no_symlinks(input_dir)

    if args.check_only:
        assert_no_leaks(input_dir)
        return 0

    if args.output_dir is None:
        raise RuntimeError("--output-dir is required unless --check-only is used")
    output_dir = args.output_dir.resolve()
    if output_dir == input_dir or output_dir.is_relative_to(input_dir):
        raise RuntimeError("output-dir must not be inside input-dir")
    if output_dir.exists():
        if not args.force:
            raise RuntimeError(f"output-dir already exists: {output_dir}")
        if not is_managed_output_dir(output_dir):
            raise RuntimeError(f"refusing to replace unmanaged output-dir: {output_dir}")
        shutil.rmtree(output_dir)

    report = redact_tree(input_dir, output_dir)
    write_private_text(output_dir / OUTPUT_MARKER, OUTPUT_MARKER_CONTENT)
    write_private_text(output_dir / "redaction-report.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    assert_no_leaks(output_dir)
    return 0


def redact_tree(input_dir: Path, output_dir: Path) -> dict[str, object]:
    text_files = 0
    redacted_files: list[dict[str, object]] = []
    private_mkdir(output_dir)
    for source in sorted(input_dir.rglob("*")):
        relative = source.relative_to(input_dir)
        target = output_dir / relative
        if source.is_dir():
            private_mkdir(target)
            continue
        if not source.is_file():
            continue
        data = source.read_bytes()
        if is_binary(data):
            raise RuntimeError(f"binary file is not allowed in anonymized fixtures: {source}")
        text = data.decode("utf-8", errors="ignore")
        redacted, count = apply_redactions(text)
        write_private_text(target, redacted)
        text_files += 1
        if count:
            redacted_files.append({"path": relative.as_posix(), "matches": count})
    return {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "text_files": text_files,
        "binary_files": 0,
        "redacted_files": redacted_files,
    }


def assert_no_leaks(root: Path) -> None:
    redactions = dynamic_redactions()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if is_binary(path.read_bytes()):
            raise RuntimeError(f"binary file is not allowed in anonymized fixtures: {path}")
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern, _ in redactions:
            if pattern.search(text):
                raise RuntimeError(f"unredacted private content remains in {path}")


def ensure_no_symlinks(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            raise RuntimeError(f"symlink is not allowed in fixtures: {path}")


def is_managed_output_dir(output_dir: Path) -> bool:
    marker = output_dir / OUTPUT_MARKER
    if not marker.exists() or marker.is_symlink():
        return False
    return marker.read_text(encoding="utf-8", errors="ignore") == OUTPUT_MARKER_CONTENT


def private_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_private_text(path: Path, content: str) -> None:
    private_mkdir(path.parent)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


if __name__ == "__main__":
    raise SystemExit(main())
