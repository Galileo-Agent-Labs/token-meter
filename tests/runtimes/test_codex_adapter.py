import json
import os
import tempfile
import unittest
from pathlib import Path

from token_meter.contracts import DetailLevel, DiscoveryContext, EvidenceBasis
from token_meter.runtimes.codex import CodexRuntimeAdapter


class CodexRuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.index = self.root / "session_index.jsonl"
        self.trace = self.sessions / "2026" / "08" / "11" / "rollout-session-1.jsonl"
        self.trace.parent.mkdir(parents=True)
        self.rows = [
            {"timestamp": "2026-08-11T00:00:00Z", "type": "session_meta", "payload": {
                "id": "session-1", "cwd": "/work/project", "model_provider": "openai",
                "dynamic_tools": [{"name": "read", "description": "private definition"}],
            }},
            {"timestamp": "2026-08-11T00:00:01Z", "type": "turn_context", "payload": {
                "model": "gpt-test", "effort": "high", "cwd": "/work/project",
            }},
            {"timestamp": "2026-08-11T00:00:02Z", "type": "event_msg", "payload": {
                "type": "task_started", "model_context_window": 1000,
            }},
            {"timestamp": "2026-08-11T00:00:03Z", "type": "event_msg", "payload": {
                "type": "user_message", "message": "private prompt",
            }},
            {"timestamp": "2026-08-11T00:00:04Z", "type": "response_item", "payload": {
                "type": "function_call", "name": "read", "call_id": "call-1",
                "arguments": {"secret": "argument"},
            }},
            {"timestamp": "2026-08-11T00:00:05Z", "type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "call-1",
                "output": "private tool output",
            }},
            {"timestamp": "2026-08-11T00:00:06Z", "type": "event_msg", "payload": {
                "type": "token_count", "info": {"model_context_window": 1000,
                    "last_token_usage": {"input_tokens": 100, "output_tokens": 20,
                                         "cached_input_tokens": 80}},
            }},
            {"timestamp": "2026-08-11T00:00:08Z", "type": "event_msg", "payload": {
                "type": "task_complete", "duration_ms": 6000,
                "time_to_first_token_ms": 1200,
            }},
        ]
        self._write(self.rows)
        self.index.write_text(json.dumps({
            "id": "session-1", "thread_name": "Safe title",
        }) + "\n")
        os.utime(self.trace, (2, 2))
        os.utime(self.index, (3, 3))
        self.adapter = CodexRuntimeAdapter(self.sessions, self.index)

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, rows):
        self.trace.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def test_discovers_identity_model_provider_and_external_title(self):
        sources = self.adapter.discover(DiscoveryContext(home=str(self.root)))

        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual(source.session_id, "session-1")
        self.assertEqual(source.project, "/work/project")
        self.assertEqual(source.model_ref.provider_id, "openai")
        self.assertEqual(source.model_ref.model_id, "gpt-test")
        self.assertEqual(source.activity_mtime, 2.0)

    def test_model_provider_mapping_is_not_derived_from_runtime_id(self):
        rows = list(self.rows)
        rows[0] = {**rows[0], "payload": {
            **rows[0]["payload"], "model_provider": "custom-provider",
        }}
        self._write(rows)

        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]

        self.assertEqual(source.runtime_id, "codex")
        self.assertEqual(source.model_ref.provider_id, "custom-provider")

    def test_normalized_load_is_measured_and_content_free(self):
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.value, 20)
        self.assertEqual(result.usage.output_tokens.value, 20)
        self.assertEqual(result.usage.cache_read_tokens.value, 80)
        self.assertEqual(result.usage.input_tokens.basis, EvidenceBasis.MEASURED)
        self.assertEqual(result.timing.active_seconds.value, 6.0)
        self.assertEqual(result.timing.ttft_seconds.value, 1.2)
        self.assertEqual([tool.name for tool in result.tools], ["read"])
        self.assertEqual(len(result.turns), 1)
        encoded = repr(result)
        for private in ("private prompt", "private tool output", "argument",
                        "private definition"):
            self.assertNotIn(private, encoded)

    def test_corrupt_partial_trace_is_bounded_and_unavailable(self):
        self.trace.write_text("not-json\n" + json.dumps(self.rows[0]) + "\n")
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.output_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(
            [warning.code for warning in result.warnings],
            ["corrupt_rows", "usage_unavailable"],
        )

    def test_trace_or_index_title_changes_revision(self):
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]
        before = self.adapter.current_revision(source)
        self.trace.write_text(self.trace.read_text() + "{}\n")
        os.utime(self.trace, (4, 4))
        after_trace = self.adapter.current_revision(source)
        self.index.write_text(json.dumps({
            "id": "session-1", "thread_name": "Renamed",
        }) + "\n")
        os.utime(self.index, (5, 5))
        after_index = self.adapter.current_revision(source)

        self.assertNotEqual(before, after_trace)
        self.assertNotEqual(after_trace, after_index)


if __name__ == "__main__":
    unittest.main()
