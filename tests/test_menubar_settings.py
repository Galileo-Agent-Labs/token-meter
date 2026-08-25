from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MenubarSettingsActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "menubar" / "TokenMeterMenuBar.swift").read_text()

    def test_settings_quick_action_opens_dashboard_and_keeps_native_preferences_reachable(self):
        action = self.source[
            self.source.index("    @objc private func performQuickAction"):
            self.source.index("    private func persistPinnedSession")
        ]
        self.assertIn("case .settings:\n                self.openSettings()", action)
        self.assertNotIn("makeSettingsMenu().popUp", action)

        rebuild = self.source[
            self.source.index("    private func rebuildMenu()"):
            self.source.index("    private func addQuickActions()")
        ]
        self.assertIn('NSMenuItem(title: "Menu bar settings", action: nil', rebuild)
        self.assertIn("settingsItem.submenu = makeSettingsMenu()", rebuild)


if __name__ == "__main__":
    unittest.main()
