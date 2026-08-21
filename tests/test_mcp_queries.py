import json
import unittest


def synthetic_source_and_state(secret="SENTINEL-PRIVATE", session_id="session-1"):
    source = {
        "id": session_id,
        "provider": "codex",
        "client": "codex_cli",
        "model": "gpt-5.6",
        "model_provider": "openai",
        "path": "/private/{}/trace.jsonl".format(secret),
        "project": "/private/{}".format(secret),
        "title": secret,
        "label": secret,
        "mtime": 1_787_254_100.0,
    }
    state = {
        "provider": "codex",
        "client": "codex_cli",
        "primary_model": "gpt-5.6",
        "total_tokens": 170,
        "total_cost": 0.12,
        "cost_approx": True,
        "tokens": {
            "input": 150,
            "output": 20,
            "cache_read": 50,
            "cache_write": 0,
        },
        "timing": {
            "start_ts": 1_787_254_000.0,
            "end_ts": 1_787_254_050.0,
            "duration_s": 42,
            "duration_available": True,
        },
        "availability": {
            "tokens": True,
            "input_tokens": True,
            "output_tokens": True,
            "cost": True,
            "cache": True,
            "context": True,
            "timing": True,
            "tool_results": True,
        },
        "source": {
            "id": session_id,
            "provider": "codex",
            "client": "codex_cli",
            "model_provider": "openai",
            "path": source["path"],
            "project": source["project"],
            "title": secret,
        },
        "executions": [{
            "idx": 1,
            "ts": 1_787_254_010.0,
            "model": "gpt-5.6",
            "cost": 0.12,
            "duration_ms": 42_000,
            "wait_s": 50.0,
            "ttft_s": 1.5,
            "context_tokens": 1_000,
            "context_window": 2_000,
            "context_pct": 0.5,
            "model_calls": 2,
            "attempts": 2,
            "failed_attempts": 1,
            "retries": 1,
            "tokens": {
                "input": 100,
                "output": 20,
                "cache_read": 50,
                "cache_write": 0,
                "fresh_input": 50,
                "retrieval": 400,
            },
            "tools": [{
                "name": "exec_command",
                "namespace": "shell",
                "category": "execution",
                "output_tokens": 400,
                "error": False,
                "arguments": secret,
                "result": secret,
            }],
            "user_message": secret,
            "reasoning_summary": secret,
        }],
        "trace": [{
            "ts": 1_787_254_020.0,
            "kind": "tool_result",
            "native_type": "tool_result",
            "native_subtype": "function_call_output",
            "status": "completed",
            "label": "exec_command",
            "detail": secret,
            "execution": 1,
            "tool": "exec_command",
            "tokens": 400,
            "cost": 0.01,
            "severity": "neutral",
            "payload": {"content": secret, "settings": {"token": secret}},
        }],
        "tools": {
            "total_calls": 1,
            "total_errors": 0,
            "total_output_tokens": 400,
            "by_name": [{
                "name": "exec_command",
                "namespace": "shell",
                "display": "Command",
                "category": "execution",
                "calls": 1,
                "output_tokens": 400,
                "errors": 0,
                "arguments": secret,
            }],
        },
        "context": {
            "latest": 1_000,
            "peak": 1_100,
            "window": 2_000,
            "latest_pct": 0.5,
            "peak_pct": 0.55,
        },
        "user_inputs": [secret],
        "reasoning": secret,
        "settings": {"credential": secret},
        "trace_truncated": True,
    }
    return source, state


