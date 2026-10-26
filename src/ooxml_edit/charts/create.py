"""A new chart from data: the chart part and its embedded workbook, written to match.

:func:`add_chart` writes everything a chart needs that is the same whichever document holds
it -- the chart part (``c:chartSpace``), the workbook behind it (what the application's
"Edit Data" opens) and the relationships between them -- and relates the chart to the part
the format's frame will live in.  The format adds only that frame -- its own graphic
frame or inline drawing -- around :meth:`NewChart.graphic`.  That is the whole
insertion hook; from then on the chart is an ordinary :class:`~.chart.Chart` behind a
:class:`~.host.GraphicHost`, edited like any other.

**The data.**  Categories in column A from row 2, each series' name in row 1 and its values
below it -- the layout Office writes and :mod:`.chart` edits -- and every series' formulas
(``Sheet1!$B$2:$B$5``) point there, so the caches and the cells are equal from the start:
:meth:`~.chart.Chart.workbook_values` reads back exactly what was given.  A number format
(``#,##0.0``) is the values' ``formatCode`` in the cache, the cells' number format in the
workbook, and what the value axis shows (it is source-linked).

**The look** is what PowerPoint and Word give a new chart of each type in the document's
theme, measured on Office for Mac 16 (Insert > Chart, each type's first subtype, saved and
read back; ``docs`` of the libraries list the facts): theme accents in order with Office's
colour cycle, no chart-area fill or line in PowerPoint and a background fill with a light
border in Word, light grey gridlines and axis lines, labels in the theme's minor face at 65%
of the text colour, the legend at the bottom (a radar chart's at the top).  Only the sizes of text differ between the two
applications (:data:`POWERPOINT_LOOK`, :data:`WORD_LOOK`).  Everything is written as theme
references (``schemeClr``, ``+mn-lt``), so the chart follows the document's theme.  Office's
chart-style and colour-style parts (``style1.xml``, ``colors1.xml``) are not written: they
only seed Office's own restyling commands, and nothing draws from them.

**A dot chart** (``"dot"``: one marker per category on the value axis, a dot plot of
averages, say) is a native line chart, so Office edits it as one: a ``c:lineChart``
whose series have their line off (``c:spPr/a:ln/a:noFill``) and a marker on, with the
group's ``c:marker`` 1 (ECMA-376 Part 1, 21.2.2, ``CT_LineChart``: markers shown).  Each
series' marker is the one Office gives a new scatter chart's series, measured: a 5 pt
circle in the series colour.  Categories run along the bottom, as in every line chart:
a horizontal line chart is not in the schema (``c:barDir`` is a bar chart's), and a
scatter chart has no category labels.  :attr:`.chart.Chart.is_dot` reads one back.

**Dates as categories.**  A category may be a :class:`~ooxml_edit.dates.DateCategory`: a
date with the Excel number format code it is shown in (``[$-413]d\\ mmmm\\ yyyy``,
:func:`ooxml_edit.dates.format_code`; month and day names measured in Excel for Mac,
2026-10-16).
Then the categories are numbers -- each date's serial in the 1900 date system, as Office
keeps a date cell -- in a ``c:numRef`` whose cache's ``c:formatCode`` is the code; the
workbook's category cells hold the same serials in that number format, and the category
axis shows them through it (``c:numFmt`` source-linked).  The axis stays a text axis
(``c:auto`` 0: ECMA-376 Part 1, 21.2.2, ``auto`` lets the application choose a date axis
from the data instead), so every category is one band, as text categories are.  What a
viewer sees follows the code and the viewer's region: see :mod:`ooxml_edit.dates`.

Nothing here knows a document format: ``tests/test_charts_neutrality.py`` checks it.
"""

from __future__ import annotations

import hashlib
import io
import math
import posixpath
import zipfile
from dataclasses import dataclass
from typing import Any, Sequence
from xml.sax.saxutils import escape, quoteattr

from lxml import etree
from ooxml_common.opc import REL_CHART
from ooxml_common.xmlutil import NS_R as R_NS

from .. import dates as _dates
from ..dates import DateCategory
from ..xml import Element, serialize
from . import namespaces as _namespaces  # noqa: F401  (registers c: before qn is used)
from . import record as _record
from .chart import ChartDataError
from .dmltext import replace_body_text
from .namespaces import A_NS, C_NS
from .workbook import column_letters, number_text

MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
C14_NS = "http://schemas.microsoft.com/office/drawing/2007/8/2/chart"
C16_NS = "http://schemas.microsoft.com/office/drawing/2014/chart"
C16R3_NS = "http://schemas.microsoft.com/office/drawing/2017/03/chart"

REL_PACKAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"
CT_CHART = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
CT_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: The chart types :func:`add_chart` makes: ``kind -> (plot, bar direction, grouping)``.
CHART_KINDS: dict[str, tuple[str, str | None, str | None]] = {
    "column": ("bar", "col", "clustered"),
    "stacked_column": ("bar", "col", "stacked"),
    "bar": ("bar", "bar", "clustered"),
    "stacked_bar": ("bar", "bar", "stacked"),
    "line": ("line", None, "standard"),
    "pie": ("pie", None, None),
    "scatter": ("scatter", None, None),
    "radar": ("radar", None, "marker"),
    "radar_filled": ("radar", None, "filled"),
    "bubble": ("bubble", None, None),
    "dot": ("line", None, "standard"),
}

#: The kinds whose series may be drawn as a line instead (a combination chart): columns.
#: PowerPoint cannot combine horizontal bars with a line.
COMBO_KINDS = ("column", "stacked_column")

#: The kinds whose categories are numbers -- x values.
XY_KINDS = ("scatter", "bubble")

#: Legend positions, as ``c:legendPos`` spells them.
LEGEND_POSITIONS = {"bottom": "b", "right": "r", "top": "t", "left": "l", "top_right": "tr"}

#: ``legend="default"``: where Office puts a new chart's legend -- at the bottom, but at the
#: top of a radar chart (measured).
DEFAULT_LEGEND = "default"


def category_format(labels: Sequence) -> str | None:
    """The format code of a new chart's date categories (:class:`DateCategory`); ``None``
    when they are text or numbers."""
    return labels[0].format_code if labels and isinstance(labels[0], DateCategory) else None


class _DefaultLang:
    """The ``lang`` of :func:`add_chart` and :func:`chart_space` when not given."""

    def __repr__(self) -> str:
        return "DEFAULT_LANG"

    def __reduce__(self) -> str:
        return "DEFAULT_LANG"


