"""The names the sibling repositories import from ooxml-edit: renaming one fails here.

Siblings' CI installs ooxml-edit from ``main``, so a rename that breaks one shows only at
its next run, not on the pull request that made it.  ``USED_BY`` is that surface, each name
with the siblings that import it (in their ``src``, ``tests`` and ``tools``);
docs/USED-BY.md documents it, and ``test_the_documented_names_are_these`` holds the two to
the same names.

A sibling that starts importing a name: add it here and there.  Renaming one: keep the old
name as an alias for one release, with a comment saying who it is kept for (``OLD_NAMES``),
and move the sibling to the new one.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parent.parent / "docs/USED-BY.md"
SECTION = None

#: module -> {name: the siblings that import it}.
USED_BY: dict[str, dict[str, str]] = {
    "ooxml_edit.charts": {
        "ChartDataError": "docx-agent",
        "ChartDataWarning": "docx-agent",
        "POWERPOINT_LOOK": "pptx-agent",
        "WORD_LOOK": "docx-agent",
        "add_chart": "docx-agent, pptx-agent",
        "provenance": "pptx-agent",
    },
    "ooxml_edit.charts.actions": {
        "DATA_ACTIONS": "pptx-agent",
        "STYLE_ACTIONS": "docx-agent, pptx-agent",
        "add_plan": "docx-agent, pptx-agent",
        "add_style_actions": "docx-agent, pptx-agent",
        "apply_data_action": "docx-agent, pptx-agent",
        "apply_smartart_action": "docx-agent, pptx-agent",
        "apply_style_action": "docx-agent, pptx-agent",
        "chart_json": "docx-agent, pptx-agent",
        "chart_read_json": "docx-agent, pptx-agent",
        "chart_style_json": "docx-agent, pptx-agent",
        "finish_add": "docx-agent, pptx-agent",
        "resolve_category": "docx-agent, pptx-agent",
        "resolve_series": "docx-agent, pptx-agent",
        "smartart_json": "docx-agent, pptx-agent",
    },
    "ooxml_edit.charts.cases": {
        "CHART": "docx-agent, pptx-agent",
        "CHART_CASES": "docx-agent, pptx-agent",
        "SMARTART_CASES": "docx-agent, pptx-agent",
        "problems": "docx-agent, pptx-agent",
    },
    "ooxml_edit.charts.chart": {
        "AXIS_TAGS": "pptx-agent",
        "C_NS": "pptx-agent",
        "Chart": "docx-agent, pptx-agent",
        "ChartDataError": "docx-agent, pptx-agent",
        "ChartDataWarning": "docx-agent, pptx-agent",
        "PLOT_TAGS": "pptx-agent",
        "PLOT_TYPES": "pptx-agent",
        "REL_PACKAGE": "pptx-agent",
        "Series": "pptx-agent",
        "chart_model": "docx-agent, pptx-agent",
        "decode_number": "pptx-agent",
        "series_of": "pptx-agent",
        "workbook_part": "pptx-agent",
    },
    "ooxml_edit.charts.create": {
        "POWERPOINT_LOOK": "pptx-agent",
        "WORD_LOOK": "docx-agent",
        "chart_data": "docx-agent",
        "check_options": "docx-agent",
    },
    "ooxml_edit.charts.diagram": {
        "DATA_MODEL_EXT_URI": "pptx-agent",
        "DGM_NS": "pptx-agent",
        "DSP_NS": "pptx-agent",
        "Diagram": "docx-agent, pptx-agent",
        "DiagramDrawingError": "docx-agent",
        "DiagramModel": "docx-agent",
        "DiagramNode": "pptx-agent",
        "REL_DIAGRAM_DRAWING": "pptx-agent",
        "TEXT_TYPES": "pptx-agent",
        "diagram_model": "docx-agent, pptx-agent",
        "new_model_id": "pptx-agent",
    },
    "ooxml_edit.charts.dmltext": {
        "LINE_BREAK": "pptx-agent",
        "RUN_TAGS": "pptx-agent",
        "inline_items": "pptx-agent",
        "make_break": "pptx-agent",
        "make_run": "pptx-agent",
        "paragraph_like": "pptx-agent",
        "paragraph_text": "pptx-agent",
        "rebuild_paragraph": "pptx-agent",
        "replace_body_text": "docx-agent, pptx-agent",
        "resegment": "pptx-agent",
        "runs": "pptx-agent",
    },
    "ooxml_edit.charts.format": {
        "scheme_color": "pptx-agent",
        "split_color": "pptx-agent",
    },
    "ooxml_edit.charts.host": {
        "GraphicHost": "docx-agent, pptx-agent",
        "chart_part": "docx-agent, pptx-agent",
        "diagram_parts": "docx-agent",
    },
    "ooxml_edit.charts.model": {
        "CHART_KEYS": "pptx-agent",
        "CHART_READ_ONLY": "pptx-agent",
        "ChartModelError": "docx-agent, pptx-agent",
        "apply_chart_model": "docx-agent, pptx-agent",
        "apply_diagram_model": "docx-agent, pptx-agent",
        "canonical_chart": "docx-agent, pptx-agent",
        "canonical_diagram": "docx-agent, pptx-agent",
        "chart_model": "pptx-agent",
        "diagram_model": "pptx-agent",
    },
    "ooxml_edit.charts.workbook": {
        "Area": "pptx-agent",
        "Cell": "pptx-agent",
        "MAX_COLUMN": "pptx-agent",
        "MAX_ROW": "pptx-agent",
        "SML_NS": "pptx-agent",
        "SheetRange": "pptx-agent",
        "Table": "pptx-agent",
        "Workbook": "pptx-agent",
        "Worksheet": "pptx-agent",
        "column_index": "pptx-agent",
        "column_letters": "pptx-agent",
        "number_text": "pptx-agent",
        "parse_formula": "pptx-agent",
    },
    "ooxml_edit.dates": {
        "LANGUAGES": "ooxml-common",
        "LCIDS": "ooxml-common",
        "NAMES": "ooxml-common",
        "format_code": "ooxml-common",
        "format_date": "ooxml-common",
        "is_date_code": "ooxml-common",
        "serial": "ooxml-common",
    },
    "ooxml_edit.errors": {
        "ArgumentError": "docx-agent, pptx-agent, xlsx-agent",
        "ArgumentTypeError": "pptx-agent",
        "UnitError": "docx-agent, pptx-agent, xlsx-agent",
    },
    "ooxml_edit.history": {
        "History": "docx-agent, pptx-agent, xlsx-agent",
        "Snapshottable": "docx-agent, pptx-agent",
    },
    "ooxml_edit.markdown": {
        "Line": "xlsx-agent",
        "Run": "xlsx-agent",
        "merge": "xlsx-agent",
        "parse": "xlsx-agent",
        "plain": "xlsx-agent",
        "to_markdown": "xlsx-agent",
    },
    "ooxml_edit.opc": {
        "CONTENT_TYPES_PART": "docx-agent, pptx-agent",
        "OpcPackage": "docx-agent, pptx-agent, xlsx-agent",
        "RELS_NS": "pptx-agent",
        "REL_IMAGE": "pptx-agent",
        "REL_OFFICE_DOCUMENT": "docx-agent, pptx-agent, xlsx-agent",
        "Relationship": "docx-agent, pptx-agent",
        "normalize_part_path": "docx-agent, pptx-agent",
        "numbered_template": "pptx-agent",
        "part_for_rels": "pptx-agent",
        "rels_path_for": "docx-agent, pptx-agent, xlsx-agent",
        "resolve_target": "pptx-agent",
    },
    "ooxml_edit.provenance": {
        "RecordError": "docx-agent, pptx-agent",
        "VERSION": "pptx-agent",
        "dumps": "docx-agent, pptx-agent",
        "facts_after_edit": "pptx-agent",
        "facts_found": "pptx-agent",
        "loads": "docx-agent, pptx-agent",
        "offset": "pptx-agent",
        "place": "pptx-agent",
        "record": "pptx-agent",
    },
    "ooxml_edit.sheets": {
        "Area": "xlsx-agent",
        "Cell": "xlsx-agent",
        "Formula": "xlsx-agent",
        "MAX_COLUMN": "xlsx-agent",
        "MAX_ROW": "xlsx-agent",
        "MAX_TEXT": "xlsx-agent",
        "RichText": "xlsx-agent",
        "column_index": "xlsx-agent",
        "column_letters": "xlsx-agent",
        "date_serial": "xlsx-agent",
        "number_text": "xlsx-agent",
        "outside_footprint": "xlsx-agent",
        "rich_runs": "xlsx-agent",
    },
    "ooxml_edit.stamp": {
        "ExtensionStamp": "pptx-agent",
    },
    "ooxml_edit.tools": {
        "ACCEPT": "docx-agent",
        "CACHE_EXTERNAL": "xlsx-agent",
        "CACHE_STATUSES": "xlsx-agent",
        "CORE": "docx-agent, pptx-agent, xlsx-agent",
        "DATASET_ID": "xlsx-agent",
        "DATASET_PREFIX": "xlsx-agent",
        "Dataset": "docx-agent, pptx-agent, xlsx-agent",
        "DatasetEntry": "docx-agent, pptx-agent, xlsx-agent",
        "DocumentFormat": "docx-agent, pptx-agent, xlsx-agent",
        "FolderCatalogue": "pptx-agent",
        "InProcess": "docx-agent, pptx-agent",
        "Param": "pptx-agent",
        "REF": "xlsx-agent",
        "REVIEW": "docx-agent, pptx-agent",
        "ROLES": "docx-agent, pptx-agent, xlsx-agent",
        "Result": "docx-agent, pptx-agent, xlsx-agent",
        "SubprocessPool": "docx-agent, pptx-agent",
        "ToolError": "docx-agent, pptx-agent, xlsx-agent",
        "ToolGroup": "docx-agent, pptx-agent, xlsx-agent",
        "Toolbox": "docx-agent, pptx-agent, xlsx-agent",
        "array": "docx-agent, pptx-agent, xlsx-agent",
        "boolean": "docx-agent, pptx-agent, xlsx-agent",
        "collect_warnings": "pptx-agent",
        "content_key": "docx-agent",
        "integer": "docx-agent, pptx-agent, xlsx-agent",
        "names_as": "docx-agent, pptx-agent",
        "number": "docx-agent, pptx-agent, xlsx-agent",
        "obj": "docx-agent, pptx-agent, xlsx-agent",
        "page_list": "docx-agent, pptx-agent",
        "page_text": "docx-agent, pptx-agent",
        "render": "docx-agent, pptx-agent",
        "strict_problems": "docx-agent, pptx-agent, xlsx-agent",
        "string": "docx-agent, pptx-agent, xlsx-agent",
        "tool": "docx-agent, pptx-agent, xlsx-agent",
        "worker": "docx-agent, pptx-agent",
        "worker_task": "docx-agent, pptx-agent",
    },
    "ooxml_edit.tools.adapters": {
        "anthropic_problems": "docx-agent, pptx-agent, xlsx-agent",
        "definitions_for": "docx-agent, pptx-agent",
        "free_strings": "xlsx-agent",
        "openai_problems": "docx-agent, pptx-agent, xlsx-agent",
    },
    "ooxml_edit.tools.assets": {
        "MAX_RESULTS": "pptx-agent",
        "fetch": "pptx-agent",
        "find": "pptx-agent",
    },
    "ooxml_edit.tools.hints": {
        "tool_hints": "docx-agent, pptx-agent, xlsx-agent",
    },
    "ooxml_edit.tools.limits": {
        "LimitError": "xlsx-agent",
        "Limits": "xlsx-agent",
        "check_package": "xlsx-agent",
    },
    "ooxml_edit.tools.render": {
        "Layout": "docx-agent, pptx-agent",
        "RendererBase": "docx-agent, pptx-agent",
        "View": "docx-agent, pptx-agent",
        "draw": "docx-agent, pptx-agent",
        "draw_tiles": "docx-agent, pptx-agent",
        "fingerprint": "docx-agent, pptx-agent",
        "lay_out_views": "docx-agent",
        "png_size": "docx-agent, pptx-agent",
        "raster_key": "pptx-agent",
        "render_views": "pptx-agent",
        "system_fonts": "docx-agent, pptx-agent",
        "warm_up": "docx-agent, pptx-agent",
    },
    "ooxml_edit.tools.results": {
        "page_list": "pptx-agent",
    },
    "ooxml_edit.tools.schema": {
        "close_match": "xlsx-agent",
        "example_arguments": "docx-agent, pptx-agent, xlsx-agent",
        "is_path_name": "docx-agent, pptx-agent, xlsx-agent",
        "subset_problems": "docx-agent, xlsx-agent",
        "walk_properties": "docx-agent, pptx-agent, xlsx-agent",
    },
    "ooxml_edit.tools.shared": {
        "CHART_TYPES": "docx-agent, pptx-agent",
        "COMMENT_ACTION_ROLES": "docx-agent, pptx-agent",
        "GROUPS": "docx-agent, pptx-agent, xlsx-agent",
        "SESSION_TOOLS": "docx-agent, pptx-agent, xlsx-agent",
        "SPECS": "pptx-agent",
        "comment_role": "docx-agent, pptx-agent",
        "definition": "pptx-agent, xlsx-agent",
        "handler": "docx-agent, pptx-agent, xlsx-agent",
    },
    "ooxml_edit.tools.sources": {
        "date_pattern": "docx-agent, pptx-agent",
        "first_rows": "docx-agent, pptx-agent",
        "no_dates": "docx-agent, pptx-agent",
        "runs": "pptx-agent",
        "what": "docx-agent, pptx-agent",
    },
    "ooxml_edit.tools.tiles": {
        "MAX_TILES": "docx-agent, pptx-agent",
        "check_size": "docx-agent, pptx-agent",
        "fit": "docx-agent, pptx-agent",
        "narrowed_note": "docx-agent, pptx-agent",
        "tiled_svg": "docx-agent, pptx-agent",
        "tiling_of": "docx-agent, pptx-agent",
    },
    "ooxml_edit.tools.worker": {
        "task_reference": "docx-agent, pptx-agent",
    },
    "ooxml_edit.xml": {
        "CHILD_ORDER": "docx-agent, pptx-agent",
        "Element": "docx-agent, pptx-agent, xlsx-agent",
        "NAMESPACES": "docx-agent, pptx-agent",
        "append_in_order": "docx-agent, pptx-agent",
        "child_elements": "docx-agent, pptx-agent",
        "child_ranks": "pptx-agent",
        "declared_prefixes": "pptx-agent",
        "find": "docx-agent, pptx-agent",
        "findall": "docx-agent, pptx-agent",
        "get_bool": "pptx-agent",
        "get_int": "docx-agent, pptx-agent",
        "insert_in_order": "docx-agent, pptx-agent",
        "iter_descendants": "pptx-agent",
        "local_name": "docx-agent, pptx-agent",
        "make": "docx-agent, pptx-agent",
        "parse_xml": "docx-agent, pptx-agent, xlsx-agent",
        "prefixed_name": "docx-agent, pptx-agent",
        "qn": "docx-agent, pptx-agent",
        "register_child_order": "docx-agent, pptx-agent",
        "register_namespaces": "docx-agent, pptx-agent",
        "remove": "docx-agent, pptx-agent",
        "replace_choice": "pptx-agent",
        "serialize": "docx-agent, pptx-agent",
        "set_attr": "docx-agent, pptx-agent",
        "set_int": "pptx-agent",
        "subelement": "docx-agent, pptx-agent",
    },
}

#: (module, old underscore name) -> the public name; aliases kept for one release.
OLD_NAMES: dict[tuple[str, str], str] = {
    ("ooxml_edit.charts.chart", "_series_of"): "series_of",
    ("ooxml_edit.charts.create", "_options"): "check_options",
    ("ooxml_edit.charts.diagram", "_Model"): "DiagramModel",
    ("ooxml_edit.opc", "_part_for_rels"): "part_for_rels",
    ("ooxml_edit.xml", "_prefixed_name"): "prefixed_name",
    ("ooxml_edit.xml", "_ranks"): "child_ranks",
}


def _used() -> list[tuple[str, str]]:
    return [(module, name) for module, names in USED_BY.items() for name in names]


@pytest.mark.parametrize(("module", "name"), _used())
def test_a_name_a_sibling_imports_is_there(module, name):
    owner = importlib.import_module(module)
    if hasattr(owner, name):
        return
    try:
        importlib.import_module(f"{module}.{name}")     # a submodule not imported yet
    except ImportError:
        pytest.fail(f"{module}.{name} is gone, and {USED_BY[module][name]} import it: keep the "
                    "old name as an alias for one release and move them to the new one")


def test_siblings_import_no_private_name():
    private = [f"{module}.{name}" for module, name in _used() if name.startswith("_")]
    assert not private, f"give these a public name (keep the old one as an alias): {private}"


def test_an_old_name_is_the_public_one():
    for (module, old), new in OLD_NAMES.items():
        owner = importlib.import_module(module)
        assert getattr(owner, old) is getattr(owner, new), f"{module}.{old} is not {new}"
        assert new in USED_BY.get(module, {}), f"{module}.{new} is missing from USED_BY"


def _documented() -> set[tuple[str, str]]:
    lines = DOC.read_text(encoding="utf-8").splitlines()
    if SECTION is not None:
        start, depth = lines.index(SECTION) + 1, len(SECTION) - len(SECTION.lstrip("#"))
        end = next((i for i in range(start, len(lines)) if lines[i].startswith("#")
                    and len(lines[i]) - len(lines[i].lstrip("#")) <= depth), len(lines))
        lines = lines[start:end]
    found = set()
    for line in lines:
        if line.startswith("- `"):
            module, *names = re.findall(r"`([^`]+)`", line)
            found.update((module, name) for name in names)
    return found


def test_the_documented_names_are_these():
    documented, used = _documented(), set(_used())
    assert not used - documented, f"add to {DOC.name}: {sorted(used - documented)}"
    extra = sorted(documented - used)
    assert not extra, f"add to USED_BY, or take out of {DOC.name}: {extra}"
