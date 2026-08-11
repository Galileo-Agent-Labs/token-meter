import json
import unittest

from token_meter.contracts import (
    AdapterFailure,
    DeletionPlan,
    DetailLevel,
    DiscoveryContext,
    EvidenceBasis,
    EvidenceValue,
    ModelRef,
    NormalizedSession,
    RuntimeDescriptor,
    SessionSource,
    SourceLocator,
    SourceRevision,
    TimingEvidence,
    UsageEvidence,
)
from token_meter.runtimes.registry import RuntimeRegistry
from token_meter.runtimes.legacy import LegacyRuntimeAdapter


class StubAdapter:
    def __init__(self, runtime_id, fail_discovery=False):
        self.descriptor = RuntimeDescriptor(
            runtime_id=runtime_id,
            label=runtime_id.title(),
            capabilities=frozenset(("sessions",)),
            symbol="runtime.generic",
            color="runtime-neutral",
        )
        self.fail_discovery = fail_discovery

    def source(self):
        return SessionSource(
            runtime_id=self.descriptor.runtime_id,
            client_id=self.descriptor.runtime_id,
            session_id="session-1",
            display_label=self.descriptor.label,
            project=None,
            locator=SourceLocator("memory", "private-source"),
            activity_mtime=1.0,
            revision=SourceRevision(("1",)),
            model_ref=ModelRef("test-models", "model-1"),
        )

    def discover(self, context):
        if self.fail_discovery:
            raise RuntimeError("/private/sentinel should not escape")
        return (self.source(),)

    def current_revision(self, source):
        return source.revision

    def load(self, source, detail):
        return NormalizedSession(
            source=source,
            started_at=None,
            ended_at=None,
            usage=UsageEvidence(
                input_tokens=EvidenceValue(0, EvidenceBasis.MEASURED),
                output_tokens=EvidenceValue(0, EvidenceBasis.MEASURED),
                cache_read_tokens=EvidenceValue(None, EvidenceBasis.UNAVAILABLE),
                cache_write_tokens=EvidenceValue(None, EvidenceBasis.UNAVAILABLE),
                cost_usd=EvidenceValue(None, EvidenceBasis.UNAVAILABLE),
            ),
            timing=TimingEvidence.unavailable(),
            tools=(),
            turns=(),
            pricing_basis=None,
            capabilities=frozenset(("sessions",)),
            warnings=(),
            detail=detail,
        )

    def deletion_plan(self, source):
        return DeletionPlan.deny("unsupported")


class RuntimeRegistryTests(unittest.TestCase):
    def test_registry_rejects_duplicate_runtime_ids(self):
        with self.assertRaises(ValueError):
            RuntimeRegistry((StubAdapter("codex"), StubAdapter("codex")))

    def test_registry_keeps_explicit_order_and_loads_a_synthetic_runtime(self):
        registry = RuntimeRegistry((StubAdapter("claude"), StubAdapter("fifth-runtime")))

        self.assertEqual(registry.runtime_ids, ("claude", "fifth-runtime"))
        source = registry.require("fifth-runtime").source()
        session = registry.load(source, DetailLevel.SUMMARY)

        self.assertEqual(session.source.runtime_id, "fifth-runtime")
        self.assertEqual(session.detail, DetailLevel.SUMMARY)

    def test_discovery_failure_is_bounded_and_does_not_block_other_adapters(self):
        registry = RuntimeRegistry((StubAdapter("broken", True), StubAdapter("healthy")))

        result = registry.discover_all(DiscoveryContext(home="/tmp/home"))

        self.assertEqual([row.runtime_id for row in result.sources], ["healthy"])
        self.assertEqual(result.failures, (
            AdapterFailure(runtime_id="broken", operation="discover", code="adapter_failed"),
        ))
        self.assertNotIn("/private/sentinel", json.dumps(result.to_public_dict()))

    def test_unknown_runtime_has_an_explicit_bounded_error(self):
        registry = RuntimeRegistry((StubAdapter("known"),))

        with self.assertRaisesRegex(KeyError, "Unknown runtime"):
            registry.require("missing")

    def test_legacy_discovery_is_ordered_and_isolates_adapter_failures(self):
        def descriptor(runtime_id):
            return RuntimeDescriptor(
                runtime_id=runtime_id,
                label=runtime_id.title(),
                capabilities=frozenset(("sessions",)),
                symbol="runtime.generic",
                color="runtime-neutral",
            )

        def broken_discovery(context):
            raise RuntimeError("/private/sentinel should not escape")

        registry = RuntimeRegistry((
            LegacyRuntimeAdapter(
                descriptor("first"), lambda source: source,
                discoverer=lambda context: ({"provider": "first", "id": "one"},),
            ),
            LegacyRuntimeAdapter(
                descriptor("broken"), lambda source: source,
                discoverer=broken_discovery,
            ),
            LegacyRuntimeAdapter(
                descriptor("third"), lambda source: source,
                discoverer=lambda context: ({"provider": "third", "id": "three"},),
            ),
        ))

        result = registry.discover_legacy_all(DiscoveryContext(home="/tmp/home"))

        self.assertEqual(
            [(row["provider"], row["id"]) for row in result.sources],
            [("first", "one"), ("third", "three")],
        )
        self.assertEqual(result.failures, (
            AdapterFailure("broken", "discover", "adapter_failed"),
        ))
        self.assertNotIn("/private/sentinel", json.dumps(result.to_public_dict()))

    def test_legacy_load_failure_is_bounded(self):
        adapter = StubAdapter("broken-load")

        def fail_load(source, detail):
            raise RuntimeError("/private/sentinel should not escape")

        adapter.load = fail_load
        registry = RuntimeRegistry((adapter,))

        result = registry.load_legacy_for(
            "broken-load", {"provider": "broken-load"}, DetailLevel.FULL
        )

        self.assertIsNone(result.value)
        self.assertEqual(
            result.failure,
            AdapterFailure("broken-load", "load", "adapter_failed"),
        )
        self.assertNotIn("/private/sentinel", json.dumps(result.to_public_dict()))


if __name__ == "__main__":
    unittest.main()
