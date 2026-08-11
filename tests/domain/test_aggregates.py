import unittest
from pathlib import Path

from token_meter.domain.aggregates import (
    aggregate_model_stats,
    aggregate_cross_session_rows,
    current_session_summaries,
    metric_coverage,
    rollup_language_signal_events,
)


def session(session_id, runtime, model, cost, tokens, *, available=True, mtime=0):
    availability = {
        "cost": available,
        "tokens": available,
        "cache": available,
    }
    return {
        "id": session_id,
        "provider": "provider",
        "runtime": runtime,
        "label": runtime,
        "project": "project",
        "mtime": mtime,
        "turns": 1,
        "cost": cost,
        "tokens": tokens,
        "availability": availability,
        "_model_cost": {model: cost},
        "_model_tok": {model: tokens},
        "_day_cost": {"2026-08-10": cost},
    }


class AggregateDomainTests(unittest.TestCase):
    def test_model_aggregation_bounds_derived_samples_without_truncating_totals(self):
        samples = [{
            "model": "model-1", "day": "", "ts": index,
            "input_tokens": 100, "peak_input_tokens": 120,
            "context_tokens": 110, "output_tokens": 20,
            "cache_read_tokens": 50, "duration_s": 2,
            "tool_calls": 1, "model_calls": 1,
        } for index in range(2_100)]
        captured_pace_groups = {}

        def capture_pace(groups):
            captured_pace_groups.update(groups)
            return {"windows": {}}

        result = aggregate_model_stats([{
            "id": "session-1", "provider": "provider", "runtime": "runtime",
            "availability": {"cost": True, "tokens": True, "cache": True},
            "model_stats": [{
                "model": "model-1", "cost": 21.0, "tokens": 252_000,
                "input_tokens": 210_000, "output_tokens": 42_000,
                "executions": 2_100,
                "availability": {"cost": True, "tokens": True, "cache": True},
            }],
            "_model_daily": [], "_performance_samples": samples, "_wait_samples": [],
        }], throughput_finalizer=lambda row: row, matched_pace=capture_pace)

        row = result["models"][0]
        self.assertEqual(row["executions"], 2_100)
        self.assertEqual(row["input_tokens"], 210_000)
        self.assertEqual(row["output_tokens"], 42_000)
        self.assertEqual(row["timed_samples"], 2_100)
        self.assertLessEqual(len(row["workload_peak_inputs"]), 2_000)
        self.assertLessEqual(len(row["workload_outputs"]), 2_000)
        self.assertLessEqual(len(captured_pace_groups["model-1::runtime"]), 500)

    def test_same_model_in_two_runtimes_stays_separate(self):
        result = aggregate_cross_session_rows([
            session("a", "runtime-a", "model-1", 1.0, 10),
            session("b", "runtime-b", "model-1", 2.0, 20),
        ])

        self.assertEqual(
            [(row["id"], row["cost"], row["tokens"]) for row in result["model_mix"]],
            [("model-1::runtime-b", 2.0, 20), ("model-1::runtime-a", 1.0, 10)],
        )

    def test_unavailable_session_does_not_become_measured_zero(self):
        rows = [
            session("measured", "runtime", "model", 0.0, 0, available=True),
            session("missing", "runtime", "model", 0.0, 0, available=False),
        ]

        self.assertEqual(metric_coverage(rows, "cost"), {
            "covered_sessions": 1,
            "total_sessions": 2,
            "complete": False,
        })
        result = aggregate_cross_session_rows(rows)
        self.assertFalse(result["coverage"]["cost"]["complete"])
        self.assertEqual(result["provenance"]["unavailable_sessions"], 1)
        self.assertTrue(result["availability"]["cost"])

    def test_cross_session_projection_preserves_order_and_reported_trend(self):
        result = aggregate_cross_session_rows([
            session("older", "runtime", "model-a", 1.0, 10, mtime=10),
            session("newer", "runtime", "model-b", 3.0, 30, mtime=20),
        ])

        self.assertEqual([row["id"] for row in result["sessions"]], ["newer", "older"])
        self.assertEqual(result["total_cost"], 4.0)
        self.assertEqual(result["trend"], [{
            "day": "2026-08-10",
            "cost": 4.0,
            "reported_cost": 4.0,
            "estimated_cost": 0.0,
            "provenance": {
                "usage_basis": "reported",
                "reported_sessions": 2,
                "estimated_sessions": 0,
                "unavailable_sessions": 0,
                "estimated_cost": 0.0,
                "estimated_tokens": 0,
            },
            "usage_basis": "reported",
            "anomaly": False,
            "anomaly_basis": "reported_only",
        }])

    def test_current_session_projection_deduplicates_and_hides_paths(self):
        rows = [
            {**session("same", "runtime", "model", 1, 10, mtime=95),
             "path": "/private/secret", "terminal": False},
            {**session("same", "runtime", "model", 2, 20, mtime=99),
             "path": "/private/new-secret", "terminal": True},
        ]

        result = current_session_summaries(rows, now=100, max_age_s=30, limit=8)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["activity_state"], "working")
        self.assertNotIn("path", result[0])

    def test_language_rollup_keeps_runtime_scoped_model_ids(self):
        result = rollup_language_signal_events([
            {"day": "2026-08-10", "week": "2026-W33", "model": "m",
             "runtime": "runtime-a", "model_id": "m::runtime-a",
             "utterance": True, "matches": 1, "term_counts": {"bad": 1}},
            {"day": "2026-08-10", "week": "2026-W33", "model": "m",
             "runtime": "runtime-b", "model_id": "m::runtime-b",
             "utterance": False, "matches": 0, "term_counts": {}},
        ])

        self.assertEqual([row["id"] for row in result["models"]], [
            "m::runtime-a", "m::runtime-b",
        ])

    def test_shared_aggregate_module_contains_no_known_runtime_identifiers(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "token_meter" / "domain" / "aggregates.py"
        ).read_text().lower()
        for runtime_id in ("claude", "codex", "cursor", "opencode", "kiro"):
            self.assertNotIn(runtime_id, source)


if __name__ == "__main__":
    unittest.main()
