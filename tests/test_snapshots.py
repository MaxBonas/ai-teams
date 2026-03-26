import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from aiteam.snapshots import SnapshotManager


class SnapshotTests(unittest.TestCase):
    def test_create_and_restore_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "aiteam").mkdir()
            target = root / "aiteam" / "file.txt"
            target.write_text("v1", encoding="utf-8")

            manager = SnapshotManager(project_root=root)
            entry = manager.create_snapshot(label="initial", max_keep=5)
            self.assertTrue(entry["id"])

            target.write_text("v2", encoding="utf-8")
            result = manager.restore_snapshot(entry["id"])
            self.assertFalse(result["dry_run"])
            self.assertEqual(target.read_text(encoding="utf-8"), "v1")

    def test_list_and_prune_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "a.md").write_text("hello", encoding="utf-8")
            manager = SnapshotManager(project_root=root)

            manager.create_snapshot(label="one", max_keep=1)
            manager.create_snapshot(label="two", max_keep=1)
            snapshots = manager.list_snapshots()
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0].get("label"), "two")

    def test_snapshot_excludes_sensitive_files_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("OPENAI_API_KEY=secret", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs" / "note.txt").write_text("hello", encoding="utf-8")

            manager = SnapshotManager(project_root=root)
            entry = manager.create_snapshot(label="safe", max_keep=5)
            archive = root / ".aiteam_snapshots" / entry["archive"]

            with ZipFile(archive, mode="r") as zip_file:
                names = {item.filename for item in zip_file.infolist()}
            self.assertNotIn(".env", names)
            self.assertIn("docs/note.txt", names)

    def test_snapshot_can_include_sensitive_files_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("OPENAI_API_KEY=secret", encoding="utf-8")

            manager = SnapshotManager(project_root=root)
            entry = manager.create_snapshot(label="full", max_keep=5, include_sensitive=True)
            archive = root / ".aiteam_snapshots" / entry["archive"]

            with ZipFile(archive, mode="r") as zip_file:
                names = {item.filename for item in zip_file.infolist()}
            self.assertIn(".env", names)


if __name__ == "__main__":
    unittest.main()
