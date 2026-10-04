"""ooxml_edit.charts speaks DrawingML, and no one document format.

A chart part, its embedded workbook and a SmartArt diagram are the same markup in a deck
and in a Word document; the subpackage only works for both if neither creeps in.  This
checks it mechanically: no PresentationML or WordprocessingML vocabulary in the source, no
import of an editor built on it, imports only of the standard library, lxml and ooxml-edit,
and on import only the DrawingML and SpreadsheetML vocabulary registered.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "src"
CHARTS = SOURCE / "ooxml_edit" / "charts"

#: Spellings that would mean the subpackage had learned one document format: the markup
#: languages, the formats, their part paths, and their prefixes spelled as a tag.
FORBIDDEN = (
    "presentationml", "wordprocessingml", "pptx", "docx", "slide",
    "ppt/", "word/", "sldid", "sptree", "graphicframe",
    '"p:', "'p:", "``p:", '"w:', "'w:", "``w:", "p14:", "w14:", "{p:", "{w:",
    "pptx_agent", "docx_agent",
)

ALLOWED_THIRD_PARTY = {"lxml"}

#: What importing the subpackage may register: the packaging namespaces, and its own.
NAMESPACES = {"r", "mc", "ct", "pr", "a", "c", "dgm", "dsp", "x"}
ORDER_PREFIXES = ("a:", "c:", "dgm:", "dsp:")


def _modules() -> list[Path]:
    return sorted(CHARTS.glob("*.py"))


def test_the_subpackage_has_its_modules():
    assert {path.stem for path in _modules()} >= {
        "__init__", "namespaces", "host", "workbook", "chart", "dmltext", "diagram", "model"}


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.stem)
def test_no_document_format_vocabulary(module):
    source = module.read_text(encoding="utf-8").lower()
    found = [token for token in FORBIDDEN if token in source]
    assert not found, f"{module.name} mentions {found}"


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.stem)
def test_imports_only_the_standard_library_lxml_and_ooxml_edit(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                # ``.chart`` within the subpackage, ``..xml`` for the core -- never further.
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
                f"{module.name} imports {name}"
            )


def test_importing_registers_only_drawingml_and_spreadsheetml():
    code = (
        "import ooxml_edit.charts\n"
        "from ooxml_edit.xml import NAMESPACES, CHILD_ORDER\n"
        f"assert set(NAMESPACES) == set({sorted(NAMESPACES)!r}), NAMESPACES\n"
        f"assert all(key.startswith({ORDER_PREFIXES!r}) for key in CHILD_ORDER), CHILD_ORDER\n"
        "assert {'a:p', 'a:r', 'a:br', 'a:fld', 'c:ser', 'dgm:pt', 'dsp:txBody'} <= "
        "set(CHILD_ORDER)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(SOURCE)
    )
    assert completed.returncode == 0, completed.stderr
