"""Provider-account quota adapters and explicit registry."""

from .base import CallableQuotaAdapter, QuotaAdapter, QuotaUnavailable
from .registry import QuotaRegistry

__all__ = ("CallableQuotaAdapter", "QuotaAdapter", "QuotaRegistry", "QuotaUnavailable")