#: ``lang`` not given: the locale's language (:func:`locale_language`).
DEFAULT_LANG: Any = _DefaultLang()


def locale_language(locale: Any) -> str:
    """The language a new chart's text is in when the caller names none: ``locale``'s
    ``language`` (an :class:`ooxml_common.locales.Locale`, a session's), else ``en-US``."""
    language = getattr(locale, "language", None) if locale is not None else None
    return language if isinstance(language, str) and language else "en-US"


def default_legend(kind: str) -> str:
    """The legend position Office gives a new chart of ``kind``."""
    return "top" if kind in ("radar", "radar_filled") else "bottom"

#: Office's colour cycle for series (and a pie's slices): the six accents, then the six again
#: in each of these variations (Office's "Colorful palette 1", the colour style of a new
#: chart, read from the colour-style part Office writes beside it).
ACCENTS = ("accent1", "accent2", "accent3", "accent4", "accent5", "accent6")
VARIATIONS: tuple[tuple[tuple[str, int], ...], ...] = (
    (),
    (("lumMod", 60000),),
    (("lumMod", 80000), ("lumOff", 20000)),
    (("lumMod", 80000),),
    (("lumMod", 60000), ("lumOff", 40000)),
    (("lumMod", 50000),),
    (("lumMod", 70000), ("lumOff", 30000)),
    (("lumMod", 70000),),
    (("lumMod", 50000), ("lumOff", 50000)),
)

#: The sheet the data goes on.
SHEET = "Sheet1"


@dataclass(frozen=True)
class ChartLook:
    """What differs between the applications' new charts: text sizes, in hundredths of a
    point, and whether the chart area is filled and outlined."""

    #: The chart title (Office: 14 pt, scaled by the application).
    title: int
    #: An axis title.
    axis_title: int
    #: Axis tick labels and the legend.
    labels: int
    #: Data labels.
    data_labels: int
    #: The chart area: ``False`` no fill and no line (PowerPoint), ``True`` the background
    #: colour and a light border (Word).
    framed: bool


#: PowerPoint's new chart (measured): 18.62 pt title, 13.3 pt axis titles, 11.97 pt labels,
#: legend and data labels; no chart-area fill or line.
POWERPOINT_LOOK = ChartLook(title=1862, axis_title=1330, labels=1197, data_labels=1197,
                            framed=False)
#: Word's new chart (measured): 14 pt title, 10 pt axis titles, 9 pt labels, legend and data
#: labels; a ``bg1`` chart area with a 0.75 pt border at 15% of the text colour.
WORD_LOOK = ChartLook(title=1400, axis_title=1000, labels=900, data_labels=900, framed=True)


@dataclass(frozen=True)
class NewChart:
    """A chart :func:`add_chart` made: the chart part, the workbook part, and the id of the
    relationship from the holder part -- what the format's frame points at."""

    part: str
    workbook: str
    rel_id: str

    def graphic(self) -> Element:
        """``a:graphic`` holding the chart: the inside of the format's frame."""
        graphic = etree.Element(f"{{{A_NS}}}graphic", nsmap={"a": A_NS})
        data = etree.SubElement(graphic, f"{{{A_NS}}}graphicData", uri=C_NS)
        chart = etree.SubElement(data, f"{{{C_NS}}}chart", nsmap={"c": C_NS, "r": R_NS})
        chart.set(f"{{{R_NS}}}id", self.rel_id)
        return graphic


# ------------------------------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------------------------------


def _number(value: Any, what: str) -> "int | float | None":
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)):
        raise ChartDataError(f"{what}: {value!r} is not a finite number")
    return value


def chart_data(kind: str, categories: Sequence | None, series: Sequence
               ) -> tuple[list, list[dict]]:
    """Check a new chart's data and return it as ``(categories, [{"name", "values", "type",
    "size"}])``.

    ``categories`` are text labels (numbers are allowed and kept numeric); a scatter or
    bubble chart's are its x values, numbers.  Each series is ``{"name": str, "values":
    [number | None]}`` (or a ``(name, values)`` pair), one value per category; ``None`` is a
    blank.  A column chart's series may say ``"type": "line"`` (a combination chart); a
    bubble chart's give ``"size"``, one bubble size per point -- or ``{"name", "x", "y",
    "size"}`` with ``categories`` ``None``, every series over the same x values."""
    if kind not in CHART_KINDS:
        raise ChartDataError(f"chart type {kind!r}; one of {', '.join(CHART_KINDS)}")
    entries = []
    for index, entry in enumerate(series):
        if isinstance(entry, dict):
            allowed = {"name", "values", "type"} | ({"size", "x", "y"} if kind == "bubble" else set())
            if set(entry) - allowed:
                raise ChartDataError(f"series {index}: unknown key "
                                     f"{sorted(set(entry) - allowed)[0]!r}; a series is "
                                     f"{{{', '.join(repr(k) for k in sorted(allowed))}}}")
            entry = dict(entry)
            if "y" in entry:
                if "values" in entry:
                    raise ChartDataError(f"series {index}: give values or y, not both")
                entry["values"] = entry.pop("y")
            if "values" not in entry:
                raise ChartDataError(f"series {index} has no values")
        else:
            name, values = entry
            entry = {"name": name, "values": values}
        entries.append(entry)
    if kind == "bubble" and categories is None:
        xs: list[Any] = [entry.get("x") for entry in entries]
        if not entries or any(x is None for x in xs):
            raise ChartDataError("a bubble chart needs x values: categories, or x in each series")
        if any(list(x) != list(xs[0]) for x in xs):
            raise ChartDataError("a bubble chart's series share their x values")
        categories = list(xs[0])
    elif categories is None:
        raise ChartDataError("a chart needs categories")
    labels = list(categories)
    if not labels:
        raise ChartDataError("a chart needs at least one category")
    dated = [isinstance(label, DateCategory) for label in labels]
    if any(dated):
        if not all(dated):
            raise ChartDataError("a chart's categories are all dates or none: category "
                                 f"{dated.index(False)} is not a DateCategory")
        if kind in XY_KINDS:
            raise ChartDataError(f"a {kind} chart's categories are its x values, numbers; "
                                 "dates are for a chart with a category axis")
        if len({label.format_code for label in labels}) > 1:
            raise ChartDataError("a chart's date categories share one format code")
        for index, label in enumerate(labels):
            try:
                _dates.serial(label)
            except ValueError as error:
                raise ChartDataError(f"category {index}: {error}") from None
    for index, label in enumerate(labels):
        if dated[index]:
            continue
        if kind in XY_KINDS:
            if label is None or isinstance(label, str):
                raise ChartDataError(f"x value {index}: {label!r} is not a number (a {kind} "
                                     f"chart's categories are its x values)")
            _number(label, f"x value {index}")
        elif not isinstance(label, str):
            _number(label, f"category {index}")
            if label is None:
                raise ChartDataError(f"category {index} has no label")
    out = []
    for index, entry in enumerate(entries):
        name, values = entry.get("name"), entry["values"]
        if name is not None and not isinstance(name, str):
            raise ChartDataError(f"series {index}: the name is text")
        values = [_number(v, f"series {index} value {k}") for k, v in enumerate(values)]
        if len(values) != len(labels):
            raise ChartDataError(f"series {index} has {len(values)} values for "
                                 f"{len(labels)} categories")
        if "x" in entry and list(entry["x"]) != labels:
            raise ChartDataError(f"series {index}: x values differ from the categories")
        kind_of = entry.get("type") or None
        if kind_of not in (None, "column", "line"):
            raise ChartDataError(f"series {index}: type {kind_of!r}; 'line' or 'column'")
        if kind_of == "line" and kind not in COMBO_KINDS:
            raise ChartDataError(
                f"series {index}: only a column chart draws a series as a line"
                + ("; PowerPoint cannot combine horizontal bars with a line" if "bar" in kind
                   else ""))
        if kind_of == "column":
            if kind not in COMBO_KINDS:
                raise ChartDataError(f"series {index}: type 'column' is for a column chart")
            kind_of = None
        size = None
        if kind == "bubble":
            if entry.get("size") is None:
                raise ChartDataError(f"series {index}: a bubble chart's series give size, one "
                                     f"per point")
            size = [_number(v, f"series {index} size {k}") for k, v in enumerate(entry["size"])]
            if len(size) != len(labels):
                raise ChartDataError(f"series {index} has {len(size)} sizes for "
                                     f"{len(labels)} points")
            if any(v is not None and v < 0 for v in size):
                raise ChartDataError(f"series {index}: a bubble size is 0 or more")
        out.append({"name": name, "values": values, "type": kind_of, "size": size})
    if not out:
        raise ChartDataError("a chart needs at least one series")
    if kind == "pie" and len(out) > 1:
        raise ChartDataError("a pie chart shows one series")
    if len(out) > 255:
        raise ChartDataError("at most 255 series")
    if out and all(entry["type"] == "line" for entry in out):
        raise ChartDataError("every series is a line: make it a line chart")
    return labels, out


