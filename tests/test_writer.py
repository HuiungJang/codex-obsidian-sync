from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codex_obsidian_sync.models import TranscriptMessage
from codex_obsidian_sync.writer import (
    TRANSCRIPT_MARKER,
    append_conversation_transcript,
    MANAGED_END,
    MANAGED_START,
    merge_managed_section,
    resolve_note_path,
    validate_vault_root,
    write_atomic,
)


class WriterTests(unittest.TestCase):
    def test_validate_vault_root_requires_absolute_existing_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            vault_root = Path(temp_dir)
            self.assertEqual(validate_vault_root(vault_root), vault_root.resolve())

        with self.assertRaises(ValueError):
            validate_vault_root(Path("relative/path"))

    def test_resolve_note_path_rejects_traversal_and_symlink(self) -> None:
        with TemporaryDirectory() as temp_dir:
            vault_root = Path(temp_dir).resolve()
            notes_dir = vault_root / "Codex" / "Conversations"
            notes_dir.mkdir(parents=True)
            target = notes_dir / "linked.md"
            target.symlink_to(vault_root / "outside.md")

            with self.assertRaises(ValueError):
                resolve_note_path(vault_root, "../escape.md")

            with self.assertRaises(ValueError):
                resolve_note_path(vault_root, "Codex/Conversations/linked.md")

    def test_merge_managed_section_preserves_user_content(self) -> None:
        existing = "# Daily\n\nUser notes stay here.\n\n" + MANAGED_START + "\nold\n" + MANAGED_END + "\n"
        merged = merge_managed_section(existing, "new managed content")

        self.assertIn("User notes stay here.", merged)
        self.assertIn("new managed content", merged)
        self.assertNotIn("\nold\n", merged)

    def test_append_conversation_transcript_appends_without_replacing_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            vault_root = Path(temp_dir).resolve()
            note_path = resolve_note_path(vault_root, "Codex/Conversations/2026/demo.md")
            note_path.write_text(f"# Demo\n\n{TRANSCRIPT_MARKER}\n", encoding="utf-8")
            before_inode = note_path.stat().st_ino

            append_conversation_transcript(
                vault_root,
                "Codex/Conversations/2026/demo.md",
                [
                    TranscriptMessage(
                        message_key="abc",
                        role="user",
                        phase="",
                        timestamp="2026-04-03T00:00:00Z",
                        text="append body",
                    )
                ],
            )

            after_text = note_path.read_text(encoding="utf-8")
            after_inode = note_path.stat().st_ino

        self.assertEqual(before_inode, after_inode)
        self.assertIn("### User", after_text)
        self.assertIn("append body", after_text)

    def test_write_atomic_fsyncs_temp_file_and_parent_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "note.md"

            with patch("codex_obsidian_sync.writer.os.fsync") as fsync:
                write_atomic(target, "durable\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "durable\n")
            self.assertEqual(fsync.call_count, 2)

    def test_write_atomic_tolerates_directory_fsync_permission_errors(self) -> None:
        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "note.md"

            with patch(
                "codex_obsidian_sync.writer.os.fsync",
                side_effect=[None, PermissionError("denied")],
            ):
                write_atomic(target, "durable\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "durable\n")


if __name__ == "__main__":
    unittest.main()
