"""Stable interface implemented by runtime evidence adapters."""

from typing import Iterable, Protocol

from token_meter.contracts import (
    DeletionPlan,
    DetailLevel,
    DiscoveryContext,
    NormalizedSession,
    RuntimeDescriptor,
    SessionSource,
    SourceRevision,
)


class RuntimeAdapter(Protocol):
    descriptor: RuntimeDescriptor

    def discover(self, context: DiscoveryContext) -> Iterable[SessionSource]:
        ...

    def current_revision(self, source: SessionSource) -> SourceRevision:
        ...

    def load(
        self, source: SessionSource, detail: DetailLevel
    ) -> NormalizedSession:
        ...

    def deletion_plan(self, source: SessionSource) -> DeletionPlan:
        ...

