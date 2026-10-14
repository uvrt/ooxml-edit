"""The shared tools: one definition each, a handler per document kind.

Tools S1-S13 of the tool layer do the same job for a deck and a document -- open, save,
undo, describe, find, replace, render, check, charts, SmartArt, properties -- so each has
**one** canonical definition here, and each format library adds its handler for its own kind::

    from ooxml_edit.tools import shared

    @shared.handler("render", kind="pptx")
    def render(call, doc, slides=None, pages=None, width=None):
        ...

    TOOLS = [render, ..., *shared.SESSION_TOOLS]

The toolbox merges same-named tools whose definitions are identical into one tool with a
handler per kind (and refuses two different definitions under one name), so a deck library
and a document library loaded together give the model one ``render``.

``batch`` runs other calls as one (see :mod:`.dispatch`); it is never sent strict, because
its ops' arguments are free-form objects, and the dispatcher validates each op against its
own tool instead.

How a call finds its handler:

* a tool that names a document (``doc``) runs the handler for that document's kind;
* ``new_document`` names no document yet: its ``kind`` argument picks the handler;
* ``describe`` gives the document at a glance, its own way per kind: one schema (``doc``)
  and one description saying what each kind returns;
* ``edit_chart`` also reads (``action: "read"``): that call runs as a reading one
  (``Tool.reads``), with no undo step and no checks;
* ``open_document``, ``close_document``, ``undo`` and ``read_blob``
  (an input's text, a page at a time) work the same for every kind, and their handlers
  are here, in :data:`SESSION_TOOLS`.  Both libraries list the same objects; the toolbox
  keeps one.

A parameter that applies to one format says so in its description (``slides`` for decks,
``pages`` and ``range`` for documents, ``find_text``'s ``range`` for workbooks too: a sheet,
range or table); the handler of another kind answers ``invalid_arguments`` naming the field
to use instead.  Lengths are points, as everywhere in the tool layer.

The kinds (:data:`KINDS`) are ``ooxml_common.kinds``' main kind per application: ``pptx``,
``docx`` and ``xlsx``.  The enums that name them (``new_document``'s ``kind``,
``save_document``'s ``format``) are static, like the descriptions: one definition whichever
libraries are loaded, so that two libraries' tools merge.  A toolbox without a kind's
library answers a call for it ``invalid_arguments`` with the kinds it has.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Mapping

from .. import provenance
from . import sources
from .registry import (CORE, Param, Tool, ToolGroup, array, boolean, build_schema,
                       free_object, integer, number, obj, string)
from .results import Result, ToolError, page_text

#: ``edit_chart``'s chart types for ``add`` (``ooxml_edit.charts.create``'s kinds).
CHART_TYPES = ("column", "stacked_column", "bar", "stacked_bar", "line", "pie", "scatter",
               "radar", "radar_filled", "bubble")

#: The group of the shared tools that are not core: loaded on demand.
MISC = "shared_misc"

GROUPS = [ToolGroup(MISC, "Session housekeeping, charts, SmartArt and document properties.")]

#: Every document kind a shared tool may serve.
KINDS = ("pptx", "docx", "xlsx")

_DOC = string("Document id.")
_TARGET = string("Address as a read tool printed it, or $ref.")


def _slides(what: str) -> Param:
    return array(integer(minimum=1), f"Decks: slide numbers from 1{what}.", optional=True)


def _slide_addresses(what: str) -> Param:
    """``render``'s and ``check``'s slides: a number, or an address -- ``s:<id>`` or a ``$ref``
    -- which stays the same slide while other calls add or move slides.  A string to the
    provider (the subset has no unions); a number still passes (``orInteger``)."""
    return array(string(or_integer=1), f"Decks: slide numbers from 1, or ids (s:256, $ref){what}.",
                 optional=True)


def _pages(what: str) -> Param:
    return array(integer(minimum=1), f"Documents: page numbers from 1{what}.", optional=True)


_RANGE = string("Documents: only within this block or span (p:A..t:B).", optional=True)
_FIND_RANGE = string("Documents: only within this block or span (p:A..t:B). Workbooks: a "
                     "sheet, range or table.", optional=True)
_STORIES = string("Documents: body (default) or all stories. Decks: body is the slides; "
                  "layouts, masters, or all.", enum=["body", "all", "layouts", "masters"],
                  optional=True)
_CURSOR = string("next_cursor of the previous page.", optional=True)


class Spec:
    """One shared tool's definition, without handlers."""

    def __init__(self, name: str, description: str, params: Mapping[str, Param], *,
                 group: str = CORE, mutates: bool = False, exactly_one: tuple = (),
                 documents: Any = ("doc",), route: str | None = None, strict: bool = True,
                 batchable: bool = True, composite: bool = False,
                 refs: tuple[str, ...] = (), datasets: tuple[str, ...] = (),
                 sources: tuple[str, ...] = (),
                 reads: Callable[[Mapping[str, Any]], bool] | None = None,
                 wrap: Callable[[Callable[..., Any]], Callable[..., Any]] | None = None,
                 aliases: Mapping[str, Any] | None = None) -> None:
        self.name = name
        self.description = description
        self.params = dict(params)
        self.group = group
        self.mutates = mutates
        self.exactly_one = exactly_one
        self.documents = documents
        self.route = route
        self.strict = strict
        self.batchable = batchable
        self.composite = composite
        self.refs = refs
        self.datasets = datasets
        self.sources = sources
        self.reads = reads
        #: Argument names read as the tool's own (:attr:`~.registry.Tool.aliases`).
        self.aliases = dict(aliases or {})
        #: What every kind's handler runs inside: the shared part of a tool's work (taking
        #: ``edit_chart``'s rows from a source), done once here for every library.
        self.wrap = wrap

    def make(self, handlers: Mapping[str | None, Callable[..., Any]]) -> Tool:
        if self.wrap is not None:
            handlers = {kind: self.wrap(fn) for kind, fn in handlers.items()}
        return Tool(name=self.name, description=self.description,
                    schema=build_schema(self.params), handlers=dict(handlers),
                    group=self.group, mutates=self.mutates, documents=self.documents,
                    exactly_one=self.exactly_one, route=self.route, strict=self.strict,
                    batchable=self.batchable, composite=self.composite, refs=self.refs,
                    datasets=self.datasets, sources=self.sources, reads=self.reads,
                    aliases=self.aliases)

    @property
    def schema(self) -> dict[str, Any]:
        return build_schema(self.params)


