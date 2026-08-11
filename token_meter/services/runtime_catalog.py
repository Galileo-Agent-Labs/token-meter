"""Bounded, inert presentation metadata for registered runtimes."""

import re

from token_meter.contracts import RuntimeDescriptor


ALLOWED_CAPABILITIES = frozenset(("sessions", "models", "tools", "quota"))
MAX_RUNTIME_CATALOG_ENTRIES = 16
UNKNOWN_RUNTIME_DESCRIPTOR = RuntimeDescriptor(
    "unknown-runtime",
    "Unknown Runtime",
    frozenset(("sessions",)),
    "runtime.generic",
    "runtime-neutral",
)
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+()/\-]{0,63}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")


def _entry(descriptor):
    if not isinstance(descriptor, RuntimeDescriptor):
        raise TypeError("runtime catalog entries require RuntimeDescriptor values")
    label = descriptor.label
    if not _SAFE_LABEL.fullmatch(label):
        raise ValueError("runtime label contains unsafe presentation text")
    if "://" in label.lower() or "javascript:" in label.lower():
        raise ValueError("runtime label must not contain a URL or executable scheme")
    if not _SAFE_TOKEN.fullmatch(descriptor.runtime_id):
        raise ValueError("runtime id is unsafe for client presentation")
    if not _SAFE_TOKEN.fullmatch(descriptor.symbol):
        raise ValueError("runtime symbol is unsafe for client presentation")
    if not _SAFE_TOKEN.fullmatch(descriptor.color):
        raise ValueError("runtime color is unsafe for client presentation")
    unknown = descriptor.capabilities - ALLOWED_CAPABILITIES
    if unknown:
        raise ValueError("runtime catalog contains an unknown capability")
    return {
        "label": label,
        "symbol": descriptor.symbol,
        "color": descriptor.color,
        "capabilities": sorted(descriptor.capabilities),
    }


def runtime_catalog(descriptors, include_unknown=True,
                    limit=MAX_RUNTIME_CATALOG_ENTRIES):
    """Project trusted descriptors into a small JSON-safe client catalog."""
    limit = max(1, min(MAX_RUNTIME_CATALOG_ENTRIES, int(limit)))
    ordered = list(descriptors)
    if include_unknown and all(
            descriptor.runtime_id != UNKNOWN_RUNTIME_DESCRIPTOR.runtime_id
            for descriptor in ordered):
        ordered.append(UNKNOWN_RUNTIME_DESCRIPTOR)
    if len(ordered) > limit:
        raise ValueError("runtime catalog exceeds its entry bound")
    result = {}
    for descriptor in ordered:
        if descriptor.runtime_id in result:
            raise ValueError("runtime catalog contains a duplicate runtime")
        result[descriptor.runtime_id] = _entry(descriptor)
    return result


def menubar_runtime_catalog(descriptors):
    """Return the same inert subset required by native recent-session UI."""
    return runtime_catalog(descriptors)
