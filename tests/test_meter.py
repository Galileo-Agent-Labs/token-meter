import unittest
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import meter


class CursorTraceTests(unittest.TestCase):
    def source(self, path="/tmp/cursor-session.jsonl", session_id="cursor-session"):
        return {
            "provider": "cursor", "client": "cursor", "label": "Cursor",
            "runtime": "Cursor", "id": session_id,
            "session": Path(path).name, "path": path, "project": "/repo",
            "mtime": 1, "title": "Cursor run", "model": "composer-2.5",
        }

    def snapshot(self):
        base_ms = 1_784_548_800_000
        return {
            "available": True,
            "header": {"name": "Cursor run"},
            "composer": {
                "modelConfig": {
                    "modelName": "composer-2.5",
                    "selectedModels": [{"modelId": "composer-2.5", "parameters": [
                        {"id": "fast", "value": "true"},
                    ]}],
                },
                "contextTokensUsed": 80448,
                "contextTokenLimit": 200000,
                "promptTokenBreakdown": {"categories": [
                    {"id": "rules", "label": "Rules", "estimatedTokens": 1234},
                ]},
            },
            "bubbles": [
                {"type": 1, "bubbleId": "user-1", "createdAt": base_ms,
                 "text": "inspect the repository", "requestId": "request-1",
                 "modelInfo": {"modelName": "composer-2.5"},
                 "contextWindowStatusAtCreation": {"tokensUsed": 78000, "tokenLimit": 200000}},
                {"type": 2, "bubbleId": "assistant-1", "createdAt": base_ms + 46000,
                 "thinking": "trace-visible", "thinkingDurationMs": 3200,
                 "toolFormerData": {
                     "name": "ripgrep_raw_search", "toolCallId": "tool-1",
                     "params": {"query": "needle"}, "additionalData": {"lines": "x" * 80},
                     "status": "error",
                 },
                 "text": "finished", "turnDurationMs": 46624},
            ],
        }

    def spans(self):
        base = 1_784_548_800.0
        return [
            {"name": "client.ttft", "start_ts": base, "end_ts": base + 4.118,
             "duration_s": 4.118, "request_id": "request-1", "error": False},
            {"name": "agent.request.attempt", "start_ts": base, "end_ts": base + 20,
             "duration_s": 20, "request_id": "attempt-1", "error": True},
            {"name": "agent.request.attempt", "start_ts": base + 21, "end_ts": base + 46,
             "duration_s": 25, "request_id": "attempt-2", "error": False},
            {"name": "ComposerChatService.submitChatMaybeAbortCurrent", "start_ts": base,
             "end_ts": base + 46.624, "duration_s": 46.624,
             "request_id": "request-1", "error": False},
        ]

    def test_cursor_discovery_uses_sqlite_metadata_and_keeps_activity_order_session_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects = root / "projects"
            db_path = root / "state.vscdb"
            logs = root / "logs"
            logs.mkdir()
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE composerHeaders (composerId TEXT, workspaceId TEXT, createdAt INTEGER, lastUpdatedAt INTEGER, isArchived INTEGER, isSubagent INTEGER, checkpointAt INTEGER, value TEXT)")
            conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
            rows = [
                ("older", 1_784_548_800_000, 0, "gpt-5.6-sol"),
                ("newer", 1_784_548_900_000, 0, "composer-2.5"),
                ("child", 1_784_549_000_000, 1, "composer-2.5"),
            ]
            for sid, updated, subagent, model in rows:
                header = {"name": sid.title(), "workspaceIdentifier": {"uri": {"fsPath": "/repo"}}}
                composer = {"modelConfig": {"modelName": model}}
                conn.execute("INSERT INTO composerHeaders VALUES (?, ?, ?, ?, 0, ?, 0, ?)",
                             (sid, "workspace", updated - 1000, updated, subagent, json.dumps(header)))
                conn.execute("INSERT INTO cursorDiskKV VALUES (?, ?)",
                             (f"composerData:{sid}", json.dumps(composer)))
                trace_dir = projects / "Users-test-repo" / "agent-transcripts" / sid
                trace_dir.mkdir(parents=True)
                transcript = trace_dir / f"{sid}.jsonl"
                transcript.write_text(json.dumps({"role": "user"}) + "\n")
                os.utime(transcript, (updated / 1000, updated / 1000))
            duplicate = projects / "empty-window" / "agent-transcripts" / "newer" / "newer.jsonl"
            duplicate.parent.mkdir(parents=True)
            duplicate.write_text(json.dumps({"role": "user", "replica": "newest"}) + "\n")
            os.utime(duplicate, (1_784_551_000, 1_784_551_000))
            conn.commit()
            conn.close()
            request_log = logs / "cursor.requestTraces.log"
            request_log.write_text("shared enrichment\n")
            os.utime(request_log, (1_784_550_000, 1_784_550_000))
            with mock.patch.object(meter, "CURSOR_PROJECTS", str(projects)), \
                    mock.patch.object(meter, "CURSOR_STATE_DB", str(db_path)), \
                    mock.patch.object(meter, "CURSOR_REQUEST_LOGS", str(logs)), \
                    mock.patch.object(meter, "CLAUDE_PROJECTS", str(root / "no-claude")), \
                    mock.patch.object(meter, "CODEX_SESSIONS", str(root / "no-codex")), \
                    mock.patch.object(meter, "CODEX_INDEX", str(root / "no-index")), \
                    mock.patch.object(meter, "claude_desktop_index", return_value={}):
                sources = meter.all_session_sources()
            self.assertEqual({row["id"] for row in sources}, {"older", "newer"})
            self.assertEqual(len(sources), 2)
            self.assertEqual(max(sources, key=lambda row: row["mtime"])["id"], "newer")
            newer = next(row for row in sources if row["id"] == "newer")
            self.assertEqual(newer["path"], str(duplicate))
            self.assertEqual(newer["model"], "composer-2.5")
            self.assertEqual(newer["project"], "/repo")
            self.assertGreater(newer["signature_mtime"], newer["mtime"])

    def test_cursor_recompute_exposes_context_tools_reasoning_wait_ttft_and_retries(self):
        with mock.patch.object(meter, "cursor_snapshot", return_value=self.snapshot()), \
                mock.patch.object(meter, "cursor_request_spans", return_value=self.spans()):
            state = meter.recompute_cursor(self.source())
        self.assertEqual(state["provider"], "cursor")
        self.assertEqual(state["primary_model"], "composer-2.5")
        self.assertEqual(state["context"]["latest"], 80448)
        self.assertEqual(state["context"]["window"], 200000)
        self.assertTrue(state["context"]["estimated"])
        self.assertEqual(state["context"]["breakdown"][0]["estimated_tokens"], 1234)
        self.assertTrue(state["availability"]["cost"])
        self.assertTrue(state["availability"]["tokens"])
        self.assertFalse(state["availability"]["cache"])
        self.assertTrue(state["availability"]["throughput"])
        self.assertTrue(state["availability"]["context"])
        self.assertTrue(state["availability"]["timing"])
        self.assertTrue(state["availability"]["tool_results"])
        execution = state["executions"][0]
        self.assertEqual(execution["tools"][0]["name"], "ripgrep_raw_search")
        self.assertTrue(execution["tools"][0]["error"])
        self.assertGreater(execution["tokens"]["retrieval"], 0)
        self.assertAlmostEqual(execution["wait_duration_ms"], 46624, places=3)
        self.assertAlmostEqual(execution["ttft_ms"], 4118, places=3)
        self.assertEqual((execution["attempts"], execution["failed_attempts"], execution["retries"]),
                         (2, 1, 1))
        self.assertEqual(execution["reasoning_duration_ms"], 3200)
        self.assertEqual(execution["reasoning_tokens"], 4)
        self.assertEqual(execution["tokens"]["input"], 80448)
        self.assertEqual(execution["tokens"]["output"], 6)
        self.assertEqual(execution["pricing_variant"], "fast")
        self.assertAlmostEqual(execution["cost"], 0.241434, places=6)
        self.assertTrue(state["cost_approx"])
        self.assertTrue(state["token_estimate"])

    def test_cursor_transcript_fallback_survives_missing_database(self):
        transcript = [
            {"role": "user", "message": {"content": [{"type": "text", "text": "<user_query>hello</user_query>"}]}},
            {"role": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "one", "name": "Read", "input": {"path": "README.md"}},
                {"type": "text", "text": "done"},
            ]}},
            {"type": "turn_ended", "status": "success"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fallback.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in transcript))
            with mock.patch.object(meter, "cursor_snapshot", return_value={"available": False}), \
                    mock.patch.object(meter, "cursor_request_spans") as spans:
                state = meter.recompute_cursor(self.source(str(path), "fallback"))
        spans.assert_not_called()
        self.assertEqual(state["turns"], 1)
        self.assertTrue(state["cursor_enrichment"]["transcript_fallback"])
        self.assertEqual(state["executions"][0]["tools"][0]["name"], "read_file_v2")
        self.assertFalse(state["availability"]["tool_results"])

    def test_cursor_pricing_uses_persisted_composer_variant_and_fails_closed_without_it(self):
        price, approximate = meter.price_for("composer-2.5", "cursor")
        self.assertEqual(price, meter.ZERO_PRICE)
        self.assertTrue(approximate)
        fast, approximate = meter.price_for("composer-2.5", "cursor", "fast")
        standard, _ = meter.price_for("composer-2.5", "cursor", "standard")
        self.assertEqual((fast["input"], fast["output"]), (3.0, 15.0))
        self.assertEqual((standard["input"], standard["output"]), (0.5, 2.5))
        self.assertTrue(approximate)
        self.assertEqual(meter.cursor_price_variant(self.snapshot()["composer"], "composer-2.5"),
                         "fast")

    def test_cursor_agent_check_reports_local_estimate_and_caveat(self):
        with mock.patch.object(meter, "cursor_snapshot", return_value=self.snapshot()), \
                mock.patch.object(meter, "cursor_request_spans", return_value=self.spans()):
            state = meter.recompute_cursor(self.source())
        with mock.patch.object(meter, "resolve_agent_source", return_value=(self.source(), "matched")), \
                mock.patch.object(meter, "recompute", return_value=state):
            result = meter.agent_check(focus="cost", caller={"runtime": "cursor", "project": "/repo"})
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["evidence"][0]["value"], 0.2414, places=4)
        self.assertTrue(result["availability"]["cost"])
        self.assertIn("local", result["caveat"].lower())
        self.assertIn("cost", result["approximate_fields"])

    def test_cursor_summary_and_model_rollup_include_local_estimates(self):
        with mock.patch.object(meter, "cursor_snapshot", return_value=self.snapshot()), \
                mock.patch.object(meter, "cursor_request_spans", return_value=self.spans()):
            state = meter.recompute_cursor(self.source())
        with mock.patch.object(meter, "recompute_cursor", return_value=state):
            row = meter.cursor_summary(self.source())
        aggregate = meter.aggregate_model_stats([row])
        model = aggregate["models"][0]
        self.assertEqual(row["turns"], 1)
        self.assertEqual(row["models"], ["composer-2.5"])
        self.assertTrue(row["availability"]["cost"])
        self.assertTrue(row["cost_approx"])
        self.assertEqual(row["input_tokens"], 80448)
        self.assertEqual(row["output_tokens"], 6)
        self.assertEqual(row["usage_basis"], "local_estimate")
        self.assertEqual(row["provenance"]["estimated_sessions"], 1)
        self.assertEqual(model["model"], "composer-2.5")
        self.assertEqual(model["runtime"], "Cursor")
        self.assertEqual(model["usage_basis"], "local_estimate")
        self.assertFalse(model["availability"]["cache"])
        self.assertEqual(model["coverage"]["cache"]["covered_sessions"], 0)
        self.assertEqual(model["executions"], 1)
        self.assertEqual(model["median_peak_input_tokens"], 80448)
        self.assertEqual(model["median_tool_calls"], 1)
        self.assertAlmostEqual(model["median_wait_s"], 46.624, places=3)
        self.assertAlmostEqual(model["avg_ttft_ms"], 4118, places=3)
        self.assertTrue(model["availability"]["tokens"])
        self.assertEqual(model["input_tokens"], 80448)
        self.assertEqual(model["output_tokens"], 6)

    def test_usage_provenance_distinguishes_reported_estimated_and_mixed(self):
        reported = {"id": "reported", "provider": "codex", "cost": 1, "tokens": 10}
        estimated = {
            "id": "estimated", "provider": "cursor", "cost": 2, "tokens": 20,
            "token_estimate": True,
            "availability": meter.metric_availability("cursor", cost=True, tokens=True),
        }
        self.assertEqual(meter.usage_provenance([reported])["usage_basis"], "reported")
        local = meter.usage_provenance([estimated])
        self.assertEqual(local["usage_basis"], "local_estimate")
        self.assertEqual(local["estimated_cost"], 2)
        mixed = meter.usage_provenance([reported, estimated])
        self.assertEqual(mixed["usage_basis"], "mixed")
        self.assertEqual((mixed["reported_sessions"], mixed["estimated_sessions"]), (1, 1))

    def test_cursor_sparse_context_is_interpolated_between_persisted_checkpoints(self):
        groups = [
            {"context": {}},
            {"context": {"tokensUsed": 60000, "tokenLimit": 200000}},
            {"context": {}},
        ]
        rows = meter.cursor_context_estimates(groups, 90000, 200000)
        self.assertEqual([row["tokens"] for row in rows], [30000, 60000, 90000])
        self.assertTrue(rows[0]["interpolated"])
        self.assertFalse(rows[1]["interpolated"])

    def test_mixed_runtime_coverage_is_explicitly_partial(self):
        covered = {"availability": meter.metric_availability("codex")}
        cursor = {"availability": meter.metric_availability("cursor")}
        self.assertEqual(meter.metric_coverage([covered, cursor], "cost"), {
            "covered_sessions": 1, "total_sessions": 2, "complete": False,
        })