def _no_documents(arguments: Mapping[str, Any]) -> list[str]:
    return []


def _slide_on_add(arguments: Mapping[str, Any]) -> str | None:
    """``edit_chart``'s ``slide``: its ``target`` when adding (a deck's slide); in another
    action the target is the chart, so ``slide`` stays an unknown argument."""
    return "target" if arguments.get("action") == "add" else None


def _reading_action(arguments: Mapping[str, Any]) -> bool:
    """``action: "read"``: the call reads, changing nothing."""
    return arguments.get("action") == "read"


SPECS: dict[str, Spec] = {spec.name: spec for spec in [
    # S1
    Spec("open_document",
         "Open an input blob as a deck or document. Returns its doc id and a summary; "
         "describe it next.",
         {"blob": string("Blob handle, e.g. b1.")},
         documents=_no_documents, batchable=False),
    # S2
    Spec("new_document",
         "Make a new deck or document, optionally from a template blob (its layouts, styles "
         "and theme, not its content). Returns its doc id.",
         {"kind": string("pptx: a deck; docx: a document; xlsx: a workbook.",
                         enum=list(KINDS)),
          "template_blob": string("Template blob handle (.potx, .dotx...).", optional=True),
          "size": string("Decks 16:9 (default) or 4:3; documents A4, Letter...",
                         optional=True),
          "title": string("Title property.", optional=True),
          "author": string("Author property.", optional=True),
          "name": string("Name, e.g. plan.pptx. Default new.<kind>.", optional=True)},
         documents=_no_documents, route="kind", batchable=False),
    # S3
    Spec("save_document",
         "Give the document to the application as a file. Refuses new validation problems. "
         "Returns the name, format, size and validation report, never the content.",
         {"doc": _DOC,
          "name": string("File name, e.g. q3-review.pptx."),
          "format": string("potx/dotx/xltx: a template; outline (decks) or markdown "
                           "(documents): a text copy.",
                           enum=["pptx", "potx", "docx", "dotx", "xlsx", "xlsm", "xltx",
                                 "markdown", "outline"])},
         batchable=False),
    # S4 (list_documents went post-T4: the application names the documents and inputs)
    Spec("close_document", "Close a document, discarding unsaved changes. The session holds "
         "a few documents at a time: close a source once you have taken what you need.",
         {"doc": _DOC}, group=MISC, batchable=False),
    # S5
    Spec("undo",
         "Undo the last changing calls, one call per step; redo=true steps forward again. "
         "Returns the version.",
         {"doc": _DOC,
          "steps": integer("Steps, 1-50. Default 1.", minimum=1, maximum=50, optional=True),
          "redo": boolean("Redo instead. Default false.", optional=True),
          "scope": string("Decks: only this slide's changes, e.g. 256. Default: the latest, "
                          "anywhere.", optional=True)},
         batchable=False),
    # S16: describe, one schema for every kind
    Spec("describe",
         "The document at a glance; call once, first. Decks: slides (id, title, layout, "
         "content area), size, theme colours, fonts, roles and tints, layouts and their "
         "placeholders. Documents: headings with ids, sections, styles in use, comments, "
         "revisions, fields, tables, drawings, pages. Workbooks: sheets, tables and regions "
         "with key columns, column types and fill, sample rows, flags. All: the problems it "
         "opened with.",
         {"doc": _DOC}),
    # S6
    Spec("find_text",
         "Find text: each match's address, kind and context, in order, across slides, notes, "
         "tables, SmartArt, a document's stories and a workbook's cells, names and comments.",
         {"doc": _DOC,
          "text": string("Text to find."),
          "regex": boolean("text is a Python regex. Default false.", optional=True),
          "slides": _slides(". Default all"),
          "range": _FIND_RANGE,
          "stories": _STORIES,
          "cursor": _CURSOR}),
    # S7
    Spec("replace_text",
         "Replace text, keeping its formatting. expect=one needs exactly one match (else the "
         "error lists them); all replaces every match. Returns the count and addresses.",
         {"doc": _DOC,
          "find": string("Text to replace."),
          "replace": string("Replacement; with regex, \\1 is a group."),
          "expect": string("Matches expected.", enum=["one", "all"]),
          "regex": boolean("find is a Python regex. Default false.", optional=True),
          "slides": _slides(". Default all"),
          "range": _RANGE,
          "stories": _STORIES},
         mutates=True),
    # S8
    Spec("render",
         "Render slides or pages to PNG to look at them, at most 4 per call. Images cost "
         "tokens; the result states how many.",
         {"doc": _DOC,
          "slides": _slide_addresses(", at most 4"),
          "pages": _pages(", at most 4"),
          "width": integer("Pixels, 200-2576. Default 1280 (slides), 1000 (pages).",
                           minimum=200, maximum=2576, optional=True)},
         batchable=False, refs=("slides[]",)),
    # S9
    Spec("check",
         "Facts as the document will show: text that does not fit, collisions, validation "
         "problems, and on request design facts. Facts, not verdicts.",
         {"doc": _DOC,
          "slides": _slide_addresses(". Default all"),
          "pages": _pages(". Default all"),
          "include": array(string(enum=[
              "fit", "collisions", "facts", "design", "validate", "reflow", "fields", "app"]),
              "Default: fit, collisions, validate (decks); validate, reflow, fields "
              "(documents).", optional=True),
          "boxes": boolean("Decks: also text boxes that overlap where their text does not. "
                           "Default false.", optional=True)},
         refs=("slides[]",)),
    # S10, S11: edit_chart reads too
    Spec("edit_chart",
         "Add, read or change a chart. add: a new chart, styled as Office inserts one. read: "
         "type, data, formats, scales, and what Edit Data holds. Others change data, titles, "
         "legend, labels, axes, colours, series type, text or gridlines; values and workbook "
         "change together.",
         {"doc": _DOC,
          "target": string("The chart; add: decks the slide, documents the paragraph to "
                           "follow. Or $ref."),
          "action": string("What to do; read changes nothing.", enum=[
              "read", "add", "set_values", "set_value", "add_category", "remove_category",
              "rename_category", "add_series", "remove_series", "rename_series", "set_title",
              "set_axis_title", "set_legend", "show_data_labels", "hide_data_labels",
              "set_gap_width", "set_axis_scale", "set_gridlines", "format_series",
              "format_point", "set_series_type", "set_text_style", "set_point_labels"]),
          "chart_type": string("add: the kind of chart; set_series_type: line or column.",
                               enum=list(CHART_TYPES), optional=True),
          "categories": array(string(), "add: category labels, in order.", optional=True),
          "data": array(obj({"name": string(), "values": array(number()),
                             "type": string("line: drawn as a line (column charts).",
                                            enum=["column", "line"], optional=True),
                             "size": array(number(), "Bubble sizes.", optional=True)}),
                        "add: each series' name and one value per category.", optional=True),
          "box": obj({"x": number(), "y": number(), "w": number(), "h": number()},
                     "add, decks: the frame, pt.", optional=True),
          "width": number("add, documents: width in pt. Default 432.", minimum=36,
                          maximum=1584, optional=True),
          "number_format": string("add: the values'; show_data_labels: the labels'. Excel "
                                  "code, e.g. #,##0.0.", optional=True),
          "series": string("Series name, or number from 0.", optional=True),
          "category": string("Category label, or number from 0.", optional=True),
          "values": array(number(), "One per category (set_values, add_series) or series "
                          "(add_category); add: scatter, bubble x values.",
                          optional=True),
          "value": number("set_value: the value; set_gap_width: % of a bar's width.",
                          optional=True),
          "text": string("New name, label or title; add: the chart title.", optional=True),
          "axis": string("set_axis_title, set_axis_scale, set_gridlines; add with scale.",
                         enum=["category", "value", "x", "y"], optional=True),
          "scale": obj({"min": number(optional=True), "max": number(optional=True),
                        "major_unit": number(optional=True), "minor_unit": number(optional=True),
                        "number_format": string(optional=True),
                        "reverse": boolean("Categories top-down.", optional=True),
                        "auto": array(string(enum=["min", "max", "major_unit", "minor_unit",
                                                   "number_format"]),
                                      "Back to automatic.", optional=True)},
                       "set_axis_scale, add: the axis' scale; keys left out stay.",
                       optional=True),
          "gridlines": string("set_gridlines, add: which gridlines.",
                              enum=["none", "major", "minor", "both"], optional=True),
          "style": obj({"fill": string(optional=True), "line": string(optional=True),
                        "line_width": number(optional=True),
                        "dash": string(enum=["solid", "dash", "dot", "dash_dot", "long_dash"],
                                       optional=True),
                        "marker": string(enum=["none", "circle", "diamond", "square",
                                               "triangle", "dash", "dot", "x", "plus", "star"],
                                         optional=True),
                        "marker_size": integer(minimum=2, maximum=72, optional=True),
                        "marker_fill": string(optional=True), "font": string(optional=True),
                        "size": number(optional=True), "bold": boolean(optional=True),
                        "color": string(optional=True)},
                       "format_series, format_point: fill to marker_fill; set_text_style: "
                       "font to color. Colours as shapes take them, or none.", optional=True),
          "part": string("set_text_style: only this text. Default all.",
                         enum=["title", "axis_titles", "axis_labels", "data_labels",
                               "legend"], optional=True),
          "labels": array(string(), "set_point_labels: one per point.", optional=True),
          "cells": boolean("set_point_labels: also in the workbook (Value From Cells).",
                           optional=True),
          "secondary": boolean("set_series_type: on a second value axis.", optional=True),
          "position": string("set_legend, add: legend position, none hides it; "
                             "set_point_labels: label position.",
                             enum=["right", "left", "top", "bottom", "none", "center",
                                   "inside_end", "inside_base", "outside_end", "best_fit"],
                             optional=True),
          "ref": string("add: ref name for the new chart.", optional=True),
          "from": string("add, set_values, add_series: rows of d:<id> or table t:<name>.",
                         optional=True),
          "columns": array(string(), "from's category column, then series columns; default all.",
                           optional=True)},
         group=MISC, mutates=True, refs=("target",), sources=("from",),
         reads=_reading_action, wrap=lambda fn: _chart_rows(fn),
         aliases={"slide": _slide_on_add, "from_columns": "columns"}),
    # S12
    Spec("edit_smartart",
         "Change a SmartArt diagram's node text, or add and remove nodes. Returns the nodes.",
         {"doc": _DOC, "target": _TARGET,
          "action": string("What to do.", enum=["set_text", "add_node", "remove_node",
                                                "add_child"]),
          "node": integer("Node number from 0 (add_child: the parent).", minimum=0,
                          optional=True),
          "text": string("The node's text.", optional=True)},
         group=MISC, mutates=True, refs=("target",)),
    # S13
    Spec("set_properties",
         "Set the title, author, language or subject property. Returns them all.",
         {"doc": _DOC,
          "title": string("Title.", optional=True),
          "author": string("Author.", optional=True),
          "language": string("Editing language, e.g. en-US.", optional=True),
          "subject": string("Subject.", optional=True)},
         group=MISC, mutates=True),
    # S15
    Spec("read_blob",
         "Read a text input blob (CSV, Markdown, plain text, JSON) a page at a time. Open "
         "documents with open_document; place images with a picture tool.",
         {"blob": string("Blob handle, e.g. b2."),
          "cursor": _CURSOR},
         group=MISC, documents=_no_documents, batchable=False),
    # the generic batch
    Spec("batch",
         "Run calls to several tools in order as one undo step, checks once at the end; a "
         "failing op is skipped with its error. Not for render, save_document or session "
         "tools.",
         {"ops": array(obj({"tool": string(),
                            "arguments": free_object("That tool's arguments.")}),
                       "The calls in order, at most 200; later ops may use earlier refs.",
                       min_items=1),
          "atomic": boolean("All or none.", optional=True)},
         documents=_no_documents, mutates=True, strict=False, batchable=False,
         composite=True),
]}

