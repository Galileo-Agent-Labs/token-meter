import json
import unittest

from token_meter.compat import (
    envelope_to_legacy_source,
    legacy_source_to_envelope,
    public_legacy_source_identity,
)


class LegacySourceCompatibilityTests(unittest.TestCase):
    def source(self, runtime_id):
        return {
            "provider": runtime_id,
            "client": runtime_id + "-client",
            "label": runtime_id.title(),
            "id": runtime_id + "-session",
            "path": "/private/sentinel/{}.jsonl".format(runtime_id),
            "project": "/private/project",
            "mtime": 12.5,
            "signature_mtime": 13.5,
            "title": "A session",
            "model": "shared-model",
            "model_provider": "test-models",
            "pricing_variant": "fast",
            "runtime_specific": {"preserve": True},
        }

    def test_each_current_runtime_round_trips_without_changing_legacy_fields(self):
        for runtime_id in ("claude", "codex", "cursor", "opencode"):
            with self.subTest(runtime_id=runtime_id):
                source = self.source(runtime_id)
                envelope = legacy_source_to_envelope(
                    source, account_provider_id=runtime_id + "-account"
                )

                self.assertEqual(envelope_to_legacy_source(envelope), source)
                self.assertEqual(envelope.normalized.runtime_id, runtime_id)
                self.assertEqual(
                    envelope.normalized.model_ref.provider_id, "test-models"
                )
                self.assertEqual(
                    envelope.normalized.account_provider_id,
                    runtime_id + "-account",
                )

    def test_runtime_model_and_account_provider_axes_remain_independent(self):
        source = self.source("kiro")
        source["model_provider"] = "anthropic"

        envelope = legacy_source_to_envelope(
            source, account_provider_id="aws-bedrock"
        )

        self.assertEqual(envelope.normalized.runtime_id, "kiro")
        self.assertEqual(envelope.normalized.model_ref.provider_id, "anthropic")
        self.assertEqual(envelope.normalized.account_provider_id, "aws-bedrock")
        self.assertEqual(envelope_to_legacy_source(envelope)["provider"], "kiro")

    def test_public_projection_excludes_private_source_and_extra_fields(self):
        envelope = legacy_source_to_envelope(self.source("codex"))

        payload = public_legacy_source_identity(envelope)
        serialized = json.dumps(payload, sort_keys=True)

        self.assertNotIn("/private/sentinel", serialized)
        self.assertNotIn("runtime_specific", serialized)
        self.assertNotIn("locator", payload)
        self.assertNotIn("revision", payload)


if __name__ == "__main__":
    unittest.main()
