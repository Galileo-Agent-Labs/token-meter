import shutil
import tempfile
import unittest
from pathlib import Path

from token_meter.packaging import (
    load_manifest,
    manifest_source_files,
    validate_staged_runtime,
)


class RuntimeManifestTests(unittest.TestCase):
    def write_manifest(self, root, contents):
        path = root / "runtime-manifest.txt"
        path.write_text(contents)
        return path

    def test_manifest_rejects_targets_outside_owned_source_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for target in ("../outside", "/private/outside"):
                with self.subTest(target=target):
                    manifest = self.write_manifest(root, "required {}\n".format(target))
                    with self.assertRaisesRegex(ValueError, "owned source root"):
                        load_manifest(manifest)

    def test_omitted_python_module_fails_staged_runtime_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            staged = root / "staged"
            (source / "token_meter").mkdir(parents=True)
            (staged / "token_meter").mkdir(parents=True)
            (source / "meter.py").write_text("from token_meter import feature\n")
            (source / "token_meter" / "__init__.py").write_text("")
            (source / "token_meter" / "feature.py").write_text("VALUE = 1\n")
            (staged / "meter.py").write_text("from token_meter import feature\n")
            (staged / "token_meter" / "__init__.py").write_text("")
            manifest = self.write_manifest(
                source,
                "required meter.py\nrequired runtime-manifest.txt\npython-tree token_meter\n",
            )
            (staged / "runtime-manifest.txt").write_text(manifest.read_text())

            errors = validate_staged_runtime(source, staged, manifest)

            self.assertEqual(errors, ("missing: token_meter/feature.py",))

    def test_matching_runtime_manifest_has_no_parity_errors(self):
        root = Path(__file__).resolve().parents[2]

        entries = load_manifest(root / "runtime-manifest.txt")

        self.assertIn(("python-tree", "token_meter"),
                      tuple((entry.kind, entry.path) for entry in entries))

    def test_repository_manifest_round_trips_to_temporary_runtime(self):
        source = Path(__file__).resolve().parents[2]
        manifest = source / "runtime-manifest.txt"
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "runtime"
            for relative in manifest_source_files(source, load_manifest(manifest)):
                destination = staged / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / relative, destination)

            self.assertEqual(validate_staged_runtime(source, staged, manifest), ())


if __name__ == "__main__":
    unittest.main()
