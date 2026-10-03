"""ooxml-edit stays format-neutral and self-contained.

The package is shared by editors of different formats, and that only works if no one
format's knowledge creeps in.  This checks it mechanically rather than by review: no format
vocabulary in the source, no imports beyond the standard library, lxml and the package
itself, and nothing registered on import beyond the packaging namespaces every format shares.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parents[1] / "src" / "ooxml_edit"

#: Spellings that would mean the package had learned one format: the markup languages, the
#: formats and applications, their part paths, their characteristic tags and prefixes.
FORBIDDEN = (
    # the markup languages
    "presentationml", "drawingml", "wordprocessingml", "spreadsheetml",
    # the formats and the applications
    "pptx", "docx", "xlsx", "powerpoint", "excel", "msword", "microsoft word",
    # one format's things
    "slide", "sldid", "sptree", "deck", "presentation", "workbook", "worksheet",
    # part paths
    "ppt/", "word/", "xl/",
    # the formats' own prefixes, spelled as a tag
    '"p:', "'p:", '"a:', "'a:", '"w:', "'w:", '"x:', "'x:", '"c:', "'c:",
    "``p:", "``a:", "``w:", "``x:", "``c:",
)

#: What the package may import besides the standard library and itself.
ALLOWED_THIRD_PARTY = {"lxml"}


def _modules() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def test_the_package_has_its_modules():
    assert {path.stem for path in _modules()} >= {"__init__", "opc", "xml", "history", "stamp"}


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.stem)
def test_no_format_vocabulary(module):
    source = module.read_text(encoding="utf-8").lower()
    found = [token for token in FORBIDDEN if token in source]
    assert not found, f"{module.name} mentions {found}"


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.stem)
def test_imports_only_the_standard_library_lxml_and_itself(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                assert node.level == 1, f"{module.name} reaches outside the package: {node.module}"
                continue
            names = [node.module or ""]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        for name in names:
            top = name.partition(".")[0]
            assert top in stdlib or top in ALLOWED_THIRD_PARTY or top == "ooxml_edit", (
                f"{module.name} imports {name}"
            )


def test_importing_registers_only_the_shared_namespaces():
    """A fresh import knows the packaging namespaces, no format's prefixes, no child orders."""
    code = (
        "import ooxml_edit, ooxml_edit.opc, ooxml_edit.history, ooxml_edit.stamp\n"
        "from ooxml_edit.xml import NAMESPACES, CHILD_ORDER\n"
        "assert set(NAMESPACES) == {'r', 'mc', 'ct', 'pr'}, NAMESPACES\n"
        "assert not CHILD_ORDER, CHILD_ORDER\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(PACKAGE.parent)
    )
    assert completed.returncode == 0, completed.stderr


def test_ordered_insertion_works_for_a_foreign_vocabulary():
    """A format can register its own namespace and sequence and get correct order."""
    from ooxml_edit.xml import append_in_order, local_name, make, parse_xml
    from ooxml_edit.xml import register_child_order, register_namespaces

    register_namespaces({"tst": "urn:ooxml-edit:test"})
    register_child_order({"tst:props": ("tst:first", ("tst:item", "tst:other"), "tst:last")})
    root = parse_xml(b'<tst:props xmlns:tst="urn:ooxml-edit:test"><tst:item/><tst:last/></tst:props>')

    append_in_order(root, make("tst:other"))
    append_in_order(root, make("tst:first"))

    assert [local_name(child) for child in root] == ["first", "item", "other", "last"]
