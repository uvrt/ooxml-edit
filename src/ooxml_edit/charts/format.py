"""The look of a chart: colours, lines, markers, axis scales, gridlines and chart text.

The pieces :class:`~.chart.Chart`'s formatting methods are made of, kept apart from the data
editing in :mod:`.chart`.  Everything is written the way Office writes it, in schema order,
and as theme references where a theme colour is given, so the chart keeps following the
document's theme.

**Colours** are given as text, the same specifications a deck's shapes take
(``"accent1"``, ``"tx2"``, ``"accent1 lumMod=75% lumOff=25%"``, ``"1F4E79"``,
``"#1F4E79"``); ``None`` means none (no fill, no line).

**Measured** (Office for Mac 16, PowerPoint and Word): minor gridlines are a 0.75 pt line at
5% of the text colour, major ones at 15%; reversing an axis is ``c:orientation
val="maxMin"`` with the *other* axis crossing at its maximum (``c:crosses val="max"``) --
without it PowerPoint moves the other axis to the far side (and rewrites its ``axPos``).
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from ..xml import Element, local_name, make, qn, remove
from .errors import FormatError
from .namespaces import A_NS, C_NS

#: The theme colours a chart may name (``ST_SchemeColorVal``).
SCHEME_COLORS = frozenset({
    "bg1", "tx1", "bg2", "tx2", "accent1", "accent2", "accent3", "accent4", "accent5",
    "accent6", "hlink", "folHlink", "dk1", "lt1", "dk2", "lt2",
})
#: Colour modifiers with a value, as a colour specification may give them.
_VALUED = frozenset({
    "tint", "shade", "alpha", "alphaOff", "alphaMod", "hue", "hueOff", "hueMod", "sat",
    "satOff", "satMod", "lum", "lumOff", "lumMod", "red", "redOff", "redMod", "green",
    "greenOff", "greenMod", "blue", "blueOff", "blueMod",
})
_FLAGS = frozenset({"comp", "inv", "gray", "gamma", "invGamma"})
_HEX = re.compile(r"#?([0-9A-Fa-f]{6})")

#: Marker symbols (``ST_MarkerStyle`` without ``picture``).
MARKERS = ("none", "auto", "circle", "dash", "diamond", "dot", "plus", "square", "star",
           "triangle", "x")
#: Line dashes: friendly names, and the schema's (``ST_PresetLineDashVal``) as themselves.
DASHES = {"solid": "solid", "dot": "sysDot", "dash": "dash", "dash_dot": "dashDot",
          "long_dash": "lgDash", "long_dash_dot": "lgDashDot",
          "long_dash_dot_dot": "lgDashDotDot", "square_dot": "sysDash"}
_DASH_VALUES = frozenset({"solid", "dot", "dash", "lgDash", "dashDot", "lgDashDot",
                          "lgDashDotDot", "sysDash", "sysDot", "sysDashDot", "sysDashDotDot"})

#: Data label positions: friendly name -> ``ST_DLblPos``.
LABEL_POSITIONS = {"center": "ctr", "inside_end": "inEnd", "inside_base": "inBase",
                   "outside_end": "outEnd", "left": "l", "right": "r", "top": "t",
                   "bottom": "b", "best_fit": "bestFit"}

#: The parts of a chart whose text :func:`text_holders` finds.
TEXT_PARTS = ("title", "axis_titles", "axis_labels", "data_labels", "legend")

_FILL_TAGS = ("a:noFill", "a:solidFill", "a:gradFill", "a:blipFill", "a:pattFill",
              "a:grpFill")
_EFFECT_TAGS = ("a:effectLst", "a:effectDag")
#: ``CT_ShapeProperties`` (``c:spPr``).
_SHAPE_ORDER = ("a:xfrm", ("a:custGeom", "a:prstGeom"), _FILL_TAGS, "a:ln", _EFFECT_TAGS,
                "a:scene3d", "a:sp3d", "a:extLst")
#: ``CT_LineProperties`` (``a:ln``).
_LINE_ORDER = (("a:noFill", "a:solidFill", "a:gradFill", "a:pattFill"),
               ("a:prstDash", "a:custDash"), ("a:round", "a:bevel", "a:miter"), "a:headEnd",
               "a:tailEnd", "a:extLst")
#: ``CT_TextCharacterProperties`` (``a:defRPr``, ``a:rPr``, ``a:endParaRPr``).
_CHARACTER_ORDER = ("a:ln", _FILL_TAGS, _EFFECT_TAGS, "a:highlight", ("a:uLnTx", "a:uLn"),
                    ("a:uFillTx", "a:uFill"), "a:latin", "a:ea", "a:cs", "a:sym",
                    "a:hlinkClick", "a:hlinkMouseOver", "a:rtl", "a:extLst")




# ------------------------------------------------------------------------------------------
# Placing children in a sequence this module knows
# ------------------------------------------------------------------------------------------


def _rank(order: Sequence, tag: str) -> int | None:
    for rank, step in enumerate(order):
        if tag == step or (not isinstance(step, str) and tag in step):
            return rank
    return None


def _prefixed(element: Element) -> str:
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    uri, _, name = tag[1:].partition("}")
    prefix = {A_NS: "a", C_NS: "c"}.get(uri)
    return f"{prefix}:{name}" if prefix else name


def place(parent: Element, child: Element, order: Sequence) -> Element:
    """Insert ``child`` into ``parent`` where ``order`` puts it (after its equals)."""
    rank = _rank(order, _prefixed(child))
    if child.getparent() is parent:
        parent.remove(child)
    if rank is None:
        parent.append(child)
        return child
    for sibling in parent:
        if not isinstance(sibling.tag, str):
            continue
        other = _rank(order, _prefixed(sibling))
        if other is None or other > rank:
            sibling.addprevious(child)
            return child
    parent.append(child)
    return child


def _replace(parent: Element, tags: Sequence[str], new: Element | None, order: Sequence) -> None:
    """Swap whichever of ``tags`` ``parent`` holds for ``new`` (``None``: just remove)."""
    wanted = {qn(t) for t in tags}
    for child in list(parent):
        if child.tag in wanted:
            remove(child)
    if new is not None:
        place(parent, new, order)


# ------------------------------------------------------------------------------------------
# Colours
# ------------------------------------------------------------------------------------------


def color_element(spec: str) -> Element:
    """``a:schemeClr`` or ``a:srgbClr`` for a colour specification (see the module)."""
    if not isinstance(spec, str) or not spec.strip():
        raise FormatError(f"{spec!r} is not a colour; give a theme colour such as accent1 or "
                          f"tx2 (with modifiers like lumMod=75%), or a hex colour such as 1F4E79")
    base, *modifiers = spec.split()
    if base in SCHEME_COLORS:
        element = make("a:schemeClr", val=base)
    else:
        match = _HEX.fullmatch(base)
        if match is None:
            raise FormatError(f"{base!r} is neither a hex colour nor a theme colour "
                              f"({', '.join(sorted(SCHEME_COLORS))})")
        element = make("a:srgbClr", val=match.group(1).upper())
    for modifier in modifiers:
        name, separator, raw = modifier.partition("=")
        if name in _FLAGS and not separator:
            element.append(make(f"a:{name}"))
            continue
        if name not in _VALUED or not separator:
            raise FormatError(f"unknown colour modifier {modifier!r}")
        raw = raw.strip()
        try:
            value = round(float(raw[:-1]) * 1000) if raw.endswith("%") else int(raw)
        except ValueError:
            raise FormatError(f"colour modifier {modifier!r}: a percentage or an integer") \
                from None
        element.append(make(f"a:{name}", val=str(value)))
    return element


def color_text(holder: Element | None) -> str | None:
    """The colour a fill (``a:solidFill``) or colour element holds, as a specification."""
    if holder is None:
        return None
    element = holder if local_name(holder) in {"schemeClr", "srgbClr", "sysClr", "prstClr"} \
        else next((c for c in holder if isinstance(c.tag, str)
                   and local_name(c) in {"schemeClr", "srgbClr", "sysClr", "prstClr"}), None)
    if element is None:
        return None
    kind = local_name(element)
    value = element.get("lastClr") if kind == "sysClr" else element.get("val", "")
    parts = [value or ""]
    for child in element:
        if not isinstance(child.tag, str):
            continue
        raw = child.get("val")
        if raw is None:
            parts.append(local_name(child))
        else:
            try:
                number = int(raw)
                parts.append(f"{local_name(child)}={number / 1000:g}%")
            except ValueError:
                parts.append(f"{local_name(child)}={raw}")
    return " ".join(parts)


def solid_fill(spec: str) -> Element:
    fill = make("a:solidFill")
    fill.append(color_element(spec))
    return fill


def fill_text(owner: Element | None) -> "str | None | bool":
    """What fills ``owner`` (a ``c:spPr``): a colour, ``None`` for no fill, ``True`` for
    another kind of fill, ``False`` when it says nothing (automatic)."""
    if owner is None:
        return False
    for child in owner:
        name = _prefixed(child)
        if name == "a:noFill":
            return None
        if name == "a:solidFill":
            return color_text(child)
        if name in _FILL_TAGS:
            return True
    return False


# ------------------------------------------------------------------------------------------
# Shape properties: fill, line, dash, width
# ------------------------------------------------------------------------------------------


def shape_properties(owner: Element) -> Element:
    """``owner``'s ``c:spPr``, created (empty, in schema position) when missing."""
    from ..xml import append_in_order

    node = owner.find(qn("c:spPr"))
    if node is None:
        node = append_in_order(owner, make("c:spPr"))
    return node