class ExecutionTimingTests(unittest.TestCase):
    def test_merges_overlapping_execution_windows(self):
        seconds = meter._merge_execution_intervals([(0, 10), (5, 15), (20, 25)])
        self.assertEqual(seconds, 20)

    def test_uses_claude_reported_turn_durations(self):
        objs = [
            {"type": "user", "timestamp": "2026-06-30T00:00:00.000Z", "message": {"content": "first"}},
            {"type": "system", "subtype": "turn_duration", "timestamp": "2026-06-30T00:00:10.000Z", "durationMs": 10000},
            {"type": "user", "timestamp": "2026-06-30T00:01:00.000Z", "message": {"content": "second"}},
            {"type": "system", "subtype": "turn_duration", "timestamp": "2026-06-30T00:01:20.000Z", "durationMs": 20000},
        ]
        timing = meter.execution_timing("claude", objs)
        self.assertEqual(timing["duration_s"], 30)
        self.assertEqual(timing["reported_executions"], 2)
        self.assertEqual(timing["basis"], "reported")

    def test_claude_observed_fallback_excludes_between_prompt_idle_gap(self):
        objs = [
            {"type": "user", "timestamp": "2026-06-30T00:00:00.000Z", "message": {"content": "first"}},
            {"type": "assistant", "timestamp": "2026-06-30T00:00:10.000Z", "message": {"content": "done"}},
            {"type": "user", "timestamp": "2026-06-30T01:00:00.000Z", "message": {"content": "second"}},
            {"type": "assistant", "timestamp": "2026-06-30T01:00:20.000Z", "message": {"content": "done"}},
        ]
        timing = meter.execution_timing("claude", objs)
        self.assertEqual(timing["duration_s"], 30)
        self.assertEqual(timing["observed_executions"], 2)
        self.assertEqual(timing["basis"], "observed")

    def test_combines_codex_reported_and_open_execution_time(self):
        objs = [
            {"timestamp": "2026-06-30T00:00:00.000Z", "payload": {"type": "task_started"}},
            {"timestamp": "2026-06-30T00:00:30.000Z", "payload": {"type": "task_complete", "duration_ms": 20000}},
            {"timestamp": "2026-06-30T00:01:00.000Z", "payload": {"type": "task_started"}},
            {"timestamp": "2026-06-30T00:01:15.000Z", "payload": {"type": "agent_message"}},
        ]
        timing = meter.execution_timing("codex", objs)
        self.assertEqual(timing["duration_s"], 35)
        self.assertEqual(timing["reported_executions"], 1)
        self.assertEqual(timing["observed_executions"], 1)
        self.assertEqual(timing["basis"], "reported + observed")

    def test_codex_context_accepts_structured_approval_policy(self):
        self.assertEqual(meter.codex_approval_policy_label("on_request"), "on request")
        self.assertEqual(
            meter.codex_approval_policy_label({
                "granular": {
                    "sandbox_approval": False,
                    "request_permissions": True,
                },
            }),
            "granular",
        )


class WaitTimeTests(unittest.TestCase):
    def test_claude_wait_is_prompt_to_completion_and_dedupes_split_messages(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-13T00:00:00.000Z",
             "message": {"content": "do the work"}},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:02.000Z", "message": {
                "id": "msg-1", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "working"}],
                "usage": {"input_tokens": 20, "output_tokens": 30}, "stop_reason": "tool_use",
            }},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:03.000Z", "message": {
                "id": "msg-1", "model": "claude-opus-4-8",
                "content": [{"type": "tool_use", "id": "tool-1", "name": "Read"}],
                "usage": {"input_tokens": 20, "output_tokens": 30}, "stop_reason": "tool_use",
            }},
            {"type": "user", "timestamp": "2026-07-13T00:00:04.000Z", "message": {
                "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "done"}],
            }},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:09.000Z", "message": {
                "id": "msg-2", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "finished"}],
                "usage": {"input_tokens": 25, "output_tokens": 20}, "stop_reason": "end_turn",
            }},
            {"type": "system", "subtype": "turn_duration",
             "timestamp": "2026-07-13T00:00:10.000Z", "durationMs": 10000},
        ]
        samples = meter.claude_wait_samples(objs)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["duration_s"], 10)
        self.assertEqual(samples[0]["tool_calls"], 1)
        self.assertEqual(samples[0]["output_tokens"], 50)
        self.assertEqual(samples[0]["timing_basis"], "reported")

    def test_codex_wait_uses_reported_duration_and_includes_tool_time(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-13T00:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-13T00:00:00.000Z", "payload": {"type": "task_started"}},
            {"timestamp": "2026-07-13T00:00:02.000Z", "payload": {
                "type": "function_call", "name": "exec_command", "call_id": "call-1",
            }},
            {"timestamp": "2026-07-13T00:00:08.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100, "output_tokens": 40, "total_tokens": 140,
                }},
            }},
            {"timestamp": "2026-07-13T00:00:10.000Z", "payload": {
                "type": "task_complete", "duration_ms": 10000,
            }},
        ]
        sample = meter.codex_wait_samples(objs)[0]
        self.assertEqual(sample["duration_s"], 10)
        self.assertEqual(sample["tool_calls"], 1)
        self.assertEqual(sample["output_tokens"], 40)
        self.assertEqual(sample["model"], "gpt-5.6")

    def test_claude_observed_wait_excludes_time_waiting_for_user_input(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-13T00:00:00.000Z",
             "message": {"content": "start"}},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:10.000Z", "message": {
                "id": "msg-1", "model": "claude-opus-4-8", "stop_reason": "tool_use",
                "usage": {"input_tokens": 20, "output_tokens": 10},
                "content": [{"type": "tool_use", "id": "question-1", "name": "AskUserQuestion"}],
            }},
            {"type": "user", "timestamp": "2026-07-13T00:01:40.000Z", "message": {
                "content": [{"type": "tool_result", "tool_use_id": "question-1", "content": "answer"}],
            }},
            {"type": "assistant", "timestamp": "2026-07-13T00:01:50.000Z", "message": {
                "id": "msg-2", "model": "claude-opus-4-8", "stop_reason": "end_turn",
                "usage": {"input_tokens": 25, "output_tokens": 20},
                "content": [{"type": "text", "text": "finished"}],
            }},
        ]
        sample = meter.claude_wait_samples(objs)[0]
        self.assertEqual(sample["wall_duration_s"], 110)
        self.assertEqual(sample["user_pause_s"], 90)
        self.assertEqual(sample["duration_s"], 20)
        self.assertEqual(meter.wait_time_summary([sample])["user_pause_s"], 90)

    def test_wait_summary_reports_average_p95_and_longest(self):
        summary = meter.wait_time_summary([
            {"duration_s": 2, "timing_basis": "reported"},
            {"duration_s": 6, "timing_basis": "observed"},
            {"duration_s": 10, "timing_basis": "reported"},
        ])
        self.assertEqual(summary["total_s"], 18)
        self.assertEqual(summary["avg_s"], 6)
        self.assertEqual(summary["median_s"], 6)
        self.assertEqual(summary["p95_s"], 10)
        self.assertEqual(summary["max_s"], 10)


class ModelPerformanceTests(unittest.TestCase):
    def test_claude_tool_free_turn_uses_reported_turn_duration(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-01T00:00:00.000Z",
             "message": {"content": "hello"}},
            {"type": "assistant", "timestamp": "2026-07-01T00:00:01.900Z", "message": {
                "id": "msg-1", "model": "claude-sonnet-5", "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 20, "output_tokens": 10}, "stop_reason": "end_turn",
            }},
            {"type": "system", "subtype": "turn_duration", "timestamp": "2026-07-01T00:00:02.000Z",
             "durationMs": 2000},
        ]
        samples = meter.claude_performance_samples(objs)
        summary = meter.performance_summary(samples, 10)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["tool_calls"], 0)
        self.assertEqual(samples[0]["peak_input_tokens"], 20)
        self.assertEqual(samples[0]["model_calls"], 1)
        self.assertEqual(summary["basis"], "tool_free")
        self.assertEqual(summary["output_tps"], 5)

    def test_claude_desktop_uses_observed_turn_timing_without_duration_records(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-13T00:00:00.000Z",
             "message": {"content": "inspect the project"}},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:02.000Z", "message": {
                "id": "msg-1", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "checking"}],
                "usage": {"input_tokens": 20, "output_tokens": 30}, "stop_reason": "tool_use",
            }},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:04.000Z", "message": {
                "id": "msg-1", "model": "claude-opus-4-8",
                "content": [{"type": "tool_use", "id": "tool-1", "name": "Read"}],
                "usage": {"input_tokens": 20, "output_tokens": 30}, "stop_reason": "tool_use",
            }},
            {"type": "user", "timestamp": "2026-07-13T00:00:05.000Z", "message": {
                "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "done"}],
            }},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:10.000Z", "message": {
                "id": "msg-2", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "finished"}],
                "usage": {"input_tokens": 25, "output_tokens": 20}, "stop_reason": "end_turn",
            }},
        ]
        samples = meter.claude_performance_samples(objs)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["timing_basis"], "observed")
        self.assertEqual(samples[0]["duration_s"], 10)
        self.assertEqual(samples[0]["output_tokens"], 50)
        self.assertEqual(samples[0]["tool_calls"], 1)
        self.assertEqual(meter.performance_summary(samples, 50)["output_tps"], 5)

    def test_claude_observed_throughput_excludes_user_input_pause(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-13T00:00:00.000Z",
             "message": {"content": "start"}},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:10.000Z", "message": {
                "id": "msg-1", "model": "claude-opus-4-8", "stop_reason": "tool_use",
                "usage": {"input_tokens": 20, "output_tokens": 10},
                "content": [{"type": "tool_use", "id": "question-1", "name": "AskUserQuestion"}],
            }},
            {"type": "user", "timestamp": "2026-07-13T00:01:40.000Z", "message": {
                "content": [{"type": "tool_result", "tool_use_id": "question-1", "content": "answer"}],
            }},
            {"type": "assistant", "timestamp": "2026-07-13T00:01:50.000Z", "message": {
                "id": "msg-2", "model": "claude-opus-4-8", "stop_reason": "end_turn",
                "usage": {"input_tokens": 25, "output_tokens": 20},
                "content": [{"type": "text", "text": "finished"}],
            }},
        ]
        sample = meter.claude_performance_samples(objs)[0]
        self.assertEqual(sample["wall_duration_s"], 110)
        self.assertEqual(sample["user_pause_s"], 90)
        self.assertEqual(sample["duration_s"], 20)

    def test_codex_tool_free_speed_excludes_time_to_first_token(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-01T00:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-01T00:00:00.000Z", "payload": {"type": "task_started"}},
            {"timestamp": "2026-07-01T00:00:09.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 200, "cached_input_tokens": 50,
                    "output_tokens": 100, "total_tokens": 300,
                }},
            }},
            {"timestamp": "2026-07-01T00:00:10.000Z", "payload": {
                "type": "task_complete", "duration_ms": 10000, "time_to_first_token_ms": 2000,
            }},
        ]
        samples = meter.codex_performance_samples(objs, "gpt-5.6")
        summary = meter.performance_summary(samples, 100)
        self.assertEqual(samples[0]["generation_s"], 8)
        self.assertEqual(samples[0]["peak_input_tokens"], 200)
        self.assertEqual(samples[0]["cache_read_tokens"], 50)
        self.assertEqual(samples[0]["uncached_input_tokens"], 150)
        self.assertEqual(samples[0]["model_calls"], 1)
        self.assertEqual(summary["output_tps"], 12.5)
        self.assertEqual(summary["avg_ttft_ms"], 2000)

    def test_tool_bearing_task_falls_back_to_end_to_end_throughput(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-01T00:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-01T00:00:00.000Z", "payload": {"type": "task_started"}},
            {"timestamp": "2026-07-01T00:00:02.000Z", "payload": {
                "type": "function_call", "name": "exec_command", "call_id": "call-1",
            }},
            {"timestamp": "2026-07-01T00:00:09.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100, "output_tokens": 100, "total_tokens": 200,
                }},
            }},
            {"timestamp": "2026-07-01T00:00:10.000Z", "payload": {
                "type": "task_complete", "duration_ms": 10000, "time_to_first_token_ms": 1000,
            }},
        ]
        summary = meter.performance_summary(meter.codex_performance_samples(objs), 100)
        self.assertEqual(summary["basis"], "end_to_end")
        self.assertEqual(summary["tool_free_samples"], 0)
        self.assertEqual(summary["output_tps"], 10)

    def test_cross_log_model_aggregation_is_weighted_and_keeps_daily_io(self):
        sessions = [{
            "provider": "codex",
            "model_stats": [{"model": "gpt-5.6", "cost": 1.0, "tokens": 330,
                             "input_tokens": 300, "output_tokens": 30, "executions": 2}],
            "_model_daily": [{"model": "gpt-5.6", "day": "2026-07-01", "cost": 1.0,
                              "input_tokens": 300, "output_tokens": 30, "executions": 2}],
            "_performance_samples": [
                {"model": "gpt-5.6", "day": "2026-07-01", "ts": 10,
                 "output_tokens": 10, "duration_s": 2, "generation_s": 1, "tool_calls": 0},
                {"model": "gpt-5.6", "day": "2026-07-01", "ts": 20,
                 "output_tokens": 20, "duration_s": 5, "generation_s": 3, "tool_calls": 0},
            ],
            "_wait_samples": [
                {"model": "gpt-5.6", "day": "2026-07-01", "duration_s": 8},
                {"model": "gpt-5.6", "day": "2026-07-01", "duration_s": 12},
            ],
        }]
        result = meter.aggregate_model_stats(sessions)
        row = result["models"][0]
        self.assertEqual(row["output_tps"], 7.5)
        self.assertEqual(row["timing_coverage"], 1)
        self.assertEqual(row["daily"][0]["input_tokens"], 300)
        self.assertEqual(row["daily"][0]["throughput_samples"], 2)
        self.assertEqual(row["avg_wait_s"], 10)
        self.assertEqual(row["median_wait_s"], 10)
        self.assertEqual(row["p95_wait_s"], 12)
        self.assertEqual(row["daily"][0]["wait_durations_s"], [8, 12])
        self.assertEqual(row["daily"][0]["max_wait_s"], 12)

    def test_tool_free_speed_coverage_counts_only_selected_output(self):
        summary = meter.performance_summary([
            {"output_tokens": 10, "duration_s": 2, "generation_s": 1, "tool_calls": 0, "ts": 1},
            {"output_tokens": 90, "duration_s": 9, "generation_s": 8, "tool_calls": 1, "ts": 2},
        ], 100)
        self.assertEqual(summary["basis"], "tool_free")
        self.assertEqual(summary["output_tps"], 10)
        self.assertEqual(summary["timing_coverage"], 0.1)

    def test_model_aggregation_omits_rows_without_io(self):
        result = meter.aggregate_model_stats([{
            "provider": "claude",
            "model_stats": [{"model": "<synthetic>", "input_tokens": 0,
                             "output_tokens": 0, "executions": 2, "cost": 0}],
        }])
        self.assertEqual(result["models"], [])
        self.assertEqual(result["total_models"], 0)

    def test_model_aggregation_splits_same_model_by_runtime(self):
        def session(runtime, output):
            return {
                "provider": "claude", "runtime": runtime,
                "model_stats": [{"model": "claude-opus", "cost": 1, "tokens": 100,
                                 "input_tokens": 90, "output_tokens": output, "executions": 1}],
                "_model_daily": [], "_performance_samples": [], "_wait_samples": [],
            }
        result = meter.aggregate_model_stats([
            session("Claude Code", 10), session("Claude-3P", 20),
        ])
        self.assertEqual(len(result["models"]), 2)
        self.assertEqual(
            {(row["model"], row["runtime"], row["id"]) for row in result["models"]},
            {
                ("claude-opus", "Claude Code", "claude-opus::Claude Code"),
                ("claude-opus", "Claude-3P", "claude-opus::Claude-3P"),
            },
        )

    def test_cursor_samples_are_excluded_from_matched_pace(self):
        samples = [{
            "model": "gpt-5.6", "day": "2026-07-20", "ts": index + 1,
            "input_tokens": 100, "peak_input_tokens": 100, "output_tokens": 20,
            "duration_s": 2, "generation_s": 2, "tool_calls": 0, "model_calls": 1,
        } for index in range(25)]
        cursor = {
            "id": "cursor", "provider": "cursor", "runtime": "Cursor",
            "token_estimate": True,
            "availability": meter.metric_availability(
                "cursor", cost=True, tokens=True, input_tokens=True,
                output_tokens=True, throughput=True, timing=True,
            ),
            "model_stats": [{"model": "gpt-5.6", "cost": 1, "tokens": 120,
                              "input_tokens": 100, "output_tokens": 20, "executions": 1}],
            "_model_daily": [], "_performance_samples": samples, "_wait_samples": [],
        }
        codex = {
            **cursor, "id": "codex", "provider": "codex", "runtime": "Codex",
            "token_estimate": False, "availability": meter.metric_availability("codex"),
        }
        result = meter.aggregate_model_stats([cursor, codex])
        self.assertEqual({row["id"] for row in result["models"]},
                         {"gpt-5.6::Cursor", "gpt-5.6::Codex"})
        self.assertFalse(any(
            any("::Cursor" in value for value in (pair["a_id"], pair["b_id"]))
            for pairs in result["matched_pace"]["windows"].values() for pair in pairs
        ))

    def test_runtime_label_distinguishes_claude_desktop_roots(self):
        standard = {
            "provider": "claude", "client": "claude_desktop",
            "metadata_path": str(Path(meter.CLAUDE_DESKTOP_DATA_ROOTS[0]) / "sessions" / "one.json"),
        }
        third_party = {
            "provider": "claude", "client": "claude_desktop",
            "metadata_path": str(Path(meter.CLAUDE_DESKTOP_DATA_ROOTS[1]) / "sessions" / "two.json"),
        }
        self.assertEqual(meter.source_runtime_label(standard), "Claude Desktop")
        self.assertEqual(meter.source_runtime_label(third_party), "Claude-3P")
        self.assertEqual(meter.source_runtime_label({"provider": "claude", "client": "claude_code"}),
                         "Claude Code")
        self.assertEqual(meter.source_runtime_label({"provider": "codex"}), "Codex")
        self.assertEqual(meter.source_runtime_label({"provider": "cursor"}), "Cursor")

    def test_matched_pace_reports_ratio_confidence_and_coverage(self):
        def sample(duration, ts):
            return {
                "duration_s": duration, "ts": ts, "day": "2026-07-13",
                "input_tokens": 100000, "peak_input_tokens": 50000,
                "cache_read_tokens": 70000, "output_tokens": 2000,
                "tool_calls": 4, "model_calls": 3,
            }
        left = [sample(10, index * 60) for index in range(24)]
        right = [sample(20, index * 60 + 1) for index in range(24)]
        comparison = meter.matched_pace_comparison("left", left, "right", right)
        self.assertTrue(comparison["available"])
        self.assertEqual(comparison["matched_pairs"], 24)
        self.assertEqual(comparison["coverage"], 1)
        self.assertEqual(comparison["pace_ratio"], 2)
        self.assertEqual(comparison["ci_low"], 2)
        self.assertEqual(comparison["ci_high"], 2)

    def test_matched_pace_withholds_sparse_or_different_tool_shapes(self):
        base = {
            "duration_s": 10, "ts": 1, "day": "2026-07-13",
            "input_tokens": 10000, "peak_input_tokens": 10000,
            "output_tokens": 1000, "model_calls": 1, "cache_read_tokens": 0,
        }
        sparse = meter.matched_pace_comparison(
            "a", [{**base, "tool_calls": 0}] * 19,
            "b", [{**base, "tool_calls": 0}] * 19,
        )
        different = meter.matched_pace_comparison(
            "a", [{**base, "tool_calls": 0}] * 20,
            "b", [{**base, "tool_calls": 1}] * 20,
        )
        self.assertFalse(sparse["available"])
        self.assertIn("20 timed turns", sparse["reason"])
        self.assertFalse(different["available"])
        self.assertEqual(different["matched_pairs"], 0)
        self.assertIn("comparable turns", different["reason"])


