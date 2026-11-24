"""Check a release tag against the project version and extract its notes.

Usage: python .github/scripts/release_notes.py vX.Y.Z [OUTPUT_FILE]

Fails before anything is built when the tag is malformed, does not match the
``pyproject.toml`` version (``v1.1`` and ``v1.1.0`` both match ``1.1.0``), or
``CHANGELOG.md`` has no non-empty section for that version. Prints the notes,
or writes them to OUTPUT_FILE.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAG = re.compile(r"v(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*)){0,2}")


def release_notes(tag: str, root: Path = ROOT) -> str:
    if not TAG.fullmatch(tag):
        raise SystemExit(f"tag {tag!r} must use the vX, vX.Y, or vX.Y.Z format")
    project = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version = "([^"]+)"$', project)
    if match is None:
        raise SystemExit("pyproject.toml has no version")
    version = match.group(1)
    parts = tag[1:].split(".")
    if version.split(".")[: len(parts)] != parts:
        raise SystemExit(
            f"tag {tag} does not match pyproject.toml version {version}; "
            "the tag probably points at a commit from before the version bump"
        )
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.search(rf"(?m)^## \[{re.escape(version)}\].*$", changelog)
    if heading is None:
        raise SystemExit(f"CHANGELOG.md has no '## [{version}]' section")
    following = re.search(r"(?m)^(## \[|\[Unreleased\]:)", changelog[heading.end() :])
    end = heading.end() + following.start() if following else len(changelog)
    notes = changelog[heading.end() : end].strip()
    if not notes:
        raise SystemExit(f"the '## [{version}]' section of CHANGELOG.md is empty")
    return notes + "\n"


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        raise SystemExit(__doc__)
    notes = release_notes(argv[0])
    if len(argv) == 2:
        Path(argv[1]).write_text(notes, encoding="utf-8")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