def set_fill(shape: Element, spec: "str | None") -> None:
    """A solid fill, or none."""
    _replace(shape, _FILL_TAGS, make("a:noFill") if spec is None else solid_fill(spec),
             _SHAPE_ORDER)


def line_of(shape: Element) -> Element:
    node = shape.find(qn("a:ln"))
    if node is None:
        node = place(shape, make("a:ln"), _SHAPE_ORDER)
    return node


def set_line(shape: Element, spec: "str | None", *, width: float | None = None,
             dash: str | None = None, keep_color: bool = False) -> None:
    """The outline (a bar's) or the line (a line series'): colour or ``None`` for none,
    ``width`` in points, ``dash`` a name from :data:`DASHES` or the schema's.
    ``keep_color`` leaves the colour as it is (only width or dash given)."""
    line = line_of(shape)
    if not keep_color:
        fills = ("a:noFill", "a:solidFill", "a:gradFill", "a:pattFill")
        _replace(line, fills, make("a:noFill") if spec is None else solid_fill(spec),
                 _LINE_ORDER)
    if width is not None:
        line.set("w", str(points_to_emu(width)))
    if dash is not None:
        _replace(line, ("a:prstDash", "a:custDash"), make("a:prstDash", val=dash_value(dash)),
                 _LINE_ORDER)


