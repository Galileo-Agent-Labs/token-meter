import unittest
from unittest import mock

import meter


class DuplicateSessionSelectionTests(unittest.TestCase):
    @staticmethod
    def source(name, *, mtime):
        return {
            "provider": "codex",
            "id": "shared-session",
            "session": f"{name}.jsonl",
            "path": f"/private/sanitized/{name}.jsonl",
            "mtime": float(mtime),
        }

    def test_logical_id_prefers_active_segment_regardless_of_discovery_order(self):
        completed_old = self.source("completed-old", mtime=100)
        active = self.source("active", mtime=90)
        completed_new = self.source("completed-new", mtime=110)
        terminal = {
            completed_old["path"]: True,
            active["path"]: False,
            completed_new["path"]: True,
        }

        with mock.patch.object(
            meter,
            "session_summary",
            side_effect=lambda source: {"terminal": terminal[source["path"]]},
        ):
            forward = meter.find_session(
                "shared-session", [completed_old, active, completed_new]
            )
            reverse = meter.find_session(
                "shared-session", [completed_new, active, completed_old]
            )

        self.assertIs(forward, active)
        self.assertIs(reverse, active)

    def test_logical_id_uses_newest_segment_when_all_are_terminal(self):
        older = self.source("older", mtime=100)
        newer = self.source("newer", mtime=120)

        with mock.patch.object(
            meter, "session_summary", return_value={"terminal": True}
        ):
            found = meter.find_session("shared-session", [older, newer])

        self.assertIs(found, newer)

    def test_exact_physical_alias_remains_exact(self):
        older = self.source("older", mtime=100)
        active = self.source("active", mtime=120)

        with mock.patch.object(meter, "session_summary") as summarize:
            found = meter.find_session("older.jsonl", [active, older])

        self.assertIs(found, older)
        summarize.assert_not_called()

    def test_single_logical_match_keeps_the_fast_path(self):
        only = self.source("only", mtime=100)

        with mock.patch.object(meter, "session_summary") as summarize:
            found = meter.find_session("shared-session", [only])

        self.assertIs(found, only)
        summarize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
