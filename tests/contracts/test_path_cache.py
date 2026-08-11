import unittest
from unittest import mock

from token_meter.runtimes.path_cache import BoundedPathCache


class BoundedPathCacheTests(unittest.TestCase):
    def test_reuses_recursive_enumeration_within_ttl_and_refreshes_afterward(self):
        cache = BoundedPathCache(ttl_seconds=2.0, max_entries=4)

        with mock.patch(
            "token_meter.runtimes.path_cache.glob.glob", return_value=["first"]
        ) as globber:
            self.assertEqual(
                cache.paths("/root/**/trace.log", recursive=True, now=10.0),
                ("first",),
            )
            globber.return_value = ["second"]
            self.assertEqual(
                cache.paths("/root/**/trace.log", recursive=True, now=11.9),
                ("first",),
            )
            self.assertEqual(
                cache.paths("/root/**/trace.log", recursive=True, now=12.1),
                ("second",),
            )

        self.assertEqual(globber.call_count, 2)

    def test_cache_is_bounded_and_returns_immutable_path_snapshots(self):
        cache = BoundedPathCache(ttl_seconds=10.0, max_entries=2)

        with mock.patch(
            "token_meter.runtimes.path_cache.glob.glob", side_effect=[["a"], ["b"], ["c"]]
        ):
            first = cache.paths("a", now=1.0)
            cache.paths("b", now=1.0)
            cache.paths("c", now=1.0)

        self.assertEqual(first, ("a",))
        self.assertEqual(cache.entry_count, 2)


if __name__ == "__main__":
    unittest.main()