def data_columns(kind: str, data: Sequence[dict]) -> list[tuple[int, int | None]]:
    """``(values column, sizes column)`` of each series in the workbook, 1-based: the
    values beside the categories (column A), a bubble chart's sizes right of its values."""
    if kind == "bubble":
        return [(2 + 2 * k, 3 + 2 * k) for k in range(len(data))]
    return [(2 + k, None) for k in range(len(data))]


# ------------------------------------------------------------------------------------------
# The chart part
# ------------------------------------------------------------------------------------------


def _e(tag: str, attributes: dict[str, str] | None = None, *children: Element,
       nsmap: dict | None = None) -> Element:
    prefix, _, name = tag.partition(":")
    uri = {"c": C_NS, "a": A_NS, "mc": MC_NS, "c14": C14_NS, "c16": C16_NS,
           "c16r3": C16R3_NS}[prefix]
    element = etree.Element(f"{{{uri}}}{name}", nsmap=nsmap)
    for key, value in (attributes or {}).items():
        element.set(key, value)
    for child in children:
        element.append(child)
    return element


def _val(tag: str, value: Any) -> Element:
    return _e(tag, {"val": str(value)})


def scheme(name: str, mods: Sequence[tuple[str, int]] = ()) -> Element:
    """``a:schemeClr`` with its modifiers."""
    return _e("a:schemeClr", {"val": name}, *[_val(f"a:{key}", value) for key, value in mods])


