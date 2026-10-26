"""SVG pictures, as Office 2016 and later store one, and their recolouring: DrawingML, shared
by every format that embeds a picture (round 17, P6; moved here from the PowerPoint library).

A picture keeps two images: the bitmap every consumer can draw, in ``a:blip@r:embed``, and
the SVG it came from, behind an extension of that blip::

    <a:blip r:embed="rId2">
      <a:extLst>
        <a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}">
          <asvg:svgBlip xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main"
                        r:embed="rId3"/>
        </a:ext>
      </a:extLst>
    </a:blip>

The SVG is a part of its own (``image/svg+xml``) behind an ordinary image relationship.
Where a format writes its parts, names its pictures and draws the fallback is the format
library's (measured in each application); what is here is the same in all of them:

* :func:`looks_like_svg`, :func:`parse` (untrusted data: no document type, no entities, no
  network, at most :data:`SVG_LIMIT` bytes), :func:`native_size`;
* :func:`svg_blip`, :func:`attach`, :func:`drop` -- the blip's extension;
* :func:`recolor`, :func:`theme_class`, :data:`THEME_SLOTS`, :func:`stroke_units` -- the
  SVG rewritten as PowerPoint's Graphics Fill and Graphics Outline rewrite one, measured in
  PowerPoint for Mac 16 on 2026-10-15 (the PowerPoint library's oracle notes,
  ``oracle2/results.md``, cited as "§n" below).
"""

from __future__ import annotations

import re
from typing import Sequence

from lxml import etree

from ..errors import ArgumentError

SVG_BLIP_EXT_URI = "{96DAC541-7B7A-43D3-8B79-37D633B846F1}"
#: The ``asvg:`` namespace of ``svgBlip``.
ASVG_NS = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
SVG_CONTENT_TYPE = "image/svg+xml"
SVG_NS = "http://www.w3.org/2000/svg"
#: The largest SVG stored as a picture, in bytes.
SVG_LIMIT = 16 * 1024 * 1024

#: CSS units per inch: an SVG's lengths, its user units being pixels at 96 dpi.
_PER_INCH = {"px": 96.0, "": 96.0, "pt": 72.0, "pc": 6.0, "in": 1.0, "cm": 2.54,
             "mm": 25.4}
_LENGTH = re.compile(r"^\s*([+]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-z]*)\s*$")
_SVG_TAG = re.compile(rb"<svg[\s>/]")

#: DrawingML's main namespace (the blip's) and the relationships namespace (``r:embed``).
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_DECLARATION = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class SvgError(ArgumentError):
    """An SVG refused: not well-formed, not an SVG, unsafe (a document type, an entity, too
    large), or with nothing a recolour can paint."""


def looks_like_svg(data: bytes, suffix: str | None = None) -> bool:
    """Whether ``data`` is meant as an SVG: a ``.svg`` path, or markup with an ``<svg`` tag
    in its first 4 KiB (a declaration, a comment or a document type may come first).
    :func:`parse` decides whether it is one, and refuses what is unsafe."""
    if suffix and suffix.lower().lstrip(".") == "svg":
        return True
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    return head.startswith(b"<") and _SVG_TAG.search(head) is not None