class FrustrationSignalTests(unittest.TestCase):
    def test_matches_whole_terms_and_counts_repeated_hits(self):
        counts = meter.frustration_term_counts(
            "Fuck, fuck this bullshit. Classify is safe.", ["fuck", "bullshit", "ass"]
        )
        self.assertEqual(counts, {"fuck": 2, "bullshit": 1})

    def test_codex_prefers_canonical_user_events_and_tracks_model(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-07T08:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"type": "response_item", "timestamp": "2026-07-07T08:00:01.000Z",
             "payload": {"type": "message", "role": "user", "content": [
                 {"type": "input_text", "text": "# AGENTS.md instructions\nshit appears in config"}
             ]}},
            {"type": "response_item", "timestamp": "2026-07-07T08:00:02.000Z",
             "payload": {"type": "message", "role": "user", "content": [
                 {"type": "input_text", "text": "fuck this"}
             ]}},
            {"type": "event_msg", "timestamp": "2026-07-07T08:00:02.000Z",
             "payload": {"type": "user_message", "message": "fuck this"}},
            {"type": "event_msg", "timestamp": "2026-07-07T08:02:00.000Z",
             "payload": {"type": "user_message", "message": "looks good"}},
        ]
        summary, events = meter.analyze_frustration("codex", objs, ["fuck", "shit"])
        self.assertEqual(summary["user_turns"], 2)
        self.assertEqual(summary["utterances"], 1)
        self.assertEqual(summary["matches"], 1)
        self.assertEqual(summary["rate"], 0.5)
        self.assertEqual(summary["models"][0]["model"], "gpt-5.6")
        self.assertNotIn("text", events[0])

    def test_claude_skips_tool_and_sidechain_user_records(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-07T08:00:00.000Z",
             "message": {"content": "this is fucking shit"}},
            {"type": "assistant", "timestamp": "2026-07-07T08:00:02.000Z",
             "message": {"model": "claude-opus-4-8", "content": []}},
            {"type": "user", "timestamp": "2026-07-07T08:00:03.000Z",
             "sourceToolAssistantUUID": "tool-call", "message": {"content": [
                 {"type": "tool_result", "content": "idiot in command output"}
             ]}},
            {"type": "user", "timestamp": "2026-07-07T08:00:04.000Z",
             "isSidechain": True, "message": {"content": "bullshit from an agent"}},
            {"type": "user", "timestamp": "2026-07-07T08:01:00.000Z",
             "message": {"content": "try again"}},
            {"type": "assistant", "timestamp": "2026-07-07T08:01:02.000Z",
             "message": {"model": "claude-sonnet-5", "content": []}},
        ]
        summary, _ = meter.analyze_frustration(
            "claude", objs, ["fucking", "shit", "idiot", "bullshit"]
        )
        self.assertEqual(summary["user_turns"], 2)
        self.assertEqual(summary["utterances"], 1)
        self.assertEqual(summary["matches"], 2)
        self.assertEqual([row["model"] for row in summary["models"]],
                         ["claude-opus-4-8", "claude-sonnet-5"])

    def test_aggregates_sessions_into_days_weeks_and_models(self):
        event_one = {"ts": 1, "day": "2026-07-06", "week": "2026-07-06",
                     "model": "gpt-5.6", "utterance": True, "matches": 2,
                     "term_counts": {"fuck": 2}}
        event_two = {"ts": 2, "day": "2026-07-07", "week": "2026-07-06",
                     "model": "claude-opus-4-8", "utterance": False, "matches": 0,
                     "term_counts": {}}
        sessions = [
            {"_frustration_events": [event_one], "frustration": {"user_turns": 1, "utterances": 1}},
            {"_frustration_events": [event_two], "frustration": {"user_turns": 1, "utterances": 0}},
        ]
        result = meter.aggregate_frustration(sessions, ["fuck"])
        self.assertEqual(result["user_turns"], 2)
        self.assertEqual(result["utterances"], 1)
        self.assertEqual(result["rate"], 0.5)
        self.assertEqual(result["matches"], 2)
        self.assertEqual(result["affected_sessions"], 1)
        self.assertEqual(result["weekly"][0]["week"], "2026-07-06")
        self.assertEqual(len(result["daily"]), 2)
        self.assertEqual(len(result["models"]), 2)

    def test_aggregate_keeps_same_model_separate_by_runtime(self):
        event = {"ts": 1, "day": "2026-07-20", "week": "2026-07-20",
                 "model": "gpt-5.6", "utterance": True, "matches": 1,
                 "term_counts": {"shit": 1}}
        sessions = [
            {"provider": "codex", "runtime": "Codex", "_frustration_events": [event],
             "frustration": {"user_turns": 1, "utterances": 1}},
            {"provider": "cursor", "runtime": "Cursor", "_frustration_events": [event],
             "frustration": {"user_turns": 1, "utterances": 1}},
        ]
        result = meter.aggregate_frustration(sessions, ["shit"])
        self.assertEqual({(row["id"], row["runtime"]) for row in result["models"]}, {
            ("gpt-5.6::Codex", "Codex"), ("gpt-5.6::Cursor", "Cursor"),
        })

    def test_persists_machine_wide_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            result = meter.set_frustration_terms("Damn, custom phrase, damn", str(path))
            loaded = meter.frustration_settings(str(path))
        self.assertTrue(result["ok"])
        self.assertEqual(loaded["terms"], ["damn", "custom phrase"])


class PricingTests(unittest.TestCase):
    def test_sonnet_5_uses_introductory_api_rates(self):
        price, approximate = meter.price_for("claude-sonnet-5", "claude")
        self.assertEqual(price, {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.2})
        self.assertFalse(approximate)

    def test_gpt_5_6_uses_sol_api_rates(self):
        price, approximate = meter.price_for("gpt-5.6", "codex")
        self.assertEqual(price, {"input": 5.0, "output": 30.0, "cache_write": 6.25, "cache_read": 0.5})
        self.assertFalse(approximate)

    def test_custom_model_price_persists_and_drives_cost_calculation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                result = meter.set_model_price(
                    "openai", "gpt-custom-1",
                    {"input": 1.25, "output": 6.5, "cache_write": 0, "cache_read": 0.2},
                )
                price, approximate = meter.price_for("gpt-custom-1", "codex")
                cost = meter.cost_of({
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "cache_creation_input_tokens": 1_000_000,
                    "cache_read_input_tokens": 1_000_000,
                }, "gpt-custom-1", "codex")
                saved = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(price, {
            "input": 1.25, "output": 6.5, "cache_write": 0.0, "cache_read": 0.2,
        })
        self.assertFalse(approximate)
        self.assertEqual(cost, {
            "input": 1.25, "output": 6.5, "cache_write": 0.0, "cache_read": 0.2,
        })
        self.assertIn("gpt-custom-1", saved["model_pricing"]["codex"])

    def test_builtin_override_can_be_restored_without_losing_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"frustration_terms": ["damn"]}))
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                changed = meter.set_model_price(
                    "claude", "claude-sonnet-5",
                    {"input": 4, "output": 20, "cache_write": 5, "cache_read": 0.4},
                )
                price, approximate = meter.price_for("claude-sonnet-5", "claude")
                pricing = meter.model_pricing_settings()
                restored = meter.set_model_price(
                    "claude", "claude-sonnet-5", remove=True,
                )
                default_price, default_approximate = meter.price_for(
                    "claude-sonnet-5", "claude",
                )
                saved = json.loads(path.read_text())
        row = next(
            item for item in pricing["models"]
            if item["provider"] == "claude" and item["model"] == "claude-sonnet-5"
        )
        self.assertTrue(changed["ok"])
        self.assertEqual(price["input"], 4.0)
        self.assertFalse(approximate)
        self.assertEqual(row["source"], "override")
        self.assertTrue(restored["ok"])
        self.assertEqual(default_price, meter.CLAUDE_PRICE["claude-sonnet-5"])
        self.assertFalse(default_approximate)
        self.assertEqual(saved["frustration_terms"], ["damn"])
        self.assertNotIn("model_pricing", saved)

    def test_invalid_custom_model_prices_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            bad_model = meter.set_model_price(
                "codex", "model with spaces",
                {"input": 1, "output": 2, "cache_write": 0, "cache_read": 0},
                path=str(path),
            )
            bad_price = meter.set_model_price(
                "codex", "gpt-custom",
                {"input": -1, "output": 2, "cache_write": 0, "cache_read": 0},
                path=str(path),
            )
        self.assertFalse(bad_model["ok"])
        self.assertFalse(bad_price["ok"])
        self.assertFalse(path.exists())

    def test_longest_model_prefix_wins(self):
        price, approximate = meter.price_for("gpt-5.4-mini-2026-07-01", "codex")
        self.assertEqual(price, meter.OPENAI_PRICE["gpt-5.4-mini"])
        self.assertFalse(approximate)


class SessionSummaryStatsTests(unittest.TestCase):
    def source(self, provider, model=None):
        return {
            "id": "session", "path": "/tmp/session.jsonl", "provider": provider,
            "client": provider, "label": provider.title(), "project": "/repo",
            "mtime": 1, "title": "Summary stats", "model": model,
        }

    def test_claude_summary_exposes_input_output_and_model_stats(self):
        objs = [{
            "type": "assistant", "timestamp": "2026-07-02T00:00:00.000Z",
            "message": {
                "id": "msg-1", "model": "claude-sonnet-4-6", "content": [],
                "usage": {"input_tokens": 100, "cache_creation_input_tokens": 20,
                          "cache_read_input_tokens": 30, "output_tokens": 10},
            },
        }]
        row = meter.claude_summary(self.source("claude"), objs)
        self.assertEqual(row["input_tokens"], 150)
        self.assertEqual(row["output_tokens"], 10)
        self.assertEqual(row["model_stats"][0]["model"], "claude-sonnet-4-6")
        self.assertEqual(row["model_stats"][0]["executions"], 1)

    def test_codex_summary_exposes_input_output_and_model_stats(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-02T00:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-02T00:00:01.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100, "cached_input_tokens": 40,
                    "output_tokens": 20, "total_tokens": 120,
                }},
            }},
        ]
        row = meter.codex_summary(self.source("codex", "gpt-5.6"), objs)
        self.assertEqual(row["input_tokens"], 100)
        self.assertEqual(row["output_tokens"], 20)
        self.assertEqual(row["model_stats"][0]["model"], "gpt-5.6")
        self.assertEqual(row["model_stats"][0]["tokens"], 120)


