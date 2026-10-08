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
``pages`` and ``range`` for documents); the handler of the other kind answers
``invalid_arguments`` naming the field to use instead.  Lengths are points, as everywhere in
the tool layer.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .registry import (CORE, Param, Tool, ToolGroup, array, boolean, build_schema,
                       free_object, integer, number, obj, string)
from .results import Result, ToolError, page_text

#: ``edit_chart``'s chart types for ``add`` (``ooxml_edit.charts.create``'s kinds).
CHART_TYPES = ("column", "stacked_column", "bar", "stacked_bar", "line", "pie", "scatter",
               "radar")

#: The group of the shared tools that are not core: loaded on demand.
MISC = "shared_misc"

GROUPS = [ToolGroup(MISC, "Session housekeeping, charts, SmartArt and document properties.")]

#: Every document kind a shared tool may serve.
KINDS = ("pptx", "docx")

_DOC = string("Document id.")
_TARGET = string("Address as a read tool printed it, or $ref.")


def _slides(what: str) -> Param:
    return array(integer(minimum=1), f"Decks: slide numbers from 1{what}.", optional=True)


def _pages(what: str) -> Param:
    return array(integer(minimum=1), f"Documents: page numbers from 1{what}.", optional=True)


_RANGE = string("Documents: only within this block or span (p:A..t:B).", optional=True)
_STORIES = string("Documents: body (default) or all stories.", enum=["body", "all"],
                  optional=True)
_CURSOR = string("next_cursor of the previous page.", optional=True)


class Spec:
    """One shared tool's definition, without handlers."""

    def __init__(self, name: str, description: str, params: Mapping[str, Param], *,
                 group: str = CORE, mutates: bool = False, exactly_one: tuple = (),
                 documents: Any = ("doc",), route: str | None = None, strict: bool = True,
                 batchable: bool = True, composite: bool = False,
                 refs: tuple[str, ...] = (),
                 reads: Callable[[Mapping[str, Any]], bool] | None = None) -> None:
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
        self.reads = reads

    def make(self, handlers: Mapping[str | None, Callable[..., Any]]) -> Tool:
        return Tool(name=self.name, description=self.description,
                    schema=build_schema(self.params), handlers=dict(handlers),
                    group=self.group, mutates=self.mutates, documents=self.documents,
                    exactly_one=self.exactly_one, route=self.route, strict=self.strict,
                    batchable=self.batchable, composite=self.composite, refs=self.refs,
                    reads=self.reads)

    @property
    def schema(self) -> dict[str, Any]:
        return build_schema(self.params)


def _no_documents(arguments: Mapping[str, Any]) -> list[str]:
    return []


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
         {"kind": string("pptx: a deck; docx: a document.", enum=list(KINDS)),
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
          "format": string("potx/dotx: a template; outline (decks) or markdown (documents): "
                           "a text copy.",
                           enum=["pptx", "potx", "docx", "dotx", "markdown", "outline"])},
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
         "revisions, fields, tables, drawings, pages. Both: the problems it opened with.",
         {"doc": _DOC}),
    # S6
    Spec("find_text",
         "Find text: each match's address, kind and context, in order, across slides, notes, "
         "tables, SmartArt and a document's stories.",
         {"doc": _DOC,
          "text": string("Text to find."),
          "regex": boolean("text is a Python regex. Default false.", optional=True),
          "slides": _slides(". Default all"),
          "range": _RANGE,
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
          "slides": _slides(", at most 4"),
          "pages": _pages(", at most 4"),
          "width": integer("Pixels, 200-2576. Default 1280 (slides), 1000 (pages).",
                           minimum=200, maximum=2576, optional=True)},
         batchable=False),
    # S9
    Spec("check",
         "Facts as the document will show: text that does not fit, collisions, validation "
         "problems, and on request design facts. Facts, not verdicts.",
         {"doc": _DOC,
          "slides": _slides(". Default all"),
          "pages": _pages(". Default all"),
          "include": array(string(enum=[
              "fit", "collisions", "facts", "design", "validate", "reflow", "fields", "app"]),
              "Default: fit, collisions, validate (decks); validate, reflow, fields "
              "(documents).", optional=True),
          "boxes": boolean("Decks: also text boxes that overlap where their text does not. "
                           "Default false.", optional=True)}),
    # S10, S11: edit_chart reads too
    Spec("edit_chart",
         "Add, read or change a chart. add: a new chart from data, styled as Office inserts "
         "one. read: type, data, formats, and what Edit Data holds. Other actions change data, "
         "titles, legend, data labels or gap width; values and workbook change together.",
         {"doc": _DOC,
          "target": string("The chart; add: decks the slide, documents the paragraph to "
                           "follow. Or $ref."),
          "action": string("What to do; read changes nothing.", enum=[
              "read", "add", "set_values", "set_value", "add_category", "remove_category",
              "rename_category", "add_series", "remove_series", "rename_series", "set_title",
              "set_axis_title", "set_legend", "show_data_labels", "hide_data_labels",
              "set_gap_width"]),
          "chart_type": string("add: the kind of chart.", enum=list(CHART_TYPES), optional=True),
          "categories": array(string(), "add: category labels, in order.", optional=True),
          "data": array(obj({"name": string(), "values": array(number())}),
                        "add: each series' name and one value per category.", optional=True),
          "box": obj({"x": number(), "y": number(), "w": number(), "h": number()},
                     "add, decks: the frame, pt.", optional=True),
          "width": number("add, documents: width in pt. Default 432.", minimum=36,
                          maximum=1584, optional=True),
          "number_format": string("add: the values'; show_data_labels: the labels'. Excel "
                                  "code, e.g. #,##0.0.", optional=True),
          "series": string("Series name, or number from 0.", optional=True),
          "category": string("Category label, or number from 0.", optional=True),
          "values": array(number(), "One per category (set_values, add_series) or per "
                          "series (add_category); add: scatter x values.", optional=True),
          "value": number("set_value: the value; set_gap_width: percent of a bar's width.",
                          optional=True),
          "text": string("New name, label or title; add: the chart title.", optional=True),
          "axis": string("set_axis_title: which axis.", enum=["category", "value"],
                         optional=True),
          "position": string("set_legend, add: legend position; none hides it.",
                             enum=["right", "left", "top", "bottom", "none"],
                             optional=True),
          "ref": string("add: ref name for the new chart.", optional=True)},
         group=MISC, mutates=True, refs=("target",), reads=_reading_action),
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
         "Run calls to several tools as one: in order, all or none, one undo step, checks "
         "once at the end. Not for render, save_document or session tools.",
         {"ops": array(obj({"tool": string(),
                            "arguments": free_object("That tool's arguments.")}),
                       "The calls in order, at most 200; later ops may use earlier refs.",
                       min_items=1)},
         documents=_no_documents, mutates=True, strict=False, batchable=False,
         composite=True),
]}

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
    call.session.close(doc)
    return Result(summary=f"Closed {doc}", removed=[doc])


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

__all__ = ["CORE_NAMES", "GROUPS", "KINDS", "MISC", "SESSION_TOOLS", "SPECS", "Spec",
           "definition", "handler"]
