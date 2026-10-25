"""The version in pyproject.toml is the one CHANGELOG.md's top version section names.

    python .github/check_version.py

Run from the repository root (CI's `build` job does). The first `## ` heading of
CHANGELOG.md that starts with a version number (an "Unreleased" heading above it is
skipped) must name pyproject.toml's `version`, and the package's `__version__`, where it
sets one, must be the same. Each version on main is tagged `v<version>` by the tag
workflow, so a version is raised together with its CHANGELOG section. Standard library
only; exits 1 and says what to change when they disagree.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = re.compile(r"\d+(?:\.\d+)+")


def project_version(text: str) -> str | None:
    """`version` in pyproject.toml's `[project]` table."""
    table = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    found = table and re.search(r'^version\s*=\s*"([^"]+)"', table.group(1), re.M)
    return found.group(1) if found else None


def top_section_version(text: str) -> str | None:
    """The version the first `## <version> ...` heading names."""
    for heading in re.findall(r"^## +(.+)$", text, re.M):
        found = VERSION.match(heading.strip())
        if found:
            return found.group(0)
    return None


def package_versions(root: pathlib.Path) -> dict[str, str]:
    """`__version__` of each package under src/, by its file."""
    versions = {}
    for init in sorted(root.glob("src/*/__init__.py")):
        found = re.search(r'^__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"), re.M)
        if found:
            versions[str(init.relative_to(root))] = found.group(1)
    return versions


def problems(root: pathlib.Path) -> list[str]:
    version = project_version((root / "pyproject.toml").read_text(encoding="utf-8"))
    if version is None:
        return ["pyproject.toml has no [project] version"]
    found = []
    section = top_section_version((root / "CHANGELOG.md").read_text(encoding="utf-8"))
    if section is None:
        found.append(f"CHANGELOG.md has no section for {version}: add `## {version} -- <date>`")
    elif section != version:
        found.append(f"CHANGELOG.md's top version section is {section} but pyproject.toml says "
                     f"{version}: name the section for the version, or raise the version with it")
    for path, value in package_versions(root).items():
        if value != version:
            found.append(f"{path} sets __version__ = {value!r} but pyproject.toml says {version}")
    return found


def main() -> int:
    found = problems(ROOT)
    for problem in found:
        print(f"check_version: {problem}", file=sys.stderr)
    if not found:
        print("check_version: pyproject.toml, CHANGELOG.md and __version__ agree")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
