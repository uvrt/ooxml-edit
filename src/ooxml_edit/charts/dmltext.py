"""DrawingML text, rewritten in place: what a chart title, a diagram node and a text box share.

Every DrawingML text body -- a shape's ``a:txBody``, a chart's ``c:rich``, a diagram node's
``dgm:t``, a cached diagram drawing's ``dsp:txBody`` -- is ``a:bodyPr``, ``a:lstStyle`` and
paragraphs of runs (``a:r``), fields (``a:fld``) and line breaks (``a:br``).  This module reads
a body's text and **replaces it while keeping mixed formatting**: :func:`replace_body_text`
diffs the new text against the old -- paragraphs first, then characters within each changed
paragraph -- and gives every new character the formatting of the old character it replaces
or follows.  A paragraph whose text did not change is not touched at all, an untouched field
stays a field, and inline content that is not text (an equation, say) keeps its place.

In text, ``"\\n"`` separates paragraphs and ``"\\v"`` (vertical tab) is a line break.
"""

from __future__ import annotations

import copy
import difflib
from dataclasses import dataclass

from ..xml import Element, local_name, make, qn, remove, subelement
from . import namespaces as _namespaces  # noqa: F401  (registers a: before qn is used)

RUN_TAGS = frozenset({qn("a:r"), qn("a:fld")})
LINE_BREAK = "\v"
#: Interior stretches of unchanged text shorter than this are treated as coincidence, not as
#: text the caller kept -- otherwise retyping "Hello" as "Goodbye" would keep the "o"'s run.
_MIN_KEPT = 3


def runs(paragraph: Element) -> list[Element]:
    """A paragraph's runs and fields (``a:r``, ``a:fld``), in order."""
    return [child for child in paragraph if child.tag in RUN_TAGS]


def paragraph_text(paragraph: Element) -> str:
    """A paragraph's text, a line break as ``"\\v"``."""
    pieces = []
    for child in paragraph:
        if child.tag in RUN_TAGS:
            node = child.find(qn("a:t"))
            pieces.append("" if node is None or node.text is None else node.text)
        elif child.tag == qn("a:br"):
            pieces.append(LINE_BREAK)
    return "".join(pieces)


def make_run(text: str, properties: Element | None) -> Element:
    run = make("a:r")
    if properties is not None:
        clone = copy.deepcopy(properties)
        clone.tag = qn("a:rPr")  # an endParaRPr is the same type, just a different name
        run.append(clone)
    node = make("a:t")
    node.text = text
    run.append(node)
    return run


def make_break(properties: Element | None) -> Element:
    br = make("a:br")
    if properties is not None:
        clone = copy.deepcopy(properties)
        clone.tag = qn("a:rPr")
        br.append(clone)
    return br


def paragraph_like(template: Element | None, text: str) -> Element:
    """A new paragraph formatted like ``template`` (its pPr, last run, end properties)."""
    paragraph = make("a:p")
    run_properties = None
    if template is not None:
        properties = template.find(qn("a:pPr"))
        if properties is not None:
            paragraph.append(copy.deepcopy(properties))
        existing = runs(template)
        if existing:
            run_properties = existing[-1].find(qn("a:rPr"))
        if run_properties is None:
            run_properties = template.find(qn("a:endParaRPr"))
    for index, piece in enumerate(text.split(LINE_BREAK)):
        if index:
            paragraph.append(make_break(run_properties))
        if piece:
            paragraph.append(make_run(piece, run_properties))
    if template is not None:
        end = template.find(qn("a:endParaRPr"))
        if end is not None:
            paragraph.append(copy.deepcopy(end))
    return paragraph


# -- the text diff -------------------------------------------------------------------------


