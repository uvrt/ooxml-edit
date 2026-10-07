"""The shared tools: one definition each, a handler per document kind.

Tools S1-S13 of the tool layer do the same job for a deck and a document -- open, save,
undo, find, replace, render, check, charts, SmartArt, properties -- so each has **one**
canonical definition here, and each format library adds its handler for its own kind::

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
* ``open_document``, ``list_documents``, ``close_document``, ``undo`` and ``read_blob``
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

#: The group of the shared tools that are not core: loaded on demand.
MISC = "shared_misc"

GROUPS = [ToolGroup(MISC, "Session housekeeping, charts, SmartArt and document properties.")]

#: Every document kind a shared tool may serve.
KINDS = ("pptx", "docx")

_DOC = string("Document id, e.g. d1.")
_TARGET = string("Address of the object, exactly as a read tool printed it.")


def _slides(what: str) -> Param:
    return array(integer("Slide number.", minimum=1),
                 f"Decks only: slide numbers from 1, {what}.", optional=True)


def _pages(what: str) -> Param:
    return array(integer("Page number.", minimum=1),
                 f"Documents only: page numbers from 1, {what}.", optional=True)


_RANGE = string("Documents only: a block or span (p:A..t:B) to search in.", optional=True)
_STORIES = string("Documents only: body (default) or all stories.", enum=["body", "all"],
                  optional=True)


class Spec:
    """One shared tool's definition, without handlers."""

    def __init__(self, name: str, description: str, params: Mapping[str, Param], *,
                 group: str = CORE, mutates: bool = False, exactly_one: tuple = (),
                 documents: Any = ("doc",), route: str | None = None, strict: bool = True,
                 batchable: bool = True, composite: bool = False,
                 refs: tuple[str, ...] = ()) -> None:
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

    def make(self, handlers: Mapping[str | None, Callable[..., Any]]) -> Tool:
        return Tool(name=self.name, description=self.description,
                    schema=build_schema(self.params), handlers=dict(handlers),
                    group=self.group, mutates=self.mutates, documents=self.documents,
                    exactly_one=self.exactly_one, route=self.route, strict=self.strict,
                    batchable=self.batchable, composite=self.composite, refs=self.refs)

    @property
    def schema(self) -> dict[str, Any]:
        return build_schema(self.params)


def _no_documents(arguments: Mapping[str, Any]) -> list[str]:
    return []


