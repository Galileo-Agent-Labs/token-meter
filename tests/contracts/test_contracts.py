import json
import unittest
from dataclasses import FrozenInstanceError

from token_meter.contracts import (
    DetailLevel,
    EvidenceBasis,
    EvidenceValue,
    ModelRef,
    NormalizedSession,
    RuntimeModelKey,
    SessionSource,
    SourceLocator,
    SourceRevision,
    TimingEvidence,
    UsageEvidence,
    session_source_public_dict,
)


class EvidenceContractTests(unittest.TestCase):
    def test_unavailable_requires_none_while_measured_zero_remains_valid(self):
        unavailable = EvidenceValue(None, EvidenceBasis.UNAVAILABLE)
        measured_zero = EvidenceValue(0, EvidenceBasis.MEASURED)

        self.assertIsNone(unavailable.value)
        self.assertEqual(measured_zero.value, 0)
        with self.assertRaises(ValueError):
            EvidenceValue(0, EvidenceBasis.UNAVAILABLE)
        with self.assertRaises(ValueError):
            EvidenceValue(None, EvidenceBasis.MEASURED)

    def test_contract_values_are_immutable(self):
        model = ModelRef(provider_id="openai", model_id="gpt-test")
        with self.assertRaises(FrozenInstanceError):
            model.model_id = "changed"

    def test_runtime_is_part_of_the_default_model_aggregation_key(self):
        model = ModelRef(provider_id="openai", model_id="shared-model")

        codex = RuntimeModelKey(runtime_id="codex", model=model)
        cursor = RuntimeModelKey(runtime_id="cursor", model=model)

        self.assertNotEqual(codex, cursor)
        self.assertEqual(len({codex, cursor}), 2)


class PublicSerializationTests(unittest.TestCase):
    def source(self):
        return SessionSource(
            runtime_id="codex",
            client_id="codex",
            session_id="session-1",
            display_label="Codex",
            project="token-meter",
            locator=SourceLocator(kind="file", value="/private/sentinel/session.jsonl"),
            activity_mtime=123.5,
            revision=SourceRevision(("123", "456")),
            model_ref=ModelRef(provider_id="openai", model_id="gpt-test"),
        )

    def test_public_source_projection_never_serializes_locator_or_revision(self):
        payload = session_source_public_dict(self.source())
        serialized = json.dumps(payload, sort_keys=True)

        self.assertNotIn("locator", payload)
        self.assertNotIn("revision", payload)
        self.assertNotIn("/private/sentinel", serialized)
        self.assertEqual(payload["runtime_id"], "codex")
        self.assertEqual(payload["model"]["provider_id"], "openai")

    def test_normalized_session_contains_no_raw_conversation_fields(self):
        session = NormalizedSession(
            source=self.source(),
            started_at=None,
            ended_at=None,
            usage=UsageEvidence.unavailable(),
            timing=TimingEvidence.unavailable(),
            tools=(),
            turns=(),
            pricing_basis=None,
            capabilities=frozenset(),
            warnings=(),
            detail=DetailLevel.SUMMARY,
        )

        self.assertFalse(hasattr(session, "prompt"))
        self.assertFalse(hasattr(session, "response"))
        self.assertFalse(hasattr(session, "reasoning"))


if __name__ == "__main__":
    unittest.main()
