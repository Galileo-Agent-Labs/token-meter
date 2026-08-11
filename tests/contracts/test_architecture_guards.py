import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_IDS = ("claude", "codex", "cursor", "opencode", "kiro")


class ArchitectureGuardTests(unittest.TestCase):
    def test_composition_contains_no_superseded_legacy_function_bodies(self):
        tree = ast.parse((ROOT / "token_meter" / "app.py").read_text(encoding="utf-8"))
        names = [
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_legacy_")
        ]
        self.assertEqual(names, [])

    def test_shared_domain_services_and_web_have_no_runtime_dispatch_conditionals(self):
        paths = [
            *(ROOT / "token_meter" / "domain").glob("*.py"),
            *(ROOT / "token_meter" / "services").glob("*.py"),
            *(ROOT / "token_meter" / "web").glob("*.py"),
        ]
        runtime_pattern = "|".join(map(re.escape, RUNTIME_IDS))
        branch = re.compile(
            r"\b(?:if|elif|case|match)\b[^\n]{0,180}\b(?:" + runtime_pattern + r")\b",
            re.IGNORECASE,
        )
        for path in paths:
            self.assertNotRegex(path.read_text(encoding="utf-8"), branch, path)

    def test_public_boundaries_do_not_serialize_internal_dataclasses_wholesale(self):
        paths = [
            ROOT / "token_meter" / "compat.py",
            *(ROOT / "token_meter" / "services").glob("*.py"),
            *(ROOT / "token_meter" / "web").glob("*.py"),
        ]
        for path in paths:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("dataclasses.asdict", source, path)
            self.assertNotRegex(source, r"\bvars\([^\n]*(?:source|session|locator)", path)
            self.assertNotRegex(source, r"(?:source|session|locator)\.__dict__", path)

    def test_contributor_guide_has_executable_extension_recipes(self):
        guide = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        for heading in (
            "Adding a runtime",
            "Adding a model",
            "Adding a quota adapter",
            "Adding a platform",
            "Adding a telemetry mapping",
        ):
            self.assertIn(heading, guide)
        self.assertIn("python3 -m unittest discover -s tests -v", guide)
        self.assertIn("runtime-manifest.txt", guide)


if __name__ == "__main__":
    unittest.main()
