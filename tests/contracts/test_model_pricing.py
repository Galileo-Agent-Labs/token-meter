import unittest
from datetime import datetime, timezone

from token_meter.contracts import EvidenceBasis, ModelRef, PriceQuery
from token_meter.models.catalog import (
    GPT_56_PRICE_UPDATE_AT,
    canonical_model_provider,
)
from token_meter.models.pricing import quote_for


class ModelPricingBoundaryTests(unittest.TestCase):
    def test_legacy_runtime_names_map_explicitly_to_model_providers(self):
        self.assertEqual(canonical_model_provider("claude"), "anthropic")
        self.assertEqual(canonical_model_provider("codex"), "openai")
        self.assertEqual(canonical_model_provider("openai"), "openai")
        self.assertIsNone(canonical_model_provider("kiro"))

    def test_model_provider_pricing_is_independent_of_runtime_identity(self):
        runtime_id = "kiro"
        query = PriceQuery(ModelRef("anthropic", "claude-sonnet-5"))

        quote = quote_for(query)

        self.assertEqual(runtime_id, "kiro")
        self.assertEqual(quote.model.provider_id, "anthropic")
        self.assertEqual(quote.input_per_million, 2.0)
        self.assertEqual(quote.output_per_million, 10.0)
        self.assertEqual(quote.basis, EvidenceBasis.ESTIMATED)
        self.assertEqual(quote.matched_rule, "claude-sonnet-5")

    def test_unknown_provider_is_explicitly_unavailable_without_cross_fallback(self):
        unknown_provider = quote_for(PriceQuery(ModelRef("unknown", "gpt-5.6")))
        wrong_provider = quote_for(PriceQuery(ModelRef("anthropic", "gpt-5.6")))

        for quote in (unknown_provider, wrong_provider):
            self.assertEqual(quote.basis, EvidenceBasis.UNAVAILABLE)
            self.assertIsNone(quote.input_per_million)
            self.assertIsNone(quote.output_per_million)
            self.assertIsNone(quote.matched_rule)

    def test_price_history_switches_at_the_existing_exact_boundary(self):
        before = datetime.fromtimestamp(GPT_56_PRICE_UPDATE_AT - 1, timezone.utc)
        boundary = datetime.fromtimestamp(GPT_56_PRICE_UPDATE_AT, timezone.utc)
        model = ModelRef("openai", "gpt-5.6-terra")

        old_quote = quote_for(PriceQuery(model, before))
        current_quote = quote_for(PriceQuery(model, boundary))

        self.assertEqual(old_quote.input_per_million, 5.0)
        self.assertEqual(old_quote.output_per_million, 30.0)
        self.assertEqual(current_quote.input_per_million, 2.0)
        self.assertEqual(current_quote.output_per_million, 12.0)

    def test_longest_catalog_prefix_is_reported_as_the_matching_rule(self):
        quote = quote_for(
            PriceQuery(ModelRef("openai", "gpt-5.4-mini-2026-07-01"))
        )

        self.assertEqual(quote.input_per_million, 0.75)
        self.assertEqual(quote.matched_rule, "gpt-5.4-mini")

    def test_cursor_variant_is_canonicalized_inside_the_model_boundary(self):
        quote = quote_for(
            PriceQuery(ModelRef("cursor", "Composer 2.5", variant="fast"))
        )

        self.assertEqual(quote.input_per_million, 3.0)
        self.assertEqual(quote.output_per_million, 15.0)
        self.assertEqual(quote.matched_rule, "composer-2.5-fast")


if __name__ == "__main__":
    unittest.main()