# -- edit_chart's rows from a source ------------------------------------------------------------

#: The ``edit_chart`` actions that take ``from``.
FROM_ACTIONS = ("add", "set_values", "add_series")


def _chart_rows(handler: Callable[..., Any]) -> Callable[..., Any]:
    """``edit_chart``'s handler for a kind, taking ``from`` and ``columns`` for it.

    With ``from`` (a dataset ``d:<id>``, or a table of an open document, ``t:<name>``;
    :mod:`.sources`) the categories and series come from the source's columns by name:
    ``columns`` is the category column (a scatter chart's x values), then a column per
    series (``add``), or one, the values (``set_values``, ``add_series``, matched to the
    chart's categories by label).  Without ``columns``, ``add`` takes every column, the first
    as the categories, and the others take a source of just two columns.  The kind's handler
    gets literal ``categories``, ``data`` and ``values``, as if the model had written them,
    and never sees ``from``.
    Its result gets ``from``: the source, its document and version, and per category and
    series column the rows placed (from 1, as runs) and their sources, merged.  A series
    value that is not a number is refused, naming its cell: text is never converted.

    The placement is also kept in the saved document, in the call's undo step: its
    :mod:`ooxml_edit.provenance` record goes into the chart part (``ooxml_edit.charts``
    notes the chart the handler used) and to the format's
    :attr:`~.session.DocumentFormat.store_provenance`, with the chart's address -- for
    ``add`` the last of the result's ``created``, else the first of its ``changed`` (else
    ``target``).  ``add`` replaces the chart's record; ``set_values`` replaces its
    categories and that series' entry, and ``add_series`` that series' entry, keeping the
    others (an entry from another source names its own).  An edit without ``from`` keeps
    the record true or drops what it no longer says (:data:`RECORD_ACTIONS`).
    ``action: "read"`` returns the chart part's record as ``from``, when it has one."""

    @functools.wraps(handler)
    def run(call: Any, **arguments: Any) -> Any:
        source = arguments.pop("from", None)
        columns = arguments.pop("columns", None)
        if source is None:
            if columns is not None:
                raise ToolError("invalid_arguments", "columns name the columns of from; "
                                "give from too, or the values in data", field="columns")
            if arguments.get("action") == "read":
                return _read_record(handler, call, arguments)
            if arguments.get("action") in RECORD_ACTIONS:
                return _edit_record(handler, call, arguments)
            return handler(call, **arguments)
        return _from_rows(handler, call, source, columns, arguments)

    return run


