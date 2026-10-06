"""Toy tools for the tool-layer tests: two per format, on ooxml-edit's own package objects.

pptx-agent and docx-agent ship their real tools in their own packages; these only exercise
the plumbing.  Both toy formats open the synthetic test package (``synthetic.py``) as an
:class:`~ooxml_edit.opc.OpcPackage` with a :class:`~ooxml_edit.history.History`, and edit it
the way a format layer would: a checkpoint, a change to a part's tree, ``mark_dirty``.

* "pptx" (the toy deck): ``toy_ppt_set_title`` sets a page's title; ``toy_ppt_render``
  renders a page to a PNG in a worker process.
* "docx" (the toy text): ``toy_word_insert`` adds an item to the main part;
  ``toy_word_read`` reads the items back, paged.
* ``toy_save`` is one shared definition with a handler per kind, behind the validate gate.

The worker functions are module-level so a spawned worker process can import them.
"""

from __future__ import annotations

import struct
import time
import warnings
import zlib

from ooxml_edit.history import History
from ooxml_edit.opc import OpcPackage
from ooxml_edit.tools import (DocumentFormat, Result, ToolError, ToolGroup, array, boolean,
                              integer, page_text, string, tool)
from ooxml_edit.xml import make, qn, register_namespaces

import synthetic

register_namespaces({"tst": synthetic.NS})


class ToyWarning(UserWarning):
    """What a library warns about; the toolbox collects it into the result."""


class ToyLabelError(LookupError):
    """A library error that knows its candidates, mapped to ``label_not_found``."""

    def __init__(self, label: str, candidates: list[str]) -> None:
        super().__init__(f"no page labelled {label!r}")
        self.candidates = candidates


class ToyDocument:
    """A document over the synthetic package: pages with titles, and a main part of items."""

    def __init__(self, data: bytes) -> None:
        self.package = OpcPackage.open(data)
        self.history = History(self.package)

    @property
    def pages(self) -> list[str]:
        return self.package.related_parts_of_type(synthetic.MAIN, synthetic.REL_PAGE)

    def page(self, number: int) -> str:
        pages = self.pages
        if not 1 <= number <= len(pages):
            raise IndexError(f"no page {number}; the pages are 1 to {len(pages)}")
        return pages[number - 1]

    def title(self, number: int) -> str | None:
        return self.package.tree(self.page(number)).get("title")

    def set_title(self, number: int, text: str) -> None:
        part = self.page(number)
        self.history.checkpoint()
        self.package.tree(part).set("title", text)
        self.package.mark_dirty(part)
        if len(text) > 40:
            warnings.warn(f"the title of page {number} is {len(text)} characters", ToyWarning)

    def items(self) -> list[str]:
        root = self.package.tree(synthetic.MAIN)
        return [item.get("val", "") for item in root.iter(qn("tst:item"))]

    def insert(self, text: str, at: str) -> int:
        self.history.checkpoint()
        root = self.package.tree(synthetic.MAIN)
        item = make("tst:item", val=text)
        if at == "start":
            root.insert(0, item)
        else:
            root.append(item)
        self.package.mark_dirty(synthetic.MAIN)
        return self.items().index(text) + 1

    def batch(self):
        return self.history.batch()

    def undo(self) -> bool:
        return self.history.undo()

    def redo(self) -> bool:
        return self.history.redo()

    def to_bytes(self) -> bytes:
        return self.package.to_bytes()

    def problems(self) -> list[str]:
        return [f"page {n} has an empty title" for n in range(1, len(self.pages) + 1)
                if self.title(n) == ""]


def _is(extension: str):
    return lambda data, name: data.startswith(b"PK") and name.lower().endswith(extension)


def _label_candidates(exc: ToyLabelError) -> list[str]:
    return exc.candidates


DECK = DocumentFormat(
    kind="pptx", open=ToyDocument, detect=_is(".pptx"), problems=ToyDocument.problems,
    warnings=(ToyWarning,), prompt="Toy deck: pages are numbered from 1.",
    errors={ToyLabelError: ("label_not_found", _label_candidates)})
TEXT = DocumentFormat(
    kind="docx", open=ToyDocument, detect=_is(".docx"), problems=ToyDocument.problems,
    warnings=(ToyWarning,), prompt="Toy text: items are numbered from 1.")

GROUPS = [ToolGroup("toy_render", "Rendering pages to images."),
          ToolGroup("toy_read", "Reading items, paged.")]


# -- the worker's side -------------------------------------------------------------------------


def png(width: int, height: int, rgb: tuple[int, int, int] = (40, 90, 160)) -> bytes:
    """A solid-colour PNG, written with the standard library."""
    row = b"\x00" + bytes(rgb) * width
    raw = zlib.compress(row * height, 9)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw)
            + chunk(b"IEND", b""))


def render_page(data: bytes, number: int, width: int, seconds: float = 0.0) -> tuple[bytes, int, int]:
    """What a renderer does in a worker: open the bytes, draw a page.  ``seconds`` stalls it."""
    document = ToyDocument(data)
    document.page(number)
    if seconds:
        time.sleep(seconds)
    height = width * 9 // 16
    return png(width, height), width, height