def series_color(index: int) -> Element:
    """Office's automatic colour for series (or slice) ``index``."""
    variation = VARIATIONS[(index // len(ACCENTS)) % len(VARIATIONS)]
    return scheme(ACCENTS[index % len(ACCENTS)], variation)


def _fill(color: Element) -> Element:
    return _e("a:solidFill", None, color)


def _no_line() -> Element:
    return _e("a:ln", None, _e("a:noFill"))


def _grey_line(level: int = 15000) -> Element:
    return _e("a:ln", {"w": "9525", "cap": "flat", "cmpd": "sng", "algn": "ctr"},
              _fill(scheme("tx1", [("lumMod", level), ("lumOff", 100000 - level)])),
              _e("a:round"))


def _sp(*children: Element) -> Element:
    return _e("c:spPr", None, *children, _e("a:effectLst"))


def _none_sp() -> Element:
    return _sp(_e("a:noFill"), _no_line())


def _body(rot: str) -> Element:
    return _e("a:bodyPr", {"rot": rot, "spcFirstLastPara": "1", "vertOverflow": "ellipsis",
                           "vert": "horz", "wrap": "square", "anchor": "ctr", "anchorCtr": "1"})


def text_defaults(size: int, *, shade: int = 65000, title: bool = False) -> Element:
    """``a:defRPr`` as Office's chart style writes it: the theme's minor face, the text colour
    at ``shade`` (65% for titles, axes and the legend; 75% for data labels)."""
    attributes = {"sz": str(size), "b": "0", "i": "0", "u": "none", "strike": "noStrike",
                  "kern": "1200"}
    if title:
        attributes["spc"] = "0"
    attributes["baseline"] = "0"
    return _e("a:defRPr", attributes,
              _fill(scheme("tx1", [("lumMod", shade), ("lumOff", 100000 - shade)])),
              _e("a:latin", {"typeface": "+mn-lt"}), _e("a:ea", {"typeface": "+mn-ea"}),
              _e("a:cs", {"typeface": "+mn-cs"}))


def _end(lang: str | None) -> Element:
    return _e("a:endParaRPr", {"lang": lang} if lang else None)


def text_properties(size: int, *, rot: str = "0", shade: int = 65000, title: bool = False,
                    lang: str | None = None) -> Element:
    """``c:txPr`` of an element whose text Office styles (title, axis, legend, labels)."""
    return _e("c:txPr", None, _body(rot), _e("a:lstStyle"),
              _e("a:p", None, _e("a:pPr", None, text_defaults(size, shade=shade, title=title)),
                 _end(lang)))


def title_element(text: str, size: int, *, vertical: bool = False, lang: str | None = None
                  ) -> Element:
    """A chart or axis ``c:title`` with ``text``, in Office's default title style."""
    rot = "-5400000" if vertical else "0"
    tx = title_text(size, vertical=vertical, lang=lang)
    replace_body_text(tx[0], text)
    return _e("c:title", None, tx, _val("c:overlay", 0), _none_sp(),
              text_properties(size, rot=rot, title=True, lang=lang))


def title_text(size: int, *, vertical: bool = False, lang: str | None = None) -> Element:
    """The empty ``c:tx`` of a title in Office's default title style, ``size`` hundredths of
    a point; text is then written into it with :func:`~.dmltext.replace_body_text`, its runs
    taking their properties from the paragraph's end (``lang``)."""
    rot = "-5400000" if vertical else "0"
    rich = _e("c:rich", None, _body(rot), _e("a:lstStyle"),
              _e("a:p", None, _e("a:pPr", None, text_defaults(size, title=True)), _end(lang)))
    return _e("c:tx", None, rich)


def _ids(seed: str) -> tuple[str, str, str]:
    """Two axis ids and the shared tail of the series' unique ids, from ``seed``: Office picks
    them at random; here they are a hash, so the same chart is the same bytes."""
    digest = hashlib.sha256(seed.encode()).hexdigest().upper()
    first = int(digest[:7], 16) + 1
    second = int(digest[7:14], 16) + 1
    tail = f"{digest[14:18]}-{digest[18:22]}-{digest[22:26]}-{digest[26:38]}"
    return str(first), str(second), tail


def _formula(column: int, first_row: int, last_row: int | None = None) -> str:
    letters = column_letters(column)
    if last_row is None or last_row == first_row:
        return f"{SHEET}!${letters}${first_row}"
    return f"{SHEET}!${letters}${first_row}:${letters}${last_row}"


def _str_ref(formula: str, values: Sequence[str]) -> Element:
    cache = _e("c:strCache", None, _val("c:ptCount", len(values)))
    for index, value in enumerate(values):
        point = _e("c:pt", {"idx": str(index)}, _e("c:v"))
        point[0].text = value
        cache.append(point)
    return _e("c:strRef", None, _f(formula), cache)


def _num_ref(formula: str, values: Sequence, format_code: str) -> Element:
    cache = _e("c:numCache", None, _e("c:formatCode"), _val("c:ptCount", len(values)))
    cache[0].text = format_code
    for index, value in enumerate(values):
        if value is None:
            continue
        point = _e("c:pt", {"idx": str(index)}, _e("c:v"))
        point[0].text = number_text(value)
        cache.append(point)
    return _e("c:numRef", None, _f(formula), cache)


def _f(formula: str) -> Element:
    node = _e("c:f")
    node.text = formula
    return node


def _category_ref(labels: list, count: int) -> Element:
    formula = _formula(1, 2, count + 1)
    code = category_format(labels)
    if code is not None:
        return _num_ref(formula, [_dates.serial(label) for label in labels], code)
    if all(isinstance(label, str) for label in labels):
        return _str_ref(formula, labels)
    if any(isinstance(label, str) for label in labels):
        return _str_ref(formula, [label if isinstance(label, str) else number_text(label)
                                  for label in labels])
    return _num_ref(formula, labels, "General")


def _unique_id(index: int, tail: str) -> Element:
    ext = _e("c:ext", {"uri": "{C3380CC4-5D6E-409C-BE32-E72D297353CC}"},
             nsmap={"c16": C16_NS})
    ext.append(_e("c16:uniqueId", {"val": f"{{{index:08X}-{tail}}}"}))
    return _e("c:extLst", None, ext)


def _series(kind: str, index: int, entry: dict, labels: list, format_code: str, tail: str,
            column: int, size_column: int | None) -> Element:
    """A new ``c:ser``: series ``index`` of a ``kind`` chart, its values in workbook column
    ``column`` (a bubble chart's sizes in ``size_column``), drawn as Office draws it."""
    plot, _, style = CHART_KINDS[kind]
    if entry.get("type") == "line":
        plot = "line"
    name, values = entry["name"], entry["values"]
    count = len(labels)
    ser = _e("c:ser", None, _val("c:idx", index), _val("c:order", index))
    if name is not None:
        ser.append(_e("c:tx", None, _str_ref(_formula(column, 1), [name])))
    color = series_color(index)
    if plot == "bar":
        ser.append(_sp(_fill(color), _no_line()))
        ser.append(_val("c:invertIfNegative", 0))
    elif plot == "radar" and style == "filled":
        # Office's filled radar: the area in the series colour, no line, no marker (measured).
        ser.append(_sp(_fill(color), _no_line()))
    elif kind == "dot":
        # A line series with its line off and a marker on (see :func:`dot_marker`).
        ser.append(no_line_shape())
        ser.append(dot_marker(index))
    elif plot in ("line", "radar"):
        ser.append(_sp(_e("a:ln", {"w": "28575", "cap": "rnd"}, _fill(color), _e("a:round"))))
        ser.append(_e("c:marker", None, _val("c:symbol", "none")))
    elif plot == "scatter":
        # Office's new scatter: markers, no line (measured).
        ser.append(no_line_shape(SCATTER_LINE_WIDTH))
        ser.append(dot_marker(index))
    elif plot == "bubble":
        # Office's bubble: the series colour at 75% opacity, no line (measured).
        translucent = series_color(index)
        translucent.append(_val("a:alpha", 75000))
        ser.append(_sp(_fill(translucent), _no_line()))
        ser.append(_val("c:invertIfNegative", 0))
    elif plot == "pie":
        for point in range(count):
            ser.append(_e("c:dPt", None, _val("c:idx", point), _val("c:bubble3D", 0),
                          _sp(_fill(series_color(point)),
                              _e("a:ln", {"w": "19050"}, _fill(scheme("lt1"))))))
    values_formula = _formula(column, 2, count + 1)
    if plot in ("scatter", "bubble"):
        ser.append(_e("c:xVal", None, _num_ref(_formula(1, 2, count + 1), labels, "General")))
        ser.append(_e("c:yVal", None, _num_ref(values_formula, values, format_code)))
    else:
        ser.append(_e("c:cat", None, _category_ref(labels, count)))
        ser.append(_e("c:val", None, _num_ref(values_formula, values, format_code)))
    if plot == "bubble":
        assert size_column is not None  # a bubble series is given its sizes' column
        ser.append(_e("c:bubbleSize", None, _num_ref(_formula(size_column, 2, count + 1),
                                                     entry["size"], "General")))
        ser.append(_val("c:bubble3D", 0))
    if plot in ("line", "scatter"):
        ser.append(_val("c:smooth", 0))
    ser.append(_unique_id(index, tail))
    return ser


#: The width of a line series' line, in EMU (2.25 pt), as Office writes a new one.
LINE_WIDTH = 28575
#: The width of a new scatter series' (unfilled) line, in EMU (1.5 pt), as Office writes it.
SCATTER_LINE_WIDTH = 19050


def no_line_shape(width: int = LINE_WIDTH) -> Element:
    """A series' ``c:spPr`` with its line off: a dot chart's -- a line series' line (as
    :func:`_series` writes it) with its fill off -- or, at :data:`SCATTER_LINE_WIDTH`, a
    new scatter chart's."""
    return _sp(_e("a:ln", {"w": str(width), "cap": "rnd"}, _e("a:noFill"), _e("a:round")))


def marker_shape(index: int) -> Element:
    """A marker's ``c:spPr``, filled and outlined (0.75 pt) in series ``index``'s colour, as
    Office writes a new scatter chart's series."""
    return _sp(_fill(series_color(index)), _e("a:ln", {"w": "9525"}, _fill(series_color(index))))


def dot_marker(index: int) -> Element:
    """The ``c:marker`` of series ``index`` of a ``dot`` chart: the marker Office gives a
    new scatter chart's series (measured, as :func:`_series` writes it) -- a 5 pt circle
    filled and outlined in the series colour."""
    return _e("c:marker", None, _val("c:symbol", "circle"), _val("c:size", 5),
              marker_shape(index))


def _no_labels(pie: bool = False) -> Element:
    labels = _e("c:dLbls", None, *[_val(f"c:{flag}", 0) for flag in (
        "showLegendKey", "showVal", "showCatName", "showSerName", "showPercent",
        "showBubbleSize")])
    if pie:
        labels.append(_val("c:showLeaderLines", 1))
    return labels


def _axis_common(tag: str, axis_id: str, cross_id: str, position: str, look: ChartLook,
                 lang: str | None, *, gridlines: bool, line: int | None,
                 format_code: str) -> Element:
    axis = _e(tag, None, _val("c:axId", axis_id),
              _e("c:scaling", None, _val("c:orientation", "minMax")), _val("c:delete", 0),
              _val("c:axPos", position))
    if gridlines:
        axis.append(_e("c:majorGridlines", None, _sp(_grey_line())))
    axis.append(_e("c:numFmt", {"formatCode": format_code, "sourceLinked": "1"}))
    axis.append(_val("c:majorTickMark", "none"))
    axis.append(_val("c:minorTickMark", "none"))
    axis.append(_val("c:tickLblPos", "nextTo"))
    axis.append(_sp(_e("a:noFill"), _grey_line(line)) if line is not None else _none_sp())
    axis.append(text_properties(look.labels, rot="-60000000", lang=lang))
    axis.append(_val("c:crossAx", cross_id))
    axis.append(_val("c:crosses", "autoZero"))
    return axis


def _axes(kind: str, ids: tuple[str, str], look: ChartLook, lang: str | None,
          format_code: str, titles: dict[str, str],
          category_code: str | None = None) -> list[Element]:
    plot, direction, _ = CHART_KINDS[kind]
    first, second = ids
    if plot in ("scatter", "bubble"):
        x = _axis_common("c:valAx", first, second, "b", look, lang, gridlines=True,
                         line=25000, format_code="General")
        x.append(_val("c:crossBetween", "midCat"))
        y = _axis_common("c:valAx", second, first, "l", look, lang, gridlines=True,
                         line=25000, format_code=format_code)
        y.append(_val("c:crossBetween", "midCat"))
        axes = [(x, "category", False), (y, "value", True)]
    else:
        # A radar's category axis is its spokes, its value axis the rings (the gridlines):
        # the same elements as a column chart's, measured.
        horizontal = direction == "bar"
        category = _axis_common("c:catAx", first, second, "l" if horizontal else "b", look,
                                lang, gridlines=False, line=15000,
                                format_code=category_code or "General")
        # Dates stay one band each: a text axis, not one Office may make a date axis.
        auto = 0 if category_code is not None else 1
        for flag, value in (("auto", auto), ("lblAlgn", "ctr"), ("lblOffset", 100),
                            ("noMultiLvlLbl", 0)):
            category.append(_val(f"c:{flag}", value))
        value = _axis_common("c:valAx", second, first, "b" if horizontal else "l", look, lang,
                             gridlines=True, line=None, format_code=format_code)
        value.append(_val("c:crossBetween", "between"))
        axes = [(category, "category", horizontal), (value, "value", not horizontal)]
    for axis, which, vertical in axes:
        text = titles.get(which)
        if text is not None:
            title = title_element(text, look.axis_title, vertical=vertical, lang=lang)
            # c:title follows c:majorGridlines (or c:axPos) and precedes c:numFmt.
            axis.find(f"{{{C_NS}}}numFmt").addprevious(title)
    return [axis for axis, _, _ in axes]


def check_options(kind: str, category_order: str, gridlines: bool, bubble_scale: int | None) -> None:
    """Raise :class:`ChartDataError`, saying what to pass instead, for options ``kind`` cannot take."""
    if category_order not in ("normal", "reversed"):
        raise ChartDataError("category_order is 'normal' or 'reversed'")
    if category_order == "reversed" and kind == "pie":
        raise ChartDataError("a pie chart has no category axis to reverse")
    if not isinstance(gridlines, bool):
        raise ChartDataError("gridlines is true or false")
    if bubble_scale is not None:
        if kind != "bubble":
            raise ChartDataError("bubble_scale is for a bubble chart")
        if isinstance(bubble_scale, bool) or not isinstance(bubble_scale, int) \
                or not 0 <= bubble_scale <= 300:
            raise ChartDataError("bubble_scale is a whole percentage, 0-300")


#: The old name, kept one release for the sibling that imports it (tests/test_public_api.py).
_options = check_options


def chart_space(kind: str, categories: Sequence | None, series: Sequence, *,
                title: str | None = None, axis_titles: dict[str, str] | None = None,
                legend: str | None = DEFAULT_LEGEND, number_format: str | None = None,
                look: ChartLook = POWERPOINT_LOOK, lang: Any = DEFAULT_LANG, seed: str = "",
                workbook_rel: str = "rId1", category_order: str = "normal",
                gridlines: bool = True, bubble_scale: int | None = None,
                locale: Any = None) -> Element:
    """The ``c:chartSpace`` of a new chart (see :func:`add_chart`).  ``workbook_rel`` is the
    id of the chart part's relationship to its workbook."""
    fallback = locale_language(locale)
    if lang is DEFAULT_LANG:
        lang = fallback
    labels, data = chart_data(kind, categories, series)
    check_options(kind, category_order, gridlines, bubble_scale)
    plot_kind, direction, grouping = CHART_KINDS[kind]
    combo = any(entry["type"] == "line" for entry in data)
    format_code = number_format or "General"
    titles = dict(axis_titles or {})
    if set(titles) - {"category", "value"}:
        raise ChartDataError("axis titles are for the 'category' and 'value' axes")
    if plot_kind == "pie" and titles:
        raise ChartDataError("a pie chart has no axes to title")
    if legend == DEFAULT_LEGEND:
        legend = default_legend(kind)
    if legend is not None and legend not in LEGEND_POSITIONS:
        raise ChartDataError(f"legend {legend!r}; one of {', '.join(LEGEND_POSITIONS)}, "
                             f"{DEFAULT_LEGEND!r} or None")
    first, second, tail = _ids(f"{seed}\n{kind}\n{len(data)}")

    space = _e("c:chartSpace", None, nsmap={"c": C_NS, "a": A_NS, "r": R_NS})
    space.append(_val("c:date1904", 0))
    space.append(_val("c:lang", lang or fallback))
    space.append(_val("c:roundedCorners", 0))
    alternate = _e("mc:AlternateContent", nsmap={"mc": MC_NS})
    choice = _e("mc:Choice", {"Requires": "c14"}, nsmap={"c14": C14_NS})
    choice.append(_val("c14:style", 102))
    alternate.append(choice)
    alternate.append(_e("mc:Fallback", None, _val("c:style", 2)))
    space.append(alternate)

    chart = _e("c:chart")
    if title is not None:
        chart.append(title_element(title, look.title, lang=lang))
        chart.append(_val("c:autoTitleDeleted", 0))
    else:
        chart.append(_val("c:autoTitleDeleted", 1))
    plot_area = _e("c:plotArea", None, _e("c:layout"))
    if plot_kind == "bar":
        plot = _e("c:barChart", None, _val("c:barDir", direction), _val("c:grouping", grouping),
                  _val("c:varyColors", 0))
    elif plot_kind == "line":
        plot = _e("c:lineChart", None, _val("c:grouping", grouping), _val("c:varyColors", 0))
    elif plot_kind == "pie":
        plot = _e("c:pieChart", None, _val("c:varyColors", 1))
    elif plot_kind == "radar":
        # Office's "marker" style with each series' marker off: lines only; "filled" for a
        # filled radar (measured).
        plot = _e("c:radarChart", None, _val("c:radarStyle", grouping), _val("c:varyColors", 0))
    elif plot_kind == "bubble":
        plot = _e("c:bubbleChart", None, _val("c:varyColors", 0))
    else:
        plot = _e("c:scatterChart", None, _val("c:scatterStyle", "lineMarker"),
                  _val("c:varyColors", 0))
    lines = _e("c:lineChart", None, _val("c:grouping", "standard"), _val("c:varyColors", 0))
    for index, (entry, (column, sizes)) in enumerate(zip(data, data_columns(kind, data))):
        (lines if entry["type"] == "line" else plot).append(
            _series(kind, index, entry, labels, format_code, tail, column, sizes))
    plot.append(_no_labels(pie=plot_kind == "pie"))
    if plot_kind == "bar":
        stacked = grouping == "stacked"
        # Clustered: Office's 219 (columns) and 182 (bars), measured; overlap -27 for columns.
        # Stacked: overlap 100 is what stacking needs; the gap width 150 is the schema's default.
        # Columns with a line: 150 and no overlap (Office's combination chart, measured).
        plot.append(_val("c:gapWidth", 150 if stacked or combo
                         else (219 if direction == "col" else 182)))
        if stacked:
            plot.append(_val("c:overlap", 100))
        elif direction == "col" and not combo:
            plot.append(_val("c:overlap", -27))
    elif plot_kind == "bubble":
        plot.append(_val("c:bubbleScale", 100 if bubble_scale is None else bubble_scale))
        plot.append(_val("c:showNegBubbles", 0))
    elif plot_kind == "line":
        if kind == "dot":
            plot.append(_val("c:marker", 1))
        plot.append(_val("c:smooth", 0))
    elif plot_kind == "pie":
        plot.append(_val("c:firstSliceAng", 0))
    if plot_kind != "pie":
        plot.append(_val("c:axId", first))
        plot.append(_val("c:axId", second))
    plot_area.append(plot)
    if combo:
        # The line plot shares the columns' axes (Office's combination chart, measured).
        lines.append(_no_labels())
        lines.append(_val("c:marker", 1))
        lines.append(_val("c:smooth", 0))
        lines.append(_val("c:axId", first))
        lines.append(_val("c:axId", second))
        plot_area.append(lines)
    if plot_kind != "pie":
        axes = _axes(kind, (first, second), look, lang, format_code, titles,
                     category_format(labels))
        if combo:  # Office writes a combination chart's axes without c:crosses (measured)
            for axis in axes:
                for node in axis.findall(f"{{{C_NS}}}crosses"):
                    axis.remove(node)
        if not gridlines:
            for axis in axes:
                for node in axis.findall(f"{{{C_NS}}}majorGridlines"):
                    axis.remove(node)
        if category_order == "reversed":
            # As PowerPoint's "Categories in reverse order", with the value axis kept where
            # it was by crossing it at the maximum category (measured).
            axes[0].find(f"{{{C_NS}}}scaling/{{{C_NS}}}orientation").set("val", "maxMin")
            crosses = axes[1].find(f"{{{C_NS}}}crosses")
            if crosses is None:
                crosses = _val("c:crosses", "max")
                axes[1].find(f"{{{C_NS}}}crossAx").addnext(crosses)
            crosses.set("val", "max")
        for axis in axes:
            plot_area.append(axis)
    plot_area.append(_none_sp())
    chart.append(plot_area)
    if legend is not None:
        chart.append(_e("c:legend", None, _val("c:legendPos", LEGEND_POSITIONS[legend]),
                        _val("c:overlay", 0), _none_sp(),
                        text_properties(look.labels, lang=lang)))
    chart.append(_val("c:plotVisOnly", 1))
    chart.append(_val("c:dispBlanksAs", "gap"))
    extension = _e("c:ext", {"uri": "{56B9EC1D-385E-4148-901F-78D8002777C0}"},
                   nsmap={"c16r3": C16R3_NS})
    extension.append(_e("c16r3:dataDisplayOptions16", None, _val("c16r3:dispNaAsBlank", 1)))
    chart.append(_e("c:extLst", None, extension))
    space.append(chart)
    if look.framed:
        space.append(_sp(_fill(scheme("bg1")), _grey_line()))
    else:
        space.append(_none_sp())
    space.append(_e("c:txPr", None, _e("a:bodyPr"), _e("a:lstStyle"),
                    _e("a:p", None, _e("a:pPr", None, _e("a:defRPr")), _end(lang))))
    external = _e("c:externalData", None, _val("c:autoUpdate", 0))
    external.set(f"{{{R_NS}}}id", workbook_rel)
    space.append(external)
    return space


# ------------------------------------------------------------------------------------------
# The workbook
# ------------------------------------------------------------------------------------------

_SML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def chart_workbook(kind: str, categories: Sequence | None, series: Sequence, *,
                   number_format: str | None = None) -> bytes:
    """The ``.xlsx`` behind a new chart: ``Sheet1`` with the categories in column A from row
    2, each series' name in row 1 and its values below (a bubble chart's sizes in the next
    column, headed "Size", as Office lays a bubble chart out), shared strings, and the number
    format on the value cells.  Written with the standard library and nothing of Office's."""
    labels, data = chart_data(kind, categories, series)
    strings: list[str] = []
    index_of: dict[str, int] = {}

    references = [0]

    def shared(text: str) -> int:
        references[0] += 1
        if text not in index_of:
            index_of[text] = len(strings)
            strings.append(text)
        return index_of[text]

    custom = number_format not in (None, "General")
    value_style = ' s="1"' if custom else ""
    dates_code = category_format(labels)
    date_style = f' s="{2 if custom else 1}"' if dates_code is not None else ""
    rows = []
    header = []
    columns = data_columns(kind, data)
    for entry, (column, sizes) in zip(data, columns):
        if entry["name"] is not None:
            header.append(f'<c r="{column_letters(column)}1" t="s"><v>{shared(entry["name"])}</v></c>')
        if sizes is not None:
            header.append(f'<c r="{column_letters(sizes)}1" t="s"><v>{shared("Size")}</v></c>')
    last_column = column_letters(max(c if s is None else s for c, s in columns))
    if header:
        rows.append(f'<row r="1">{"".join(header)}</row>')
    for row, label in enumerate(labels, start=2):
        cells = []
        if isinstance(label, str):
            cells.append(f'<c r="A{row}" t="s"><v>{shared(label)}</v></c>')
        elif dates_code is not None:
            cells.append(f'<c r="A{row}"{date_style}><v>{number_text(_dates.serial(label))}'
                         '</v></c>')
        else:
            cells.append(f'<c r="A{row}"><v>{number_text(label)}</v></c>')
        for entry, (column, sizes) in zip(data, columns):
            value = entry["values"][row - 2]
            if value is not None:
                cells.append(f'<c r="{column_letters(column)}{row}"{value_style}>'
                             f'<v>{number_text(value)}</v></c>')
            size = None if sizes is None else entry["size"][row - 2]
            if sizes is not None and size is not None:
                cells.append(f'<c r="{column_letters(sizes)}{row}"><v>{number_text(size)}</v></c>')
        rows.append(f'<row r="{row}">{"".join(cells)}</row>')
    first_row = 1 if header else 2
    dimension = f"A{first_row}:{last_column}{len(labels) + 1}"
    sheet = (f'<worksheet xmlns="{_SML}" xmlns:r="{_DOC_REL}"><dimension ref="{dimension}"/>'
             f'<sheetViews><sheetView tabSelected="1" workbookViewId="0"/></sheetViews>'
             f'<sheetFormatPr defaultRowHeight="15"/><sheetData>{"".join(rows)}</sheetData>'
             f'<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" '
             f'footer="0.3"/></worksheet>')
    preserve = ' xml:space="preserve"'
    sst = "".join(f"<si><t{preserve if s != s.strip() else ''}>{escape(s)}</t></si>"
                  for s in strings)
    shared_strings = (f'<sst xmlns="{_SML}" count="{references[0]}" uniqueCount="{len(strings)}">'
                      f'{sst}</sst>')
    codes = ([number_format] if custom and number_format is not None else []) + ([dates_code] if dates_code else [])
    formats = (f'<numFmts count="{len(codes)}">'
               + "".join(f'<numFmt numFmtId="{164 + i}" formatCode={quoteattr(code)}/>'
                         for i, code in enumerate(codes)) + '</numFmts>' if codes else "")
    styled = "".join(f'<xf numFmtId="{164 + i}" fontId="0" fillId="0" borderId="0" xfId="0" '
                     'applyNumberFormat="1"/>' for i in range(len(codes)))
    styles = (f'<styleSheet xmlns="{_SML}">{formats}'
              '<fonts count="1"><font><sz val="11"/><color theme="1"/><name val="Calibri"/>'
              '<family val="2"/><scheme val="minor"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill>'
              '<fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              + f'<cellXfs count="{1 + len(codes)}"><xf numFmtId="0" fontId="0" fillId="0" '
                f'borderId="0" xfId="0"/>{styled}</cellXfs>' 
              + '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
              '</styleSheet>')
    workbook = (f'<workbook xmlns="{_SML}" xmlns:r="{_DOC_REL}"><bookViews><workbookView/>'
                f'</bookViews><sheets><sheet name="{SHEET}" sheetId="1" r:id="rId1"/></sheets>'
                f'</workbook>')
    workbook_rels = (f'<Relationships xmlns="{_PKG_RELS}">'
                     f'<Relationship Id="rId1" Type="{_DOC_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
                     f'<Relationship Id="rId2" Type="{_DOC_REL}/styles" Target="styles.xml"/>'
                     f'<Relationship Id="rId3" Type="{_DOC_REL}/sharedStrings" Target="sharedStrings.xml"/>'
                     f'</Relationships>')
    root_rels = (f'<Relationships xmlns="{_PKG_RELS}"><Relationship Id="rId1" '
                 f'Type="{_DOC_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    main = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{main}.sheet.main+xml"/>'
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{main}.worksheet+xml"/>'
        f'<Override PartName="/xl/styles.xml" ContentType="{main}.styles+xml"/>'
        f'<Override PartName="/xl/sharedStrings.xml" ContentType="{main}.sharedStrings+xml"/>'
        '</Types>')
    declaration = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
    parts = [("[Content_Types].xml", content_types), ("_rels/.rels", root_rels),
             ("xl/workbook.xml", workbook), ("xl/_rels/workbook.xml.rels", workbook_rels),
             ("xl/worksheets/sheet1.xml", sheet), ("xl/styles.xml", styles),
             ("xl/sharedStrings.xml", shared_strings)]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # the same bytes on every platform (opc._new_entry)
            archive.writestr(info, (declaration + text).encode("utf-8"))
    return buffer.getvalue()


# ------------------------------------------------------------------------------------------
# Into a package
# ------------------------------------------------------------------------------------------


def _charts_folder(part: str) -> str:
    """Where a document keeps its charts: ``charts`` in the top folder of the holder part,
    as Office does."""
    top = part.strip("/").split("/")[0]
    return f"{top}/charts" if "/" in part.strip("/") else "charts"


def add_chart(package, part: str, kind: str, categories: Sequence | None, series: Sequence, *,
              title: str | None = None, axis_titles: dict[str, str] | None = None,
              legend: str | None = DEFAULT_LEGEND, number_format: str | None = None,
              look: ChartLook = POWERPOINT_LOOK, lang: Any = DEFAULT_LANG,
              category_order: str = "normal", gridlines: bool = True,
              bubble_scale: int | None = None, locale: Any = None) -> NewChart:
    """Write a new chart into ``package`` and relate it to ``part`` (the part whose
    relationships the frame's ``r:id`` resolves in) -- everything but the frame, which the format adds around
    :meth:`NewChart.graphic`.

    ``kind`` is one of :data:`CHART_KINDS`; ``categories`` the category labels (a scatter
    or bubble chart's x values), or dates each a :class:`DateCategory` (see the module's
    notes); ``series`` a list of ``{"name", "values"}``, one value per
    category, ``None`` for a blank -- with ``"type": "line"`` for a series of a column chart
    drawn as a line (Office's combination chart), and a bubble chart's ``"size"`` (or
    ``{"name", "x", "y", "size"}``, ``categories`` ``None``) -- see :func:`chart_data`.
    ``category_order="reversed"`` puts the first category at the top of a bar chart (at the
    right of a column chart), the value axis staying where it was; ``gridlines=False`` draws
    none; ``bubble_scale`` is the bubbles' size in percent of Office's default (100).  ``title`` and ``axis_titles`` (``{"category": ..., "value":
    ...}``) are text; ``legend`` a position from :data:`LEGEND_POSITIONS`, ``None`` for none
    or ``"default"`` for Office's (the bottom; a radar chart's top);
    ``number_format`` an Excel format code for the values (``#,##0.0``).  ``look`` is the
    application's (:data:`POWERPOINT_LOOK`, :data:`WORD_LOOK`); ``lang`` goes on the text
    and in ``c:lang`` -- the document's language, which the caller reads from it.  Not
    given, it is ``locale.language`` (``locale`` an :class:`ooxml_common.locales.Locale`,
    or anything with a ``language``: a session's :attr:`~ooxml_edit.tools.Session.locale`),
    else ``en-US``; ``None`` writes no ``lang`` on the text and the same fallback in
    ``c:lang``.

    Not an undo step of its own: call it inside the format's edit, as its frame is added.
    Raises :class:`~.chart.ChartDataError` for data that cannot be charted, before anything
    is written.
    """
    labels, data = chart_data(kind, categories, series)
    check_options(kind, category_order, gridlines, bubble_scale)
    data = [{key: value for key, value in entry.items() if value is not None or key == "name"}
            for entry in data]
    for entry in data:
        entry.setdefault("values", [])
    folder = _charts_folder(part)
    chart_name = package.unused_part_name(f"{folder}/chart{{n}}.xml")
    embeddings = posixpath.join(posixpath.dirname(folder), "embeddings")
    book_name = package.unused_part_name(f"{embeddings}/Microsoft_Excel_Worksheet{{n}}.xlsx")
    space = chart_space(kind, labels, data, title=title, axis_titles=axis_titles, legend=legend,
                        number_format=number_format, look=look, lang=lang, seed=chart_name,
                        locale=locale,
                        category_order=category_order, gridlines=gridlines,
                        bubble_scale=bubble_scale)
    book = chart_workbook(kind, labels, data, number_format=number_format)
    package.add_part(book_name, book, CT_XLSX)
    package.add_part(chart_name, serialize(space), CT_CHART, override=True)
    workbook_rel = package.add_relationship(chart_name, REL_PACKAGE, book_name)
    external = space.find(f"{{{C_NS}}}externalData")
    if external.get(f"{{{R_NS}}}id") != workbook_rel:  # pragma: no cover (a new part: rId1)
        external.set(f"{{{R_NS}}}id", workbook_rel)
        package.replace_part(chart_name, serialize(space))
    rel_id = package.add_relationship(part, REL_CHART, chart_name)
    _record.note(package, chart_name)  # for a placement watching (record.py)
    return NewChart(part=chart_name, workbook=book_name, rel_id=rel_id)


__all__ = ["ACCENTS", "CHART_KINDS", "COMBO_KINDS", "XY_KINDS", "data_columns", "ChartLook",
           "DateCategory", "DEFAULT_LANG", "DEFAULT_LEGEND", "category_format",
           "LEGEND_POSITIONS", "locale_language",
           "NewChart", "POWERPOINT_LOOK", "VARIATIONS", "WORD_LOOK", "add_chart", "chart_data",
           "chart_space", "chart_workbook", "check_options", "default_legend", "series_color",
           "text_properties", "title_element", "title_text"]
