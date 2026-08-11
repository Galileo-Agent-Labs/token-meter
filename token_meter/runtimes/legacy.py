"""Temporary callable adapter used while legacy parsers move into the package."""

from typing import Any, Callable, Optional

from token_meter.contracts import (
    DeletionPlan,
    DetailLevel,
    RuntimeDescriptor,
    SessionSource,
    SourceRevision,
)


class LegacyRuntimeAdapter:
    """Expose an existing parser through the registry without changing output.

    This adapter is intentionally transitional. Discovery and normalized loads
    are implemented by each native runtime adapter in later migration tasks.
    """

    def __init__(
        self,
        descriptor: RuntimeDescriptor,
        loader: Callable[[Any], Any],
        discoverer: Optional[Callable[[Any], Any]] = None,
    ):
        self.descriptor = descriptor
        self._loader = loader
        self._discoverer = discoverer or (lambda context: ())

    def discover(self, context):
        return ()

    def discover_legacy(self, context):
        return self._discoverer(context)

    def current_revision(self, source):
        if isinstance(source, SessionSource):
            return source.revision
        signature = source.get("signature_mtime") or source.get("mtime") or 0
        title = source.get("title") or ""
        return SourceRevision((str(signature), str(title)))

    def load(self, source, detail: DetailLevel):
        return self._loader(source)

    def deletion_plan(self, source):
        return DeletionPlan.deny("legacy adapter deletion is handled by the facade")