def replace_body_text(body: Element, value: str) -> None:
    """Give a text body the text ``value`` (``"\\n"`` between paragraphs), keeping its
    formatting character by character; an empty body gets a ``bodyPr`` and ``lstStyle``."""
    paragraphs = list(body.findall(qn("a:p")))
    if not paragraphs:
        subelement(body, "a:bodyPr")
        subelement(body, "a:lstStyle")
        paragraphs = [body.makeelement(qn("a:p"))]
        body.append(paragraphs[0])
    old_texts = [paragraph_text(p) for p in paragraphs]
    new_texts = value.split("\n")

    matcher = difflib.SequenceMatcher(None, old_texts, new_texts, autojunk=False)
    # Work back to front so earlier positions stay valid while later ones change.
    for tag, i1, i2, j1, j2 in reversed(matcher.get_opcodes()):
        if tag == "equal":
            continue
        old_block = paragraphs[i1:i2]
        new_block = new_texts[j1:j2]
        paired = min(len(old_block), len(new_block))
        for offset in range(paired):
            rebuild_paragraph(old_block[offset], new_block[offset])
        if len(new_block) > paired:
            # Extra new paragraphs continue the formatting of the last one in this block, or
            # of the paragraph before the block, or (at the very start) the one after it.
            if old_block:
                template, anchor, after = old_block[-1], old_block[-1], True
            elif i1 > 0:
                template, anchor, after = paragraphs[i1 - 1], paragraphs[i1 - 1], True
            else:
                template, anchor, after = paragraphs[0], paragraphs[0], False
            extras = [paragraph_like(template, text) for text in new_block[paired:]]
            if after:
                for element in reversed(extras):
                    anchor.addnext(element)
            else:
                for element in extras:
                    anchor.addprevious(element)
        for element in old_block[paired:]:
            remove(element)


@dataclass
class _Item:
    element: Element
    kind: str  # "r", "fld", "br"
    start: int
    text: str


def inline_items(paragraph: Element) -> tuple[list[_Item], list[tuple[int, Element]]]:
    """A paragraph's runs, fields and breaks, and the other inline content by char offset."""
    items: list[_Item] = []
    others: list[tuple[int, Element]] = []  # inline content that is not text, by char offset
    offset = 0
    for child in paragraph:
        if child.tag in RUN_TAGS:
            node = child.find(qn("a:t"))
            text = "" if node is None or node.text is None else node.text
            items.append(_Item(child, local_name(child), offset, text))
            offset += len(text)
        elif child.tag == qn("a:br"):
            items.append(_Item(child, "br", offset, LINE_BREAK))
            offset += 1
        elif child.tag not in {qn("a:pPr"), qn("a:endParaRPr")} and isinstance(child.tag, str):
            others.append((offset, child))
    return items, others


def _text_properties(paragraph: Element, items: list[_Item], index: int | None) -> Element | None:
    """Run properties to use for text sourced from item ``index``."""
    if index is None:
        return paragraph.find(qn("a:endParaRPr"))
    item = items[index]
    if item.kind != "br":
        return item.element.find(qn("a:rPr"))
    # A break's formatting is fine for a break; for text, find the nearest real run.
    for step in list(range(index - 1, -1, -1)) + list(range(index + 1, len(items))):
        if items[step].kind != "br":
            return items[step].element.find(qn("a:rPr"))
    return item.element.find(qn("a:rPr"))


def _replace_inline(paragraph: Element, built: list[tuple[int, Element]]) -> None:
    """Swap a paragraph's inline content for ``built`` (sorted by char offset)."""
    for child in list(paragraph):
        if child.tag not in {qn("a:pPr"), qn("a:endParaRPr")}:
            paragraph.remove(child)
    end_properties = paragraph.find(qn("a:endParaRPr"))
    for _, element in built:
        if end_properties is not None:
            end_properties.addprevious(element)
        else:
            paragraph.append(element)


def resegment(paragraph: Element, pieces: list[str]) -> None:
    """Re-cut a paragraph's (unchanged) text into the runs and breaks ``pieces`` spell."""
    items, others = inline_items(paragraph)
    owner = []
    for index, item in enumerate(items):
        owner.extend([index] * len(item.text))
    built: list[tuple[int, Element]] = []
    offset = 0
    for piece in pieces:
        index = owner[offset]
        item = items[index]
        if piece == LINE_BREAK:
            element = (copy.deepcopy(item.element) if item.kind == "br"
                       else make_break(_text_properties(paragraph, items, index)))
        elif item.kind == "fld" and item.start == offset and item.text == piece:
            element = copy.deepcopy(item.element)  # an untouched field stays a field
        else:
            element = make_run(piece, _text_properties(paragraph, items, index))
        built.append((offset, element))
        offset += len(piece)
    # The text did not change, so inline objects keep their offsets.
    built.extend(others)
    built.sort(key=lambda pair: pair[0])
    _replace_inline(paragraph, built)


