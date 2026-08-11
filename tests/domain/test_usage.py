import unittest
from pathlib import Path

from token_meter.contracts import (
    EvidenceBasis,
    EvidenceValue,
    ModelRef,
    PriceQuote,
    RuntimeModelKey,
    UsageEvidence,
)
from token_meter.domain.usage import (
    cache_metrics,
    cache_savings_for_rate,
    cost_breakdown,
    distribute_reported_cost,
    usage_provenance,
    usage_io_tokens,
    usage_token_total,
)


def measured(value):
    return EvidenceValue(value, EvidenceBasis.MEASURED)


def usage(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    return UsageEvidence(
        input_tokens=measured(input_tokens),
        output_tokens=measured(output_tokens),
        cache_read_tokens=measured(cache_read),
        cache_write_tokens=measured(cache_write),
        cost_usd=EvidenceValue.unavailable(),
    )


def quote(provider="model-provider", model="model"):
    return PriceQuote(
        model=ModelRef(provider, model),
        input_per_million=2.0,
        output_per_million=10.0,
        cache_read_per_million=0.2,
        cache_write_per_million=2.5,
        basis=EvidenceBasis.ESTIMATED,
        matched_rule="model",
    )


class UsageDomainTests(unittest.TestCase):
    def test_shared_usage_module_contains_no_known_runtime_identifiers(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "token_meter"
            / "domain"
            / "usage.py"
        ).read_text().lower()

        for runtime_id in ("claude", "codex", "cursor", "opencode", "kiro"):
            self.assertNotIn(runtime_id, source)

    def test_golden_cost_and_cache_token_semantics(self):
        evidence = usage(
            input_tokens=1_000_000,
            output_tokens=200_000,
            cache_read=3_000_000,
            cache_write=400_000,
        )

        breakdown = cost_breakdown(evidence, quote())

        self.assertEqual(breakdown.to_legacy_dict(), {
            "input": 2.0,
            "cache_write": 1.0,
            "cache_read": 0.6,
            "output": 2.0,
        })
        self.assertEqual(usage_token_total(evidence), measured(4_600_000))
        self.assertEqual(usage_io_tokens(evidence), (measured(4_400_000), measured(200_000)))

    def test_unknown_price_and_unavailable_tokens_remain_unavailable(self):
        evidence = UsageEvidence(
            input_tokens=EvidenceValue.unavailable(),
            output_tokens=measured(0),
            cache_read_tokens=measured(0),
            cache_write_tokens=measured(0),
            cost_usd=EvidenceValue.unavailable(),
        )

        missing_price = cost_breakdown(
            evidence,
            PriceQuote.unavailable(ModelRef("model-provider", "unknown")),
        )

        self.assertFalse(missing_price.available)
        self.assertIsNone(missing_price.input_usd.value)
        self.assertEqual(missing_price.output_usd.value, None)
        self.assertEqual(usage_token_total(evidence), EvidenceValue.unavailable())

    def test_measured_zero_is_available_and_distinct_from_missing(self):
        breakdown = cost_breakdown(usage(), quote())

        self.assertTrue(breakdown.available)
        self.assertEqual(
            breakdown.total_usd,
            EvidenceValue(0.0, EvidenceBasis.ESTIMATED),
        )

    def test_input_and_output_multipliers_preserve_current_long_context_math(self):
        breakdown = cost_breakdown(
            usage(input_tokens=1_000_000, output_tokens=1_000_000,
                  cache_read=1_000_000, cache_write=1_000_000),
            quote(),
            input_multiplier=2.0,
            output_multiplier=1.5,
        )

        self.assertEqual(breakdown.to_legacy_dict(), {
            "input": 4.0,
            "cache_write": 5.0,
            "cache_read": 0.4,
            "output": 15.0,
        })

    def test_equivalent_evidence_is_runtime_neutral_but_keys_stay_scoped(self):
        evidence = usage(input_tokens=100, output_tokens=20)
        model = quote().model

        left = cost_breakdown(evidence, quote())
        right = cost_breakdown(evidence, quote())

        self.assertEqual(left, right)
        self.assertNotEqual(RuntimeModelKey("runtime-a", model),
                            RuntimeModelKey("runtime-b", model))

    def test_reported_cost_distribution_preserves_authoritative_total(self):
        split = distribute_reported_cost(
            0.02,
            usage(input_tokens=100, output_tokens=20, cache_read=30, cache_write=10),
        )

        self.assertAlmostEqual(sum(split.values()), 0.02)
        self.assertEqual(distribute_reported_cost(0.0, usage())["output"], 0.0)

    def test_cache_metrics_preserve_fresh_read_write_and_latest_semantics(self):
        metrics = cache_metrics(
            fresh=100,
            read=300,
            write=50,
            read_cost=0.03,
            write_cost=0.02,
            saved=0.27,
            latest_input=40,
            latest_cache=35,
            latest_read=30,
            latest_write=5,
        )

        self.assertEqual(metrics["total"], 350)
        self.assertEqual(metrics["input_total"], 450)
        self.assertAlmostEqual(metrics["hit_ratio"], 300 / 350)
        self.assertAlmostEqual(metrics["input_share"], 350 / 450)
        self.assertEqual(metrics["cost"], 0.05)
        self.assertEqual(metrics["latest"], {
            "tokens": 35,
            "read": 30,
            "write": 5,
            "input": 40,
            "share": 35 / 40,
        })
        self.assertEqual(cache_savings_for_rate(300, 2.0, 0.2), 0.00054)

    def test_usage_provenance_preserves_reported_estimated_mixed_and_missing(self):
        reported = {"id": "reported", "cost": 1.0, "tokens": 10}
        estimated = {
            "id": "estimated",
            "cost": 2.0,
            "tokens": 20,
            "token_estimate": True,
        }
        unavailable = {
            "id": "missing",
            "availability": {"cost": False, "tokens": False},
        }

        self.assertEqual(usage_provenance([reported])["usage_basis"], "reported")
        self.assertEqual(usage_provenance([estimated]), {
            "usage_basis": "local_estimate",
            "reported_sessions": 0,
            "estimated_sessions": 1,
            "unavailable_sessions": 0,
            "estimated_cost": 2.0,
            "estimated_tokens": 20,
        })
        self.assertEqual(
            usage_provenance([reported, estimated])["usage_basis"], "mixed"
        )
        self.assertEqual(
            usage_provenance([unavailable])["usage_basis"], "unavailable"
        )


if __name__ == "__main__":
    unittest.main()