class SessionRouteTests(unittest.TestCase):
    def test_dashboard_accepts_root_and_unique_session_paths(self):
        self.assertTrue(meter.is_dashboard_page_path("/"))
        self.assertTrue(meter.is_dashboard_page_path("/sessions/019f16fa-dc6c-7a62-839c-25c15dca4e75"))
        self.assertTrue(meter.is_dashboard_page_path("/sessions/claude%20session/"))

    def test_dashboard_rejects_api_nested_and_empty_session_paths(self):
        self.assertFalse(meter.is_dashboard_page_path("/session"))
        self.assertFalse(meter.is_dashboard_page_path("/sessions/"))
        self.assertFalse(meter.is_dashboard_page_path("/sessions/one/two"))


class SessionDeleteTests(unittest.TestCase):
    def test_moves_only_exact_discovered_jsonl_to_trash(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "logs"
            trash_dir = Path(tmp) / "Trash"
            source_dir.mkdir()
            path = source_dir / "session-one.jsonl"
            path.write_text('{"type":"test"}\n')
            source = {
                "id": "session-one", "session": path.name, "path": str(path),
                "provider": "codex", "project": "/repo", "title": "One", "mtime": 1,
            }
            result = meter.trash_session_log("session-one", sources=[source], trash_dir=str(trash_dir))
            trashed = trash_dir / result["trash_name"]
            self.assertTrue(result["ok"])
            self.assertFalse(path.exists())
            self.assertTrue(trashed.exists())
            self.assertEqual(trashed.read_text(), '{"type":"test"}\n')
            self.assertNotIn(str(path), json.dumps(result))

    def test_rejects_alias_and_unknown_session_ids(self):
        source = {
            "id": "canonical", "session": "alias.jsonl", "path": "/tmp/alias.jsonl",
            "provider": "codex", "project": "/repo", "mtime": 1,
        }
        self.assertEqual(meter.trash_session_log("alias.jsonl", sources=[source])["error_code"], "not_found")
        self.assertEqual(meter.trash_session_log("missing", sources=[source])["error_code"], "not_found")

    def test_cursor_delete_moves_only_transcript_and_preserves_shared_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = root / "projects" / "repo" / "agent-transcripts" / "cursor-one" / "cursor-one.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text('{"role":"user"}\n')
            database = root / "state.vscdb"
            database.write_bytes(b"shared cursor database")
            source = {
                "id": "cursor-one", "session": transcript.name, "path": str(transcript),
                "provider": "cursor", "project": "/repo", "title": "Cursor", "mtime": 1,
            }
            result = meter.trash_session_log(
                "cursor-one", sources=[source], trash_dir=str(root / "Trash")
            )
            self.assertTrue(result["ok"])
            self.assertFalse(transcript.exists())
            self.assertTrue(database.exists())
            self.assertEqual(database.read_bytes(), b"shared cursor database")

    def test_publish_after_delete_selects_newest_remaining_session(self):
        sources = [{"id": "older", "path": "/tmp/older.jsonl", "mtime": 1},
                   {"id": "newer", "path": "/tmp/newer.jsonl", "mtime": 2}]
        published = []
        with mock.patch.object(meter, "all_session_sources", return_value=sources), \
                mock.patch.object(meter, "cross_session", return_value={"sessions": []}), \
                mock.patch.object(meter, "recompute", return_value={"source": {"id": "newer"}}), \
                mock.patch.object(meter, "publish", side_effect=published.append):
            selected = meter.publish_after_session_delete()
        self.assertEqual(selected, "newer")
        self.assertEqual(published[0]["source"]["id"], "newer")


class LiveCrossSessionRefreshTests(unittest.TestCase):
    def test_full_sse_queue_keeps_subscriber_and_coalesces_to_latest_state(self):
        q_ = meter.queue.Queue(maxsize=2)
        q_.put_nowait("stale-one")
        q_.put_nowait("stale-two")
        self.assertTrue(meter.enqueue_latest(q_, "latest"))
        self.assertEqual(q_.qsize(), 1)
        self.assertEqual(q_.get_nowait(), "latest")

    def test_source_signature_changes_when_a_background_log_changes(self):
        before = meter.source_mtime_signature([
            {"path": "/logs/current.jsonl", "mtime": 20},
            {"path": "/logs/background.jsonl", "mtime": 10},
        ])
        after = meter.source_mtime_signature([
            {"path": "/logs/current.jsonl", "mtime": 20},
            {"path": "/logs/background.jsonl", "mtime": 11},
        ])
        self.assertNotEqual(before, after)

    def test_cross_session_refresh_replaces_cached_snapshot_before_publish(self):
        saved_cache = dict(meter._xsess)
        published = []
        fresh = {"sessions": [{"id": "fresh"}], "capabilities": {}}
        state = {"provider": "codex", "tools": {}, "source": {"id": "current"}}
        try:
            meter._xsess["data"], meter._xsess["at"] = {"sessions": [{"id": "stale"}]}, 123
            result = meter.refresh_cross_session_state(
                state, builder=lambda: fresh, publisher=published.append,
            )
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)
        self.assertIs(result, fresh)
        self.assertEqual(published[0]["xsession"]["sessions"][0]["id"], "fresh")

    def test_logs_inventory_keeps_full_rows_beyond_state_preview(self):
        saved_cache = dict(meter._xsess)
        preview = [{"id": "recent"}]
        full = [{"id": "recent"}, {"id": "older-claude", "client": "claude_code"}]
        try:
            meter._xsess.update({
                "data": {"generated_at": 123, "sessions": preview, "total_sessions": 2},
                "at": meter.time.time(),
                "sessions": full,
            })
            result = meter.log_sessions_state()
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)
        self.assertEqual(result["sessions"], full)
        self.assertEqual(result["total_sessions"], 2)
        self.assertEqual(result["generated_at"], 123)

    def test_cross_session_separates_runtime_models_and_reported_alert_basis(self):
        def row(source):
            estimated = source["provider"] == "cursor"
            cost = 2.0 if estimated else 1.0
            tokens = 200 if estimated else 100
            return {
                **source, "turns": 1, "cost": cost, "tokens": tokens,
                "input_tokens": tokens - 10, "output_tokens": 10,
                "models": ["gpt-5.6"], "token_estimate": estimated,
                "availability": meter.metric_availability(
                    source["provider"], cost=True, tokens=True,
                    input_tokens=True, output_tokens=True,
                ),
                "_model_cost": {"gpt-5.6": cost}, "_model_tok": {"gpt-5.6": tokens},
                "_day_cost": {"2026-07-20": cost}, "model_stats": [],
                "_model_daily": [], "_performance_samples": [], "_wait_samples": [],
                "_tool_evidence": {}, "frustration": {}, "_frustration_events": [],
            }

        sources = [
            {"id": "codex", "path": "/tmp/codex", "provider": "codex",
             "runtime": "Codex", "label": "Codex", "project": "/repo", "mtime": 2},
            {"id": "cursor", "path": "/tmp/cursor", "provider": "cursor",
             "runtime": "Cursor", "label": "Cursor", "project": "/repo", "mtime": 1},
        ]
        rows = {source["id"]: row(source) for source in sources}
        saved_cache = dict(meter._xsess)
        try:
            meter._xsess["data"], meter._xsess["at"] = None, 0
            with mock.patch.object(meter, "all_session_sources", return_value=sources), \
                    mock.patch.object(meter, "session_summary",
                                      side_effect=lambda source: rows[source["id"]]), \
                    mock.patch.object(meter, "capability_inventory", return_value={}):
                result = meter.cross_session()
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)
        self.assertEqual({item["id"] for item in result["model_mix"]},
                         {"gpt-5.6::Codex", "gpt-5.6::Cursor"})
        self.assertEqual(result["usage_basis"], "mixed")
        self.assertEqual(result["reported_cost"], 1.0)
        self.assertEqual(result["estimated_cost"], 2.0)
        self.assertEqual(result["trend"][0]["reported_cost"], 1.0)
        self.assertEqual(result["trend"][0]["anomaly_basis"], "reported_only")


class DashboardLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path(meter.__file__).with_name("page.html").read_text()

    def test_current_tabs_exclude_efficiency_panel(self):
        self.assertNotIn("data-panel=efficiency", self.page)
        self.assertNotIn("id=panel-efficiency", self.page)
        self.assertIn("const PANEL_KEYS=['summary','activity','tools','insights','alerts'];", self.page)
        self.assertIn("efficiency:'summary'", self.page)

    def test_dashboard_uses_inline_token_meter_favicon(self):
        self.assertIn('rel=icon type="image/svg+xml"', self.page)
        self.assertIn("data:image/svg+xml", self.page)
        self.assertIn("stop-color='%2300bceb'", self.page)
        self.assertIn('name=theme-color content="#07090c"', self.page)

    def test_current_header_keeps_session_start_message_visible(self):
        self.assertIn("id=session-start", self.page)
        self.assertIn("id=session-start-text", self.page)
        self.assertIn("function sessionStartMessage(s)", self.page)
        self.assertIn("$('session-start-text').textContent=startMessage", self.page)
        self.assertLess(self.page.index("id=session-start"), self.page.index("id=session-tabs"))

    def test_current_output_card_shows_trace_backed_output_speed(self):
        for marker in ("id=output-tps", "s.throughput||{}", "speedFmt(throughput.output_tps)",
                       "tool-free", "end-to-end", "reasoning and thinking output",
                       "external tool-result tokens"):
            self.assertIn(marker, self.page)

    def test_model_stats_is_a_first_class_top_level_route(self):
        for marker in ("id=tab-models", "id=view-models", "id=m-speed", "id=m-chart",
                       "id=m-table", "renderModelStats", "aggregateModelDays"):
            self.assertIn(marker, self.page)
        self.assertRegex(self.page, r"id=tab-models[^>]*>Models</button>")
        self.assertIn("$('tab-models').onclick=()=>setHashRoute('models')", self.page)
        self.assertIn("if(h==='models'||h==='frustration')", self.page)
        self.assertLess(self.page.index("id=tab-logs"), self.page.index("id=tab-models"))
        self.assertNotIn("Timing evidence", self.page)
        self.assertIn("Observed output pace is a secondary diagnostic.", self.page)
        self.assertIn("colspan=8", self.page)

    def test_model_stats_supports_multi_model_comparison(self):
        for marker in (
            "id=m-model-picker", "id=m-model-options", "id=m-model-summary",
            "tm_model_filters", "MODEL_COLORS", "buildModelTrend",
            "bars show output", "observed tok/s", "id=m-legend",
            "modelTipMetrics", "<small>input</small>", "<small>executions</small>",
            "Typical wait", "median wait", "human pause excluded",
            "modelWaitDistribution", "wait_durations_s", "p95_wait_s",
            "Matched pace", "renderMatchedPace", "modelRuntimeLabel",
            "migrateModelRuntimeFilters", "Typical workload", "median_peak_input_tokens",
            "95% CI", "select exactly two model runtimes", "TTFT unavailable",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn('id=m-model aria-label="Models filter"', self.page)
        self.assertNotIn("Speed change", self.page)

    def test_wait_time_is_first_class_across_current_logs_global_models_and_daily(self):
        for marker in (
            "data-chart=wait", "drawWaitChart", "id=g-wait", "data-gsort=wait",
            "id=lf-wait", "id=m-wait", "id=m-metric", "data-model-metric=wait",
            "id=d-wait", "id=d-trend-mode", "data-daily-trend=wait",
            "Prompt-to-completed-response", "lower is better",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("wait_time?.total_s", self.page)
        self.assertIn("CURRENT?.wait_time?.samples", self.page)

    def test_frustration_is_inside_models_with_global_settings(self):
        for marker in (
            "id=model-frustration", "id=f-utterances",
            "id=f-rate", "id=f-chart", "id=f-models", "id=f-chats",
            "id=f-chat-mode", "drawFrustrationTrend",
            "id=f-add-terms", "id=frustration-terms", "id=frustration-save",
            "/settings/frustration",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("if(h==='models'||h==='frustration')", self.page)
        self.assertIn("renderFrustration(LATEST.xsession?.frustration,LATEST.xsession?.sessions)", self.page)
        self.assertIn("$('f-add-terms').onclick=()=>{setHashRoute('settings')", self.page)
        self.assertIn("$('frustration-settings').scrollIntoView", self.page)
        self.assertIn("Add more terms", self.page)
        self.assertIn("Save and recalculate", self.page)
        self.assertNotIn("id=tab-frustration", self.page)
        self.assertNotIn("id=view-frustration", self.page)
        self.assertLess(self.page.index("id=view-models"), self.page.index("id=model-frustration"))
        self.assertLess(self.page.index("id=model-frustration"), self.page.index("id=view-daily"))
        self.assertNotIn("id=f-model-table", self.page)
        self.assertNotIn("id=f-session-table", self.page)

    def test_model_pricing_is_editable_and_supports_new_models_in_settings(self):
        for marker in (
            "id=model-pricing-settings", "id=model-pricing-rows",
            "id=model-price-add-form", "id=model-price-provider",
            "id=model-price-model", "id=model-price-input",
            "id=model-price-output", "id=model-price-cache-write",
            "id=model-price-cache-read", "data-model-price-save",
            "data-model-price-remove", "function renderModelPricing",
            "function postModelPrice", "/settings/model-pricing",
            "Save", "Use default", "Add model",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("USD per 1 million tokens", self.page)
        self.assertIn("recalculate current and historical cost estimates", self.page)
        self.assertLess(
            self.page.index("id=frustration-settings"),
            self.page.index("id=model-pricing-settings"),
        )
        self.assertNotIn("id=cursor-source-status", self.page)
        self.assertNotIn("Local trace source", self.page)
        self.assertNotIn("function renderCursorSourceStatus", self.page)
        self.assertIn("if(h==='settings'||h==='model-pricing')", self.page)
        self.assertIn("$('model-pricing-settings').scrollIntoView", self.page)

    def test_execution_overview_separates_activity_from_removable_optimization(self):
        for marker in ("id=ov-activity-tools", "id=ov-optional-use", "id=ov-unused-packs"):
            self.assertIn(marker, self.page)
        self.assertIn("renderCurrentOptimization(s)", self.page)
        self.assertNotIn("renderCapabilityUsage('ov-cap'", self.page)
        self.assertIn("Default tools and MCP servers are read-only evidence", self.page)

    def test_tools_and_skills_uses_removable_groups_and_review_filter(self):
        for marker in ("id=c-opt-enabled", "id=c-opt-used", "id=c-opt-review", "id=c-mcp-observed"):
            self.assertIn(marker, self.page)
        self.assertRegex(self.page, r"id=tab-capabilities[^>]*>Tools</button>")
        self.assertIn("data-cstate=review", self.page)
        self.assertIn("MCP servers remain read-only evidence", self.page)

    def test_capability_card_tooltips_are_not_clipped(self):
        self.assertIn(".capHero .pad{padding:13px 15px;overflow:visible}", self.page)
        self.assertIn(".capHero .card:hover,.capHero .card:focus-within{z-index:50}", self.page)
        self.assertIn(".capHero .fieldtip:after{bottom:auto;top:calc(100% + 8px);z-index:40}", self.page)

    def test_skill_pack_changes_confirm_exact_control_and_use_verified_state(self):
        self.assertIn("id=cap-dialog", self.page)
        self.assertIn("control_id:controlId", self.page)
        self.assertIn("result.capabilities||cap", self.page)
        self.assertIn("Setting verified.", self.page)
        self.assertIn("row.reviewable!==false", self.page)
        self.assertNotIn("...group,id:group.item_id", self.page)

    def test_global_daily_learn_and_settings_views_are_first_class_routes(self):
        for marker in ("id=tab-logs", "id=view-logs", "id=tab-daily", "id=view-daily", "id=tab-learn", "id=view-learn",
                       "id=tab-settings", "id=view-settings"):
            self.assertIn(marker, self.page)
        self.assertIn("data-global-panel=overview", self.page)
        self.assertIn("id=global-panel-overview", self.page)
        self.assertNotIn("data-global-panel=logs", self.page)
        self.assertNotIn("id=global-panel-logs", self.page)
        self.assertIn("const GLOBAL_PANEL_KEYS=['overview','insights','evidence'];", self.page)
        self.assertIn("h==='logs'||h==='global-logs'", self.page)
        self.assertIn("live · updated ${new Date(generatedAt*1000).toLocaleTimeString", self.page)
        self.assertLess(self.page.index("id=tab-daily"), self.page.index("id=tab-logs"))
        self.assertLess(self.page.index("id=tab-logs"), self.page.index("id=tab-global"))
        self.assertIn("id=d-day-select", self.page)
        self.assertIn("id=learn-glossary", self.page)
        self.assertIn("if(h==='daily')", self.page)
        self.assertIn("if(h==='learn')", self.page)
        self.assertIn("if(h==='settings'||h==='model-pricing')", self.page)
        self.assertIn("activeTop.scrollIntoView({block:'nearest',inline:'nearest'})", self.page)

    def test_top_navigation_and_command_palette_share_the_same_workflow_order(self):
        tab_ids = [
            "tab-session", "tab-daily", "tab-logs", "tab-global", "tab-models",
            "tab-capabilities", "tab-learn", "tab-settings",
        ]
        positions = [self.page.index(f"id={tab_id}") for tab_id in tab_ids]
        self.assertEqual(positions, sorted(positions))
        for marker in (
            "id=command-trigger", "id=command-palette", "id=command-search",
            "const NAV_COMMANDS=[", "directKey:'Digit1'", "directKey:'Digit8'",
            "key==='k'", "event.key==='ArrowDown'", "event.key==='Enter'",
            "aria-keyshortcuts=\"Meta+K Control+K\"",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("if(command.latest)goToLatestSession()", self.page)
        self.assertIn("else setHashRoute(command.route)", self.page)
        self.assertNotIn("shortcut:'⌥", self.page)
        self.assertNotIn("id=command-alt-key", self.page)
        self.assertNotIn("class=commandShortcut", self.page)

    def test_logs_support_app_project_and_time_range_filters(self):
        for marker in ("id=g-app", "id=g-project", "id=g-time", "App filter",
                       "Projects filter", "Time range filter", "id=g-active-filters",
                       "id=g-clear", "id=lf-cost", "id=lf-input", "id=lf-output", "id=lf-models"):
            self.assertIn(marker, self.page)
        for value in ("value=24h", "value=7d", "value=30d", "value=90d"):
            self.assertIn(value, self.page)
        self.assertIn("globalApp&&appFilterGroup(s)!==globalApp", self.page)
        self.assertIn("['claude','claude_code','claude_desktop'].includes(client)?'claude':client", self.page)
        self.assertIn("appFilterGroup(session)==='claude'?'Claude'", self.page)
        self.assertIn("['claude_code','claude_desktop'].includes(globalApp)", self.page)
        self.assertIn("globalProject&&(s.project||'No project')!==globalProject", self.page)
        self.assertIn("Date.now()/1000-rangeSeconds", self.page)
        self.assertIn("tm_global_app", self.page)
        self.assertIn("tm_global_project", self.page)
        self.assertIn("tm_global_time", self.page)
        self.assertIn("renderLogStats(sessions)", self.page)
        self.assertIn("session.model_stats", self.page)
        self.assertIn("globalSearch='';globalApp='';globalProject='';globalTime='all'", self.page)
        self.assertIn("['tm_global_search','tm_global_app','tm_global_project','tm_global_time']", self.page)
        self.assertIn("fetch('/logs',{cache:'no-store'})", self.page)
        self.assertIn("logSessionInventory?logSessionInventory:(xs.sessions||[])", self.page)

    def test_current_and_logs_share_the_defined_app_badge_helper(self):
        self.assertIn("const appBadgeClass=session=>", self.page)
        self.assertIn("'badge app '+appBadgeClass(s)", self.page)
        self.assertIn("${appBadgeClass(s)}", self.page)
        self.assertNotIn("providerBadgeClass", self.page)

    def test_session_delete_actions_require_confirmation_and_use_trash_endpoint(self):
        for marker in ("id=session-delete", "data-delete-session", "id=session-delete-dialog",
                       "id=session-delete-confirm", "Move to Trash", "/session/delete"):
            self.assertIn(marker, self.page)
        self.assertIn("openSessionDeleteDialog", self.page)
        self.assertIn("event.stopPropagation()", self.page)
        self.assertIn("X-Token-Meter-Action", self.page)
        self.assertIn("Provider metadata and configuration are not changed", self.page)

    def test_bulk_unused_action_has_confirmation_and_exact_control_ids(self):
        self.assertIn("id=c-disable-unused", self.page)
        self.assertIn("id=bulk-dialog", self.page)
        self.assertIn("/capability/disable-unused", self.page)
        self.assertIn("control_ids:groups.map(row=>row.id)", self.page)
        self.assertIn("MCP servers, runtime packs, built-ins, standalone skills, and used groups are excluded.", self.page)

    def test_agent_access_has_a_dedicated_settings_tab(self):
        for marker in ("id=agent-discovery", "id=agent-access", "id=agent-clients",
                       "id=agent-dialog", "/agent-access/status", "/agent-access/toggle"):
            self.assertIn(marker, self.page)
        for tool in ("mcp__tokenmeter__check", "mcp__tokenmeter__usage",
                     "mcp__tokenmeter__capabilities"):
            self.assertIn(tool, self.page)
        self.assertLess(self.page.index("id=view-capabilities"), self.page.index("id=view-settings"))
        self.assertLess(self.page.index("id=view-settings"), self.page.index("id=agent-access"))
        self.assertIn("setHashRoute('settings')", self.page)
        self.assertIn("After connecting Token Meter in Settings", self.page)
        self.assertIn("tm_agent_discovery_dismissed", self.page)

    def test_learn_has_user_question_starters(self):
        self.assertIn("Should I keep this run going?", self.page)
        self.assertIn("Why was the last phase expensive?", self.page)
        self.assertIn("What should I change before the next phase?", self.page)

    def test_model_comparison_has_tooltips_and_learn_guidance(self):
        for marker in (
            "modelHelp", "id=m-coverage", 'aria-label="Output timing coverage"',
            "same model can appear more than once",
            "A ratio above 1 favors the named faster runtime",
            "The median is primary because the average can be pulled upward",
            "id=learn-model-guide", "Compare model speed without fooling yourself",
            "Select two model runtimes", "Read matched pace first",
            "Check coverage and uncertainty", "Treat tok/s as diagnostic",
            "What matching cannot prove", "data-learn-route=models",
            "Confidence interval", "Match coverage", "Matched pace",
            "Model runtime", "Observed output pace", "Typical workload",
            "Typical wait", "semantic difficulty", "less than 30% coverage",
            "The 95% confidence interval crosses 1.00",
            "$('m-coverage').setAttribute('aria-valuenow'",
            "stripNativeFieldTipTitles", "removeAttribute('title')",
            "aria-description",
        ):
            self.assertIn(marker, self.page)
        self.assertIn('<div class=filterControl><span>Models</span>', self.page)
        self.assertIn('<label class=filterControl><span>History</span>', self.page)
        self.assertNotIn("Select model-runtime histories, not just model names.", self.page)
        self.assertNotIn("Limits tokens, executions, workload summaries", self.page)

    def test_custom_fieldtips_remove_delayed_native_tooltips_globally(self):
        self.assertIn("root.querySelectorAll('.fieldtip[title]')", self.page)
        self.assertIn("stripNativeFieldTipTitles();", self.page)
        self.assertIn('return `class=fieldtip tabindex=0 aria-description="${safe}" data-tip="${safe}"`', self.page)
        self.assertNotIn('return `class=fieldtip tabindex=0 title="${safe}"', self.page)

    def test_daily_omits_tool_result_health_and_uses_the_logs_open_path(self):
        self.assertNotIn("Tool-result health", self.page)
        self.assertNotIn("Tool results need attention", self.page)
        self.assertNotIn("function openDailySession", self.page)
        self.assertIn("button.onclick=()=>selectSession(button.dataset.dailySession)", self.page)
        self.assertIn("el.onclick=()=>selectSession(el.dataset.id)", self.page)

    def test_cursor_provenance_is_integrated_across_dashboard_views(self):
        for marker in (
            "const hasLocalEstimate", "const estimateSuffix", "reported-cost",
            "local token proxies are not comparable", "stroke-dasharray",
            "id=c-runtime-filter", "Cursor local estimate", "Input context proxy",
            "Visible-output estimate", "m.runtime||'unknown'",
        ):
            self.assertIn(marker, self.page)

    def test_handler_swallows_browser_disconnects(self):
        source = Path(meter.__file__).read_text()
        self.assertIn("except (BrokenPipeError, ConnectionResetError):", source)

    def test_dashboard_live_updates_do_not_hold_event_stream_connections(self):
        self.assertNotIn("new EventSource('/events')", self.page)
        self.assertIn("setInterval(refreshLiveState,2000)", self.page)
        self.assertIn("if(statePollBusy)return", self.page)
        source = Path(meter.__file__).read_text()
        events = source.index('elif req_path == "/events":')
        self.assertIn("self.send_response(204)", source[events:events + 700])

    def test_http_server_tolerates_browser_connection_bursts(self):
        self.assertTrue(meter.TokenMeterHTTPServer.daemon_threads)
        self.assertGreaterEqual(meter.TokenMeterHTTPServer.request_queue_size, 32)

    def test_dynamic_responses_cannot_be_reused_from_an_http_cache(self):
        handler = mock.Mock()
        meter.H._send(handler, "{}", "application/json")

        handler.send_header.assert_any_call("Cache-Control", "no-store, max-age=0")
        handler.send_header.assert_any_call("Pragma", "no-cache")
        handler.send_header.assert_any_call("Expires", "0")


class MenubarSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(meter.__file__).with_name("menubar").joinpath("TokenMeterMenuBar.swift").read_text()

    def test_daily_brief_action_opens_cross_session_daily_route(self):
        self.assertIn('addAction("Open Daily Brief", #selector(openDailyBrief))', self.source)
        self.assertIn('@objc private func openDailyBrief()', self.source)
        self.assertIn('openDashboardPanel("daily", includePinnedSession: false)', self.source)

    def test_model_prices_setting_opens_dashboard_pricing_editor(self):
        self.assertIn('NSMenuItem(title: "Model Prices", action: #selector(openModelPrices)', self.source)
        self.assertIn('@objc private func openModelPrices()', self.source)
        self.assertIn('openDashboardPanel("model-pricing", includePinnedSession: false)', self.source)

    def test_output_speed_is_a_dedicated_honest_metric_row(self):
        self.assertIn('addMetricRow("Output speed", snapshot.outputSpeedLabel', self.source)
        self.assertIn('addMetricRow("Model", snapshot.model)', self.source)
        self.assertIn('· \(outputSpeedLabel) · \(model)', self.source)
        self.assertIn('formatTokenRate(rate)', self.source)
        self.assertIn('Tool execution time may be included.', self.source)
        self.assertIn('print(snapshot.outputSpeedLabel)', self.source)

    def test_core_info_starts_with_amount_and_omits_operational_rows(self):
        self.assertIn('return "\\(costLabel) · \\(contextLabel) · \\(outputSpeedLabel) · \\(model)"', self.source)
        self.assertNotIn('return "\\(verdict.prefix) \\(formatMoney(totalCost))', self.source)
        self.assertNotIn('addActivityRow()', self.source)
        self.assertNotIn('addRecommendationRow()', self.source)
        self.assertNotIn('addMetricRow("Status"', self.source)
        self.assertNotIn('label("Now"', self.source)
        self.assertNotIn('label("Action"', self.source)

    def test_cursor_uses_provider_identity_and_estimated_usage_labels(self):
        self.assertIn('case "cursor": return "Cursor"', self.source)
        self.assertIn('case "cursor": return "cursorarrow"', self.source)
        self.assertIn('let costAvailable = metricAvailable(availability, "cost")', self.source)
        self.assertIn('let tokensAvailable = metricAvailable(availability, "tokens")', self.source)
        self.assertIn('let estimatedTokens = bool(source["token_estimate"])', self.source)
        self.assertIn('snapshot.estimatedTokens ? " est" : ""', self.source)
        self.assertIn('Cursor tokens are local context-and-visible-output proxies', self.source)

    def test_live_polling_bypasses_cached_menubar_responses(self):
        self.assertIn('cachePolicy: .reloadIgnoringLocalCacheData', self.source)
        self.assertIn('request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")', self.source)
        self.assertIn('request.setValue("no-cache", forHTTPHeaderField: "Pragma")', self.source)

    def test_provider_tabs_and_quota_cards_are_native_and_persisted(self):
        self.assertIn('enum MenuTab: String, CaseIterable', self.source)
        self.assertIn('case overview', self.source)
        self.assertIn('case claude', self.source)
        self.assertIn('case codex', self.source)
        self.assertIn('case cursor', self.source)
        self.assertIn('labels: tabs.map(\\.title)', self.source)
        self.assertIn('private func addOverviewMenu()', self.source)
        self.assertIn('private func addProviderMenu(_ providerID: String)', self.source)
        self.assertIn('private func addQuotaWindowRow(_ window: QuotaWindow, stale: Bool)', self.source)
        self.assertIn('tokenMeterDefaults.set(selectedTab.rawValue, forKey: selectedTabDefaultsKey)', self.source)
        self.assertIn('coverageNote: string(dict["coverage_note"]) ?? ""', self.source)
        self.assertIn('addSignalRow("Coverage", provider.coverageNote', self.source)
        self.assertIn("Only provider-reported limits are shown", self.source)
        self.assertIn('coverage=\\(coverage)', self.source)

    def test_limits_title_and_quota_notifications_have_safe_defaults(self):
        self.assertIn('enum StatusTitleMode: String', self.source)
        self.assertIn('private func limitsStatusTitle() -> String?', self.source)
        self.assertIn('return "\\(constrained.provider.label) \\(constrained.window.percentLabel) · \\(constrained.window.compactKind)"', self.source)
        self.assertIn('if tokenMeterDefaults.object(forKey: quotaAlertsEnabledDefaultsKey) == nil { return true }', self.source)
        self.assertIn('let thresholds = Array(Set([quotaAlertThreshold, 95, 100])).sorted()', self.source)
        self.assertIn('guard var previous = quotaNotificationStates[key] else', self.source)
        self.assertIn('quotaAlertsEnabled && quotaObservationEstablished', self.source)
        self.assertIn('quotaObservationEstablished = true', self.source)
        self.assertIn('process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")', self.source)
        self.assertIn('"--", title, body', self.source)


class ProviderQuotaTests(unittest.TestCase):
    def tearDown(self):
        meter.reset_provider_quota_cache()

    def test_pace_forecast_distinguishes_runout_reserve_and_early_window(self):
        now = 1_000_000.0
        base = {"window_seconds": 1000, "reset_at": now + 500}

        runout = meter.quota_pace({**base, "used_percent": 70}, now=now)
        reserve = meter.quota_pace({**base, "used_percent": 30}, now=now)
        on_pace = meter.quota_pace({**base, "used_percent": 50}, now=now)
        early = meter.quota_pace({"window_seconds": 1000, "reset_at": now + 980,
                                  "used_percent": 20}, now=now)

        self.assertEqual(runout["state"], "deficit")
        self.assertFalse(runout["will_last_to_reset"])
        self.assertIn("runs out in", runout["summary"])
        self.assertEqual(reserve["state"], "reserve")
        self.assertTrue(reserve["will_last_to_reset"])
        self.assertEqual(on_pace["state"], "on_pace")
        self.assertIsNone(early)

    def test_codex_parser_preserves_main_and_named_spark_windows(self):
        now = 1_000_000.0
        payload = {
            "rateLimits": {
                "planType": "business",
                "primary": {"usedPercent": 12, "windowDurationMins": 300,
                            "resetsAt": now + 1800},
                "secondary": {"usedPercent": 34, "windowDurationMins": 10080,
                              "resetsAt": now + 3600},
            },
            "rateLimitsByLimitId": {
                "codex-spark": {
                    "limitName": "GPT-5.3-Codex-Spark",
                    "primary": {"usedPercent": 56, "windowDurationMins": 300,
                                "resetsAt": now + 1800},
                    "secondary": {"usedPercent": 78, "windowDurationMins": 10080,
                                  "resetsAt": now + 3600},
                },
            },
        }

        result = meter.parse_codex_quota(payload, now=now)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["plan"], "business")
        self.assertEqual(
            [(row["label"], row["used_percent"]) for row in result["windows"]],
            [("Session", 12.0), ("Weekly", 34.0),
             ("Spark session", 56.0), ("Spark weekly", 78.0)],
        )
        self.assertEqual(result["coverage_note"], "")

    def test_codex_parser_does_not_fabricate_absent_main_windows(self):
        result = meter.parse_codex_quota({
            "rate_limit": {"primary_window": None, "secondary_window": None},
            "additional_rate_limits": [{
                "metered_feature": "codex_spark",
                "limit_name": "GPT-5.3-Codex-Spark",
                "rate_limit": {
                    "primary_window": {"used_percent": 4, "limit_window_seconds": 18000,
                                       "reset_at": 1_001_000},
                    "secondary_window": {"used_percent": 9, "limit_window_seconds": 604800,
                                         "reset_at": 1_002_000},
                },
            }],
        }, source="Codex OAuth API", now=1_000_000)

        self.assertEqual([row["label"] for row in result["windows"]],
                         ["Spark session", "Spark weekly"])
        self.assertIn("Regular Session and Weekly limits were not reported by Codex",
                      result["coverage_note"])
        self.assertIn("missing does not mean 0%", result["coverage_note"])

    def test_codex_app_server_uses_wrapper_safe_launchagent_environment(self):
        process = mock.Mock()
        process.stdin = mock.Mock()
        process.stdout = mock.Mock()
        process.poll.return_value = 0
        selector = mock.Mock()
        with mock.patch.object(meter, "provider_cli_path", return_value="/nvm/bin/codex"), \
                mock.patch.object(meter, "agent_client_environment", return_value={"PATH": "/nvm/bin"}) as environment, \
                mock.patch.object(meter.subprocess, "Popen", return_value=process) as popen, \
                mock.patch.object(meter.selectors, "DefaultSelector", return_value=selector), \
                mock.patch.object(meter, "_rpc_read_response", side_effect=[{}, {"rateLimits": {}}]):
            result = meter.codex_app_server_rate_limits()

        environment.assert_called_once_with("/nvm/bin/codex")
        self.assertEqual(popen.call_args.kwargs["env"], {"PATH": "/nvm/bin"})
        self.assertEqual(result, {"rateLimits": {}})

    def test_claude_parser_maps_reported_and_scoped_windows(self):
        now = 1_000_000.0
        result = meter.parse_claude_quota({
            "five_hour": {"utilization": 8, "resets_at": now + 3600},
            "seven_day": {"utilization": 23, "resets_at": now + 7200},
            "seven_day_opus": {"utilization": 41, "resets_at": now + 7200},
            "limits": [{
                "kind": "weekly_scoped", "is_active": True, "percent": 52,
                "resets_at": now + 7200,
                "scope": {"model": {"display_name": "Sonnet 4.5"}},
            }],
        }, credentials={"subscriptionType": "max"}, now=now)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["plan"], "max")
        self.assertEqual([row["label"] for row in result["windows"]],
                         ["Session", "Weekly", "Opus weekly", "Sonnet 4.5 weekly"])
        self.assertEqual(result["coverage_note"], "")

    def test_claude_scoped_weekly_does_not_hide_missing_regular_weekly_limit(self):
        result = meter.parse_claude_quota({
            "five_hour": {"utilization": 8, "resets_at": 1_001_000},
            "limits": [{
                "kind": "weekly_scoped", "percent": 52, "resets_at": 1_002_000,
                "scope": {"model": {"display_name": "Sonnet"}},
            }],
        }, now=1_000_000)

        self.assertEqual([row["label"] for row in result["windows"]],
                         ["Session", "Sonnet weekly"])
        self.assertIn("Weekly limit was not reported by Claude", result["coverage_note"])

    def test_third_party_claude_auth_is_explicitly_unavailable(self):
        with mock.patch.object(meter, "claude_auth_status", return_value={
            "loggedIn": True, "authMethod": "third_party", "apiProvider": "bedrock",
        }), mock.patch.object(meter, "claude_oauth_credentials") as credentials:
            result = meter.load_claude_quota(now=1_000_000)

        credentials.assert_not_called()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["plan"], "Bedrock")
        self.assertIn("not exposed", result["error"])
        self.assertIn("Session and Weekly limits were not reported by Claude",
                      result["coverage_note"])

    def test_cursor_parser_labels_monthly_individual_cap(self):
        now = 1_000_000.0
        result = meter.parse_cursor_quota({
            "membershipType": "enterprise",
            "billingCycleStart": now - 1000,
            "billingCycleEnd": now + 2000,
            "individualUsage": {
                "plan": {"used": 0, "limit": 0},
                "overall": {"used": 987, "limit": 100000},
            },
        }, now=now)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["windows"][0]["kind"], "monthly")
        self.assertEqual(result["windows"][0]["label"], "Plan")
        self.assertAlmostEqual(result["windows"][0]["used_percent"], 0.99, places=2)
        self.assertEqual(result["windows"][0]["window_seconds"], 3000)
        self.assertIn("Session and Weekly limits were not reported by Cursor",
                      result["coverage_note"])
        self.assertIn("missing does not mean 0%", result["coverage_note"])

    def test_cache_marks_old_provider_data_stale_and_recomputes_pace(self):
        row = meter.quota_provider("codex", "Codex", "ok", "test", windows=[
            meter.quota_window("codex", "codex-session", "session", "Session", 50,
                               window_seconds=1000, reset_at=1_001_000, now=1_000_000),
        ])
        with meter._quota_lock:
            meter._quota_cache["codex"] = {**row, "fetched_at": 1_000_000,
                                            "attempted_at": 1_000_000}

        result = meter.provider_quota_snapshots(
            now=1_000_601, loaders={"codex": mock.Mock()}, start_refresh=False,
        )[0]

        self.assertEqual(result["status"], "stale")
        self.assertTrue(result["stale"])
        self.assertEqual(result["age_seconds"], 601)

    def test_empty_cache_returns_loading_and_starts_only_one_worker(self):
        loader = mock.Mock()
        worker = mock.Mock()
        with mock.patch.object(meter.threading, "Thread", return_value=worker) as thread:
            first = meter.provider_quota_snapshots(now=1_000_000, loaders={"codex": loader})
            second = meter.provider_quota_snapshots(now=1_000_001, loaders={"codex": loader})

        self.assertEqual(first[0]["status"], "loading")
        self.assertEqual(second[0]["status"], "loading")
        thread.assert_called_once()
        worker.start.assert_called_once()

    def test_refresh_retains_last_good_windows_and_sanitizes_unexpected_errors(self):
        good = meter.quota_provider("codex", "Codex", "ok", "test", windows=[
            meter.quota_window("codex", "codex-session", "session", "Session", 44,
                               window_seconds=1000, reset_at=1_001_000, now=1_000_000),
        ])
        meter.refresh_provider_quota("codex", lambda now: good, now=1_000_000)

        def fail(now):
            raise RuntimeError("secret bearer token")

        result = meter.refresh_provider_quota("codex", fail, now=1_000_100)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["windows"][0]["used_percent"], 44.0)
        self.assertEqual(result["fetched_at"], 1_000_000)
        self.assertEqual(result["attempted_at"], 1_000_100)
        self.assertEqual(result["error"], "Provider quota refresh failed.")
        self.assertNotIn("secret", result["error"])