def parse(data: bytes):
    """The SVG's root element, parsed as untrusted data; :class:`SvgError` for anything
    else -- a document type or entity declaration, more than :data:`SVG_LIMIT` bytes, XML
    that is not well-formed, a root that is not ``svg`` in the SVG namespace."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > SVG_LIMIT:
        raise SvgError(f"the SVG is {len(data)} bytes; the limit is {SVG_LIMIT}")
    if _DECLARATION.search(data):
        raise SvgError("the SVG contains a document type or entity declaration")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             dtd_validation=False, huge_tree=False, remove_blank_text=False,
                             collect_ids=False)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as error:
        raise SvgError(f"the SVG is not well-formed XML: {error}") from None
    tree = root.getroottree()
    if tree.docinfo.doctype or tree.docinfo.internalDTD is not None:
        raise SvgError("the SVG declares a document type")
    for _ in root.iter(etree.Entity):
        raise SvgError("the SVG contains an entity reference")
    if root.tag != f"{{{SVG_NS}}}svg":
        raise SvgError(f"not an SVG document: the root is {root.tag!r}, not svg in the "
                       f"namespace {SVG_NS}")
    return root


def _length(value: str | None) -> float | None:
    if not value:
        return None
    match = _LENGTH.match(value)
    if match is None or match.group(2) not in _PER_INCH:
        return None              # a percentage, em, or something unreadable
    number = float(match.group(1))
    return number * 96.0 / _PER_INCH[match.group(2)] if number > 0 else None


def native_size(root) -> tuple[float, float] | None:
    """``(width, height)`` in pixels at 96 dpi: the root's ``width`` and ``height`` (in px,
    pt, pc, in, cm or mm), one of them with the ``viewBox``'s proportions, or the
    ``viewBox`` alone -- what PowerPoint inserts an SVG at.  ``None`` when it states none."""
    width, height = _length(root.get("width")), _length(root.get("height"))
    box = None
    numbers = re.split(r"[\s,]+", (root.get("viewBox") or "").strip())
    if len(numbers) == 4:
        try:
            box = tuple(float(n) for n in numbers)
        except ValueError:
            box = None
        if box is not None and (box[2] <= 0 or box[3] <= 0):
            box = None
    if width and height:
        return width, height
    if box is not None:
        if width:
            return width, width * box[3] / box[2]
        if height:
            return height * box[2] / box[3], height
        return box[2], box[3]
    return None


def svg_blip(blip):
    """The ``asvg:svgBlip`` under a blip, else ``None``."""
    extensions = blip.find(f"{{{A_NS}}}extLst")
    if extensions is None:
        return None
    for extension in extensions:
        if extension.get("uri") == SVG_BLIP_EXT_URI:
            found = extension.find(f"{{{ASVG_NS}}}svgBlip")
            if found is not None:
                return found
    return None


def drop(blip) -> None:
    """Remove a blip's SVG extension (and an ``a:extLst`` it leaves empty)."""
    extensions = blip.find(f"{{{A_NS}}}extLst")
    if extensions is None:
        return
    for extension in list(extensions):
        if extension.get("uri") == SVG_BLIP_EXT_URI:
            extensions.remove(extension)
    if len(extensions) == 0:
        blip.remove(extensions)


def attach(blip, rel_id: str) -> None:
    """Point a blip's SVG extension at ``rel_id``, writing the extension as PowerPoint does
    (``xmlns:asvg`` on the ``svgBlip``) when there is none."""
    found = svg_blip(blip)
    if found is not None:
        found.set(f"{{{R_NS}}}embed", rel_id)
        return
    extensions = blip.find(f"{{{A_NS}}}extLst")
    if extensions is None:
        extensions = etree.SubElement(blip, f"{{{A_NS}}}extLst")
    extension = etree.SubElement(extensions, f"{{{A_NS}}}ext")
    extension.set("uri", SVG_BLIP_EXT_URI)
    element = etree.SubElement(extension, f"{{{ASVG_NS}}}svgBlip", nsmap={"asvg": ASVG_NS})
    element.set(f"{{{R_NS}}}embed", rel_id)


# -- recolouring (PowerPoint's Graphics Fill and Graphics Outline) -----------------------------
#
# Everything below writes what PowerPoint writes, measured with PowerPoint for Mac 16 on
# 2026-10-15 (oracle2/results.md, cited as "§n" below; the first run, accent 2 and RGB on a
# simple icon, is ../oracle/results.md section B).  PowerPoint also normalises path data to
# absolute polylines, adds ``overflow="hidden"`` and ``xmlns:xlink`` to the root (and
# ``overflow`` to a ``symbol``) and spells ``href`` as ``xlink:href``; none of that changes
# what is drawn, so it is not repeated here.

