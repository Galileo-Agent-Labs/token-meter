import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import (
    DeletionPlan,
    DiscoveryContext,
    ModelRef,
    RuntimeDescriptor,
    SessionSource,
    SourceLocator,
    SourceRevision,
)
from token_meter.runtimes.registry import RuntimeRegistry
from token_meter.services.application import Application
from token_meter.services.sessions import SessionService


class SyntheticAdapter:
    descriptor = RuntimeDescriptor(
        "synthetic", "Synthetic", frozenset(("sessions",)),
        "runtime.generic", "runtime-neutral",
    )

    def discover(self, context):
        return (SessionSource(
            "synthetic", "synthetic", "session-1", "Synthetic",
            None, SourceLocator("memory", "private"), 1.0,
            SourceRevision(("one",)),
            ModelRef("synthetic-models", "model-1"),
        ),)

    def current_revision(self, source):
        return source.revision

    def load(self, source, detail):
        return {"source": source, "detail": detail}

    def deletion_plan(self, source):
        return DeletionPlan.deny("read only")


class ApplicationCompositionTests(unittest.TestCase):
    def test_application_accepts_a_synthetic_runtime_registry(self):
        sessions = SessionService(
            RuntimeRegistry((SyntheticAdapter(),)),
            lambda: DiscoveryContext(home="/private/test-home"),
        )
        application = Application(
            sessions=sessions,
            settings=mock.Mock(), budgets=mock.Mock(), capabilities=mock.Mock(),
            updates=mock.Mock(), deletion=mock.Mock(), menubar=mock.Mock(),
            agent_api=mock.Mock(), current_state=lambda: {"ok": True},
            cross_session=lambda: {"sessions": 1}, health=lambda: ({"ok": True}, 200),
        )

        discovered = application.sessions.discover()

        self.assertEqual([source.runtime_id for source in discovered.sources], ["synthetic"])
        self.assertEqual(application.current_state(), {"ok": True})
        self.assertEqual(application.health(), ({"ok": True}, 200))

    def test_root_meter_is_a_small_mutable_compatibility_facade(self):
        root = Path(__file__).resolve().parents[2]
        lines = [line for line in (root / "meter.py").read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]

        self.assertLessEqual(len(lines), 25)
        self.assertEqual(meter.__name__, "token_meter.app")
        with mock.patch.object(meter, "PORT", 9999):
            self.assertEqual(meter.PORT, 9999)

    def test_default_application_exposes_all_transport_services(self):
        application = meter.application()

        self.assertIs(application.sessions.registry, meter.runtime_registry())
        self.assertTrue(callable(application.agent_api.check))
        self.assertTrue(callable(application.agent_api.sessions))
        self.assertTrue(callable(application.agent_api.trace))
        self.assertTrue(callable(application.agent_api.stats))
        self.assertTrue(callable(application.agent_api.schema))
        self.assertTrue(callable(application.menubar.state))


if __name__ == "__main__":
    unittest.main()
