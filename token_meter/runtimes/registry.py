"""Deterministic registry and failure isolation for runtime adapters."""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Tuple

from token_meter.contracts import (
    AdapterFailure,
    DeletionPlan,
    DetailLevel,
    DiscoveryContext,
    DiscoveryResult,
    NormalizedSession,
    RuntimeDescriptor,
    SessionSource,
    SourceRevision,
)
from token_meter.runtimes.base import RuntimeAdapter


@dataclass(frozen=True)
class LegacyDiscoveryResult:
    sources: Tuple[Mapping[str, Any], ...]
    failures: Tuple[AdapterFailure, ...]

    def to_public_dict(self):
        return {
            "source_count": len(self.sources),
            "failures": [failure.to_public_dict() for failure in self.failures],
        }


@dataclass(frozen=True)
class LegacyLoadResult:
    value: Any
    failure: Optional[AdapterFailure]

    def to_public_dict(self):
        return {
            "ok": self.failure is None,
            "failure": self.failure.to_public_dict() if self.failure else None,
        }


class RuntimeRegistry:
    """An explicit, ordered collection of trusted in-repository adapters."""

    def __init__(self, adapters: Iterable[RuntimeAdapter]):
        ordered = []
        by_id = {}
        for adapter in adapters:
            descriptor = getattr(adapter, "descriptor", None)
            if not isinstance(descriptor, RuntimeDescriptor):
                raise TypeError("runtime adapter must expose a RuntimeDescriptor")
            runtime_id = descriptor.runtime_id
            if runtime_id in by_id:
                raise ValueError("duplicate runtime id: {}".format(runtime_id))
            ordered.append(adapter)
            by_id[runtime_id] = adapter
        self._ordered = tuple(ordered)
        self._by_id = by_id

    @property
    def runtime_ids(self) -> Tuple[str, ...]:
        return tuple(adapter.descriptor.runtime_id for adapter in self._ordered)

    @property
    def descriptors(self) -> Tuple[RuntimeDescriptor, ...]:
        return tuple(adapter.descriptor for adapter in self._ordered)

    def get(self, runtime_id: str) -> Optional[RuntimeAdapter]:
        return self._by_id.get(str(runtime_id or ""))

    def require(self, runtime_id: str) -> RuntimeAdapter:
        adapter = self.get(runtime_id)
        if adapter is None:
            raise KeyError("Unknown runtime: {}".format(str(runtime_id or "")))
        return adapter

    def discover_all(self, context: DiscoveryContext) -> DiscoveryResult:
        sources = []
        failures = []
        for adapter in self._ordered:
            runtime_id = adapter.descriptor.runtime_id
            try:
                discovered = tuple(adapter.discover(context))
                if any(not isinstance(source, SessionSource) for source in discovered):
                    raise TypeError("adapter returned an invalid source")
                if any(source.runtime_id != runtime_id for source in discovered):
                    raise ValueError("adapter returned a source owned by another runtime")
                sources.extend(discovered)
            except Exception:
                failures.append(AdapterFailure(
                    runtime_id=runtime_id,
                    operation="discover",
                    code="adapter_failed",
                ))
        return DiscoveryResult(tuple(sources), tuple(failures))

    def discover_legacy_all(self, context: DiscoveryContext) -> LegacyDiscoveryResult:
        """Discover current dictionary sources while adapters are being migrated."""

        sources = []
        failures = []
        for adapter in self._ordered:
            runtime_id = adapter.descriptor.runtime_id
            try:
                discoverer = getattr(adapter, "discover_legacy")
                discovered = tuple(discoverer(context))
                if any(not isinstance(source, Mapping) for source in discovered):
                    raise TypeError("adapter returned an invalid legacy source")
                if any(source.get("provider") != runtime_id for source in discovered):
                    raise ValueError("adapter returned a source owned by another runtime")
                sources.extend(discovered)
            except Exception:
                failures.append(AdapterFailure(
                    runtime_id=runtime_id,
                    operation="discover",
                    code="adapter_failed",
                ))
        return LegacyDiscoveryResult(tuple(sources), tuple(failures))

    def load(
        self, source: SessionSource, detail: DetailLevel
    ) -> NormalizedSession:
        return self.require(source.runtime_id).load(source, detail)

    def load_for(self, runtime_id: str, source: Any, detail: DetailLevel) -> Any:
        """Load through an explicit runtime during the compatibility migration.

        Current ``meter.py`` sources are dictionaries. This method keeps that
        legacy shape at the facade while the adapters are migrated one by one
        to the normalized ``SessionSource`` contract used by ``load``.
        """

        return self.require(runtime_id).load(source, detail)

    def load_legacy_for(
        self, runtime_id: str, source: Any, detail: DetailLevel
    ) -> LegacyLoadResult:
        try:
            value = self.require(runtime_id).load(source, detail)
            return LegacyLoadResult(value, None)
        except Exception:
            return LegacyLoadResult(None, AdapterFailure(
                runtime_id=str(runtime_id or "unknown"),
                operation="load",
                code="adapter_failed",
            ))

    def current_revision(self, source: SessionSource) -> SourceRevision:
        return self.require(source.runtime_id).current_revision(source)

    def deletion_plan(self, source: SessionSource) -> DeletionPlan:
        return self.require(source.runtime_id).deletion_plan(source)
