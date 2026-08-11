"""Operating-system paths, process behavior, and safe trash planning."""

from .base import (
    PlatformCapabilities,
    PlatformPaths,
    PlatformServices,
    ProcessOptions,
    ProcessPurpose,
    TrashPlan,
    UpdatePlan,
)
from .registry import platform_services

__all__ = (
    "PlatformCapabilities",
    "PlatformPaths",
    "PlatformServices",
    "ProcessOptions",
    "ProcessPurpose",
    "TrashPlan",
    "UpdatePlan",
    "platform_services",
)
