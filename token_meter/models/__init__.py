"""Runtime-neutral model identity and pricing boundaries."""

from .catalog import canonical_model_provider
from .pricing import quote_for

__all__ = ("canonical_model_provider", "quote_for")
