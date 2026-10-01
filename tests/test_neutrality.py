"""The format-neutral core stays format-neutral.

``pptx_agent.core`` -- the OPC package, the XML helpers and ordered insertion, undo history and
id stamping -- is meant to be lifted out and shared with a future docx editor.  That only works
if nothing PowerPoint-specific creeps in, so this checks it mechanically rather than by review.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

CORE = Path(__file__).parents[1] / "src" / "pptx_agent" / "core"

#: Spellings that would mean the core had learned PresentationML or DrawingML.
FORBIDDEN = (
    "presentationml", "drawingml", "wordprocessingml", "slide", "sldid", "sptree",
    '"p:', "'p:", '"a:', "'a:", '"w:', "'w:", "ppt/", "word/document",
)


def _modules() -> list[Path]:
    return sorted(CORE.glob("*.py"))


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.stem)
def test_core_has_no_format_vocabulary(module):
    source = module.read_text(encoding="utf-8").lower()
    found = [token for token in FORBIDDEN if token in source]
    assert not found, f"{module.name} mentions {found}"


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.stem)
def test_core_imports_only_itself(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                assert node.level == 1, f"{module.name} reaches outside core: {node.module}"
            else:
                assert not (node.module or "").startswith("pptx_agent"), node.module
        elif isinstance(node, ast.Import):
            assert not any(alias.name.startswith("pptx_agent") for alias in node.names)


def test_core_imports_without_the_presentation_layer():
    """Importing the core alone must not drag in (and register) the PresentationML layer."""
    code = (
        "import sys, pptx_agent.core.opc, pptx_agent.core.history, pptx_agent.core.stamp\n"
        "from pptx_agent.core.xml import NAMESPACES\n"
        "assert 'p' not in NAMESPACES and 'a' not in NAMESPACES, NAMESPACES\n"
        "assert not [m for m in sys.modules if m.startswith(('pptx_agent.oxml', 'pptx_agent.edit'))]\n"
    )
    # pptx_agent/__init__ imports the edit layer, so load the core modules without it.
    bootstrap = (
        "import importlib.util, sys, types, pathlib\n"
        f"root = pathlib.Path({str(CORE.parent)!r})\n"
        "package = types.ModuleType('pptx_agent'); package.__path__ = [str(root)]\n"
        "sys.modules['pptx_agent'] = package\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", bootstrap + code], capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr


def test_ordered_insertion_works_for_a_foreign_vocabulary():
    """A different format can register its own namespace and sequence and get correct order."""
    from pptx_agent.core.xml import make, parse_xml, register_child_order, register_namespaces
    from pptx_agent.core.xml import append_in_order, local_name

    register_namespaces({"tst": "urn:pptx-agent:test"})
    register_child_order({"tst:props": ("tst:first", ("tst:item", "tst:other"), "tst:last")})
    root = parse_xml(b'<tst:props xmlns:tst="urn:pptx-agent:test"><tst:item/><tst:last/></tst:props>')

    append_in_order(root, make("tst:other"))
    append_in_order(root, make("tst:first"))

    assert [local_name(child) for child in root] == ["first", "item", "other", "last"]
