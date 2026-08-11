"""Path-safe runtime manifest expansion and source/staged parity checks."""

import argparse
import filecmp
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


MANIFEST_KINDS = ("required", "optional", "python-tree", "tree")


@dataclass(frozen=True)
class ManifestEntry:
    kind: str
    path: str


def _owned_relative_path(value):
    value = str(value or "").strip()
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or value in (".", "..")
        or ".." in path.parts
    ):
        raise ValueError("Runtime manifest targets must stay inside the owned source root.")
    return path.as_posix()


def load_manifest(manifest_path):
    manifest_path = Path(manifest_path)
    entries = []
    seen = set()
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError("Runtime manifest is unavailable.") from exc
    for line_number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2 or parts[0] not in MANIFEST_KINDS:
            raise ValueError("Invalid runtime manifest entry on line {}.".format(line_number))
        entry = ManifestEntry(parts[0], _owned_relative_path(parts[1]))
        key = (entry.kind, entry.path)
        if key in seen:
            raise ValueError("Duplicate runtime manifest entry on line {}.".format(line_number))
        seen.add(key)
        entries.append(entry)
    if not entries:
        raise ValueError("Runtime manifest is empty.")
    return tuple(entries)


def _tree_files(root, suffix=None):
    if not root.is_dir():
        raise ValueError("Required runtime tree is unavailable: {}".format(root.name))
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if suffix is not None and path.suffix != suffix:
            continue
        files.append(path)
    return tuple(sorted(files, key=lambda path: path.as_posix()))


def manifest_source_files(source_root, entries):
    source_root = Path(source_root)
    files = []
    seen = set()
    for entry in entries:
        source_path = source_root / entry.path
        if entry.kind in ("required", "optional"):
            if not source_path.exists():
                if entry.kind == "optional":
                    continue
                raise ValueError("Required runtime path is unavailable: {}".format(entry.path))
            candidates = (source_path,)
        elif entry.kind == "python-tree":
            candidates = _tree_files(source_path, suffix=".py")
        else:
            candidates = _tree_files(source_path)
        for candidate in candidates:
            relative = candidate.relative_to(source_root).as_posix()
            if relative not in seen:
                seen.add(relative)
                files.append(relative)
    return tuple(files)


def validate_staged_runtime(source_root, staged_root, manifest_path):
    source_root = Path(source_root)
    staged_root = Path(staged_root)
    entries = load_manifest(manifest_path)
    errors = []
    for relative in manifest_source_files(source_root, entries):
        source_path = source_root / relative
        staged_path = staged_root / relative
        if not staged_path.is_file():
            errors.append("missing: {}".format(relative))
        elif not filecmp.cmp(str(source_path), str(staged_path), shallow=False):
            errors.append("different: {}".format(relative))
    return tuple(errors)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate Token Meter runtime packaging.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    manifest_parser = subparsers.add_parser("manifest")
    manifest_parser.add_argument("manifest")
    parity_parser = subparsers.add_parser("parity")
    parity_parser.add_argument("source_root")
    parity_parser.add_argument("staged_root")
    parity_parser.add_argument("manifest")
    args = parser.parse_args(argv)
    if args.command == "manifest":
        manifest = Path(args.manifest)
        manifest_source_files(manifest.parent, load_manifest(manifest))
        return 0
    errors = validate_staged_runtime(args.source_root, args.staged_root, args.manifest)
    if errors:
        for error in errors:
            print(error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
