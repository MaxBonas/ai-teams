import tempfile
import unittest
from pathlib import Path

from aiteam.persistence import AtomicFileWriter


class PersistenceTests(unittest.TestCase):
    def test_write_json_atomic_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.json"
            payload = {"key": "value", "nested": {"a": 1}}
            AtomicFileWriter.write_json_atomic(path, payload)
            self.assertTrue(path.exists())
            import json

            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded, payload)

    def test_write_jsonl_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.jsonl"
            lines = [{"id": 1}, {"id": 2}, {"id": 3}]
            AtomicFileWriter.write_jsonl_atomic(path, lines)
            self.assertTrue(path.exists())
            import json

            loaded = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    loaded.append(json.loads(line))
            self.assertEqual(loaded, lines)

    def test_append_jsonl_with_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.jsonl"
            AtomicFileWriter.append_jsonl_with_checksum(path, {"a": 1})
            AtomicFileWriter.append_jsonl_with_checksum(path, {"a": 1})
            import json

            records = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
            self.assertEqual(len(records), 2)
            self.assertIn("_checksum", records[0])

    def test_read_jsonl_with_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.jsonl"
            AtomicFileWriter.append_jsonl_with_checksum(path, {"a": 1})
            AtomicFileWriter.append_jsonl_with_checksum(path, {"a": 1})
            AtomicFileWriter.append_jsonl_with_checksum(path, {"a": 2})

            deduped = AtomicFileWriter.read_jsonl_with_dedup(path)
            self.assertEqual(len(deduped), 2)

    def test_read_jsonl_skips_corrupted_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.jsonl"
            path.write_text(
                '{"valid": 1}\n'
                '{broken json\n'
                '{"valid": 2}\n',
                encoding="utf-8",
            )
            records = AtomicFileWriter.read_jsonl_with_dedup(path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["valid"], 1)
            self.assertEqual(records[1]["valid"], 2)


if __name__ == "__main__":
    unittest.main()