def _read_record(handler: Callable[..., Any], call: Any, arguments: dict[str, Any]) -> Any:
    """``action: "read"`` with the record kept in the chart's part, as ``from``."""
    with provenance.watch() as used:
        returned = handler(call, **arguments)
    data = returned.data if isinstance(returned, Result) else returned
    if len(used) == 1 and isinstance(data, dict) and "from" not in data:
        found = used[0].read()
        if found is not None:
            data["from"] = found
    return returned


def _from_rows(handler: Callable[..., Any], call: Any, source: str, columns: Any,
               arguments: dict[str, Any]) -> Result:
    action = arguments.get("action")
    if action not in FROM_ACTIONS:
        raise ToolError("invalid_arguments", f"{action} takes no from; from is for "
                        f"{', '.join(FROM_ACTIONS)}", field="from",
                        valid_options=list(FROM_ACTIONS))
    for name in ("categories", "data", "values"):
        if arguments.get(name) is not None:
            raise ToolError("invalid_arguments", f"from replaces {name}: name the columns "
                            "in columns instead", field=name)
    rows = call.source(source, field="from")
    names = rows.dataset.columns
    if columns:
        at = [rows.column(name, field=f"columns[{i}]") for i, name in enumerate(columns)]
        if len(set(at)) != len(at):
            raise ToolError("invalid_arguments", "a column is named twice", field="columns")
        fields = [f"columns[{i}]" for i in range(len(at))]
    else:
        # Every column, the first the categories, as the table tools place every column when
        # from_columns is left out; set_values and add_series only when there are just two.
        if action != "add" and len(names) != 2:
            raise ToolError("invalid_arguments", f"{rows.address} has {len(names)} columns: "
                            "name the category column and the values' in columns",
                            field="columns")
        at = list(range(len(names)))
        fields = ["from"] * len(at)
    chart_type = arguments.get("chart_type")
    if action == "add":
        if len(at) < 2:
            raise ToolError("invalid_arguments", "add from rows needs the category column "
                            "and at least one series column", field="columns")
        if chart_type == "bubble":
            raise ToolError("invalid_arguments", "a bubble chart's sizes are not taken from "
                            "a source: give categories and data", field="from")
        if not rows.dataset.rows:
            raise ToolError("refused", f"{rows.address} has no rows", field="from")
        order = list(range(len(rows.dataset.rows)))
        if chart_type == "scatter":
            arguments["values"] = _numbers(rows, at[0], order, fields[0])
        else:
            arguments["categories"] = _labels(call, rows, at[0], order, fields[0])
        arguments["data"] = [{"name": names[column],
                              "values": _numbers(rows, column, order, field)}
                             for column, field in zip(at[1:], fields[1:])]
        series = [names[column] for column in at[1:]]
    else:
        if len(at) != 2:
            raise ToolError("invalid_arguments", f"{action} from rows takes two columns: "
                            "the category column, then the values'", field="columns")
        read = handler(call, doc=arguments["doc"], target=arguments["target"], action="read")
        data = read.data if isinstance(read, Result) else read
        order = _matched(call, rows, at[0], list((data or {}).get("categories") or []),
                         fields[0])
        arguments["values"] = _numbers(rows, at[1], order, fields[1])
        if action == "add_series" and arguments.get("text") is None:
            arguments["text"] = names[at[1]]
        series = [arguments.get("text") if action == "add_series"
                  else arguments.get("series")]
    with provenance.watch() as used:
        returned = handler(call, **arguments)
    result = returned if isinstance(returned, Result) else Result(data=returned)
    placed = rows.describe()
    placed["categories"] = rows.provenance(at[0], order)
    placed["series"] = [{"name": name, **rows.provenance(column, order)}
                        for name, column in zip(series, at[1:])]
    if result.ok:
        _keep(call, rows, placed, used, _chart_address(action, result, arguments), action)
    if isinstance(result.data, dict):
        result.data["from"] = placed
    else:
        result.data = {"chart": result.data, "from": placed}
    return result