def points_to_emu(points: float) -> int:
    if isinstance(points, bool) or not isinstance(points, (int, float)) \
            or not 0 <= points <= 1584:
        raise FormatError(f"a line width is in points, 0-1584 (got {points!r})")
    return round(points * 12700)


def dash_value(dash: str) -> str:
    if dash in DASHES:
        return DASHES[dash]
    if dash in _DASH_VALUES:
        return dash
    raise FormatError(f"dash {dash!r}; one of {', '.join(DASHES)}")


def line_text(shape: Element | None) -> dict[str, Any]:
    """``{"line", "line_width", "dash"}`` of a ``c:spPr``: the line colour (``None`` for no
    line, absent when automatic), its width in points and its dash, where given."""
    out: dict[str, Any] = {}
    line = None if shape is None else shape.find(qn("a:ln"))
    if line is None:
        return out
    color = fill_text(line)
    if color is not False:
        out["line"] = color
    if line.get("w"):
        try:
            out["line_width"] = round(int(line.get("w")) / 12700, 2)
        except ValueError:
            pass
    dash = line.find(qn("a:prstDash"))
    if dash is not None:
        reverse = {v: k for k, v in DASHES.items()}
        out["dash"] = reverse.get(dash.get("val"), dash.get("val"))
    return out


# ------------------------------------------------------------------------------------------
# Markers
# ------------------------------------------------------------------------------------------