#: The theme slot PowerPoint names in a recoloured SVG's class, by the scheme colour this
#: library writes -- ``MsftOfcThm_<slot>_Fill_v2`` for a Graphics Fill, ``..._Stroke_v2`` for
#: a Graphics Outline.  The class is how the theme link survives a save and a reopen.
#: PowerPoint for Mac, 2026-10-15, oracle2/results.md §1 (every slot's Fill class) and E1
#: (every slot's Stroke class).  ``tx1``/``bg1``/``tx2``/``bg2`` (``text1``, ``background1``...)
#: name the Text/Background slots and ``dk1``/``lt1``/``dk2``/``lt2`` the MainDark/MainLight
#: ones: two classes for the same colour, as in PowerPoint.
THEME_SLOTS = {
    "dk1": "MainDark1", "lt1": "MainLight1", "dk2": "MainDark2", "lt2": "MainLight2",
    "accent1": "Accent1", "accent2": "Accent2", "accent3": "Accent3", "accent4": "Accent4",
    "accent5": "Accent5", "accent6": "Accent6", "hlink": "Hyperlink",
    "folHlink": "FollowedHyperlink", "tx1": "Text1", "bg1": "Background1", "tx2": "Text2",
    "bg2": "Background2",
}


def theme_class(scheme: str, part: str = "Fill") -> str | None:
    """The class PowerPoint writes for theme colour ``scheme`` (``"accent2"``, ``"tx1"``) on
    a Graphics Fill (``part="Fill"``) or Outline (``"Stroke"``); ``None`` for anything else
    (``phClr``).  A tint or shade has none: its colour is written as its value alone (not
    measured: PowerPoint offers no script property for one)."""
    slot = THEME_SLOTS.get(scheme)
    return f"MsftOfcThm_{slot}_{part}_v2" if slot else None


#: The elements a Graphics Fill or Outline paints: SVG's basic shapes, paths, text, and
#: ``use`` (§6: both ``use`` elements got the class, the stroke attributes and the fill).
_PAINTED = frozenset({"path", "rect", "circle", "ellipse", "polygon", "polyline", "line",
                      "text", "tspan", "textPath", "use"})
#: Containers whose shapes are not drawn as themselves: a clip path's outline, a mask's
#: luminance.  Their shapes keep their colours.
_NOT_DRAWN = frozenset({"clipPath", "mask"})
_OUR_CLASS = re.compile(r"^MsftOfcThm_\w+_(Fill|Stroke)_v2$")
#: The stroke attributes PowerPoint writes on every painted element, in its order (§1, §2).
_STROKE = ("stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit",
           "stroke-opacity")
#: Their initial values (SVG 1.1), which PowerPoint writes where nothing states one.
_STROKE_INITIAL = {"stroke-width": "1", "stroke-linecap": "butt", "stroke-linejoin": "miter",
                   "stroke-miterlimit": "4", "stroke-opacity": "1"}
