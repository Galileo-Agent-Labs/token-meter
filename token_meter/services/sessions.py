"""Runtime-registry-backed session orchestration."""

from token_meter.contracts import DetailLevel


class SessionService:
    def __init__(self, registry, context_factory):
        self.registry = registry
        self.context_factory = context_factory

    def discover(self):
        return self.registry.discover_all(self.context_factory())

    def discover_legacy(self):
        return self.registry.discover_legacy_all(self.context_factory())

    def load(self, source, detail=DetailLevel.FULL):
        return self.registry.load(source, detail)
