import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from token_meter.contracts import DetailLevel, DiscoveryContext, EvidenceBasis
from token_meter.runtimes.opencode import OpenCodeRuntimeAdapter


SCHEMA = """
CREATE TABLE session (
  id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT, title TEXT, agent TEXT,
  model TEXT, time_created INTEGER, time_updated INTEGER, time_archived INTEGER,
  tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER,
  tokens_cache_read INTEGER, tokens_cache_write INTEGER, cost REAL
);
CREATE TABLE message (
  id TEXT PRIMARY KEY, session_id TEXT, data TEXT,
  time_created INTEGER, time_updated INTEGER
);
CREATE TABLE part (
  id TEXT PRIMARY KEY, session_id TEXT, message_id TEXT, data TEXT,
  time_created INTEGER, time_updated INTEGER
);
"""


class OpenCodeRuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "opencode.db"
        self.models_path = self.root / "models.json"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("session-1", None, "/work/project", "A safe title", "build",
                 json.dumps({"providerID": "models", "id": "model-1"}),
                 1_000, 4_000, None, 10, 3, 2, 4, 1, 0.25),
            )
            conn.execute(
                "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("child", "session-1", "/work/project", "Child", "build", "{}",
                 1_000, 5_000, None, 0, 0, 0, 0, 0, 0.0),
            )
            conn.execute(
                "INSERT INTO message VALUES (?,?,?,?,?)",
                ("user-1", "session-1", json.dumps({
                    "role": "user", "content": "private prompt",
                    "time": {"created": 1_000},
                }), 1_000, 1_000),
            )
            conn.execute(
                "INSERT INTO message VALUES (?,?,?,?,?)",
                ("assistant-1", "session-1", json.dumps({
                    "role": "assistant", "modelID": "model-1", "providerID": "models",
                    "time": {"created": 2_000, "completed": 4_000},
                    "tokens": {"input": 10, "output": 3, "reasoning": 2,
                               "cache": {"read": 4, "write": 1}},
                    "cost": 0.25, "content": "private response",
                }), 2_000, 4_000),
            )
            conn.execute(
                "INSERT INTO part VALUES (?,?,?,?,?,?)",
                ("part-1", "session-1", "assistant-1",
                 json.dumps({"type": "tool", "tool": "read", "input": "secret"}),
                 3_000, 4_000),
            )
        self.models_path.write_text(json.dumps({
            "models": {"id": "models", "models": {
                "model-1": {"limit": {"context": 1000}},
            }},
        }))
        self.adapter = OpenCodeRuntimeAdapter(self.db_path, self.models_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_discovers_only_top_level_active_sessions_with_stable_revision(self):
        sources = tuple(self.adapter.discover(DiscoveryContext(home=str(self.root))))

        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual(source.runtime_id, "opencode")
        self.assertEqual(source.session_id, "session-1")
        self.assertEqual(source.model_ref.model_id, "model-1")
        self.assertEqual(source.model_ref.provider_id, "models")
        self.assertEqual(source.project, "/work/project")
        self.assertNotIn("private", repr(source))

    def test_revision_changes_when_message_or_part_changes(self):
        source = tuple(self.adapter.discover(DiscoveryContext(home=str(self.root))))[0]
        before = self.adapter.current_revision(source)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE part SET time_updated=9000 WHERE id='part-1'")
        after = self.adapter.current_revision(source)

        self.assertNotEqual(before, after)

    def test_normalized_load_preserves_measured_zero_and_never_loads_content(self):
        source = tuple(self.adapter.discover(DiscoveryContext(home=str(self.root))))[0]
        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.value, 10)
        self.assertEqual(result.usage.output_tokens.value, 5)
        self.assertEqual(result.usage.cache_read_tokens.value, 4)
        self.assertEqual(result.usage.cache_write_tokens.value, 1)
        self.assertEqual(result.usage.cost_usd.value, 0.25)
        self.assertEqual(result.usage.cost_usd.basis, EvidenceBasis.MEASURED)
        self.assertEqual(result.timing.active_seconds.value, 2.0)
        self.assertEqual([(tool.name, tool.category) for tool in result.tools], [("read", "tool")])
        serialized = repr(result)
        for private in ("private prompt", "private response", "secret"):
            self.assertNotIn(private, serialized)

    def test_summary_detail_omits_turns_and_null_evidence_stays_unavailable(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE session SET tokens_cache_write=NULL, cost=NULL WHERE id='session-1'"
            )
        source = tuple(self.adapter.discover(DiscoveryContext(home=str(self.root))))[0]

        result = self.adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(result.turns, ())
        self.assertEqual(result.usage.cache_write_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.input_tokens.value, 10)

    def test_corrupt_and_partial_databases_fail_bounded(self):
        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"not sqlite")
        partial = self.root / "partial.db"
        with sqlite3.connect(partial) as conn:
            conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY)")

        for path in (corrupt, partial):
            with self.subTest(path=path.name):
                adapter = OpenCodeRuntimeAdapter(path, self.models_path)
                self.assertEqual(tuple(adapter.discover(DiscoveryContext(home=str(self.root)))), ())

    def test_connection_is_query_only(self):
        with self.adapter.connection() as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM session")

    def test_legacy_discovery_projection_matches_current_shape(self):
        rows = self.adapter.discover_legacy(DiscoveryContext(home=str(self.root)))

        self.assertEqual(rows, ({
            "provider": "opencode", "client": "opencode", "label": "OpenCode",
            "runtime": "OpenCode", "id": "session-1", "session": "session-1",
            "path": "opencode:session-1", "project": "/work/project", "mtime": 4.0,
            "signature_mtime": 4.0, "title": "A safe title", "model": "model-1",
            "model_provider": "models", "agent": "build", "tools_loaded": 0,
        },))


if __name__ == "__main__":
    unittest.main()
