import unittest


class MCPQueryContractTests(unittest.TestCase):
    def test_cursor_is_bound_to_query_and_revision(self):
        from token_meter.mcp.contracts import (
            MCPQueryError,
            make_cursor,
            read_cursor,
        )

        cursor = make_cursor(7, {"runtime": "codex"}, ("rev-1",))

        self.assertEqual(
            read_cursor(cursor, {"runtime": "codex"}, ("rev-1",)),
            7,
        )
        with self.assertRaises(MCPQueryError) as raised:
            read_cursor(cursor, {"runtime": "codex"}, ("rev-2",))
        self.assertEqual(raised.exception.code, "stale_cursor")

    def test_cursor_rejects_a_different_query_and_malformed_value(self):
        from token_meter.mcp.contracts import (
            MCPQueryError,
            make_cursor,
            read_cursor,
        )

        cursor = make_cursor(1, {"runtime": "codex"}, ("rev-1",))

        for value, query in (
            (cursor, {"runtime": "claude"}),
            ("not-a-cursor", {"runtime": "codex"}),
        ):
            with self.subTest(value=value, query=query):
                with self.assertRaises(MCPQueryError) as raised:
                    read_cursor(value, query, ("rev-1",))
                self.assertEqual(raised.exception.code, "invalid_argument")

    def test_bounded_page_returns_complete_prefix_and_cursor(self):
        from token_meter.mcp.contracts import bounded_page, read_cursor

        query = {"view": "standardized"}
        revision = ("rev",)
        page = bounded_page(
            [
                {"value": "x" * 80},
                {"value": "y" * 80},
                {"value": "z" * 80},
            ],
            offset=0,
            limit=2,
            query=query,
            revision=revision,
            max_bytes=400,
        )

        self.assertEqual(len(page["items"]), 1)
        self.assertTrue(page["truncated"])
        self.assertEqual(read_cursor(
            page["next_cursor"], query, revision,
        ), 1)

    def test_limits_and_string_lists_are_strictly_bounded(self):
        from token_meter.mcp.contracts import (
            MCPQueryError,
            normalize_limit,
            normalize_string_list,
        )

        self.assertEqual(normalize_limit(None, 20, 100), 20)
        self.assertEqual(
            normalize_string_list(
                ["events", "tools"], "sections",
                {"events", "tools", "context"}, 3,
            ),
            ("events", "tools"),
        )
        for value in (0, 101, "many"):
            with self.subTest(limit=value):
                with self.assertRaises(MCPQueryError) as raised:
                    normalize_limit(value, 20, 100)
                self.assertEqual(raised.exception.code, "invalid_argument")
        with self.assertRaises(MCPQueryError):
            normalize_string_list(
                ["events", "events"], "sections", {"events"}, 3,
            )


if __name__ == "__main__":
    unittest.main()
