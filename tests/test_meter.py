import unittest
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import meter


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
        self.assertEqual(summary["basis"], "tool_free")
        self.assertEqual(summary["output_tps"], 5)

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
        }]
        result = meter.aggregate_model_stats(sessions)
        row = result["models"][0]
        self.assertEqual(row["output_tps"], 7.5)
        self.assertEqual(row["timing_coverage"], 1)
        self.assertEqual(row["daily"][0]["input_tokens"], 300)
        self.assertEqual(row["daily"][0]["throughput_samples"], 2)

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
        self.assertIn("if(h==='models')", self.page)
        self.assertLess(self.page.index("id=tab-logs"), self.page.index("id=tab-models"))
        self.assertLess(self.page.index("id=tab-models"), self.page.index("id=tab-frustration"))
        self.assertNotIn("Timing evidence", self.page)
        self.assertIn("Hover a value for timing basis, samples, and coverage.", self.page)
        self.assertIn("colspan=7", self.page)

    def test_frustration_is_a_first_class_route_with_global_settings(self):
        for marker in (
            "id=tab-frustration", "id=view-frustration", "id=f-utterances",
            "id=f-rate", "id=f-chart", "id=f-models", "id=f-chats",
            "id=f-chat-mode", "drawFrustrationTrend",
            "id=f-add-terms", "id=frustration-terms", "id=frustration-save",
            "/settings/frustration",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("if(h==='frustration')", self.page)
        self.assertIn("renderFrustration(LATEST.xsession?.frustration,LATEST.xsession?.sessions)", self.page)
        self.assertIn("$('f-add-terms').onclick=()=>{setHashRoute('settings')", self.page)
        self.assertIn("$('frustration-settings').scrollIntoView", self.page)
        self.assertIn("Add more terms", self.page)
        self.assertIn("Save and recalculate", self.page)
        self.assertLess(self.page.index("id=tab-models"), self.page.index("id=tab-frustration"))
        self.assertLess(self.page.index("id=tab-frustration"), self.page.index("id=tab-capabilities"))
        self.assertNotIn("id=f-model-table", self.page)
        self.assertNotIn("id=f-session-table", self.page)

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
        self.assertIn("if(h==='settings')", self.page)
        self.assertIn("activeTop.scrollIntoView({block:'nearest',inline:'nearest'})", self.page)

    def test_top_navigation_and_command_palette_share_the_same_workflow_order(self):
        tab_ids = [
            "tab-session", "tab-daily", "tab-logs", "tab-global", "tab-models",
            "tab-frustration", "tab-capabilities", "tab-learn", "tab-settings",
        ]
        positions = [self.page.index(f"id={tab_id}") for tab_id in tab_ids]
        self.assertEqual(positions, sorted(positions))
        for marker in (
            "id=command-trigger", "id=command-palette", "id=command-search",
            "const NAV_COMMANDS=[", "directKey:'Digit1'", "directKey:'Digit9'",
            "key==='k'", "event.key==='ArrowDown'", "event.key==='Enter'",
            "aria-keyshortcuts=\"Meta+K Control+K\"",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("if(command.latest)goToLatestSession()", self.page)
        self.assertIn("else setHashRoute(command.route)", self.page)

    def test_logs_support_project_and_time_range_filters(self):
        for marker in ("id=g-project", "id=g-time", "Projects filter", "Time range filter",
                       "id=g-clear", "id=lf-cost", "id=lf-input", "id=lf-output", "id=lf-models"):
            self.assertIn(marker, self.page)
        for value in ("value=24h", "value=7d", "value=30d", "value=90d"):
            self.assertIn(value, self.page)
        self.assertIn("globalProject&&(s.project||'No project')!==globalProject", self.page)
        self.assertIn("Date.now()/1000-rangeSeconds", self.page)
        self.assertIn("tm_global_project", self.page)
        self.assertIn("tm_global_time", self.page)
        self.assertIn("renderLogStats(sessions)", self.page)
        self.assertIn("session.model_stats", self.page)
        self.assertIn("globalSearch='';globalProject='';globalTime='all'", self.page)
        self.assertIn("['tm_global_search','tm_global_project','tm_global_time']", self.page)

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

    def test_daily_omits_tool_result_health_and_uses_the_logs_open_path(self):
        self.assertNotIn("Tool-result health", self.page)
        self.assertNotIn("Tool results need attention", self.page)
        self.assertNotIn("function openDailySession", self.page)
        self.assertIn("button.onclick=()=>selectSession(button.dataset.dailySession)", self.page)
        self.assertIn("el.onclick=()=>selectSession(el.dataset.id)", self.page)

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


class MenubarSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(meter.__file__).with_name("menubar").joinpath("TokenMeterMenuBar.swift").read_text()

    def test_daily_brief_action_opens_cross_session_daily_route(self):
        self.assertIn('addAction("Open Daily Brief", #selector(openDailyBrief))', self.source)
        self.assertIn('@objc private func openDailyBrief()', self.source)
        self.assertIn('openDashboardPanel("daily", includePinnedSession: false)', self.source)

    def test_output_speed_is_a_dedicated_honest_metric_row(self):
        self.assertIn('addMetricRow("Output speed", snapshot.outputSpeedLabel', self.source)
        self.assertIn('addMetricRow("Model", snapshot.model)', self.source)
        self.assertIn('· \(outputSpeedLabel) · \(model)', self.source)
        self.assertIn('formatTokenRate(rate)', self.source)
        self.assertIn('Tool execution time may be included.', self.source)
        self.assertIn('print(snapshot.outputSpeedLabel)', self.source)

    def test_core_info_starts_with_amount_and_omits_operational_rows(self):
        self.assertIn('return "\\(formatMoney(totalCost)) · \\(contextLabel) · \\(outputSpeedLabel) · \\(model)"', self.source)
        self.assertNotIn('return "\\(verdict.prefix) \\(formatMoney(totalCost))', self.source)
        self.assertNotIn('addActivityRow()', self.source)
        self.assertNotIn('addRecommendationRow()', self.source)
        self.assertNotIn('addMetricRow("Status"', self.source)
        self.assertNotIn('label("Now"', self.source)
        self.assertNotIn('label("Action"', self.source)


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
                mock.patch.object(meter, "recompute", return_value=state):
            payload = meter.menubar_state("pinned")

        self.assertTrue(payload["selection"]["pinned"])
        self.assertFalse(payload["selection"]["missing"])
        self.assertEqual(payload["selection"]["selected_id"], "pinned")
        self.assertEqual(payload["recent_sessions"][0]["name"], "Pinned task")
        self.assertEqual(payload["model"], "gpt-5.6-sol")
        self.assertEqual(payload["throughput"], {
            "available": True, "output_tps": 42.5, "basis": "end_to_end",
            "sample_count": 2, "timing_coverage": 0.75,
        })


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


class PackagingTests(unittest.TestCase):
    def test_postinstall_waits_for_initialized_dashboard_after_launch(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "packaging" / "scripts" / "postinstall").read_text()
        kickstart = script.rindex('launchctl kickstart -k "gui/$USER_UID/$LABEL"')
        readiness_check = script.rindex("if ! wait_for_dashboard; then")
        self.assertLess(kickstart, readiness_check)
        self.assertIn("state_ready", script)


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


class EdgeTelemetryTests(unittest.TestCase):
    def _sample_objs(self):
        return [
            {"type": "user", "timestamp": "2026-07-09T00:00:00.000Z",
             "gitBranch": "main", "entrypoint": "cli", "version": "2.1.196",
             "message": {"content": "fix the parser"}},
            {"type": "assistant", "timestamp": "2026-07-09T00:00:01.000Z",
             "message": {
                 "id": "msg-1", "model": "claude-sonnet-4-6",
                 "stop_reason": "tool_use",
                 "content": [{"type": "tool_use", "id": "tu-1", "name": "Edit",
                              "input": {"file_path": "/repo/a.py"}}],
                 "usage": {
                     "input_tokens": 10, "output_tokens": 5,
                     "cache_creation_input_tokens": 4, "cache_read_input_tokens": 6,
                     "service_tier": "standard", "speed": "standard",
                     "cache_creation": {"ephemeral_5m_input_tokens": 4, "ephemeral_1h_input_tokens": 0},
                     "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 0},
                 },
             }},
            {"type": "user", "timestamp": "2026-07-09T00:00:30.000Z",
             "gitBranch": "feature/edge", "entrypoint": "vscode", "version": "2.1.196",
             "message": {"content": "fix the parser"}},
            {"type": "assistant", "timestamp": "2026-07-09T00:00:31.000Z",
             "message": {
                 "id": "msg-2", "model": "claude-sonnet-4-6",
                 "stop_reason": "end_turn",
                 "content": [{"type": "text", "text": "done"}],
                 "usage": {"input_tokens": 8, "output_tokens": 12},
             }},
        ]

    def test_extracts_session_usage_and_stop_reason_fields(self):
        msgs = meter.iter_claude_messages(self._sample_objs())
        edge = meter.claude_edge_telemetry(self._sample_objs(), msgs, 45, 1)
        self.assertEqual(edge["session"]["git_branch"], "feature/edge")
        self.assertEqual(edge["session"]["entrypoint"], "vscode")
        self.assertEqual(edge["session"]["version"], "2.1.196")
        self.assertEqual(sorted(edge["session"]["branches_seen"]), ["feature/edge", "main"])
        self.assertEqual(edge["usage"]["cache_ephemeral_5m"], 4)
        self.assertEqual(edge["usage"]["web_search_requests"], 1)
        self.assertEqual(edge["stop_reasons"]["end_turn"], 1)
        self.assertEqual(edge["stop_reasons"]["tool_use"], 1)
        self.assertAlmostEqual(edge["stop_reasons"]["completion_rate"], 0.5)
        self.assertEqual(edge["edited_files"], ["/repo/a.py"])
        self.assertEqual(edge["derived"]["files_edited"], 1)
        self.assertEqual(edge["derived"]["tokens_per_tool_call"], 45)
        self.assertEqual(edge["derived"]["human_turns"], 2)

    def test_repeat_queries_use_history_counts(self):
        objs = [
            {"type": "user", "message": {"content": "repeat me"}},
            {"type": "assistant", "message": {
                "id": "msg-1", "model": "claude-sonnet-4-6", "content": [],
                "usage": {"input_tokens": 1, "output_tokens": 1}, "stop_reason": "end_turn",
            }},
        ]
        history = {"repeat-me-fp": 2}
        with mock.patch.object(meter, "query_fingerprint", return_value="repeat-me-fp"):
            repeats = meter.claude_repeat_queries(objs, history_counts=history)
        self.assertEqual(repeats["repeat_queries"], 1)

    def test_recompute_claude_includes_edge_telemetry(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            for obj in self._sample_objs():
                fh.write(json.dumps(obj) + "\n")
            path = fh.name
        try:
            source = meter.source_from_path(path)
            state = meter.recompute(source)
            self.assertIn("edge_telemetry", state)
            self.assertEqual(state["edge_telemetry"]["derived"]["files_edited"], 1)
            self.assertEqual(state["executions"][0]["stop_reason"], "tool_use")
        finally:
            os.unlink(path)


class DailySummaryTests(unittest.TestCase):
    def test_aggregates_daily_spend_providers_and_logs(self):
        sessions = [
            {"id": "one", "title": "One", "project": "/repo/a", "provider": "codex",
             "label": "Codex", "_day_cost": {"2026-07-01": 1.25}},
            {"id": "two", "title": "Two", "project": "/repo/b", "provider": "claude",
             "label": "Claude Code", "_day_cost": {"2026-07-01": 0.75, "2026-06-30": 0.5}},
        ]
        days = meter.daily_summaries(sessions)
        self.assertEqual(days[0]["day"], "2026-07-01")
        self.assertEqual(days[0]["cost"], 2.0)
        self.assertEqual(days[0]["sessions"], 2)
        self.assertEqual(days[0]["projects"], 2)
        self.assertNotIn("tool_tokens", days[0])
        self.assertNotIn("flagged_tokens", days[0])
        self.assertEqual(days[0]["providers"][0], {"provider": "codex", "cost": 1.25})


if __name__ == "__main__":
    unittest.main()