#: ``CT_Marker``.
MARKER_ORDER = ("c:symbol", "c:size", "c:spPr", "c:extLst")


def set_marker(owner: Element, symbol: str | None, size: int | None, fill: Any,
               line: Any, *, keep: object) -> None:
    """``owner``'s ``c:marker``: ``symbol`` (:data:`MARKERS`), ``size`` 2-72, ``fill`` and
    ``line`` colours (``None`` none); arguments that are ``keep`` stay as they are."""
    from ..xml import append_in_order

    marker = owner.find(qn("c:marker"))
    if marker is None:
        marker = append_in_order(owner, make("c:marker"))
    if symbol is not keep:
        if symbol not in MARKERS:
            raise FormatError(f"marker {symbol!r}; one of {', '.join(MARKERS)}")
        _replace(marker, ("c:symbol",), make("c:symbol", val=symbol), MARKER_ORDER)
    if size is not keep:
        if isinstance(size, bool) or not isinstance(size, int) or not 2 <= size <= 72:
            raise FormatError("a marker size is a whole number, 2-72")
        _replace(marker, ("c:size",), make("c:size", val=str(size)), MARKER_ORDER)
    if fill is not keep or line is not keep:
        shape = marker.find(qn("c:spPr"))
        if shape is None:
            shape = place(marker, make("c:spPr"), MARKER_ORDER)
        if fill is not keep:
            set_fill(shape, fill)
        if line is not keep:
            set_line(shape, line, width=0.75 if line is not None and line_of(shape).get("w")
                     is None else None)
        if shape.find(qn("a:effectLst")) is None:
            place(shape, make("a:effectLst"), _SHAPE_ORDER)


