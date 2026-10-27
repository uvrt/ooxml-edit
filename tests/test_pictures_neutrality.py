"""ooxml_edit.pictures speaks DrawingML (and SVG), and no one document format: a picture is
the same markup in a deck and in a Word document.  Held as ``test_charts_neutrality.py``
holds the charts subpackage: no format vocabulary, imports only of the standard library,
lxml, ooxml-common and ooxml-edit, and no namespace added to the table on import."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "src"
PICTURES = SOURCE / "ooxml_edit" / "pictures"

FORBIDDEN = (
    "presentationml", "wordprocessingml", "pptx", "docx", "slide",
    "ppt/", "word/", "sldid", "sptree",
    '"p:', "'p:", "``p:", '"w:', "'w:", "``w:", "p14:", "w14:", "{p:", "{w:",
    "pptx_agent", "docx_agent",
)
ALLOWED_THIRD_PARTY = {"lxml", "ooxml_common"}


def _modules() -> list[Path]:
    return sorted(PICTURES.glob("*.py"))


def test_the_subpackage_has_its_modules():
    assert {path.stem for path in _modules()} >= {"__init__", "svg"}


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.stem)
def test_no_document_format_vocabulary(module):
    source = module.read_text(encoding="utf-8").lower()
    found = [token for token in FORBIDDEN if token in source]
    assert not found, f"{module.name} mentions {found}"


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.stem)
def test_imports_only_the_standard_library_lxml_ooxml_common_and_ooxml_edit(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                assert node.level <= 2, f"{module.name} reaches outside ooxml_edit"
                continue
            names = [node.module or ""]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        for name in names:
            top = name.partition(".")[0]
            assert top in stdlib or top in ALLOWED_THIRD_PARTY or top == "ooxml_edit", (
                f"{module.name} imports {name}")


def test_importing_adds_no_namespace():
    code = ("from ooxml_edit.xml import NAMESPACES\n"
            "before = dict(NAMESPACES)\n"
            "import ooxml_edit.pictures\n"
            "assert dict(NAMESPACES) == before, set(NAMESPACES) - set(before)\n")
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                               cwd=str(SOURCE))
    assert completed.returncode == 0, completed.stderr
