"""ooxml-edit's core stays format-neutral and self-contained.

The package is shared by editors of different formats, and that only works if no one
format's knowledge creeps in.  This checks the core -- the modules directly in
``ooxml_edit`` -- mechanically rather than by review: no format vocabulary in the source,
no imports beyond the standard library, lxml and the core itself, and nothing registered on
import beyond the packaging namespaces every format shares.

The optional :mod:`ooxml_edit.charts` subpackage speaks DrawingML, which every format
embeds; the core never imports it, and ``test_charts_neutrality.py`` keeps it free of any
one document format.

The optional :mod:`ooxml_edit.tools` subpackage -- the agent tool layer's plumbing -- is
held to the core's rule: no format vocabulary at all (the format libraries bring their own
tools), no import beyond the standard library, lxml and ooxml-edit's core, no provider SDK,
and nothing registered on import.  The core never imports it either.
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
    """The core: the package's own modules, not its subpackages."""
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


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.stem)
def test_the_core_does_not_import_the_charts_subpackage(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [alias.name for alias in node.names]
            if node.level == 0:
                names = [node.module or ""]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        for name in names:
            assert "charts" not in name.split("."), f"{module.name} imports {name}"


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.stem)
def test_the_core_does_not_import_the_tools_subpackage(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [getattr(node, "module", None) or ""] + [alias.name for alias in node.names]
            assert not any("tools" in name.split(".") for name in names), (
                f"{module.name} imports the tools subpackage")


def test_importing_the_core_never_imports_the_charts_subpackage():
    code = (
        "import sys\n"
        "import ooxml_edit, ooxml_edit.opc, ooxml_edit.xml, ooxml_edit.history\n"
        "import ooxml_edit.stamp\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('ooxml_edit.charts'))\n"
        "assert not loaded, loaded\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(PACKAGE.parent)
    )
    assert completed.returncode == 0, completed.stderr


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


# -- the tools subpackage ----------------------------------------------------------------------

TOOLS = PACKAGE / "tools"

#: Beyond the core's list: the format libraries, and any provider SDK.
TOOLS_FORBIDDEN = FORBIDDEN + ("pptx_agent", "docx_agent", "import anthropic", "import openai",
                               "from anthropic", "from openai")


def _tool_modules() -> list[Path]:
    return sorted(TOOLS.glob("*.py"))


def test_the_tools_subpackage_has_its_modules():
    assert {path.stem for path in _tool_modules()} >= {
        "__init__", "registry", "schema", "adapters", "results", "session", "limits", "worker",
        "logs", "prompts", "dispatch"}


#: The one module that names formats: the shared tools' definitions, which a model reads
#: ("slides, for decks"), as data.  It still imports no format library (the test below).
FORMAT_FACING = {"shared"}


@pytest.mark.parametrize("module", [path for path in _tool_modules()
                                    if path.stem not in FORMAT_FACING],
                         ids=lambda path: path.stem)
def test_the_tools_have_no_format_vocabulary(module):
    source = module.read_text(encoding="utf-8").lower()
    found = [token for token in TOOLS_FORBIDDEN if token in source]
    assert not found, f"tools/{module.name} mentions {found}"


@pytest.mark.parametrize("module", _tool_modules(), ids=lambda path: path.stem)
def test_the_tools_import_only_the_standard_library_lxml_and_the_core(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                # ``.schema`` within the subpackage, ``..xml`` for the core -- never further.
                assert node.level <= 2, f"tools/{module.name} reaches outside ooxml_edit"
                assert node.level == 1 or "charts" not in (node.module or ""), module.name
                continue
            names = [node.module or ""]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        for name in names:
            top = name.partition(".")[0]
            assert top in stdlib or top in ALLOWED_THIRD_PARTY or (
                top == "ooxml_edit" and "charts" not in name), f"tools/{module.name} imports {name}"


def test_importing_the_tools_registers_nothing_and_loads_no_format():
    code = (
        "import sys\n"
        "import ooxml_edit.tools\n"
        "from ooxml_edit.xml import NAMESPACES, CHILD_ORDER\n"
        "assert set(NAMESPACES) == {'r', 'mc', 'ct', 'pr'}, NAMESPACES\n"
        "assert not CHILD_ORDER, CHILD_ORDER\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('ooxml_edit.charts')\n"
        "                or m.split('.')[0] in ('anthropic', 'openai', 'pptx_agent', 'docx_agent'))\n"
        "assert not loaded, loaded\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(PACKAGE.parent)
    )
    assert completed.returncode == 0, completed.stderr


def test_importing_the_core_never_imports_the_tools_subpackage():
    code = (
        "import sys\n"
        "import ooxml_edit, ooxml_edit.opc, ooxml_edit.xml, ooxml_edit.history, ooxml_edit.stamp\n"
        "import ooxml_edit.charts\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('ooxml_edit.tools'))\n"
        "assert not loaded, loaded\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(PACKAGE.parent)
    )
    assert completed.returncode == 0, completed.stderr
