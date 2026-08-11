"""Pure OpenTelemetry-shaped mapping pinned to GenAI conventions 1.42.0."""

from token_meter.telemetry.privacy import TelemetryAggregate


OTEL_GENAI_SEMCONV_VERSION = "1.42.0"
OTEL_GENAI_SCHEMA_URL = "https://opentelemetry.io/schemas/gen-ai/1.42.0"


def _base_attributes(value):
    attributes = {
        "gen_ai.operation.name": "chat",
        "token_meter.runtime.id": value.runtime_id,
        "os.type": value.os_family,
        "service.version": value.token_meter_version,
    }
    if value.model_provider_id:
        attributes["gen_ai.provider.name"] = value.model_provider_id
    if value.model_id:
        attributes["gen_ai.request.model"] = value.model_id
    return attributes


def map_to_otel(value):
    """Map only a privacy-projected aggregate; this function performs no I/O."""
    if not isinstance(value, TelemetryAggregate):
        raise TypeError("OTel mapping accepts only TelemetryAggregate")
    base = _base_attributes(value)
    metrics = []
    for token_type in ("input", "output"):
        if token_type not in value.usage:
            continue
        attributes = {**base, "gen_ai.token.type": token_type}
        basis = value.usage_basis.get(token_type)
        if basis:
            attributes["token_meter.evidence.basis"] = basis
        metrics.append({
            "name": "gen_ai.client.token.usage",
            "unit": "{token}",
            "value": value.usage[token_type],
            "attributes": attributes,
        })
    for cache_kind in ("cache_read", "cache_write"):
        if cache_kind not in value.usage:
            continue
        attributes = {**base, "token_meter.cache.kind": cache_kind}
        basis = value.usage_basis.get(cache_kind)
        if basis:
            attributes["token_meter.evidence.basis"] = basis
        metrics.append({
            "name": "token_meter.cache.token.usage",
            "unit": "{token}",
            "value": value.usage[cache_kind],
            "attributes": attributes,
        })
    if value.duration_basis:
        metrics.append({
            "name": "token_meter.session.active_duration",
            "unit": "s",
            "value": value.duration_seconds,
            "attributes": {
                **base,
                "token_meter.evidence.basis": value.duration_basis,
            },
        })
    for category, count in sorted(value.tool_categories.items()):
        metrics.append({
            "name": "token_meter.tool.call.count",
            "unit": "{call}",
            "value": count,
            "attributes": {**base, "token_meter.tool.category": category},
        })
    return {
        "schema_url": OTEL_GENAI_SCHEMA_URL,
        "convention_version": OTEL_GENAI_SEMCONV_VERSION,
        "metrics": metrics,
    }
