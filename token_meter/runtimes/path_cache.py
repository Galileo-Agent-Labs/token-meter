"""Short-lived bounded cache for expensive recursive path enumeration."""

import glob
import threading
import time
from collections import OrderedDict


class BoundedPathCache:
    """Cache path snapshots while continuing to inspect known files normally."""

    def __init__(self, ttl_seconds=2.0, max_entries=32):
        ttl_seconds = float(ttl_seconds)
        max_entries = int(max_entries)
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries = OrderedDict()
        self._lock = threading.Lock()

    @property
    def entry_count(self):
        with self._lock:
            return len(self._entries)

    def clear(self):
        with self._lock:
            self._entries.clear()

    def paths(self, pattern, recursive=False, now=None):
        now = time.monotonic() if now is None else float(now)
        key = (str(pattern), bool(recursive))
        with self._lock:
            cached = self._entries.get(key)
            if cached and 0 <= now - cached[0] < self.ttl_seconds:
                self._entries.move_to_end(key)
                return cached[1]

        paths = tuple(glob.glob(key[0], recursive=key[1]))
        with self._lock:
            self._entries[key] = (now, paths)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return paths