#: The ``edit_chart`` actions without ``from`` that change what a chart's record says:
#: ``rename_series`` renames the series' entry, ``remove_series`` drops it, ``set_values``
#: and ``set_value`` drop it (its values no longer come from the source),
#: ``rename_category`` drops the categories' entry, and ``add_category`` and
#: ``remove_category`` drop the record (every series' rows have changed).  A stale address
#: is worse than none.
RECORD_ACTIONS = ("set_values", "set_value", "add_category", "remove_category",
                  "rename_category", "remove_series", "rename_series")

#: Where a record's values came from: the record's own, or a series entry's when it differs.
_ORIGIN = ("source", "document", "version", "stale")


def _edit_record(handler: Callable[..., Any], call: Any, arguments: dict[str, Any]) -> Any:
    """An edit without ``from`` that changes the chart's data, with its record kept true."""
    with provenance.watch() as used:
        returned = handler(call, **arguments)
    result = returned if isinstance(returned, Result) else Result(data=returned)
    if not result.ok or len(used) != 1:
        return returned
    before = used[0].read()
    if before is None:
        return returned
    after = _updated(before, arguments, _series_names(result.data))
    if after != before:
        used[0].store(after)
        address = result.changed[0] if result.changed else arguments.get("target")
        if address is not None:
            call.store_provenance(address, after)
    return returned


