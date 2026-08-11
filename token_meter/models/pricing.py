"""Pure effective-dated price resolution over the model catalog."""

import time
from datetime import timezone

from token_meter.contracts import EvidenceBasis, PriceQuote

from .catalog import (
    BUILTIN_MODEL_PRICE_HISTORY,
    BUILTIN_PRICE_TABLES,
    MODEL_PRICE_FIELDS,
    MODEL_PROVIDER_IDS,
)


def observed_timestamp(observed_at):
    if observed_at is None:
        return None
    if isinstance(observed_at, bool):
        return None
    if isinstance(observed_at, (int, float)) and not isinstance(observed_at, bool):
        return float(observed_at)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return observed_at.timestamp()


def builtin_price_table(provider_id, observed_at=None):
    """Return a copy of bundled prices effective at one observation time."""

    if provider_id not in MODEL_PROVIDER_IDS:
        return {}
    table = {
        model: dict(prices)
        for model, prices in BUILTIN_PRICE_TABLES[provider_id].items()
    }
    timestamp = observed_timestamp(observed_at)
    if timestamp is None:
        return table
    for model, periods in BUILTIN_MODEL_PRICE_HISTORY.get(provider_id, {}).items():
        selected = None
        for effective_from, prices in periods:
            if effective_from is None or timestamp >= effective_from:
                selected = prices
        if selected is not None:
            table[model] = dict(selected)
    return table


def price_period_key(provider_id, observed_at=None, histories=None):
    """Return the bounded cache bucket containing one observation time."""

    timestamp = observed_timestamp(observed_at)
    if timestamp is None:
        return "current"
    cutovers = {
        effective_from
        for periods in BUILTIN_MODEL_PRICE_HISTORY.get(provider_id, {}).values()
        for effective_from, _prices in periods
        if effective_from is not None
    }
    for periods in (histories or {}).values():
        cutovers.update(
            revision["effective_from"]
            for revision in periods
            if revision["effective_from"] is not None
        )
    return max((value for value in cutovers if value <= timestamp), default=0)


def revision_at(periods, observed_at=None, now=None):
    timestamp = observed_timestamp(observed_at)
    if timestamp is None:
        timestamp = time.time() if now is None else now
    selected = None
    for revision in periods or ():
        effective_from = revision["effective_from"]
        if effective_from is None or effective_from <= timestamp:
            selected = revision
        else:
            break
    return selected


def effective_price_table(provider_id, observed_at=None, histories=None, now=None):
    """Apply validated user history to one canonical provider's bundled table."""

    table = builtin_price_table(provider_id, observed_at)
    if provider_id not in MODEL_PROVIDER_IDS:
        return table
    for model, periods in (histories or {}).items():
        revision = revision_at(periods, observed_at, now=now)
        if not revision or revision.get("use_builtin") is True:
            continue
        if revision.get("inactive") is True:
            table.pop(model, None)
        else:
            table[model] = dict(revision["prices"])
    return table


def matching_price(model_id, table):
    """Return the longest matching catalog rule and its price table row."""

    if model_id in table:
        return model_id, table[model_id]
    compact = str(model_id or "").replace(" ", "-").lower()
    for rule in sorted(table, key=len, reverse=True):
        if compact.startswith(str(rule).replace(" ", "-").lower()):
            return rule, table[rule]
    return None, None


def quote_for(query, effective_table=None):
    """Return an exact provider-scoped quote; never search another provider."""

    provider_id = query.model.provider_id
    if provider_id not in MODEL_PROVIDER_IDS:
        return PriceQuote.unavailable(query.model)
    table = (
        builtin_price_table(provider_id, query.observed_at)
        if effective_table is None else effective_table
    )
    model_id = query.model.model_id
    compact_model_id = str(model_id).replace(" ", "-").lower()
    if provider_id == "cursor" and compact_model_id.startswith("composer-2.5"):
        model_id = "composer-2.5-{}".format(query.model.variant or "")
    matched_rule, prices = matching_price(model_id, table)
    if prices is None:
        return PriceQuote.unavailable(query.model)
    if any(field not in prices for field in MODEL_PRICE_FIELDS):
        return PriceQuote.unavailable(query.model)
    return PriceQuote(
        model=query.model,
        input_per_million=float(prices["input"]),
        output_per_million=float(prices["output"]),
        cache_read_per_million=float(prices["cache_read"]),
        cache_write_per_million=float(prices["cache_write"]),
        basis=EvidenceBasis.ESTIMATED,
        matched_rule=matched_rule,
    )