def rebuild_paragraph(paragraph: Element, new_text: str) -> None:
    """Give ``paragraph`` the text ``new_text``, keeping per-character formatting."""
    items, others = inline_items(paragraph)
    old_text = "".join(item.text for item in items)
    if old_text == new_text:
        return

    owner = []  # old char index -> item index
    for index, item in enumerate(items):
        owner.extend([index] * len(item.text))

    opcodes = _coarse_opcodes(old_text, new_text)
    # For each new character: (source item index or None, old char index if kept verbatim).
    sources: list[tuple[int | None, int | None]] = []
    new_offset_of_old: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for k in range(i2 - i1):
                sources.append((owner[i1 + k], i1 + k))
                new_offset_of_old[i1 + k] = j1 + k
            continue
        if i1 < i2:
            source = owner[i1]  # a replacement takes the formatting of what it replaces
        elif i1 > 0:
            source = owner[i1 - 1]  # an insertion continues the character before it
        elif owner:
            source = owner[0]
        else:
            source = None
        for _ in range(j2 - j1):
            sources.append((source, None))
        for k in range(i1, i2):
            new_offset_of_old[k] = j1

    def text_source(index: int | None) -> Element | None:
        return _text_properties(paragraph, items, index)

    # Group consecutive characters by source into new inline elements.
    built: list[tuple[int, Element]] = []  # (new char offset, element)
    position = 0
    while position < len(new_text):
        source, kept = sources[position]
        if new_text[position] == LINE_BREAK:
            if source is not None and items[source].kind == "br" and kept is not None:
                element = copy.deepcopy(items[source].element)
            else:
                element = make_break(text_source(source))
            built.append((position, element))
            position += 1
            continue
        end = position
        while (end < len(new_text) and new_text[end] != LINE_BREAK
               and sources[end][0] == source):
            end += 1
        piece = new_text[position:end]
        kept_span = [sources[k][1] for k in range(position, end)]
        item = items[source] if source is not None else None
        if (item is not None and item.kind == "fld" and None not in kept_span
                and kept_span == list(range(item.start, item.start + len(item.text)))):
            element = copy.deepcopy(item.element)  # an untouched field stays a field
        else:
            element = make_run(piece, text_source(source))
        built.append((position, element))
        position = end

    # Inline objects (equations and the like) go back where their old position maps to.
    for old_offset, element in others:
        mapped = new_offset_of_old.get(old_offset)
        if mapped is None:
            mapped = len(new_text) if old_offset >= len(old_text) else 0
        built.append((mapped, element))
    built.sort(key=lambda pair: pair[0])  # stable: objects land before text at the same offset
    _replace_inline(paragraph, built)


def _coarse_opcodes(old: str, new: str) -> list[tuple[str, int, int, int, int]]:
    """Character opcodes with coincidental short matches folded into the edits around them."""
    raw = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    result: list[list] = []
    for index, (tag, i1, i2, j1, j2) in enumerate(raw):
        interior = 0 < index < len(raw) - 1
        if tag == "equal" and interior and (i2 - i1) < _MIN_KEPT:
            tag = "replace"
        if tag != "equal" and result and result[-1][0] != "equal":
            result[-1][2] = i2
            result[-1][4] = j2
            continue
        result.append([("equal" if tag == "equal" else "replace"), i1, i2, j1, j2])
    return [tuple(op) for op in result]


__all__ = ["LINE_BREAK", "RUN_TAGS", "inline_items", "make_break", "make_run", "paragraph_like",
           "paragraph_text", "rebuild_paragraph", "replace_body_text", "resegment", "runs"]
