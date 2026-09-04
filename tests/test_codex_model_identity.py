import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter


class CodexModelIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.trace = self.root / "rollout-auto-review.jsonl"
        self.trace.write_text('{"local":"trace"}\n', encoding="utf-8")
        self.identity_path = self.root / "session-model-identities.json"

    def tearDown(self):
        self.temp.cleanup()

    def source(self, model="unknown-model", **overrides):
        source = {
            "provider": "codex",
            "label": "Codex",
            "id": "logical-session-id",
            "session": self.trace.name,
            "path": str(self.trace),
            "project": "~/private-project",
            "mtime": self.trace.stat().st_mtime,
            "physical_trace_id": "physical-child-id",
            "parent_thread_id": "physical-parent-id",
            "observed_model": "codex-auto-review",
            "model": model,
            "model_provider": "openai",
        }
        source.update(overrides)
        return source

    def verified_evidence(self):
        mtime_ns, size = meter.file_signature(str(self.trace))
        return {
            "model": "gpt-5.6-sol",
            "model_provider": "openai",
            "revision": [str(mtime_ns), str(size)],
            "parent_id": "physical-parent-id",
            "inherited_prefix": 1,
        }

    def test_verified_parent_identity_survives_parent_disappearance_at_same_revision(self):
        source = self.source(model="gpt-5.6-sol")
        with mock.patch.object(
            meter, "codex_auto_review_evidence", return_value=self.verified_evidence(),
        ):
            live = meter.enrich_codex_model_identities(
                [source], path=str(self.identity_path),
            )

        self.assertEqual(live[0]["model"], "gpt-5.6-sol")
        self.assertEqual(live[0]["model_identity"]["source"], "verified_parent")
        self.assertEqual(live[0]["_verified_inherited_prefix"], 1)
        stored = json.loads(self.identity_path.read_text(encoding="utf-8"))
        encoded = json.dumps(stored)
        self.assertNotIn("physical-child-id", encoded)
        self.assertNotIn("physical-parent-id", encoded)
        self.assertNotIn("model_pricing", encoded)

        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            retained = meter.enrich_codex_model_identities(
                [self.source()], path=str(self.identity_path),
            )

        self.assertEqual(retained[0]["model"], "gpt-5.6-sol")
        self.assertEqual(retained[0]["model_identity"]["source"], "verified_history")
        self.assertEqual(retained[0]["_verified_inherited_prefix"], 1)

        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            cyclic = meter.enrich_codex_model_identities(
                [self.source(model_resolution_reason="cyclic")],
                path=str(self.identity_path),
            )
        self.assertEqual(cyclic[0]["model"], "unknown-model")
        self.assertEqual(cyclic[0]["model_identity"]["source"], "unresolved")

    def test_verified_parent_identity_fails_closed_when_child_trace_changes(self):
        with mock.patch.object(
            meter, "codex_auto_review_evidence", return_value=self.verified_evidence(),
        ):
            meter.enrich_codex_model_identities(
                [self.source(model="gpt-5.6-sol")], path=str(self.identity_path),
            )
        with self.trace.open("a", encoding="utf-8") as handle:
            handle.write('{"new":"local evidence"}\n')

        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            changed = meter.enrich_codex_model_identities(
                [self.source()], path=str(self.identity_path),
            )

        self.assertEqual(changed[0]["model"], "unknown-model")
        self.assertEqual(changed[0]["model_identity"]["source"], "unresolved")
        self.assertTrue(changed[0]["model_identity"]["assignable"])

    def test_user_assignment_is_scoped_to_one_session_and_does_not_change_pricing(self):
        settings_path = self.root / "settings.json"
        settings_path.write_text(
            json.dumps({"model_pricing": {"codex": {"saved-custom": []}}}),
            encoding="utf-8",
        )
        before = settings_path.read_text(encoding="utf-8")
        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            unresolved = meter.enrich_codex_model_identities(
                [self.source()], path=str(self.identity_path),
            )
        key = unresolved[0]["model_identity"]["key"]

        assigned = meter.set_session_model_identity(
            key,
            model="gpt-5.6-sol",
            sources=unresolved,
            path=str(self.identity_path),
        )

        self.assertTrue(assigned["ok"])
        self.assertTrue(assigned["changed"])
        self.assertEqual(settings_path.read_text(encoding="utf-8"), before)
        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            applied = meter.enrich_codex_model_identities(
                [self.source()], path=str(self.identity_path),
            )
        self.assertEqual(applied[0]["model"], "gpt-5.6-sol")
        self.assertEqual(applied[0]["model_identity"]["source"], "user_assigned")

        removed = meter.set_session_model_identity(
            key,
            remove=True,
            sources=applied,
            path=str(self.identity_path),
        )
        self.assertTrue(removed["ok"])
        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            cleared = meter.enrich_codex_model_identities(
                [self.source()], path=str(self.identity_path),
            )
        self.assertEqual(cleared[0]["model"], "unknown-model")
        self.assertEqual(cleared[0]["model_identity"]["source"], "unresolved")

    def test_public_identity_projection_is_opaque_and_content_free(self):
        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            source = meter.enrich_codex_model_identities(
                [self.source()], path=str(self.identity_path),
            )[0]
        row = meter.summary_row(
            source, "Safe title", 0.0, 0, 1, {"unknown-model"},
            None, None, {}, {}, {}, True,
        )
        identity = row["model_identity"]

        self.assertEqual(identity["kind"], "auto_review")
        self.assertEqual(identity["source"], "unresolved")
        self.assertTrue(identity["assignable"])
        self.assertRegex(identity["key"], r"^[a-f0-9]{64}$")
        encoded = json.dumps(identity)
        self.assertNotIn("physical-child-id", encoded)
        self.assertNotIn("physical-parent-id", encoded)
        self.assertNotIn(str(self.trace), encoded)

    def test_assignment_rejects_a_raw_trace_identity(self):
        result = meter.set_session_model_identity(
            "physical-child-id", model="gpt-5.6-sol", sources=[],
            path=str(self.identity_path),
        )

        self.assertFalse(result["ok"])

    def test_local_action_route_accepts_only_the_opaque_session_key(self):
        handler = object.__new__(meter.H)
        handler.path = "/settings/session-model-identity"
        handler.headers = {
            "Content-Type": "application/json",
            "Content-Length": "115",
            "X-Token-Meter-Action": meter._ACTION_TOKEN,
            "Origin": "http://127.0.0.1:8722",
        }
        key = "a" * 64
        body = json.dumps({
            "session_key": key,
            "model": "gpt-5.6-sol",
            "provider": "codex",
        }).encode("utf-8")
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler._send = mock.Mock()
        handler.send_error = mock.Mock()
        with (
            mock.patch.object(
                meter, "set_session_model_identity",
                return_value={"ok": True, "changed": True, "session_key": key},
            ) as set_identity,
            mock.patch.object(meter, "newest_source", return_value=None),
            mock.patch.object(meter, "refresh_cross_session_state", return_value={}),
            mock.patch.object(meter, "STATE", {}),
        ):
            handler.do_POST()

        set_identity.assert_called_once_with(
            key, model="gpt-5.6-sol", provider="codex", remove=False,
        )
        self.assertFalse(handler.send_error.called)
        payload = json.loads(handler._send.call_args.args[0])
        self.assertTrue(payload["ok"])


class CodexModelIdentityDashboardContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (Path(__file__).resolve().parents[1] / "page.html").read_text(
            encoding="utf-8"
        )

    def test_all_sessions_offers_a_scoped_identity_assignment(self):
        for marker in (
            "session-model-identity-dialog",
            "/settings/session-model-identity",
            "Assign model",
            "This affects only this saved session",
            "does not change model prices",
        ):
            self.assertIn(marker, self.page)


if __name__ == "__main__":
    unittest.main()