class MCPQueryContractTests(unittest.TestCase):
    def test_cursor_is_bound_to_query_and_revision(self):
        from token_meter.mcp.contracts import (
            MCPQueryError,
            make_cursor,
            read_cursor,
        )

        cursor = make_cursor(7, {"runtime": "codex"}, ("rev-1",))

        self.assertEqual(
            read_cursor(cursor, {"runtime": "codex"}, ("rev-1",)),
            7,
        )
        with self.assertRaises(MCPQueryError) as raised:
            read_cursor(cursor, {"runtime": "codex"}, ("rev-2",))
        self.assertEqual(raised.exception.code, "stale_cursor")

    def test_cursor_rejects_a_different_query_and_malformed_value(self):
        from token_meter.mcp.contracts import (
            MCPQueryError,
            make_cursor,
            read_cursor,
        )

        cursor = make_cursor(1, {"runtime": "codex"}, ("rev-1",))

        for value, query in (
            (cursor, {"runtime": "claude"}),
            ("not-a-cursor", {"runtime": "codex"}),
        ):
            with self.subTest(value=value, query=query):
                with self.assertRaises(MCPQueryError) as raised:
                    read_cursor(value, query, ("rev-1",))
                self.assertEqual(raised.exception.code, "invalid_argument")

    def test_bounded_page_returns_complete_prefix_and_cursor(self):
        from token_meter.mcp.contracts import bounded_page, read_cursor

        query = {"view": "standardized"}
        revision = ("rev",)
        page = bounded_page(
            [
                {"value": "x" * 80},
                {"value": "y" * 80},
                {"value": "z" * 80},
            ],
            offset=0,
            limit=2,
            query=query,
            revision=revision,
            max_bytes=400,
        )

        self.assertEqual(len(page["items"]), 1)
        self.assertTrue(page["truncated"])
        self.assertEqual(read_cursor(
            page["next_cursor"], query, revision,
        ), 1)

    def test_limits_and_string_lists_are_strictly_bounded(self):
        from token_meter.mcp.contracts import (
            MCPQueryError,
            normalize_limit,
            normalize_string_list,
        )

        self.assertEqual(normalize_limit(None, 20, 100), 20)
        self.assertEqual(
            normalize_string_list(
                ["events", "tools"], "sections",
                {"events", "tools", "context"}, 3,
            ),
            ("events", "tools"),
        )
        for value in (0, 101, "many"):
            with self.subTest(limit=value):
                with self.assertRaises(MCPQueryError) as raised:
                    normalize_limit(value, 20, 100)
                self.assertEqual(raised.exception.code, "invalid_argument")
        with self.assertRaises(MCPQueryError):
            normalize_string_list(
                ["events", "events"], "sections", {"events"}, 3,
            )


class MCPTraceProjectionTests(unittest.TestCase):
    def test_standardized_trace_is_detailed_and_content_free(self):
        from token_meter.mcp.projections import standardized_trace_projection

        source, state = synthetic_source_and_state()
        result = standardized_trace_projection(
            source,
            state,
            sections=(
                "session", "executions", "events", "tools", "context",
                "coverage", "warnings",
            ),
            execution=None,
            event_types=(),
        )

        encoded = json.dumps(result)
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["executions"][0]["tokens"]["input"], 100)
        self.assertEqual(result["events"][0]["type"], "tool_result")
        self.assertEqual(result["tools"][0]["output_tokens"], 400)
        self.assertNotIn("SENTINEL-PRIVATE", encoded)
        self.assertNotIn(source["path"], encoded)
        self.assertNotIn("detail", encoded)
        self.assertNotIn("arguments", encoded)

    def test_native_structure_keeps_types_and_numbers_but_drops_payloads(self):
        from token_meter.mcp.projections import native_structure_projection

        source, state = synthetic_source_and_state()
        rows = native_structure_projection(source, state, None, ())

        self.assertEqual(rows[0]["native_type"], "tool_result")
        self.assertEqual(rows[0]["native_subtype"], "function_call_output")
        self.assertEqual(rows[0]["numeric"]["tokens"], 400)
        self.assertNotIn("SENTINEL-PRIVATE", json.dumps(rows))

    def test_execution_and_event_filters_are_applied_after_sanitizing(self):
        from token_meter.mcp.projections import standardized_trace_projection

        source, state = synthetic_source_and_state()

        missing = standardized_trace_projection(
            source, state, ("executions", "events"), 2, (),
        )
        selected = standardized_trace_projection(
            source, state, ("events",), 1, ("tool_result",),
        )

        self.assertEqual(missing["executions"], [])
        self.assertEqual(missing["events"], [])
        self.assertEqual(len(selected["events"]), 1)


if __name__ == "__main__":
    unittest.main()