def worker_pid() -> int:
    import os
    return os.getpid()


# -- the toy tools -----------------------------------------------------------------------------


@tool("toy_ppt_set_title", "Set the title of one page of a deck. Returns the page's address.",
      {"doc": string("Document id, e.g. d1."),
       "page": integer("Page number, from 1.", minimum=1),
       "text": string("The new title text.", max_length=200),
       "key": string("Idempotence key: a retry with the same key changes nothing.",
                     optional=True)},
      kind="pptx", mutates=True)
def toy_ppt_set_title(call, doc, page, text, key=None):
    call.document.set_title(page, text)
    return Result(summary=f"Set the title of page {page}", changed=[f"page:{page}"])


@tool("toy_ppt_render", "Render one page of a deck to a PNG. Returns the image, its size and "
      "its token cost.",
      {"doc": string("Document id, e.g. d1."),
       "page": integer("Page number, from 1.", minimum=1),
       "width": integer("Width in pixels, 16 to 2576. Default 1280.", minimum=16, maximum=2576,
                        optional=True),
       "stall": integer("Test only: seconds the renderer stalls.", minimum=0,
                        optional=True)},
      kind="pptx", group="toy_render")
def toy_ppt_render(call, doc, page, width=1280, stall=0):
    entry = call.entry
    key = ("page", page, width)
    hit = entry.render_cache.get((entry.version, key))
    if hit is None:
        hit = call.run(render_page, call.document.to_bytes(), page, width, stall)
        entry.render_cache.put((entry.version, key), hit)
    data, w, h = hit
    call.image(data, w, h, label=f"page {page}")
    return Result(summary=f"Rendered page {page}")


@tool("toy_word_insert", "Insert an item of text at the start or end of a document. Returns "
      "the item's position.",
      {"doc": string("Document id, e.g. d1."),
       "text": string("The item's text.", min_length=1),
       "at": string("Where: start or end.", enum=["start", "end"])},
      kind="docx", mutates=True)
def toy_word_insert(call, doc, text, at):
    position = call.document.insert(text, at)
    return Result(summary=f"Inserted item {position}", created=[f"item:{position}"])


@tool("toy_word_read", "Read a document's items, one per line, in pages. Pass next_cursor "
      "back for the next page.",
      {"doc": string("Document id, e.g. d1."),
       "cursor": string("next_cursor from the previous page.", optional=True),
       "page_chars": integer("Characters per page, 1 to 24000. Default 24000.", minimum=1,
                             maximum=24000, optional=True)},
      kind="docx", group="toy_read")
def toy_word_read(call, doc, cursor=None, page_chars=24000):
    text = "".join(f"{n}. {item}\n" for n, item in enumerate(call.document.items(), 1))
    page, next_cursor = page_text(text, cursor=cursor, limit=page_chars)
    return Result(summary="Read items", data=page, next_cursor=next_cursor)


_SAVE = {"doc": string("Document id, e.g. d1."),
         "name": string("File name for the application, e.g. report.pptx."),
         "format": string("The file format.", enum=["pptx", "docx"])}


@tool("toy_save", "Hand a document to the application as a file. Refuses new validation "
      "problems. Returns the name, format, size and validation report.", _SAVE, kind="pptx")
def toy_save_deck(call, doc, name, format):
    if format != "pptx":
        raise ToolError("invalid_arguments", "a deck saves as pptx", field="format",
                        valid_options=["pptx"])
    return Result(summary=f"Saved {name}", data=call.output(name, format, call.document.to_bytes()))


@toy_save_deck.for_kind("docx")
def toy_save_text(call, doc, name, format):
    if format != "docx":
        raise ToolError("invalid_arguments", "a text document saves as docx", field="format",
                        valid_options=["docx"])
    return Result(summary=f"Saved {name}", data=call.output(name, format, call.document.to_bytes()))


@tool("toy_ppt_find_label", "Find the page with a label. Returns its number.",
      {"doc": string("Document id, e.g. d1."), "label": string("The label.")},
      kind="pptx", group="toy_read")
def toy_ppt_find_label(call, doc, label):
    labels = [call.document.title(n) or f"untitled {n}"
              for n in range(1, len(call.document.pages) + 1)]
    if label not in labels:
        raise ToyLabelError(label, labels)
    return {"page": labels.index(label) + 1}


TOOLS = [toy_ppt_set_title, toy_ppt_render, toy_word_insert, toy_word_read, toy_save_deck,
         toy_ppt_find_label]
FORMATS = [DECK, TEXT]

# ``boolean`` and ``array`` are re-exported for the tests that build tools of their own.
__all__ = ["TOOLS", "FORMATS", "GROUPS", "ToyDocument", "ToyWarning", "ToyLabelError", "png",
           "render_page", "worker_pid", "boolean", "array"]


def raise_tool_error() -> None:
    raise ToolError("refused", "not this way", valid_options=["a", "b"])
