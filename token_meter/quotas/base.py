"""Small dependency-free quota adapter contract."""

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Protocol


class QuotaUnavailable(Exception):
    """A bounded user-facing reason that provider quota evidence is unavailable."""


def _identifier(value, field_name):
    value = str(value or "").strip()
    if not value or len(value) > 80:
        raise ValueError("{} must be between 1 and 80 characters".format(field_name))
    return value


class QuotaAdapter(Protocol):
    account_provider_id: str
    public_id: str
    label: str

    def load(self, now: Optional[float] = None) -> Mapping[str, object]:
        ...


@dataclass(frozen=True)
class CallableQuotaAdapter:
    """Compatibility adapter around one bounded provider loader."""

    account_provider_id: str
    public_id: str
    label: str
    loader: Callable[..., Mapping[str, object]]

    def __post_init__(self):
        object.__setattr__(
            self,
            "account_provider_id",
            _identifier(self.account_provider_id, "account_provider_id"),
        )
        object.__setattr__(self, "public_id", _identifier(self.public_id, "public_id"))
        label = " ".join(str(self.label or "").split())
        if not label or len(label) > 80:
            raise ValueError("label must be between 1 and 80 characters")
        object.__setattr__(self, "label", label)
        if not callable(self.loader):
            raise ValueError("loader must be callable")

    def load(self, now=None):
        return self.loader(now=now)