#: SVG's presentation attributes: what a style sheet's or a ``style`` attribute's
#: declarations become when PowerPoint removes them (§3).
_PRESENTATION = frozenset({
    "alignment-baseline", "baseline-shift", "clip", "clip-path", "clip-rule", "color",
    "color-interpolation", "color-interpolation-filters", "color-rendering", "cursor",
    "direction", "display", "dominant-baseline", "fill", "fill-opacity", "fill-rule",
    "filter", "flood-color", "flood-opacity", "font-family", "font-size", "font-size-adjust",
    "font-stretch", "font-style", "font-variant", "font-weight",
    "glyph-orientation-horizontal", "glyph-orientation-vertical", "image-rendering",
    "letter-spacing", "lighting-color", "marker-end", "marker-mid", "marker-start", "mask",
    "opacity", "overflow", "pointer-events", "shape-rendering", "stop-color", "stop-opacity",
    "stroke", "stroke-dasharray", "stroke-dashoffset", "stroke-linecap", "stroke-linejoin",
    "stroke-miterlimit", "stroke-opacity", "stroke-width", "text-anchor", "text-decoration",
    "text-rendering", "unicode-bidi", "visibility", "word-spacing", "writing-mode",
    "enable-background", "kerning", "mask-type", "paint-order", "solid-color",
    "solid-opacity", "vector-effect",
})
_COMPOUND = re.compile(r"^(\*|[A-Za-z][\w-]*)?((?:[.#][A-Za-z_][\w-]*)*)$")
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _number(value: float) -> str:
    """A number as PowerPoint writes one: ``1``, ``0.5``."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _selector(text: str) -> tuple[list[tuple[str, tuple]], tuple[int, int, int]]:
    """A selector as ``[(combinator, (tag, classes, ids)), ...]`` from left to right, and
    its specificity; ``ValueError`` for what is not tags, classes, ids and descendant or
    child combinators."""
    tokens = text.replace(">", " > ").split()
    steps: list[tuple[str, tuple]] = []
    combinator, specificity = " ", [0, 0, 0]
    for token in tokens:
        if token == ">":
            combinator = ">"
            continue
        match = _COMPOUND.match(token)
        if match is None or not token:
            raise SvgError(f"the SVG's style sheet has the selector {text.strip()!r}: only "
                        "tags, classes, ids and descendant or child selectors can be "
                        "carried over when a recolour replaces the style sheet; "
                        "simplify the SVG's styles first")
        tag = match.group(1) if match.group(1) not in (None, "*") else None
        parts = re.findall(r"[.#][\w-]+", match.group(2))
        classes = tuple(p[1:] for p in parts if p[0] == ".")
        ids = tuple(p[1:] for p in parts if p[0] == "#")
        specificity[0] += len(ids)
        specificity[1] += len(classes)
        specificity[2] += tag is not None
        steps.append((combinator, (tag, classes, ids)))
        combinator = " "
    if not steps:
        raise SvgError(f"the SVG's style sheet has an empty selector in {text!r}")
    return steps, (specificity[0], specificity[1], specificity[2])


def _local(element) -> str:
    tag = element.tag
    return tag.rpartition("}")[2] if isinstance(tag, str) else ""


def _matches_compound(element, compound) -> bool:
    tag, classes, ids = compound
    name = _local(element)
    if tag is not None and name != tag:
        return False
    own = set((element.get("class") or "").split())
    return all(c in own for c in classes) and all(element.get("id") == i for i in ids)


def _matches(element, steps) -> bool:
    """Whether ``element`` is what the selector ``steps`` selects (right to left)."""
    if not _matches_compound(element, steps[-1][1]):
        return False
    node, index = element, len(steps) - 1
    while index > 0:
        combinator = steps[index][0]
        wanted = steps[index - 1][1]
        node = node.getparent()
        if combinator == ">":
            if node is None or not _matches_compound(node, wanted):
                return False
        else:
            while node is not None and not _matches_compound(node, wanted):
                node = node.getparent()
            if node is None:
                return False
        index -= 1
    return True


def _declarations(text: str) -> list[tuple[str, str, bool]]:
    """``(property, value, important)`` for each declaration of a block."""
    found = []
    for declaration in text.split(";"):
        name, separator, value = declaration.partition(":")
        name, value = name.strip().lower(), value.strip()
        if not separator or not name:
            continue
        important = value.lower().endswith("!important")
        if important:
            value = value[:-len("!important")].strip()
        found.append((name, value, important))
    return found


def _style_sheet(text: str) -> list[tuple[str, list, tuple, list]]:
    """A ``<style>``'s rules: ``(selector text, steps, specificity, declarations)`` for each
    selector of each rule; ``ValueError`` for an at-rule (``@media``, ``@font-face``...)."""
    text = _COMMENT.sub("", text).replace("<!--", "").replace("-->", "")
    if "@" in text:
        raise SvgError("the SVG's style sheet has an at-rule (@media, @import, "
                    "@font-face...), which cannot be carried over when a recolour "
                    "replaces the style sheet; simplify the SVG's styles first")
    rules = []
    for block in text.split("}"):
        selectors, separator, body = block.partition("{")
        if not separator:
            if selectors.strip():
                raise SvgError(f"the SVG's style sheet cannot be read near "
                            f"{selectors.strip()[:40]!r}")
            continue
        declarations = _declarations(body)
        for selector in selectors.split(","):
            steps, specificity = _selector(selector)
            rules.append((selector.strip(), steps, specificity, declarations))
    return rules


def _carry_styles(root, rules) -> None:
    """Every element's style sheet and ``style`` attribute declarations, as presentation
    attributes, in CSS's cascade order (important first, then a ``style`` attribute, then
    specificity, then order); the ``style`` attributes removed (§3).  ``ValueError`` for a
    property that has no presentation attribute."""
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        candidates = []
        for order, (_, steps, specificity, declarations) in enumerate(rules):
            if _matches(element, steps):
                candidates += [((important, 0, specificity, order), name, value)
                               for name, value, important in declarations]
        inline = element.attrib.pop("style", None)
        if inline is not None:
            candidates += [((important, 1, (0, 0, 0), 0), name, value)
                           for name, value, important in _declarations(inline)]
        winners: dict[str, tuple] = {}
        for key, name, value in candidates:
            if name not in winners or key >= winners[name][0]:
                winners[name] = (key, value)
        for name, (_, value) in winners.items():
            if name not in _PRESENTATION:
                raise SvgError(f"the SVG's styles set {name!r}, which has no presentation "
                            "attribute to carry it over when a recolour replaces the "
                            "styles; simplify the SVG's styles first")
            element.set(name, value)


def _stated(element, name: str) -> str | None:
    """The value of inherited property ``name`` an element or an ancestor states as an
    attribute, else ``None``."""
    node = element
    while node is not None and isinstance(node.tag, str):
        value = node.get(name)
        if value is not None and value.strip().lower() != "inherit":
            return value.strip()
        node = node.getparent()
    return None


def stroke_units(root, width_emu: int, height_emu: int) -> float:
    """The SVG's user units per EMU at the picture's placed size: how an outline weight in
    points becomes a ``stroke-width`` (§2: 3 pt at 192 px placed over a 24-unit viewBox,
    8 px per unit, is ``stroke-width="0.5"``).  The viewBox's ``meet`` scale (the smaller
    of the two, as SVG fits one by default), else the root's own size's; ``ValueError``
    when the SVG states neither."""
    numbers = re.split(r"[\s,]+", (root.get("viewBox") or "").strip())
    box: "Sequence[float] | None" = None
    if len(numbers) == 4:
        try:
            box = [float(n) for n in numbers][2:]
        except ValueError:
            box = None
    if not box or box[0] <= 0 or box[1] <= 0:
        box = native_size(root)
    if not box or width_emu <= 0 or height_emu <= 0:
        raise SvgError("the SVG states no size (width, height or viewBox) to scale an "
                    "outline weight by")
    pixels_per_unit = min(width_emu / 9525 / box[0], height_emu / 9525 / box[1])
    return 1 / (pixels_per_unit * 9525)


def recolor(data: bytes, fill: str | None = None, fill_class: str | None = None,
            opacity: float = 1.0, *, stroke: str | None = None,
            stroke_class: str | None = None, stroke_opacity: float = 1.0,
            stroke_width: float | None = None, default_stroke: str = "#000000",
            default_stroke_class: str | None = "MsftOfcThm_Text1_Stroke_v2") -> bytes:
    """An SVG recoloured as PowerPoint's Graphics Fill (``fill``, ``"#RRGGBB"``) and
    Graphics Outline (``stroke``, ``stroke_width`` in the SVG's user units) rewrite one,
    measured with PowerPoint for Mac 16 (2026-10-15, oracle2/results.md):

    * every path, basic shape, text and ``use`` element -- in ``defs`` and ``symbol`` too,
      not in a clip path or mask -- gets ``fill`` and ``fill-opacity`` (``opacity``, 1 for
      an opaque colour; §5), whatever it was: ``none``, ``currentColor`` and a gradient
      included (§2, §6; the gradient's definition stays, unreferenced);
    * a theme colour's class: one ``<style>`` first in the root,
      ``.MsftOfcThm_Accent2_Fill_v2 { fill:#E97132; }`` and, for an outline,
      ``.MsftOfcThm_Accent5_Stroke_v2 { stroke:#A02B93; }``, and the class or classes on
      every element (§1, §2); an RGB colour has neither;
    * an outline colour strokes the shapes that have a stroke (§2, E1) and adds none to
      the others (E2); a weight gives every shape a stroke, the others in the outline
      colour, else ``default_stroke`` -- PowerPoint's default outline, Text 1, with its
      class ``default_stroke_class`` (E2; a colour and a weight together on a shape with
      no stroke are those two rules composed, not measured together);
      ``stroke_opacity`` below 1 is the new strokes' ``stroke-opacity`` (E3);
    * stroke attributes on every element in PowerPoint's order -- ``stroke`` (the outline
      colour, else the element's own), ``stroke-width`` (``stroke_width``, else its own),
      ``stroke-linecap``, ``stroke-linejoin``, ``stroke-miterlimit``, ``stroke-opacity`` --
      as PowerPoint writes them: an element with a ``stroke`` attribute of its own gets
      its linecap only if stated, and ``stroke-opacity``; any other gets the initial
      linecap and no ``stroke-opacity``, and ``stroke="none"`` unless the SVG had a style
      sheet (§1, §2, §3);
    * the SVG's style sheet and ``style`` attributes go (§3): every declaration becomes a
      presentation attribute of the element it applied to, so strokes draw the same, and
      classes other than PowerPoint's are dropped from painted elements.  A selector
      beyond tags, classes, ids and descendant or child combinators, an at-rule, or a
      property with no presentation attribute is refused (``ValueError``);
    * PowerPoint's own classes and rules from an earlier recolour are replaced -- the Fill
      one when filling, the Stroke one when outlining -- and the other kept.

    With only ``stroke``/``stroke_width``, fills are left as they are (§2), a shape with no
    stroke of its own getting ``fill-opacity="1"`` stated (E2)."""
    root = parse(data)
    svg = f"{{{SVG_NS}}}"
    kept: dict[str, str] = {}                        # PowerPoint's class -> its rule
    rules: list = []
    had_sheet = False
    for style in list(root.iter(f"{svg}style")):
        for rule in _style_sheet(style.text or ""):
            name = rule[0][1:] if rule[0].startswith(".") else ""
            if _OUR_CLASS.match(name):
                kept[name] = "".join(f"\n {prop}:{value}; \n" for prop, value, _ in rule[3])
            else:
                rules.append(rule)
                had_sheet = True
        _drop(style)
    painted = [element for element in root.iter()
               if isinstance(element.tag, str) and element.tag.startswith(svg)
               and element.tag[len(svg):] in _PAINTED
               and not any(isinstance(a.tag, str) and a.tag[len(svg):] in _NOT_DRAWN
                           for a in element.iterancestors())]
    if not painted:
        raise SvgError("the SVG has no shape to recolour (no path, basic shape, text or "
                         "use element outside a clip path or mask)")
    own_stroke = {id(element): (element.get("stroke") or "none").strip().lower()
                  not in ("none", "inherit") for element in painted}
    _carry_styles(root, rules)
    for part, new_class, given in (("Fill", fill_class, fill), ("Stroke", stroke_class,
                                                                    stroke)):
        if given is not None:
            kept = {name: rule for name, rule in kept.items()
                    if not name.endswith(f"_{part}_v2")}
            if new_class:
                kept[new_class] = (f"\n fill:{given}; \n" if part == "Fill"
                                   else f"\n stroke:{given}; \n")
    if stroke_width is not None and stroke is None and default_stroke_class:
        kept[default_stroke_class] = f"\n stroke:{default_stroke}; \n"
    width = _number(stroke_width) if stroke_width is not None else None
    used: set[str] = set()
    for element in painted:
        values = {name: _stated(element, name) for name in _STROKE}
        own = own_stroke[id(element)]
        stroked = (values["stroke"] or "none").lower() != "none"
        # E2: an outline colour strokes only what has a stroke; a weight gives every
        # shape one, in the colour given, else the default outline colour (Text 1).
        new_stroke = new_class = None
        if stroke is not None and (stroked or width is not None):
            new_stroke, new_class = stroke, stroke_class
        elif width is not None and not stroked:
            new_stroke, new_class = default_stroke, default_stroke_class
        classes = [c for c in (element.get("class") or "").split() if c in kept
                   and not (new_stroke is not None and c.endswith("_Stroke_v2"))]
        for added in (fill_class if fill is not None else None, new_class):
            if added and added not in classes:
                classes.append(added)
        classes.sort(key=lambda c: c.endswith("_Stroke_v2"))
        used.update(classes)
        fill_opacity = _stated(element, "fill-opacity")
        own_fill = element.get("fill")
        for name in ("class", *_STROKE, "fill", "fill-opacity"):
            element.attrib.pop(name, None)
        if classes:
            element.set("class", " ".join(classes))
        if new_stroke is not None:
            element.set("stroke", new_stroke)
        elif values["stroke"] is not None:
            element.set("stroke", values["stroke"])
        elif not had_sheet:
            element.set("stroke", "none")
        element.set("stroke-width", width or values["stroke-width"] or "1")
        if own:
            if values["stroke-linecap"] is not None:
                element.set("stroke-linecap", values["stroke-linecap"])
        else:
            element.set("stroke-linecap", values["stroke-linecap"] or "butt")
        element.set("stroke-linejoin", values["stroke-linejoin"] or "miter")
        element.set("stroke-miterlimit", values["stroke-miterlimit"] or "4")
        if new_stroke is not None and (own or stroke_opacity != 1):
            element.set("stroke-opacity", _number(stroke_opacity))    # E3
        elif own or values["stroke-opacity"] is not None:
            element.set("stroke-opacity", values["stroke-opacity"] or "1")
        if fill is not None:
            element.set("fill", fill)
            element.set("fill-opacity", _number(opacity))
        else:
            if own_fill is not None:
                element.set("fill", own_fill)
            # E2: a shape with no stroke of its own gets its fill-opacity stated.
            if not own or fill_opacity is not None:
                element.set("fill-opacity", fill_opacity or "1")
    kept = {name: rule for name, rule in kept.items() if name in used}
    if kept:
        order = sorted(kept, key=lambda c: c.endswith("_Stroke_v2"))
        style = etree.SubElement(root, f"{svg}style")    # in the root's namespaces
        style.text = "\n" + "".join(f".{name} {{{kept[name]}}}\n" for name in order)
        style.tail = "\n"
        root.insert(0, style)
    return etree.tostring(root, encoding="utf-8")


def _drop(element) -> None:
    parent = element.getparent()
    if element.tail and element.tail.strip():
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + element.tail
        else:
            parent.text = (parent.text or "") + element.tail
    parent.remove(element)


__all__ = ["ASVG_NS", "A_NS", "R_NS", "SVG_BLIP_EXT_URI", "SVG_CONTENT_TYPE", "SVG_LIMIT", "SVG_NS",
           "SvgError", "THEME_SLOTS", "attach", "drop", "looks_like_svg", "native_size", "parse",
           "recolor", "stroke_units", "svg_blip", "theme_class"]
