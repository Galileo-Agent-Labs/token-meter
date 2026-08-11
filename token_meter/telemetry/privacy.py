"""Deny-by-default privacy projection for optional telemetry interoperability."""

import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from token_meter.contracts import EvidenceBasis, EvidenceValue, NormalizedSession


MAX_COUNT = (1 << 63) - 1
MAX_DURATION_SECONDS = 366 * 24 * 60 * 60
SAFE_TOOL_CATEGORIES = frozenset((
    "browser", "database", "execution", "filesystem", "other", "retrieval",
    "search", "shell", "tool",
))
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,159}$")


@dataclass(frozen=True)
class TelemetryAggregate:
    runtime_id: str
    model_provider_id: str
    model_id: str
    usage: Mapping[str, int]
    usage_basis: Mapping[str, str]
    duration_seconds: float
    duration_basis: str
    tool_categories: Mapping[str, int]
    os_family: str
    token_meter_version: str

    def __post_init__(self):
        object.__setattr__(self, "usage", MappingProxyType(dict(self.usage)))
        object.__setattr__(self, "usage_basis", MappingProxyType(dict(self.usage_basis)))
        object.__setattr__(
            self, "tool_categories", MappingProxyType(dict(self.tool_categories))
        )


def _field(value, name, default=None):
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _identifier(value, fallback=""):
    value = str(value or "").strip()
    return value if _SAFE_IDENTIFIER.fullmatch(value) else fallback


def _basis(value):
    if isinstance(value, EvidenceBasis):
        return value.value
    value = str(value or "").lower()
    return value if value in {basis.value for basis in EvidenceBasis} else ""


def _evidence(value):
    if isinstance(value, EvidenceValue):
        raw, basis = value.value, value.basis.value
    elif isinstance(value, Mapping):
        raw, basis = value.get("value"), _basis(value.get("basis"))
    else:
        raw, basis = value, "measured" if value is not None else ""
    if basis == EvidenceBasis.UNAVAILABLE.value or raw is None:
        return None, ""
    if isinstance(raw, bool):
        return None, ""
    try:
        number = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None, ""
    if number < 0 or number > MAX_COUNT:
        return None, ""
    return number, basis


def _duration(value):
    if isinstance(value, EvidenceValue):
        raw, basis = value.value, value.basis.value
    elif isinstance(value, Mapping):
        raw, basis = value.get("value"), _basis(value.get("basis"))
    else:
        raw, basis = value, "measured" if value is not None else ""
    if basis == EvidenceBasis.UNAVAILABLE.value or raw is None:
        return 0.0, ""
    if isinstance(raw, bool):
        return 0.0, ""
    try:
        seconds = float(raw)
    except (TypeError, ValueError, OverflowError):
        return 0.0, ""
    if not math.isfinite(seconds) or not 0 <= seconds <= MAX_DURATION_SECONDS:
        return 0.0, ""
    return seconds, basis


def _candidate_values(candidate):
    if isinstance(candidate, NormalizedSession):
        source = candidate.source
        model = source.model_ref
        return {
            "runtime_id": source.runtime_id,
            "model_provider_id": model.provider_id if model else "",
            "model_id": model.model_id if model else "",
            "usage": candidate.usage,
            "timing": candidate.timing,
            "tools": candidate.tools,
        }
    if isinstance(candidate, Mapping):
        model = candidate.get("model") or {}
        return {
            "runtime_id": candidate.get("runtime_id"),
            "model_provider_id": _field(model, "provider_id", candidate.get("model_provider_id")),
            "model_id": _field(model, "model_id", candidate.get("model_id")),
            "usage": candidate.get("usage") or {},
            "timing": candidate.get("timing") or {},
            "tools": candidate.get("tools") or (),
        }
    raise TypeError("telemetry projection requires normalized aggregate evidence")


def project_aggregate(candidate, *, os_family="unknown", token_meter_version="unknown"):
    """Return an immutable allowlisted aggregate; all unknown/content fields vanish."""
    values = _candidate_values(candidate)
    usage_source = values["usage"]
    usage = {}
    usage_basis = {}
    for public_name, source_name in (
        ("input", "input_tokens"),
        ("output", "output_tokens"),
        ("cache_read", "cache_read_tokens"),
        ("cache_write", "cache_write_tokens"),
    ):
        value, basis = _evidence(_field(usage_source, source_name))
        if value is not None:
            usage[public_name] = value
            if basis:
                usage_basis[public_name] = basis

    timing_source = values["timing"]
    duration, duration_basis = _duration(
        _field(timing_source, "active_seconds", _field(timing_source, "duration_seconds"))
    )
    categories = {}
    for tool in tuple(values["tools"] or ())[:2000]:
        category = str(_field(tool, "category", "") or "").lower()
        if category in SAFE_TOOL_CATEGORIES:
            categories[category] = min(MAX_COUNT, categories.get(category, 0) + 1)

    return TelemetryAggregate(
        runtime_id=_identifier(values["runtime_id"], "unknown-runtime"),
        model_provider_id=_identifier(values["model_provider_id"]),
        model_id=_identifier(values["model_id"]),
        usage=usage,
        usage_basis=usage_basis,
        duration_seconds=duration,
        duration_basis=duration_basis,
        tool_categories=categories,
        os_family=_identifier(os_family, "unknown"),
        token_meter_version=_identifier(token_meter_version, "unknown"),
    )
