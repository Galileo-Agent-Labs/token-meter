"""Explicit compatibility projections between legacy dictionaries and contracts."""

import copy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional

from token_meter.contracts import (
    ModelRef,
    SessionSource,
    SourceLocator,
    SourceRevision,
    session_source_public_dict,
)


@dataclass(frozen=True)
class LegacySourceEnvelope:
    """Normalized identity paired with its private exact legacy representation."""

    normalized: SessionSource
    legacy_fields: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(
            self,
            "legacy_fields",
            MappingProxyType(copy.deepcopy(dict(self.legacy_fields))),
        )


def legacy_source_to_envelope(
    source: Mapping[str, Any], account_provider_id: Optional[str] = None
) -> LegacySourceEnvelope:
    """Normalize identity without exposing or discarding legacy-only fields."""

    if not isinstance(source, Mapping):
        raise TypeError("legacy source must be a mapping")
    runtime_id = str(source.get("provider") or "").strip()
    session_id = str(source.get("id") or source.get("session") or "").strip()
    if not runtime_id or not session_id:
        raise ValueError("legacy source requires provider and session identity")
    path = str(source.get("path") or "{}:{}".format(runtime_id, session_id))
    locator_kind = "virtual" if ":" in path and not path.startswith(("/", "~")) else "file"
    model_id = str(source.get("model") or "").strip()
    model_provider_id = str(
        source.get("model_provider") or source.get("model_provider_id") or ""
    ).strip()
    model_ref = None
    if model_id and model_provider_id:
        model_ref = ModelRef(
            provider_id=model_provider_id,
            model_id=model_id,
            variant=str(source.get("pricing_variant") or "").strip() or None,
        )
    activity_mtime = source.get("mtime") or 0
    revision = SourceRevision((
        str(source.get("signature_mtime") or activity_mtime),
        str(source.get("title") or ""),
    ))
    normalized = SessionSource(
        runtime_id=runtime_id,
        client_id=str(source.get("client") or runtime_id),
        session_id=session_id,
        display_label=str(source.get("label") or runtime_id),
        project=source.get("project"),
        locator=SourceLocator(locator_kind, path),
        activity_mtime=activity_mtime,
        revision=revision,
        model_ref=model_ref,
        account_provider_id=account_provider_id,
    )
    return LegacySourceEnvelope(normalized, source)


def envelope_to_legacy_source(envelope: LegacySourceEnvelope):
    """Return a detached exact copy for current parsers and serializers."""

    if not isinstance(envelope, LegacySourceEnvelope):
        raise TypeError("expected a LegacySourceEnvelope")
    return copy.deepcopy(dict(envelope.legacy_fields))


def public_legacy_source_identity(envelope: LegacySourceEnvelope):
    """Return content-free identity; private locators and extras remain internal."""

    if not isinstance(envelope, LegacySourceEnvelope):
        raise TypeError("expected a LegacySourceEnvelope")
    return session_source_public_dict(envelope.normalized)