def marker_text(owner: Element | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    marker = None if owner is None else owner.find(qn("c:marker"))
    if marker is None:
        return out
    symbol = marker.find(qn("c:symbol"))
    if symbol is not None:
        out["marker"] = symbol.get("val")
    size = marker.find(qn("c:size"))
    if size is not None and (size.get("val") or "").isdigit():
        out["marker_size"] = int(size.get("val"))
    shape = marker.find(qn("c:spPr"))
    fill = fill_text(shape)
    if fill is not False:
        out["marker_fill"] = fill
    line = line_text(shape).get("line", False)
    if line is not False:
        out["marker_line"] = line
    return out


# ------------------------------------------------------------------------------------------
# Gridlines
# ------------------------------------------------------------------------------------------


def gridline(level: int) -> Element:
    """``c:majorGridlines`` (``level`` 15000) or ``c:minorGridlines`` (5000) as Office draws
    them: 0.75 pt at ``level`` of the text colour (measured)."""
    tag = "c:majorGridlines" if level == 15000 else "c:minorGridlines"
    holder = make(tag)
    shape = make("c:spPr")
    line = make("a:ln", w="9525", cap="flat", cmpd="sng", algn="ctr")
    fill = make("a:solidFill")
    color = make("a:schemeClr", val="tx1")
    color.append(make("a:lumMod", val=str(level)))
    color.append(make("a:lumOff", val=str(100000 - level)))
    fill.append(color)
    line.append(fill)
    line.append(make("a:round"))
    shape.append(line)
    shape.append(make("a:effectLst"))
    holder.append(shape)
    return holder


# ------------------------------------------------------------------------------------------
# Text
# ------------------------------------------------------------------------------------------


def style_characters(properties: Element, *, font: Any, size: Any, bold: Any, color: Any,
                     keep: object) -> None:
    """Write ``font`` (a typeface, or ``+mn-lt``/``+mj-lt``), ``size`` (points), ``bold`` and
    ``color`` into one ``a:defRPr`` / ``a:rPr`` / ``a:endParaRPr``."""
    if size is not keep:
        properties.set("sz", str(text_size(size)))
    if bold is not keep:
        properties.set("b", "1" if bold else "0")
    if color is not keep:
        _replace(properties, _FILL_TAGS, solid_fill(color), _CHARACTER_ORDER)
    if font is not keep:
        if not isinstance(font, str) or not font.strip() or len(font) > 31:
            raise FormatError("a font is a typeface name of at most 31 characters, or "
                              "+mn-lt / +mj-lt for the theme's")
        _replace(properties, ("a:latin",), make("a:latin", typeface=font.strip()),
                 _CHARACTER_ORDER)
        theme = {"+mn-lt": "mn", "+mj-lt": "mj"}.get(font.strip())
        if theme is not None:
            for script, tag in (("ea", "a:ea"), ("cs", "a:cs")):
                _replace(properties, (tag,), make(tag, typeface=f"+{theme}-{script}"),
                         _CHARACTER_ORDER)


def text_size(points: Any) -> int:
    if isinstance(points, bool) or not isinstance(points, (int, float)) \
            or not 1 <= points <= 400:
        raise FormatError(f"a text size is in points, 1-400 (got {points!r})")
    return round(points * 100)


def defaults_of(text_properties: Element) -> Element:
    """The ``a:defRPr`` of a ``c:txPr`` (its first paragraph's), created when missing."""
    paragraph = text_properties.find(qn("a:p"))
    if paragraph is None:
        paragraph = make("a:p")
        text_properties.append(paragraph)
    properties = paragraph.find(qn("a:pPr"))
    if properties is None:
        properties = make("a:pPr")
        paragraph.insert(0, properties)
    defaults = properties.find(qn("a:defRPr"))
    if defaults is None:
        defaults = make("a:defRPr")
        properties.append(defaults)
    return defaults


def rich_character_properties(rich: Element) -> list[Element]:
    """Every character-property element of a ``c:rich`` body: each paragraph's ``a:defRPr``
    (created), its runs' and fields' ``a:rPr`` and its ``a:endParaRPr``."""
    out = []
    for paragraph in rich.findall(qn("a:p")):
        properties = paragraph.find(qn("a:pPr"))
        if properties is None:
            properties = make("a:pPr")
            paragraph.insert(0, properties)
        defaults = properties.find(qn("a:defRPr"))
        if defaults is None:
            defaults = make("a:defRPr")
            properties.append(defaults)
        out.append(defaults)
        for run in paragraph:
            if _prefixed(run) in ("a:r", "a:fld"):
                rpr = run.find(qn("a:rPr"))
                if rpr is None:
                    rpr = make("a:rPr")
                    run.insert(0, rpr)
                out.append(rpr)
        end = paragraph.find(qn("a:endParaRPr"))
        if end is not None:
            out.append(end)
    return out


def text_style_of(properties: Element | None) -> dict[str, Any]:
    """``{"font", "size", "bold", "color"}`` an ``a:defRPr`` gives (absent: inherited)."""
    out: dict[str, Any] = {}
    if properties is None:
        return out
    latin = properties.find(qn("a:latin"))
    if latin is not None:
        out["font"] = latin.get("typeface")
    if (properties.get("sz") or "").isdigit():
        out["size"] = int(properties.get("sz")) / 100
    if properties.get("b") is not None:
        out["bold"] = properties.get("b") in ("1", "true")
    color = fill_text(properties)
    if color not in (False, True):
        out["color"] = color
    return out


__all__ = ["DASHES", "FormatError", "LABEL_POSITIONS", "MARKERS", "SCHEME_COLORS",
           "TEXT_PARTS", "color_element", "color_text", "fill_text", "gridline", "line_text",
           "marker_text", "place", "set_fill", "set_line", "set_marker", "shape_properties",
           "solid_fill", "style_characters", "text_size", "text_style_of"]
