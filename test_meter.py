import unittest
import json
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


class SessionRouteTests(unittest.TestCase):
    def test_dashboard_accepts_root_and_unique_session_paths(self):
        self.assertTrue(meter.is_dashboard_page_path("/"))
        self.assertTrue(meter.is_dashboard_page_path("/sessions/019f16fa-dc6c-7a62-839c-25c15dca4e75"))
        self.assertTrue(meter.is_dashboard_page_path("/sessions/claude%20session/"))

    def test_dashboard_rejects_api_nested_and_empty_session_paths(self):
        self.assertFalse(meter.is_dashboard_page_path("/session"))
        self.assertFalse(meter.is_dashboard_page_path("/sessions/"))
        self.assertFalse(meter.is_dashboard_page_path("/sessions/one/two"))


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
        }
        with mock.patch.object(meter, "all_session_sources", return_value=sources), \
                mock.patch.object(meter, "recompute", return_value=state):
            payload = meter.menubar_state("pinned")

        self.assertTrue(payload["selection"]["pinned"])
        self.assertFalse(payload["selection"]["missing"])
        self.assertEqual(payload["selection"]["selected_id"], "pinned")
        self.assertEqual(payload["recent_sessions"][0]["name"], "Pinned task")


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

    def test_discovers_enterprise_no_project_agent_trace(self):
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


class McpActionTests(unittest.TestCase):
    def test_disable_uses_fixed_argument_vector_without_shell(self):
        observed = {}

        class Completed:
            returncode = 0
            stdout = "removed"
            stderr = ""

        def fake_runner(argv, **kwargs):
            observed["argv"] = argv
            observed["kwargs"] = kwargs
            return Completed()

        result = meter.disable_mcp_server("jira", ghost_path="/usr/local/bin/ghost", runner=fake_runner)
        self.assertTrue(result["ok"])
        self.assertEqual(observed["argv"], ["/usr/local/bin/ghost", "mcp", "all", "remove", "jira"])
        self.assertNotIn("shell", observed["kwargs"])
        self.assertTrue(result["restart_required"])

    def test_disable_rejects_untrusted_server_name(self):
        result = meter.disable_mcp_server("jira; rm -rf /", ghost_path="ghost", runner=lambda *a, **k: None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Invalid MCP server name.")

    def test_enable_uses_fixed_add_argument_vector(self):
        observed = {}

        class Completed:
            returncode = 0
            stdout = "added"
            stderr = ""

        def fake_runner(argv, **kwargs):
            observed["argv"] = argv
            return Completed()

        result = meter.set_mcp_server_enabled("context7", True, ghost_path="/usr/local/bin/ghost", runner=fake_runner)
        self.assertTrue(result["ok"])
        self.assertEqual(observed["argv"], ["/usr/local/bin/ghost", "mcp", "all", "add", "context7"])


class CapabilityConfigTests(unittest.TestCase):
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
        self.assertIn("enabled = false", text)
        self.assertIn('source = "keep"', text)


if __name__ == "__main__":
    unittest.main()