def _series_names(data: Any) -> list[str] | None:
    """The chart's series names after an edit, from its result's ``series`` (``None``
    when the result does not say)."""
    series = data.get("series") if isinstance(data, dict) else None
    if not isinstance(series, list) or not all(isinstance(s, dict) for s in series):
        return None
    return [str(s.get("name")) for s in series]


def _updated(before: dict[str, Any], arguments: Mapping[str, Any],
             names: list[str] | None) -> dict[str, Any] | None:
    """``before`` after an edit without ``from``; ``None`` when nothing of it stays true."""
    action = arguments.get("action")
    if action in ("add_category", "remove_category") or names is None:
        return None
    after = dict(before)
    entries = [dict(entry) for entry in before.get("series") or []]
    if action == "rename_category":
        after.pop("categories", None)
    elif action in ("set_values", "set_value"):
        which = str(arguments.get("series"))
        if which not in names and which.isdigit() and int(which) < len(names):
            which = names[int(which)]
        entries = [entry for entry in entries if str(entry.get("name")) != which]
    elif action == "rename_series":
        gone = [entry for entry in entries if str(entry.get("name")) not in names]
        text = arguments.get("text")
        if len(gone) == 1 and text is not None and str(text) in names:
            gone[0]["name"] = text
    # Whatever the edit, an entry names a series the chart has.
    entries = [entry for entry in entries if str(entry.get("name")) in names]
    if entries:
        after["series"] = entries
    else:
        after.pop("series", None)
    if "series" not in after and "categories" not in after:
        return None
    return after


def _merged(before: dict[str, Any] | None, found: dict[str, Any],
            action: str) -> dict[str, Any]:
    """The chart's record after a placement from a source: ``add`` replaces it;
    ``set_values`` takes the new categories and the series' new entry, ``add_series`` the
    series' new entry, keeping the other series' entries.  An entry whose origin is not
    the record's names its own (``source``, ``document``, ``version``, ``stale``)."""
    if action == "add" or before is None:
        return found
    new = found.get("series") or []
    name = str(new[0].get("name")) if new else None
    if action == "set_values":
        top, out = _origin(found), {k: v for k, v in found.items() if k != "series"}
    else:
        top = _origin(before)
        out = {k: v for k, v in before.items() if k != "series"}
    fresh = [_with_origin(entry, _origin(found), top) for entry in new]
    entries: list[dict[str, Any]] = []
    for entry in before.get("series") or []:
        if str(entry.get("name")) == name:      # replaced where it stands
            entries += fresh
            fresh = []
            continue
        entries.append(_with_origin(entry, _origin(entry) or _origin(before), top))
    entries += fresh
    out["series"] = entries
    return out


def _origin(found: Mapping[str, Any]) -> dict[str, Any]:
    return {key: found[key] for key in _ORIGIN if key in found}