class MenubarSessionTests(unittest.TestCase):
    def test_recent_sessions_are_limited_and_keep_an_older_pin_visible(self):
        sources = [{
            "id": f"s{idx}", "provider": "codex" if idx % 2 else "claude",
            "label": "Codex" if idx % 2 else "Claude Code", "path": f"/tmp/s{idx}.jsonl",
            "session": f"s{idx}.jsonl", "project": f"/repo/project-{idx}", "mtime": idx,
            "title": f"Task {idx}",
        } for idx in range(7)]

        latest = meter.menubar_recent_sessions(sources)
        pinned = meter.menubar_recent_sessions(sources, selected_id="s0")

        self.assertEqual([row["id"] for row in latest], ["s6", "s5", "s4", "s3", "s2"])
        self.assertEqual([row["id"] for row in pinned], ["s0", "s6", "s5", "s4", "s3"])
        self.assertEqual(pinned[0]["name"], "Task 0")

    def test_menubar_state_uses_requested_session_and_marks_pin(self):
        sources = [{
            "id": "pinned", "provider": "codex", "label": "Codex", "path": "/tmp/pinned.jsonl",
            "session": "pinned.jsonl", "project": "/repo/pinned-project", "mtime": 1, "title": "Pinned task",
        }]
        state = {
            "provider": "codex", "source": sources[0], "session": "pinned.jsonl",
            "project": "/repo/pinned-project", "context": {}, "cache": {}, "trace": [], "insights": [],
            "executions": [{"idx": 1, "model": "gpt-5.6-sol"}],
            "throughput": {"available": True, "output_tps": 42.5, "basis": "end_to_end",
                           "sample_count": 2, "timing_coverage": 0.75},
        }
        with mock.patch.object(meter, "all_session_sources", return_value=sources), \
                mock.patch.object(meter, "recompute", return_value=state), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]):
            payload = meter.menubar_state("pinned")

        self.assertTrue(payload["selection"]["pinned"])
        self.assertFalse(payload["selection"]["missing"])
        self.assertEqual(payload["selection"]["selected_id"], "pinned")
        self.assertEqual(payload["recent_sessions"][0]["name"], "Pinned task")
        self.assertEqual(payload["model"], "gpt-5.6-sol")
        self.assertEqual(payload["provider_quotas"], [])
        self.assertEqual(payload["throughput"], {
            "available": True, "output_tps": 42.5, "basis": "end_to_end",
            "sample_count": 2, "timing_coverage": 0.75,
        })

    def test_cold_start_does_not_rebuild_all_history_for_each_menu_poll(self):
        with mock.patch.object(meter, "STATE", {}), \
                mock.patch.object(meter, "all_session_sources", return_value=[]), \
                mock.patch.object(meter, "current_state") as current, \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]):
            payload = meter.menubar_state()

        current.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["provider_quotas"], [])


class DynamicCatalogTests(unittest.TestCase):
    def test_flattens_namespace_and_legacy_functions(self):
        catalog = meter.normalize_dynamic_tools([
            {
                "type": "namespace",
                "name": "codex_app",
                "tools": [
                    {"name": "open_page", "deferLoading": False, "description": "Open a page"},
                    {"name": "create_thread", "deferLoading": True, "description": "Create a thread"},
                ],
            },
            {"name": "mcp__jira__search", "namespace": "jira", "deferLoading": True},
        ])
        self.assertEqual([row["name"] for row in catalog], ["open_page", "create_thread", "mcp__jira__search"])
        self.assertEqual(catalog[0]["namespace"], "codex_app")
        self.assertEqual(catalog[2]["namespace"], "jira")
        self.assertEqual(catalog[2]["kind"], "mcp")
        self.assertEqual(meter.catalog_counts(catalog), {"advertised": 3, "eager": 1, "deferred": 2})

    def test_embedded_image_bytes_are_not_counted_as_text(self):
        chars = meter.observable_output_chars({"image_url": "data:image/png;base64," + "A" * 10000})
        self.assertLess(chars, 100)


