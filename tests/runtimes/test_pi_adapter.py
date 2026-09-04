import json
import tempfile
import unittest
from pathlib import Path

from token_meter.contracts import DetailLevel, DiscoveryContext, EvidenceBasis, session_source_public_dict
from token_meter.runtimes.pi import PiRuntimeAdapter


class PiRuntimeAdapterTests(unittest.TestCase):
    def _write_session(self, agent_dir, *, with_cost=True, provider="anthropic",
                       model="claude-test"):
        path = Path(agent_dir) / "sessions" / "--repo--" / "pi-session.jsonl"
        path.parent.mkdir(parents=True)
        usage = {
            "input": 100, "output": 20, "cacheRead": 10,
            "cacheWrite": 5, "totalTokens": 135,
        }
        if with_cost:
            usage["cost"] = {
                "input": 0.001, "output": 0.002,
                "cacheRead": 0.0001, "cacheWrite": 0.0002,
            }
        rows = [
            {"type": "session", "id": "pi-session", "cwd": "/repo"},
            {"type": "model_change", "provider": provider, "modelId": model},
            {"type": "message", "timestamp": "2026-09-04T10:00:00Z",
             "message": {"role": "user", "content": []}},
            {"type": "message", "timestamp": "2026-09-04T10:00:03Z", "message": {
                "role": "assistant", "provider": provider, "model": model,
                "content": [{"type": "toolCall", "id": "call", "name": "read",
                             "arguments": {}}],
                "usage": usage,
            }},
            {"type": "message", "timestamp": "2026-09-04T10:00:04Z",
             "message": {"role": "toolResult", "toolCallId": "call", "toolName": "read"}},
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    def test_normalizes_measured_tokens_and_estimated_local_cost_without_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp)
            adapter = PiRuntimeAdapter(tmp)
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            loaded = adapter.load(source, DetailLevel.FULL)

        self.assertEqual(source.model_ref.provider_id, "anthropic")
        self.assertEqual(loaded.usage.input_tokens.value, 100)
        self.assertEqual(loaded.usage.output_tokens.value, 20)
        self.assertEqual(loaded.usage.cache_read_tokens.value, 10)
        self.assertEqual(loaded.usage.cache_write_tokens.value, 5)
        self.assertAlmostEqual(loaded.usage.cost_usd.value, 0.0033)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.ESTIMATED)
        self.assertEqual([(tool.name, tool.status) for tool in loaded.tools], [("read", "success")])
        self.assertNotIn("locator", session_source_public_dict(source))
        self.assertNotIn("arguments", repr(loaded))

    def test_missing_pi_cost_is_unavailable_without_changing_measured_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, with_cost=False)
            adapter = PiRuntimeAdapter(tmp)
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(loaded.usage.input_tokens.value, 100)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIsNone(loaded.usage.cost_usd.value)
        self.assertEqual(loaded.turns, ())

    def test_redacts_account_bearing_bedrock_profile_from_native_model_identity(self):
        raw_profile = (
            "arn:aws:bedrock:us-west-2:123456789012:"
            "application-inference-profile/private-profile"
        )
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, provider="amazon-bedrock", model=raw_profile)
            adapter = PiRuntimeAdapter(tmp)
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(source.model_ref.provider_id, "amazon")
        self.assertEqual(source.model_ref.model_id, "aws-bedrock-profile")
        self.assertNotIn(raw_profile, repr(source))
        self.assertNotIn("123456789012", repr(loaded))


if __name__ == "__main__":
    unittest.main()