def _with_origin(entry: Mapping[str, Any], origin: dict[str, Any],
                 top: dict[str, Any]) -> dict[str, Any]:
    """``entry`` naming ``origin`` itself when it is not the record's (``top``)."""
    out = {key: value for key, value in entry.items() if key not in _ORIGIN}
    if origin != top:
        out = {"name": out.pop("name"), **origin, **out} if "name" in out else {**origin, **out}
    return out


def _chart_address(action: str, result: Result, arguments: Mapping[str, Any]) -> str | None:
    """The chart a placement went into: a new one last in ``created``, else the first
    ``changed``, else the call's ``target``."""
    if action == "add":
        return result.created[-1] if result.created else None
    return result.changed[0] if result.changed else arguments.get("target")


def _keep(call: Any, rows: Any, placed: Mapping[str, Any], used: list[Any],
          address: str | None, action: str) -> None:
    """Keep a chart placement's record, merged with the chart's (:func:`_merged`): in the
    chart's part, when the handler used one chart, and with the format, when the chart has
    an address."""
    found = provenance.record(placed, document=rows.name)
    if len(used) == 1:
        found = _merged(used[0].read(), found, action)
        used[0].store(found)
    if address is not None:
        call.store_provenance(address, found)


def _numbers(rows: Any, column: int, order: list[int], field: str) -> list[Any]:
    """A series' values from a column, in ``order``: numbers, or a refusal naming the cell."""
    out = []
    for row in order:
        value = rows.dataset.rows[row][column]
        if not sources.number(value):
            choose = ("name the category and series columns in columns, as without columns "
                      "every column after the first is a series" if field == "from"
                      else "choose another column")
            raise ToolError("invalid_arguments", f"{rows.where(row, column)} is "
                            f"{sources.what(value)}, not a number: a series takes numbers and "
                            f"text is never converted; fix the source or {choose}",
                            field=field)
        out.append(value)
    return out


def _labels(call: Any, rows: Any, column: int, order: list[int], field: str) -> list[str]:
    """Category labels from a column, in ``order``: text as it is, numbers written in the
    session's locale; a blank is refused, naming its cell."""
    out = []
    for row in order:
        text = sources.label(rows.dataset.rows[row][column], call.locale)
        if text is None:
            raise ToolError("invalid_arguments", f"{rows.where(row, column)} is blank: "
                            "a category needs a label", field=field)
        out.append(text)
    return out


def _key(value: Any, locale: Any) -> tuple[str, Any]:
    """What a category is matched by: its number when it is or reads as one, else its text."""
    if sources.number(value):
        return ("n", float(value))
    text = str(value).strip()
    separator = getattr(locale, "decimal_separator", ".")
    for candidate in (text, text.replace(separator, ".") if separator != "." else None):
        if candidate:
            try:
                number = float(candidate)
            except ValueError:
                continue
            if sources.number(number):
                return ("n", number)
    return ("t", str(value))


def _matched(call: Any, rows: Any, column: int, categories: list[Any],
             field: str) -> list[int]:
    """The row for each of the chart's categories, matched by label (a number by value):
    every category needs exactly one row, and every row a category."""
    locale = call.locale
    wanted = [_key(category, locale) for category in categories]
    if len(set(wanted)) != len(wanted):
        raise ToolError("ambiguous", "the chart has two categories alike, so rows cannot be "
                        "matched to them by label; set values by position instead",
                        field="from")
    found: dict[tuple[str, Any], int] = {}
    for row, record in enumerate(rows.dataset.rows):
        value = record[column]
        if sources.label(value, locale) is None:
            raise ToolError("invalid_arguments", f"{rows.where(row, column)} is blank: "
                            "a row is matched to a category by its label", field=field)
        key = _key(value, locale)
        if key in found:
            raise ToolError("ambiguous", f"rows {found[key] + 1} and {row + 1} of "
                            f"{rows.address} have the same category", field=field)
        found[key] = row
    missing = [str(category) for category, key in zip(categories, wanted) if key not in found]
    if missing:
        raise ToolError("not_found", f"no row of {rows.address} for the chart's "
                        f"categor{'y' if len(missing) == 1 else 'ies'} "
                        f"{', '.join(missing[:5])}" + (" and more" if len(missing) > 5 else ""),
                        field=field, valid_options=[str(c) for c in categories][:50])
    extra = sorted(set(found.values()) - {found[key] for key in wanted})
    if extra:
        raise ToolError("refused", f"row{'s' if len(extra) > 1 else ''} "
                        f"{sources.runs(extra)} of {rows.address} match no category of the "
                        "chart: add_category first, or leave them out of the source",
                        field="from")
    return [found[key] for key in wanted]


#: The shared tools in the core group (never deferred).
CORE_NAMES = [name for name, spec in SPECS.items() if spec.group == CORE]