class ClaudeDesktopDiscoveryTests(unittest.TestCase):
    def test_default_discovery_scans_standard_and_third_party_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            standard = Path(tmp) / "Claude"
            third_party = Path(tmp) / "Claude-3p"
            standard_metadata = standard / "claude-code-sessions" / "account" / "workspace" / "local_standard.json"
            third_party_metadata = third_party / "local-agent-mode-sessions" / "account" / "workspace" / "local_bedrock.json"
            standard_metadata.parent.mkdir(parents=True)
            third_party_metadata.parent.mkdir(parents=True)
            standard_metadata.write_text(json.dumps({"cliSessionId": "standard-cli"}))
            third_party_metadata.write_text(json.dumps({"cliSessionId": "bedrock-cli"}))

            with mock.patch.object(meter, "CLAUDE_DESKTOP_DATA_ROOTS", [str(standard), str(third_party)]):
                paths = set(meter.claude_desktop_metadata_paths())

        self.assertEqual(paths, {str(standard_metadata), str(third_party_metadata)})

    def test_indexes_desktop_metadata_by_cli_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "account" / "workspace" / "local_desktop-session.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "sessionId": "local_desktop-session",
                "cliSessionId": "cli-session-id",
                "cwd": "/tmp/project",
                "title": "Desktop project task",
                "model": "claude-fable-5",
                "lastActivityAt": 123456,
            }))
            idx = meter.claude_desktop_index(tmp)
        self.assertIn("cli-session-id", idx)
        self.assertEqual(idx["cli-session-id"]["label"], "Claude Desktop")
        self.assertEqual(idx["cli-session-id"]["desktop_session_id"], "local_desktop-session")
        self.assertEqual(idx["cli-session-id"]["cwd"], "/tmp/project")
        self.assertEqual(idx["cli-session-id"]["title"], "Desktop project task")

    def test_discovers_no_project_agent_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "local-agent-mode-sessions" / "account" / "org"
            metadata = root / "local_agent-session.json"
            trace = root / "local_agent-session" / ".claude" / "projects" / "outputs" / "cli-agent-id.jsonl"
            trace.parent.mkdir(parents=True)
            metadata.write_text(json.dumps({
                "sessionId": "local_agent-session",
                "cliSessionId": "cli-agent-id",
                "cwd": str(root / "local_agent-session" / "outputs"),
                "title": "No-project task",
                "lastActivityAt": 123456,
            }))
            trace.write_text("{}\n")
            idx = meter.claude_desktop_index(tmp)
            sources = meter.claude_local_agent_sources(idx)
        self.assertEqual(idx["cli-agent-id"]["source_kind"], "agent")
        self.assertEqual(idx["cli-agent-id"]["project"], "No project")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["client"], "claude_desktop")
        self.assertEqual(sources[0]["title"], "No-project task")

    def test_agent_uses_user_selected_folder_as_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "local-agent-mode-sessions" / "account" / "org"
            metadata = root / "local_agent-session.json"
            metadata.parent.mkdir(parents=True)
            metadata.write_text(json.dumps({
                "sessionId": "local_agent-session",
                "cliSessionId": "cli-agent-id",
                "cwd": str(root / "local_agent-session" / "outputs"),
                "userSelectedFolders": ["/tmp/selected-project"],
                "lastActivityAt": 123456,
            }))
            idx = meter.claude_desktop_index(tmp)

        self.assertEqual(idx["cli-agent-id"]["cwd"], "/tmp/selected-project")
        self.assertEqual(idx["cli-agent-id"]["project"], "/tmp/selected-project")


class ToolEvidenceTests(unittest.TestCase):
    def test_tool_summary_reconciles_types_calls_and_per_execution_peak(self):
        executions = [
            {"idx": 1, "tools": [{"name": "exec", "namespace": "exec"}]},
            {"idx": 2, "tools": [
                {"name": "exec", "namespace": "exec"},
                {"name": "wait", "namespace": "wait", "skills": ["browser"]},
            ]},
        ]
        summary = meter.tool_summary(executions)
        self.assertEqual(summary["activity"]["observed_unique"], 2)
        self.assertEqual(summary["activity"]["total_calls"], 3)
        self.assertEqual(summary["activity"]["peak_calls_per_execution"], 2)
        self.assertEqual(summary["skills"], [{"name": "browser", "activations": 1}])
        self.assertFalse(summary["execution_rows_truncated"])

    def test_tool_summary_discloses_latest_execution_window(self):
        executions = [{"idx": idx, "tools": [{"name": "exec", "namespace": "exec"}]}
                      for idx in range(1, 82)]
        summary = meter.tool_summary(executions)
        self.assertEqual(summary["total_calls"], 81)
        self.assertEqual(summary["execution_rows_total"], 81)
        self.assertEqual(summary["execution_rows_shown"], 80)
        self.assertEqual(summary["execution_calls_shown"], 80)
        self.assertTrue(summary["execution_rows_truncated"])

    def test_flags_tokens_once_across_oversize_repeat_and_error_rules(self):
        calls = [
            {**meter.tool_identity("exec_command"), "output_tokens": 9000, "ts": 100,
             "args_fingerprint": "same", "error": False},
            {**meter.tool_identity("exec_command"), "output_tokens": 100, "ts": 110,
             "args_fingerprint": "same", "error": False},
            {**meter.tool_identity("mcp__jira__search"), "output_tokens": 50, "ts": 120,
             "args_fingerprint": "jira", "error": True},
        ]
        evidence = meter.summarize_tool_evidence(calls)
        self.assertEqual(evidence["total_output_tokens"], 9150)
        self.assertEqual(evidence["flagged_tokens"], 9150)
        self.assertEqual(evidence["oversized_calls"], 1)
        self.assertEqual(evidence["repeat_calls"], 1)
        self.assertEqual(evidence["errors"], 1)

    def test_global_aggregation_finds_reported_unused_mcp(self):
        catalog = [{
            "name": "mcp__salesforce__query", "namespace": "salesforce", "kind": "mcp",
            "defer_loading": False, "definition_tokens": 100,
        }]
        rows = []
        for idx in range(5):
            calls = []
            if idx == 0:
                calls = [{**meter.tool_identity("exec_command"), "output_tokens": 9000, "ts": 100,
                          "args_fingerprint": "one", "error": False}]
            rows.append({
                "id": f"s{idx}", "path": f"/tmp/s{idx}", "provider": "codex",
                "project": "/repo", "_tool_evidence": meter.summarize_tool_evidence(calls, catalog),
            })
        waste = meter.global_tool_waste(rows)
        self.assertEqual(waste["total_calls"], 1)
        self.assertEqual(waste["oversized_calls"], 1)
        candidate = next(row for row in waste["by_name"] if row["name"] == "mcp__salesforce__query")
        self.assertEqual(candidate["recommendation"], "disable")
        self.assertEqual(candidate["advertised_sessions"], 5)
        self.assertEqual(candidate["sessions_used"], 0)

    def test_tracks_eager_definition_tax_and_skill_trace_references(self):
        calls = [{**meter.tool_identity("exec_command"), "output_tokens": 10, "ts": 100,
                  "args_fingerprint": "x", "skills": ["execution-plan"]}]
        catalog = [
            {"name": "unused_eager", "namespace": "app", "kind": "tool",
             "defer_loading": False, "definition_tokens": 120},
            {"name": "later", "namespace": "app", "kind": "tool",
             "defer_loading": True, "definition_tokens": 80},
        ]
        evidence = meter.summarize_tool_evidence(calls, catalog)
        self.assertEqual(evidence["definition_tokens"], 200)
        self.assertEqual(evidence["unused_eager_definition_tokens"], 120)
        self.assertEqual(evidence["skills"][0]["name"], "execution-plan")

    def test_plain_tool_names_stay_separate_by_runtime(self):
        call = {**meter.tool_identity("read_file"), "output_tokens": 10, "ts": 100,
                "args_fingerprint": "x", "error": False}
        rows = [
            {"id": "codex", "provider": "codex", "runtime": "Codex", "project": "/repo",
             "_tool_evidence": meter.summarize_tool_evidence([call])},
            {"id": "cursor", "provider": "cursor", "runtime": "Cursor", "project": "/repo",
             "_tool_evidence": meter.summarize_tool_evidence([call])},
        ]
        waste = meter.global_tool_waste(rows)
        tools = [row for row in waste["inventory_tools"] if row["name"] == "read_file"]
        self.assertEqual({row["id"] for row in tools}, {"codex::read_file", "cursor::read_file"})
        self.assertEqual({row["runtime"] for row in tools}, {"Codex", "Cursor"})

    def test_skill_name_is_inferred_from_skill_descriptor_path(self):
        value = {"cmd": "sed -n '1,80p' /tmp/skills/execution-plan/SKILL.md"}
        self.assertEqual(meter.skill_names_from_value(value), ["execution-plan"])

    def test_token_meter_diagnostics_are_accounted_but_never_recommended_for_cleanup(self):
        calls = [{**meter.tool_identity("mcp__tokenmeter__check"), "output_tokens": 50000,
                  "ts": 100, "args_fingerprint": "one", "error": False}]
        rows = [{
            "id": "s1", "path": "/tmp/s1", "provider": "codex", "project": "/repo",
            "_tool_evidence": meter.summarize_tool_evidence(calls),
        }]
        waste = meter.global_tool_waste(rows)
        diagnostic = next(row for row in waste["by_name"] if row["namespace"] == "tokenmeter")
        self.assertTrue(diagnostic["diagnostic"])
        self.assertEqual(diagnostic["recommendation"], "keep")
        self.assertFalse(any("tokenmeter" in row.get("key", "") for row in waste["insights"]))


class AgentDataContractTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "id": "safe-session", "provider": "codex", "client": "codex", "label": "Codex",
            "path": "/private/logs/secret.jsonl", "session": "secret.jsonl",
            "project": "/Users/test/work/repository", "mtime": meter.time.time(),
        }
        self.state = {
            "provider": "codex", "source": self.source, "session": "secret.jsonl",
            "project": self.source["project"], "total_cost": 1.25, "cost_approx": True,
            "total_tokens": 120000, "turns": 4, "last_turn_cost": 0.21,
            "context": {"latest": 40000, "window": 200000, "latest_pct": 0.2},
            "tools": {"total_output_tokens": 9000, "flagged_tokens": 1000, "by_name": []},
            "executions": [{"idx": 4, "cost": 0.21, "tokens": {
                "input": 40000, "output": 900, "retrieval": 1000,
            }, "context_pct": 0.2}],
            "trace": [
                {"execution": 4, "kind": "user", "label": "User message", "detail": "private prompt"},
                {"execution": 4, "kind": "tool_call", "label": "search", "detail": "private args"},
                {"execution": 4, "kind": "tool_result", "label": "search", "detail": "private result", "tokens": 1000},
            ],
            "insights": [], "ended": False,
        }

    def test_check_matches_runtime_and_project_and_omits_content(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(execution=4, caller={
                "runtime": "codex", "project": "/Users/test/work/repository/subdir",
            })
        encoded = json.dumps(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_session"]["project"], "repository")
        self.assertEqual(result["data_scope"], "matched_current_run")
        self.assertNotIn("private prompt", encoded)
        self.assertNotIn("private args", encoded)
        self.assertNotIn("private result", encoded)
        self.assertNotIn("/private/logs", encoded)
        self.assertLessEqual(len(result["evidence"]), 3)
        self.assertLessEqual(len(result["execution"]["activity"]), 5)
        selected_tools = next(row for row in result["evidence"] if row["label"] == "Selected execution tool results")
        self.assertEqual(selected_tools["value"], 1000)

    def test_default_check_does_not_present_run_wide_tool_tokens_as_latest(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })
        latest_tools = next(row for row in result["evidence"] if row["label"] == "Latest execution tool results")
        self.assertEqual(latest_tools["value"], 1000)
        self.assertNotEqual(latest_tools["value"], self.state["tools"]["total_output_tokens"])

    def test_missing_context_percentage_is_not_described_as_zero(self):
        state = {"context": {"latest_pct": None}, "last_turn_cost": 0,
                 "insights": [], "executions": [], "ended": False}
        recommendation = meter.menubar_recommendation(state)
        verdict = meter.menubar_verdict(state, recommendation)
        self.assertIn("not reported", verdict["detail"])
        self.assertNotIn("Context is 0%", verdict["detail"])

    def test_check_refuses_to_fall_back_across_projects(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]):
            result = meter.agent_check(caller={"runtime": "codex", "project": "/another/repository"})
        self.assertFalse(result["ok"])
        self.assertIn("did not fall back", result["caveat"])

    def test_claude_trace_cwd_preserves_hyphenated_project_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text(json.dumps({
                "type": "user", "cwd": "/Users/test/Documents/github/token-meter",
            }) + "\n")
            cwd = meter.claude_trace_cwd(str(path))
        self.assertEqual(cwd, "/Users/test/Documents/github/token-meter")

    def test_current_run_resolution_prefers_exact_project_over_newer_parent(self):
        now = meter.time.time()
        exact = {**self.source, "id": "exact", "project": "/Users/test/work/repository", "mtime": now - 10}
        parent = {**self.source, "id": "parent", "project": "/Users/test/work", "mtime": now}
        selected, resolution = meter.resolve_agent_source(
            caller={"runtime": "codex", "project": "/Users/test/work/repository"},
            sources=[parent, exact],
        )
        self.assertEqual(selected["id"], "exact")
        self.assertEqual(resolution, "matched")

    def test_current_run_resolution_rejects_stale_implicit_match(self):
        stale = {**self.source, "mtime": meter.time.time() - meter.AGENT_CURRENT_MAX_AGE_S - 1}
        selected, resolution = meter.resolve_agent_source(
            caller={"runtime": "codex", "project": "/Users/test/work/repository"},
            sources=[stale],
        )
        self.assertIsNone(selected)
        self.assertIn("No recent Codex run", resolution)

    def test_usage_is_aggregate_only_and_ranked_categories_are_bounded(self):
        cross = {
            "daily": [{"day": time_day, "cost": 2.0, "sessions": 2,
                       "providers": [{"provider": "codex", "cost": 2.0}],
                       "tool_tokens": 1000, "flagged_tokens": 300}
                      for time_day in ("2026-07-01", "2026-06-30")],
            "sessions": [{"cost_approx": True, "title": "private title", "project": "/private/repo"}],
            "model_mix": [{"model": f"model-{idx}", "cost": idx, "tokens": idx * 100}
                          for idx in range(8)],
            "tool_waste": {"by_name": [
                {"name": f"tool-{idx}", "namespace": "safe", "output_tokens": idx * 100, "calls": idx}
                for idx in range(8)
            ]},
        }
        with mock.patch.object(meter, "cross_session", return_value=cross):
            result = meter.agent_usage(window="7d", focus="models")
        encoded = json.dumps(result)
        self.assertEqual(result["data_scope"], "anonymous_aggregate_history")
        self.assertLessEqual(len(result["categories"]), 5)
        self.assertNotIn("private title", encoded)
        self.assertNotIn("/private/repo", encoded)

    def test_capability_result_names_only_requested_evidence(self):
        cross = {"capabilities": {
            "summary": {"optional": {"enabled": 2, "unused": 1}},
            "control_groups": [
                {"name": "docs@personal", "control_type": "skill_pack", "runtime": "Codex",
                 "used": False, "enabled": True, "activations": 0,
                 "environment": {"TOKEN": "secret"}},
                {"name": "tokenmeter", "control_type": "skill_pack", "runtime": "Codex",
                 "used": False, "enabled": True},
            ],
        }}
        with mock.patch.object(meter, "cross_session", return_value=cross):
            result = meter.agent_capabilities(scope="all", limit=5)
        encoded = json.dumps(result)
        self.assertEqual([row["name"] for row in result["candidates"]], ["docs@personal"])
        self.assertNotIn("TOKEN", encoded)
        self.assertNotIn("secret", encoded)

    def test_agent_result_has_a_hard_serialized_bound(self):
        result = meter.bounded_agent_result({
            "answer": "a" * 10000, "evidence": [{"label": "x", "value": "y" * 10000}] * 10,
            "recommended_action": "z" * 10000, "caveat": "c" * 10000,
        })
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["evidence"]), 2)


