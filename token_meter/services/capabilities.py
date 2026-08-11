"""Capability inventory and mutation service."""


class CapabilityService:
    def __init__(self, inventory, set_enabled):
        self._inventory = inventory
        self._set_enabled = set_enabled

    def inventory(self, waste=None):
        return self._inventory(waste)

    def set_enabled(self, control, enabled):
        return self._set_enabled(control, enabled)
