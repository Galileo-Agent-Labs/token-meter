import contextlib
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import DetailLevel, DiscoveryContext, EvidenceBasis
from token_meter.runtimes.cursor import CursorRuntimeAdapter
from tests.runtime_projection_privacy import assert_runtime_trace_privacy


class CursorRuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.projects = self.root / "projects"
        self.database = self.root / "state.vscdb"
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.transcript = (
            self.projects / "Users-test-project" / "agent-transcripts" /
            "session-1" / "session-1.jsonl"
        )
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text(json.dumps({"role": "user", "text": "private"}) + "\n")
        os.utime(self.transcript, (2, 2))
        with contextlib.closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute(
                "CREATE TABLE composerHeaders (composerId TEXT, workspaceId TEXT, "
                "createdAt INTEGER, lastUpdatedAt INTEGER, isArchived INTEGER, "
                "isSubagent INTEGER, checkpointAt INTEGER, value TEXT)"
            )
            conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
            header = {
                "name": "Safe session",
                "workspaceIdentifier": {"uri": {"fsPath": "/work/project"}},
            }
            composer = {
                "modelConfig": {"modelName": "model-1"},
                "contextTokensUsed": 100,
                "contextTokenLimit": 1000,
                "fullConversationHeadersOnly": [
                    {"bubbleId": "user-1"}, {"bubbleId": "assistant-1"},
                ],
            }
            conn.execute(
                "INSERT INTO composerHeaders VALUES (?,?,?,?,0,0,0,?)",
                ("session-1", "workspace", 1000, 3000, json.dumps(header)),
            )
            conn.execute("INSERT INTO cursorDiskKV VALUES (?,?)", (
                "composerData:session-1", json.dumps(composer),
            ))
            conn.execute("INSERT INTO cursorDiskKV VALUES (?,?)", (
                "bubbleId:session-1:user-1",
                json.dumps({"type": 1, "createdAt": 1000, "text": "private prompt"}),
            ))
            conn.execute("INSERT INTO cursorDiskKV VALUES (?,?)", (
                "bubbleId:session-1:assistant-1",
                json.dumps({"type": 2, "createdAt": 3000, "text": "private response",
                            "toolFormerData": {"name": "read", "params": {"secret": 1}}}),
            ))
        self.adapter = CursorRuntimeAdapter(self.projects, self.database, self.logs)

    def tearDown(self):
        self.temp.cleanup()

    def add_second_session(self):
        other = (
            self.projects / "Users-test-other" / "agent-transcripts" /
            "session-2" / "session-2.jsonl"
        )
        other.parent.mkdir(parents=True)
        other.write_text("{}\n")
        os.utime(other, (2, 2))
        with contextlib.closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute(
                "INSERT INTO composerHeaders VALUES (?,?,?,?,0,0,0,?)",
                ("session-2", "workspace", 1000, 3000, json.dumps({
                    "name": "Other", "workspaceIdentifier": {
                        "uri": {"fsPath": "/work/other"}
                    },
                })),
            )
            conn.execute("INSERT INTO cursorDiskKV VALUES (?,?)", (
                "composerData:session-2", json.dumps({
                    "modelConfig": {"modelName": "model-2"},
                }),
            ))

    def test_discovers_database_enriched_transcript(self):
        sources = self.adapter.discover(DiscoveryContext(home=str(self.root)))

        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual(source.runtime_id, "cursor")
        self.assertEqual(source.session_id, "session-1")
        self.assertEqual(source.project, "/work/project")
        self.assertEqual(source.model_ref.model_id, "model-1")
        self.assertEqual(source.activity_mtime, 3.0)

    def test_revision_changes_only_when_database_or_transcript_changes(self):
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]
        before = self.adapter.current_revision(source)
        self.transcript.write_text("{}\n{}\n")
        os.utime(self.transcript, (4, 4))
        after_transcript = self.adapter.current_revision(source)
        with contextlib.closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE composerHeaders SET lastUpdatedAt=5000")
        after_database = self.adapter.current_revision(source)

        self.assertNotEqual(before, after_transcript)
        self.assertNotEqual(after_transcript, after_database)

    def test_database_revision_invalidates_only_the_affected_session(self):
        self.add_second_session()
        before = {
            source.session_id: source.revision
            for source in self.adapter.discover(DiscoveryContext(home=str(self.root)))
        }

        with contextlib.closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute(
                "UPDATE composerHeaders SET lastUpdatedAt=6000 WHERE composerId=?",
                ("session-1",),
            )
        after = {
            source.session_id: source.revision
            for source in self.adapter.discover(DiscoveryContext(home=str(self.root)))
        }

        self.assertNotEqual(before["session-1"], after["session-1"])
        self.assertEqual(before["session-2"], after["session-2"])

    def test_request_trace_revision_invalidates_only_the_affected_session(self):
        self.add_second_session()
        before = {
            source.session_id: source.revision
            for source in self.adapter.discover(DiscoveryContext(home=str(self.root)))
        }
        request_log = self.logs / "cursor.requestTraces.log"
        request_log.write_text(
            "2026-08-14T00:00:01Z span_completed name=client.ttft "
            "composerId=session-1 durationMs=100 requestId=r1 traceId=t1 error=false\n"
        )
        future = time.time() + 10
        os.utime(request_log, (future, future))

        after = {
            source.session_id: source.revision
            for source in self.adapter.discover(DiscoveryContext(home=str(self.root)))
        }

        self.assertNotEqual(before["session-1"], after["session-1"])
        self.assertEqual(before["session-2"], after["session-2"])

    def test_normalized_load_is_content_free_and_marks_estimates(self):
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]
        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.value, 100)
        self.assertEqual(result.usage.input_tokens.basis, EvidenceBasis.ESTIMATED)
        self.assertGreater(result.usage.output_tokens.value, 0)
        self.assertEqual(result.usage.output_tokens.basis, EvidenceBasis.ESTIMATED)
        self.assertEqual(result.usage.cache_read_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual([tool.name for tool in result.tools], ["read"])
        encoded = repr(result)
        for private in ("private prompt", "private response", "secret"):
            self.assertNotIn(private, encoded)

    def test_mcp_trace_views_are_structural_and_content_free(self):
        self.adapter.compatibility = meter._cursor_compatibility()
        self.adapter.compatibility.update({
            "snapshot": self.adapter.snapshot_legacy,
            "request_spans": self.adapter.request_spans,
        })
        source = self.adapter.discover_legacy(
            DiscoveryContext(home=str(self.root)),
        )[0]
        state = self.adapter.load(source, DetailLevel.FULL)

        assert_runtime_trace_privacy(
            self, source, state, runtime="cursor", model="model-1",
            tool="read_file_v2", native_types=("message", "request"),
            forbidden=(
                "private prompt", "private response", "secret",
                "/work/project",
            ),
        )

    def test_partial_database_falls_back_to_transcript_discovery(self):
        partial = self.root / "partial.vscdb"
        with contextlib.closing(sqlite3.connect(partial)) as conn, conn:
            conn.execute("CREATE TABLE unrelated (value TEXT)")
        adapter = CursorRuntimeAdapter(self.projects, partial, self.logs)

        sources = adapter.discover(DiscoveryContext(home=str(self.root)))

        self.assertEqual([source.session_id for source in sources], ["session-1"])

    def test_missing_usage_stays_unavailable_instead_of_measured_zero(self):
        with contextlib.closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute(
                "UPDATE cursorDiskKV SET value=? WHERE key=?",
                (json.dumps({"modelConfig": {"modelName": "model-1"}}),
                 "composerData:session-1"),
            )
            conn.execute(
                "DELETE FROM cursorDiskKV WHERE key LIKE 'bubbleId:session-1:%'"
            )
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.output_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual([warning.code for warning in result.warnings], [
            "usage_unavailable",
        ])

    def test_connection_is_query_only(self):
        with contextlib.closing(self.adapter.connection()) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM composerHeaders")

    def test_adapter_closes_owned_database_connections(self):
        connection = self.adapter.connection()
        self.addCleanup(connection.close)

        with mock.patch.object(self.adapter, "connection", return_value=connection):
            self.adapter.reset_metadata_cache()
            self.adapter.metadata_index()

        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
