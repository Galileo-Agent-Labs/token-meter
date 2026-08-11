import builtins
import json
import re
import socket
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from token_meter.telemetry.otel_mapping import (
    OTEL_GENAI_SCHEMA_URL,
    OTEL_GENAI_SEMCONV_VERSION,
    map_to_otel,
)
from token_meter.telemetry.privacy import project_aggregate


SENTINELS = (
    "PRIVATE_PROMPT", "PRIVATE_RESPONSE", "PRIVATE_REASONING",
    "PRIVATE_TOOL_PAYLOAD", "PRIVATE_CREDENTIAL", "PRIVATE_ACCOUNT",
    "/private/local/path", "PRIVATE_PROJECT", "PRIVATE_SESSION_LABEL",
    "PRIVATE_UNKNOWN_FIELD",
)


def adversarial_candidate():
    return {
        "runtime_id": "synthetic",
        "model": {"provider_id": "openai", "model_id": "gpt-test"},
        "usage": {
            "input_tokens": {"value": 120, "basis": "measured"},
            "output_tokens": {"value": 30, "basis": "estimated"},
            "cache_read_tokens": {"value": None, "basis": "unavailable"},
            "cache_write_tokens": {"value": 10, "basis": "measured"},
            "prompt": SENTINELS[0], "response": SENTINELS[1],
        },
        "timing": {
            "active_seconds": {"value": 4.5, "basis": "inferred"},
            "reasoning": SENTINELS[2],
        },
        "tools": [{
            "name": "PRIVATE_TOOL_NAME", "category": "search",
            "arguments": SENTINELS[3], "result": SENTINELS[3],
        }],
        "credential": SENTINELS[4], "account": SENTINELS[5],
        "path": SENTINELS[6], "project": SENTINELS[7],
        "session_label": SENTINELS[8], "unknown": SENTINELS[9],
    }


class TelemetryPrivacyTests(unittest.TestCase):
    def test_adversarial_content_and_unknown_fields_cannot_reach_mapping(self):
        projected = project_aggregate(
            adversarial_candidate(), os_family="darwin", token_meter_version="0.1.0"
        )
        mapped = map_to_otel(projected)
        encoded = repr(projected) + json.dumps(mapped, sort_keys=True)

        for sentinel in SENTINELS + ("PRIVATE_TOOL_NAME",):
            self.assertNotIn(sentinel, encoded)
        self.assertEqual(projected.tool_categories, {"search": 1})
        self.assertNotIn("cache_read", projected.usage)

    def test_golden_mapping_pins_the_small_genai_subset(self):
        mapped = map_to_otel(project_aggregate(
            adversarial_candidate(), os_family="darwin", token_meter_version="0.1.0"
        ))

        self.assertEqual(OTEL_GENAI_SEMCONV_VERSION, "1.42.0")
        self.assertEqual(
            OTEL_GENAI_SCHEMA_URL,
            "https://opentelemetry.io/schemas/gen-ai/1.42.0",
        )
        self.assertEqual([row["name"] for row in mapped["metrics"]], [
            "gen_ai.client.token.usage", "gen_ai.client.token.usage",
            "token_meter.cache.token.usage",
            "token_meter.session.active_duration", "token_meter.tool.call.count",
        ])
        self.assertEqual(mapped["metrics"][0], {
            "name": "gen_ai.client.token.usage",
            "unit": "{token}",
            "value": 120,
            "attributes": {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": "gpt-test",
                "gen_ai.token.type": "input",
                "os.type": "darwin",
                "service.version": "0.1.0",
                "token_meter.evidence.basis": "measured",
                "token_meter.runtime.id": "synthetic",
            },
        })

    def test_mapping_rejects_unprojected_objects(self):
        with self.assertRaises(TypeError):
            map_to_otel(adversarial_candidate())

    def test_projection_and_mapping_have_no_io_side_effects(self):
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process")), \
                mock.patch.object(builtins, "open", side_effect=AssertionError("file")):
            mapped = map_to_otel(project_aggregate(adversarial_candidate()))
        self.assertTrue(mapped["metrics"])

    def test_default_package_has_no_otel_sdk_or_exporter_dependency(self):
        root = Path(__file__).resolve().parents[2]
        sources = "\n".join(
            path.read_text() for path in (root / "token_meter" / "telemetry").glob("*.py")
        ).lower()
        self.assertIsNone(re.search(
            r"(^|\n)\s*(?:from|import)\s+opentelemetry(?:\.|\s|$)", sources
        ))
        self.assertNotIn("socket", sources)
        self.assertNotIn("subprocess", sources)
        self.assertFalse((root / "token_meter" / "telemetry" / "exporter.py").exists())


if __name__ == "__main__":
    unittest.main()