def handler(name: str, *, kind: str) -> Callable[[Callable[..., Any]], Tool]:
    """A decorator: the shared tool ``name`` with ``kind``'s handler.

    The tool's definition is the shared one; only the handler is the library's.  Put the
    returned :class:`Tool` in the library's tool list.
    """
    if name not in SPECS:
        raise KeyError(f"no shared tool {name!r}; one of {sorted(SPECS)}")
    if kind not in KINDS:
        raise ValueError(f"kind {kind!r}; one of {KINDS}")
    spec = SPECS[name]

    def make(fn: Callable[..., Any]) -> Tool:
        return spec.make({kind: fn})

    return make


def definition(name: str) -> Tool:
    """The shared tool ``name`` without handlers: for tests and listings."""
    return SPECS[name].make({})


# -- the handlers every kind shares ------------------------------------------------------------


def _composite(call: Any, **arguments: Any) -> None:
    raise ToolError("internal", "batch is run by the toolbox itself")


def _open_document(call: Any, blob: str) -> Result:
    session = call.session
    doc_id = session.open_blob(blob)
    entry = session.entry(doc_id)
    fmt = session.formats.get(entry.kind)
    data: dict[str, Any] = entry.describe()
    data["baseline_problems"] = len(entry.baseline_problems)
    if fmt is not None and fmt.summary is not None:
        data["summary"] = fmt.summary(entry.document)
    return Result(summary=f"Opened {entry.name} as {doc_id} ({entry.kind})", created=[doc_id],
                  data=data)


def _close_document(call: Any, doc: str) -> Result:
    kept = call.session.close(doc)
    if not kept:
        return Result(summary=f"Closed {doc}", removed=[doc])
    return Result(summary=f"Closed {doc}; the datasets derived from it stay: "
                  f"{', '.join(kept)}", removed=[doc], data={"datasets": kept})


def _undo(call: Any, doc: str, steps: int = 1, redo: bool = False,
          scope: str | None = None) -> Result:
    session = call.session
    done = (session.redo(doc, steps, scope=scope) if redo
            else session.undo(doc, steps, scope=scope))
    verb = "Redid" if redo else "Undid"
    where = f" of {scope}" if scope is not None else ""
    if done == 0:
        raise ToolError("refused", f"nothing{where} to {'redo' if redo else 'undo'} in {doc}")
    entry = session.entry(doc)
    entry.check_cache.clear()
    if scope is None:
        entry.restore_refs()
    data = {"steps": done, "version": entry.version}
    if scope is not None:
        data["scope"] = scope
    result = Result(summary=f"{verb} {done} step(s){where} of {doc}", changed=[doc], data=data)
    if scope is not None and done < steps:
        result.warnings.append(f"{done} of {steps} step(s): no more{where}, or the next is "
                               "entangled with changes outside it")
    return result


#: Text a blob may hold, by type; anything else that decodes as UTF-8 is read too.
_TEXT_TYPES = ("text/", "application/json", "application/csv", "application/xml")


def _read_blob(call: Any, blob: str, cursor: str | None = None) -> Result:
    found = call.blob(blob)
    mime = found.mime or "application/octet-stream"
    if mime.startswith("image/"):
        raise ToolError("invalid_arguments", f"{blob} is an image ({mime}); read_blob reads "
                        "text. Use a picture tool to place it.", field="blob")
    if found.data.startswith(b"PK"):
        raise ToolError("invalid_arguments", f"{blob} is a package ({found.name}); open it "
                        "with open_document.", field="blob")
    try:
        text = found.data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ToolError("invalid_arguments", f"{blob} ({mime}) is not UTF-8 text",
                        field="blob") from None
    if not mime.startswith(_TEXT_TYPES) and "\x00" in text:
        raise ToolError("invalid_arguments", f"{blob} ({mime}) is binary, not text",
                        field="blob")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    page, next_cursor = page_text(text, cursor=cursor,
                                  limit=max(1000, call.limits.max_result_chars - 2000))
    start = int(cursor[1:]) if cursor else 0
    data = {"blob": blob, "name": found.name, "mime": mime, "chars": len(text),
            "lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1),
            "from": start, "text": page}
    return Result(summary=f"Read {len(page):,} of {len(text):,} characters of {found.name}",
                  data=data, next_cursor=next_cursor)


#: The shared tools whose handlers serve every kind.  Each format library lists these same
#: objects in its tools; the toolbox keeps one of each.
SESSION_TOOLS: list[Tool] = [
    SPECS["open_document"].make({None: _open_document}),
    SPECS["close_document"].make({None: _close_document}),
    SPECS["undo"].make({None: _undo}),
    SPECS["batch"].make({None: _composite}),
    SPECS["read_blob"].make({None: _read_blob}),
]

__all__ = ["CORE_NAMES", "FROM_ACTIONS", "GROUPS", "KINDS", "MISC", "SESSION_TOOLS", "SPECS", "Spec",
           "definition", "handler"]
