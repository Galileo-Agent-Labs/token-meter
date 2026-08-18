import contextlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter


class CursorMetadataCacheTests(unittest.TestCase):
    def database(self, root):
        path = Path(root) / "state.vscdb"
        with contextlib.closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(
                "CREATE TABLE composerHeaders (composerId TEXT, workspaceId TEXT, "
                "createdAt INTEGER, lastUpdatedAt INTEGER, isArchived INTEGER, "
                "isSubagent INTEGER, checkpointAt INTEGER, value TEXT)"
            )
            conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute(
                "INSERT INTO composerHeaders VALUES (?, ?, ?, ?, 0, 0, 0, ?)",
                ("session-1", "workspace", 1, 2, json.dumps({"name": "Session"})),
            )
            conn.execute(
                "INSERT INTO cursorDiskKV VALUES (?, ?)",
                ("composerData:session-1", json.dumps({
                    "modelConfig": {"modelName": "model-one"}
                })),
            )
        return path

    def test_unchanged_database_reuses_metadata_and_change_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.database(tmp)
            meter.reset_cursor_metadata_cache()
            first = meter.cursor_metadata_index(str(path))

            with mock.patch.object(
                meter,
                "_cursor_db_connection",
                side_effect=AssertionError("unchanged metadata should come from cache"),
            ):
                second = meter.cursor_metadata_index(str(path))

            second["session-1"]["model"] = "mutated-copy"
            with contextlib.closing(sqlite3.connect(path)) as conn, conn:
                conn.execute(
                    "UPDATE cursorDiskKV SET value = ? WHERE key = ?",
                    (json.dumps({"modelConfig": {"modelName": "model-two-longer"}}),
                     "composerData:session-1"),
                )
            third = meter.cursor_metadata_index(str(path))

        self.assertEqual(first["session-1"]["model"], "model-one")
        self.assertEqual(third["session-1"]["model"], "model-two-longer")

    def test_cached_path_inventory_still_observes_known_log_file_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "nested" / "cursor.requestTraces.log"
            log.parent.mkdir()
            log.write_text("first")
            os.utime(log, (10, 10))
            meter._recursive_path_cache.clear()

            first = meter.cursor_enrichment_mtime(
                db_path=str(Path(tmp) / "missing.db"), log_root=tmp
            )
            log.write_text("second")
            os.utime(log, (20, 20))
            second = meter.cursor_enrichment_mtime(
                db_path=str(Path(tmp) / "missing.db"), log_root=tmp
            )

        self.assertEqual(first, 10)
        self.assertEqual(second, 20)


if __name__ == "__main__":
    unittest.main()
