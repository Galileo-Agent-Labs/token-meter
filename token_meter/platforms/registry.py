"""Deterministic host-platform selection."""

import os
import sys

from .linux import LinuxPlatformServices
from .macos import MacOSPlatformServices
from .unsupported import UnsupportedPlatformServices
from .windows import WindowsPlatformServices


def platform_services(platform_name=None, environment=None, home=None):
    platform_name = str(platform_name if platform_name is not None else sys.platform).lower()
    environment = os.environ if environment is None else environment
    if platform_name in ("darwin", "macos"):
        return MacOSPlatformServices(environment, home)
    if platform_name.startswith("linux"):
        return LinuxPlatformServices(environment, home)
    if platform_name in ("win32", "windows", "nt") or platform_name.startswith("cygwin"):
        return WindowsPlatformServices(environment, home)
    return UnsupportedPlatformServices(platform_name)
