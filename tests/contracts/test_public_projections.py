import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from token_meter.contracts import (
    DetailLevel,
    EvidenceBasis,
    EvidenceValue,
    ModelRef,
    NormalizedSession,
    ParseWarning,
    SessionSource,
    SourceLocator,
    SourceRevision,
    TimingEvidence,
    ToolEvent,
    UsageEvidence,
)
from token_meter.projections import projection_bundle


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "normalized"


def evidence(pair):
    value, basis = pair
    return EvidenceValue(value, EvidenceBasis(basis))


def normalized(row):
    return NormalizedSession(
        source=SessionSource(
            runtime_id=row["runtime_id"],
            client_id=row["client_id"],
            session_id=row["session_id"],
            display_label=row["label"],
            project="/private/sentinel/project",
            locator=SourceLocator("file", "/private/sentinel/session.jsonl"),
            activity_mtime=1004.0,
            revision=SourceRevision(("private-revision",)),
            model_ref=ModelRef(row["model_provider_id"], row["model_id"]),
            account_provider_id=row["account_provider_id"],
        ),
        started_at=datetime.fromtimestamp(1000, timezone.utc),
        ended_at=datetime.fromtimestamp(1004, timezone.utc),
        usage=UsageEvidence(
            input_tokens=evidence(row["input"]),
            output_tokens=evidence(row["output"]),
            cache_read_tokens=evidence(row["cache_read"]),
            cache_write_tokens=evidence(row["cache_write"]),
            cost_usd=evidence(row["cost"]),
        ),
        timing=TimingEvidence(
            evidence(row.get("active", [None, "unavailable"])),
            evidence(row.get("wait", [None, "unavailable"])),
            evidence(row.get("ttft", [None, "unavailable"])),
        ),
        tools=tuple(ToolEvent(*tool) for tool in row.get("tools", [])),
        turns=(),
        pricing_basis=None,
        capabilities=frozenset(),
        warnings=tuple(ParseWarning(*warning) for warning in row.get("warnings", [])),
        detail=DetailLevel.FULL,
    )


class PublicProjectionTests(unittest.TestCase):
    def test_each_current_runtime_projects_without_private_source_data(self):
        fixture = json.loads((FIXTURES / "current-runtimes.json").read_text())
        for row in fixture["sessions"]:
            with self.subTest(runtime=row["runtime_id"]):
                bundle = projection_bundle(normalized(row), runtime_catalog={})
                encoded = json.dumps(bundle, sort_keys=True)
                self.assertEqual(bundle["session"]["provider"], row["runtime_id"])
                self.assertEqual(
                    bundle["session"]["model_provider"], row["model_provider_id"]
                )
                for sentinel in fixture["forbidden_sentinels"]:
                    self.assertNotIn(sentinel, encoded)

    def test_session_state_model_menubar_and_mcp_match_golden(self):
        row = {
            "runtime_id": "kiro", "client_id": "kiro",
            "session_id": "kiro-fixture", "label": "Kiro",
            "model_provider_id": "anthropic", "model_id": "claude-fixture-model",
            "account_provider_id": None,
            "input": [12, "measured"], "output": [8, "measured"],
            "cache_read": [None, "unavailable"], "cache_write": [0, "measured"],
            "cost": [0.25, "estimated"],
            "active": [3.0, "measured"], "wait": [None, "unavailable"],
            "ttft": [0.0, "measured"],
            "tools": [["read", "filesystem"], ["exec", "shell"]],
            "warnings": [["partial", "Some evidence is unavailable."]],
        }
        catalog = {
            "kiro": {"label": "Kiro", "symbol": "runtime.generic",
                     "color": "runtime-neutral", "capabilities": ["sessions"]},
            "unknown-runtime": {"label": "Unknown Runtime", "symbol": "runtime.generic",
                                "color": "runtime-neutral", "capabilities": ["sessions"]},
        }
        expected = json.loads((FIXTURES / "projection-golden.json").read_text())

        result = projection_bundle(normalized(row), runtime_catalog=catalog)

        self.assertEqual(result, expected)
        self.assertEqual(result["session"]["provider"], "kiro")
        self.assertEqual(result["session"]["model_provider"], "anthropic")

    def test_unavailable_is_omitted_from_mcp_but_measured_zero_is_preserved(self):
        fixture = json.loads((FIXTURES / "current-runtimes.json").read_text())
        opencode = next(row for row in fixture["sessions"] if row["runtime_id"] == "opencode")

        result = projection_bundle(normalized(opencode), runtime_catalog={})

        self.assertEqual(result["mcp"]["usage"]["input_tokens"], 0)
        self.assertNotIn("cost_usd", result["mcp"]["usage"])
        self.assertTrue(result["mcp"]["availability"]["input_tokens"])
        self.assertFalse(result["mcp"]["availability"]["cost"])


if __name__ == "__main__":
    unittest.main()