SPECS: dict[str, Spec] = {spec.name: spec for spec in [
    # S1
    Spec("open_document",
         "Open an input the user supplied (a blob) as a deck or document. Returns its doc id, "
         "kind and a short summary; then use the format's describe tool.",
         {"blob": string("Blob handle of the input, e.g. b1.")},
         documents=_no_documents, batchable=False),
    # S2
    Spec("new_document",
         "Make a new, empty deck or document, optionally from a template blob (its masters, "
         "layouts, styles and theme, without its content). Returns its doc id.",
         {"kind": string("pptx for a deck, docx for a document.", enum=list(KINDS)),
          "template_blob": string("Blob handle of a template (.potx/.pptx or .dotx/.docx) "
                                  "to start from.", optional=True),
          "size": string("Decks: slide size 16:9 (default) or 4:3. Documents: page size A4 "
                         "or Letter.", optional=True),
          "title": string("Document title property.", optional=True),
          "author": string("Author property.", optional=True),
          "name": string("Name for the document, e.g. plan.pptx. Default: new.<kind>.",
                         optional=True)},
         documents=_no_documents, route="kind", batchable=False),
    # S3
    Spec("save_document",
         "Hand the document to the application as a file. Refuses new validation problems. "
         "Returns the name, format, size and validation report, never the content.",
         {"doc": _DOC,
          "name": string("File name for the application, e.g. q3-review.pptx."),
          "format": string("pptx or potx (template) for decks, docx or dotx for documents; "
                           "markdown (documents) or outline (decks) for a text copy.",
                           enum=["pptx", "potx", "docx", "dotx", "markdown", "outline"])},
         batchable=False),
    # S4
    Spec("list_documents",
         "List the open documents (ids, kinds, names, versions) and the user's inputs "
         "(blob handles, names, types).",
         {}, group=MISC, documents=_no_documents, batchable=False),
    Spec("close_document",
         "Close an open document, discarding unsaved changes. Save first to keep them.",
         {"doc": _DOC}, group=MISC, batchable=False),
    # S5
    Spec("undo",
         "Undo the last calls that changed a document, one step per call; redo=true steps "
         "forward again. Returns the version and how many steps were taken.",
         {"doc": _DOC,
          "steps": integer("How many calls to undo or redo, 1 to 50. Default 1.", minimum=1,
                           maximum=50, optional=True),
          "redo": boolean("True to redo instead of undo. Default false.", optional=True)},
         batchable=False),
    # S6
    Spec("find_text",
         "Find text: every match's address, kind and context, in order (slides, notes, "
         "tables, SmartArt; a document's stories).",
         {"doc": _DOC,
          "text": string("Text to find."),
          "regex": boolean("text is a Python regular expression. Default false.",
                           optional=True),
          "slides": _slides("to search. Default: all"),
          "range": _RANGE,
          "stories": _STORIES,
          "cursor": string("next_cursor from the previous page of matches.", optional=True)}),
    # S7
    Spec("replace_text",
         "Replace text, keeping its formatting. expect=one: exactly one match must exist "
         "(else an error lists them); all: every match. Returns the count and addresses.",
         {"doc": _DOC,
          "find": string("Text to replace."),
          "replace": string("Replacement; with regex, \\1 is a group."),
          "expect": string("one or all.", enum=["one", "all"]),
          "regex": boolean("find is a Python regular expression. Default false.",
                           optional=True),
          "slides": _slides("to search. Default: all"),
          "range": _RANGE,
          "stories": _STORIES},
         mutates=True),
    # S8
    Spec("render",
         "Render slides or pages to PNG images, to look at the result. Costs image tokens "
         "(stated in the result); at most 4 images per call.",
         {"doc": _DOC,
          "slides": _slides("to render, at most 4"),
          "pages": _pages("to render, at most 4"),
          "width": integer("Image width in pixels, 200 to 2576. Default 1280 for slides, 1000 "
                           "for pages.", minimum=200, maximum=2576, optional=True)},
         batchable=False),
    # S9
    Spec("check",
         "Facts as the document will show: text that does not fit, collisions, validation "
         "problems. Facts, not verdicts.",
         {"doc": _DOC,
          "slides": _slides("to check. Default: all"),
          "pages": _pages("to check. Default: all"),
          "include": array(string("A kind of fact.", enum=[
              "fit", "collisions", "facts", "design", "validate", "reflow", "fields", "app"]),
              "What to report. Default: decks fit, collisions, validate; documents "
              "validate, reflow, fields.", optional=True),
          "boxes": boolean("Decks: also text boxes that overlap where their text does not. "
                           "Default false.", optional=True)}),
    # S10
    Spec("edit_chart",
         "Change a chart's data or labels; the drawn values and the embedded workbook change "
         "together. Returns the chart's state after the edit.",
         {"doc": _DOC, "target": _TARGET,
          "action": string("What to do.", enum=[
              "set_values", "set_value", "add_category", "remove_category", "rename_category",
              "add_series", "remove_series", "rename_series", "set_title", "set_axis_title",
              "set_legend"]),
          "series": string("Series name, or its number from 0.", optional=True),
          "category": string("Category label, or its number from 0.", optional=True),
          "values": array(number("A value."), "Values, one per category (set_values, "
                          "add_series) or per series (add_category).", optional=True),
          "value": number("The value (set_value).", optional=True),
          "text": string("New name, label or title text.", optional=True),
          "axis": string("Which axis (set_axis_title).", enum=["category", "value"],
                         optional=True),
          "position": string("Legend position (set_legend); none hides it.",
                             enum=["right", "left", "top", "bottom", "none"],
                             optional=True)},
         group=MISC, mutates=True, refs=("target",)),
    # S11
    Spec("read_chart",
         "Read a chart: type, categories, series and values as drawn, number formats, and "
         "what Edit Data holds.",
         {"doc": _DOC, "target": _TARGET}, group=MISC, refs=("target",)),
    # S12
    Spec("edit_smartart",
         "Change a SmartArt diagram's node text, or add and remove nodes. Returns the nodes "
         "with their text and levels.",
         {"doc": _DOC, "target": _TARGET,
          "action": string("What to do.", enum=["set_text", "add_node", "remove_node",
                                                "add_child"]),
          "node": integer("Node number, from 0 (set_text, remove_node, add_child: the "
                          "parent).", minimum=0, optional=True),
          "text": string("The node's text.", optional=True)},
         group=MISC, mutates=True, refs=("target",)),
    # S13
    Spec("set_properties",
         "Set document properties: title, author, language, subject. Returns them all.",
         {"doc": _DOC,
          "title": string("Title.", optional=True),
          "author": string("Author.", optional=True),
          "language": string("Default editing language, e.g. en-US.", optional=True),
          "subject": string("Subject.", optional=True)},
         group=MISC, mutates=True),
    # S15
    Spec("read_blob",
         "Read an input the user supplied as text (CSV, Markdown, plain text, JSON), a page "
         "at a time. Not for images or documents: open those with their tools.",
         {"blob": string("Blob handle of the input, e.g. b2."),
          "cursor": string("next_cursor from the previous page.", optional=True)},
         group=MISC, documents=_no_documents, batchable=False),
    # the generic batch
    Spec("batch",
         "Run several tool calls as one: in order, all or none, one undo step, checks once "
         "at the end. Not for render, save_document or session tools.",
         {"ops": array(obj({"tool": string("Tool name."),
                            "arguments": free_object("Its arguments, as that tool takes them.")},
                           "One call."),
                       "The calls, in order; at most 200. Later ops may use earlier ops' refs.",
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


def _list_documents(call: Any) -> Result:
    data = call.session.describe()
    return Result(summary=f"{len(data['documents'])} document(s), {len(data['blobs'])} input(s)",
                  data=data)


def _close_document(call: Any, doc: str) -> Result:
    call.session.close(doc)
    return Result(summary=f"Closed {doc}", removed=[doc])


def _undo(call: Any, doc: str, steps: int = 1, redo: bool = False) -> Result:
    session = call.session
    done = session.redo(doc, steps) if redo else session.undo(doc, steps)
    verb = "Redid" if redo else "Undid"
    if done == 0:
        raise ToolError("refused", f"nothing to {'redo' if redo else 'undo'} in {doc}")
    entry = session.entry(doc)
    entry.check_cache.clear()
    entry.restore_refs()
    return Result(summary=f"{verb} {done} step(s) of {doc}", changed=[doc],
                  data={"steps": done, "version": entry.version})


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
    SPECS["list_documents"].make({None: _list_documents}),
    SPECS["close_document"].make({None: _close_document}),
    SPECS["undo"].make({None: _undo}),
    SPECS["batch"].make({None: _composite}),
    SPECS["read_blob"].make({None: _read_blob}),
]

__all__ = ["CORE_NAMES", "GROUPS", "KINDS", "MISC", "SESSION_TOOLS", "SPECS", "Spec",
           "definition", "handler"]