class InstallationTests(unittest.TestCase):
    def test_user_installer_waits_for_both_supervised_runtime_jobs_and_returns_control(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "install").read_text()
        self.assertIn('"$INSTALL_ROOT/scripts/install-launch-agent"', script)
        self.assertIn('$HOME/Library/Application Support/Token Meter/runtime', script)
        self.assertIn('"state_ready": true', script)
        self.assertIn('launchctl print "gui/$UID/$SERVER_LABEL"', script)
        self.assertIn('launchctl print "gui/$UID/$MENUBAR_LABEL"', script)
        self.assertIn('"$INSTALL_ROOT/meter.py"', script)
        self.assertIn('"$INSTALL_ROOT/scripts/run-menubar"', script)
        self.assertIn("Token Meter installation complete.", script)
        self.assertNotRegex(script, r"(?m)^exec ")

    def test_launch_agents_supervise_server_and_menu_bar_independently(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "install-launch-agent").read_text()
        self.assertIn('SERVER_LABEL="com.token-meter.server"', script)
        self.assertIn('MENUBAR_LABEL="com.token-meter.menubar"', script)
        self.assertIn("<string>$SERVER_PROGRAM</string>", script)
        self.assertIn("<string>$MENUBAR_PROGRAM</string>", script)
        self.assertEqual(script.count("<key>KeepAlive</key>"), 2)
        self.assertNotIn("start-token-meter", script)
        for label in ("SERVER_LABEL", "MENUBAR_LABEL"):
            self.assertIn(f'launchctl bootout "gui/$UID/${label}"', script)

    def test_uninstaller_removes_both_supervised_jobs(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "uninstall-launch-agent").read_text()
        self.assertIn('launchctl bootout "gui/$UID/$SERVER_LABEL"', script)
        self.assertIn('launchctl bootout "gui/$UID/$MENUBAR_LABEL"', script)
        self.assertIn('rm -f "$MENUBAR_PLIST" "$SERVER_PLIST"', script)


class AgentAccessTests(unittest.TestCase):
    def test_client_environment_prepends_wrapper_runtime_directory(self):
        with mock.patch.dict(meter.os.environ, {"PATH": "/usr/bin:/bin"}, clear=False):
            env = meter.agent_client_environment("/Users/test/.nvm/versions/node/v24/bin/codex")
        self.assertEqual(env["PATH"].split(":" )[0], "/Users/test/.nvm/versions/node/v24/bin")
        self.assertIn("/usr/bin", env["PATH"].split(":"))

    def test_client_environment_keeps_symlink_directory_for_sibling_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrapper_dir = Path(tmp) / "node" / "bin"
            target_dir = Path(tmp) / "package" / "bin"
            wrapper_dir.mkdir(parents=True)
            target_dir.mkdir(parents=True)
            target = target_dir / "codex.js"
            target.write_text("#!/usr/bin/env node\n")
            wrapper = wrapper_dir / "codex"
            wrapper.symlink_to(target)
            env = meter.agent_client_environment(str(wrapper))
        self.assertEqual(env["PATH"].split(":")[0], str(wrapper_dir))

    def test_client_discovery_checks_user_bin_when_service_path_is_minimal(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / ".local" / "bin" / "claude"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o755)
            with mock.patch.object(meter.os.path, "expanduser",
                                   side_effect=lambda value: tmp if value == "~" else value):
                found = meter.agent_client_executable("claude", which=lambda name: None)
        self.assertEqual(found, str(binary))

    def test_connection_commands_use_fixed_vectors_and_user_scope(self):
        launcher = "/Applications/Token Meter/bin/token-meter-mcp"
        codex = meter.agent_access_command("codex", True, launcher=launcher, cli_path="/usr/bin/codex")
        claude = meter.agent_access_command("claude", True, launcher=launcher, cli_path="/usr/bin/claude")
        self.assertEqual(codex, ["/usr/bin/codex", "mcp", "add", "--env",
                                "TOKEN_METER_CALLER=codex", "tokenmeter", "--", launcher])
        self.assertEqual(claude, ["/usr/bin/claude", "mcp", "add", "--transport", "stdio",
                                 "--scope", "user", "tokenmeter", "--env",
                                 "TOKEN_METER_CALLER=claude", "--", launcher])

    def test_codex_status_requires_exact_launcher_and_caller_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "token-meter-mcp"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)

            class Completed:
                returncode = 0
                stdout = json.dumps({"enabled": True, "transport": {
                    "type": "stdio", "command": str(launcher), "args": [],
                    "env": {"TOKEN_METER_CALLER": "codex"},
                }})
                stderr = ""

            status = meter.agent_access_client_status(
                "codex", launcher=str(launcher), runner=lambda *a, **k: Completed(),
                which=lambda name: f"/usr/bin/{name}")
        self.assertTrue(status["connected"])
        self.assertFalse(status["conflict"])
        self.assertNotIn("/usr/bin/codex", status["connect_command"])

    def test_claude_status_reads_only_the_user_scope_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "token-meter-mcp"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)
            config = Path(tmp) / ".claude.json"
            config.write_text(json.dumps({"mcpServers": {"tokenmeter": {
                "type": "stdio", "command": str(launcher), "args": [],
                "env": {"TOKEN_METER_CALLER": "claude"},
            }}}))
            status = meter.agent_access_client_status(
                "claude", launcher=str(launcher), claude_config_path=str(config),
                which=lambda name: f"/usr/bin/{name}")
        self.assertTrue(status["connected"])

    def test_different_existing_entry_is_reported_as_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "token-meter-mcp"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)
            config = Path(tmp) / ".claude.json"
            config.write_text(json.dumps({"mcpServers": {"tokenmeter": {
                "type": "stdio", "command": "/tmp/different", "args": [], "env": {},
            }}}))
            status = meter.agent_access_client_status(
                "claude", launcher=str(launcher), claude_config_path=str(config),
                which=lambda name: f"/usr/bin/{name}")
        self.assertFalse(status["connected"])
        self.assertTrue(status["conflict"])

    def test_connection_change_is_verified_after_cli_success(self):
        before = {"label": "Codex", "detected": True, "available": True,
                  "configured": False, "connected": False, "conflict": False}
        after = {**before, "configured": True, "connected": True}
        states = iter((before, after))
        observed = {}

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        def runner(argv, **kwargs):
            observed["argv"] = argv
            observed["kwargs"] = kwargs
            return Completed()

        with mock.patch.object(meter, "agent_access_launcher", return_value="/tmp/token-meter-mcp"):
            result = meter.set_agent_access("codex", True, runner=runner,
                                            status_getter=lambda client: next(states))
        self.assertTrue(result["ok"])
        self.assertTrue(result["restart_required"])
        self.assertEqual(observed["argv"][-3:], ["tokenmeter", "--", "/tmp/token-meter-mcp"])
        self.assertNotIn("shell", observed["kwargs"])

    def test_connection_failure_includes_bounded_cli_reason(self):
        before = {"label": "Codex", "detected": True, "available": True,
                  "configured": False, "connected": False, "conflict": False}

        class Completed:
            returncode = 127
            stdout = ""
            stderr = "env: node: No such file or directory\n"

        with mock.patch.object(meter, "agent_access_launcher", return_value="/tmp/token-meter-mcp"), \
                mock.patch.object(meter, "agent_client_executable", return_value="/tmp/codex"):
            result = meter.set_agent_access("codex", True, runner=lambda *a, **k: Completed(),
                                            status_getter=lambda client: before)
        self.assertFalse(result["ok"])
        self.assertIn("env: node", result["error"])


class CapabilityConfigTests(unittest.TestCase):
    def test_skill_identity_separates_runtime_origin_and_plugin(self):
        identities = {
            meter.skill_identity("Codex", "browser", "codex:built-in"),
            meter.skill_identity("Codex", "browser", "codex:user"),
            meter.skill_identity("Codex", "browser", "codex:plugin:openai-bundled", "browser@openai-bundled"),
            meter.skill_identity("Claude", "browser", "claude:plugin:skills-marketplace", "browser@skills-marketplace"),
        }
        self.assertEqual(len(identities), 4)

    def test_optional_summary_counts_only_mutable_skill_packs(self):
        mcp_items = [
            {"name": "context7", "mutable": True, "enabled": True, "used": False,
             "codex_enabled": True, "claude_enabled": False},
            {"name": "core", "mutable": False, "enabled": True, "used": False},
        ]
        skill_items = [
            {"name": "browser", "runtime": "Codex", "plugin_id": "browser@bundled",
             "mutable": True, "enabled": True, "used": True, "activations": 2},
            {"name": "local", "runtime": "Codex", "plugin_id": "",
             "mutable": False, "enabled": True, "used": False},
        ]
        groups = meter.capability_control_groups(mcp_items, skill_items)
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["used"], 1)
        self.assertEqual(summary["unused"], 0)
        self.assertEqual(summary["mcp_enabled"], 0)
        self.assertEqual(summary["skill_packs_enabled"], 1)

    def test_runtime_skill_packs_are_not_review_candidates(self):
        skill_items = [
            {"id": "runtime-skill", "name": "browser", "runtime": "Codex",
             "plugin_id": "browser@openai-bundled", "mutable": True, "enabled": True,
             "used": False, "reviewable": False, "origin": "runtime_pack"},
            {"id": "user-skill", "name": "custom", "runtime": "Codex",
             "plugin_id": "custom@personal", "mutable": True, "enabled": True,
             "used": False, "reviewable": True, "origin": "user_plugin"},
        ]
        groups = meter.capability_control_groups([], skill_items)
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["review_candidates"], ["skill_pack:Codex:custom@personal"])

    def test_bulk_disable_accepts_only_exact_review_candidates(self):
        capabilities = {
            "summary": {"optional": {"review_candidates": ["skill_pack:Claude:custom@personal"]}},
            "control_groups": [
                {"id": "skill_pack:Claude:custom@personal", "name": "custom@personal",
                 "control_type": "skill_pack", "enabled": True, "used": False,
                 "mutable": True, "reviewable": True},
                {"id": "skill_pack:Codex:browser@openai-bundled", "name": "browser@openai-bundled",
                 "control_type": "skill_pack", "enabled": True, "used": False,
                 "mutable": True, "reviewable": False},
            ],
        }
        changed = []

        def fake_setter(control, enabled):
            changed.append((control["id"], enabled))
            return {"ok": True, "verified": True}

        result = meter.disable_capability_controls(
            ["skill_pack:Claude:custom@personal"], capabilities, fake_setter)
        rejected = meter.disable_capability_controls(
            ["skill_pack:Codex:browser@openai-bundled"], capabilities, fake_setter)
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed"], 1)
        self.assertEqual(changed, [("skill_pack:Claude:custom@personal", False)])
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["invalid_control_ids"], ["skill_pack:Codex:browser@openai-bundled"])

    def test_session_optional_summary_excludes_mcp_and_default_activity(self):
        capabilities = {"control_groups": [
            {"id": "skill_pack:Codex:browser@bundled", "control_type": "skill_pack",
             "name": "browser@bundled", "runtime": "Codex", "enabled": True,
             "mutable": True, "members": ["browser"]},
        ], "summary": {"optional": {"review_candidate_names": []}}}
        state = {"provider": "codex", "tools": {
            "by_name": [
                {"name": "exec", "namespace": "exec", "kind": "tool", "calls": 3},
                {"name": "mcp__context7__query", "namespace": "context7", "kind": "mcp", "calls": 1},
            ],
            "skills": [{"name": "browser", "activations": 1}],
        }}
        summary = meter.session_optional_capabilities(state, capabilities)
        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["used"], 1)
        self.assertEqual(summary["mcp_enabled"], 0)
        self.assertEqual(summary["avoidable_eager_definition_tokens"], 0)
        self.assertNotIn("context7", [row["name"] for row in summary["groups"]])

    def test_mcp_parser_ignores_nested_env_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[mcp_servers.node_repl]\nenabled = false\n[mcp_servers.node_repl.env]\nTOKEN = 'x'\n")
            rows = meter.toml_named_sections(str(path), "mcp_servers")
        self.assertEqual(list(rows), ["node_repl"])

    def test_codex_plugin_toggle_changes_only_enabled_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('[plugins."docs@runtime"]\nenabled = true\nsource = "keep"\n[features]\nskills = true\n')
            with mock.patch.object(meter, "CODEX_CONFIG", str(path)):
                result = meter.set_codex_plugin_enabled("docs@runtime", False)
            text = path.read_text()
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertIn("enabled = false", text)
        self.assertIn('source = "keep"', text)

    def test_claude_plugin_toggle_verifies_written_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"enabledPlugins": {"docs@personal": True}}))
            with (mock.patch.object(meter, "CLAUDE_SETTINGS", str(path)),
                  mock.patch.object(meter, "claude_plugin_installations",
                                    return_value={"docs@personal": {"installPath": tmp}})):
                result = meter.set_claude_plugin_enabled("docs@personal", False)
            written = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertFalse(written["enabledPlugins"]["docs@personal"])


class DailySummaryTests(unittest.TestCase):
    def test_aggregates_daily_spend_providers_and_logs(self):
        sessions = [
            {"id": "one", "title": "One", "project": "/repo/a", "provider": "codex",
             "label": "Codex", "_day_cost": {"2026-07-01": 1.25},
             "_wait_samples": [
                 {"day": "2026-07-01", "duration_s": 20},
                 {"day": "2026-07-01", "duration_s": 40},
             ]},
            {"id": "two", "title": "Two", "project": "/repo/b", "provider": "claude",
             "label": "Claude Code", "_day_cost": {"2026-07-01": 0.75, "2026-06-30": 0.5},
             "_wait_samples": [{"day": "2026-07-01", "duration_s": 30}]},
        ]
        days = meter.daily_summaries(sessions)
        self.assertEqual(days[0]["day"], "2026-07-01")
        self.assertEqual(days[0]["cost"], 2.0)
        self.assertEqual(days[0]["sessions"], 2)
        self.assertEqual(days[0]["projects"], 2)
        self.assertNotIn("tool_tokens", days[0])
        self.assertNotIn("flagged_tokens", days[0])
        provider = days[0]["providers"][0]
        self.assertEqual(provider["provider"], "codex")
        self.assertEqual(provider["cost"], 1.25)
        self.assertEqual(provider["wait_s"], 60.0)
        self.assertEqual(provider["wait_samples"], 2)
        self.assertEqual(provider["usage_basis"], "reported")
        self.assertEqual(days[0]["wait_time"]["total_s"], 90)
        self.assertEqual(days[0]["wait_time"]["avg_s"], 30)
        self.assertEqual(days[0]["wait_time"]["max_s"], 40)


if __name__ == "__main__":
    unittest.main()
