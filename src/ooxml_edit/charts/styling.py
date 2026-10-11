"""A chart's formatting, as :class:`~.chart.Chart` methods: axis scales, series and point
colours, lines and markers, combination charts, chart text, gridlines and point labels.

Kept apart from the data editing in :mod:`.chart`; :class:`~.chart.Chart` inherits it.  Each
method is one undo step, through the host, and changes the chart part only -- but
:meth:`ChartStyling.set_point_labels` with ``cells=True``, which writes the labels into the
embedded workbook too, so Edit Data and PowerPoint's "Value From Cells" show them.

Arguments left out keep what the chart has (the sentinel :data:`KEEP`); ``None`` means
"automatic" for a scale and "none" for a colour.
"""

from __future__ import annotations

import copy
import hashlib
import math
from typing import Any, Sequence

from lxml import etree

from ..xml import Element, append_in_order, find, local_name, make, qn, remove, subelement
from . import format as _fmt
from .errors import ChartDataError, FormatError


class _Keep:
    def __repr__(self) -> str:
        return "KEEP"


#: "Leave this as it is" -- the default of every formatting argument.
KEEP: Any = _Keep()

C15_NS = "http://schemas.microsoft.com/office/drawing/2012/chart"
#: The ``c:ext`` a series' Value From Cells range is in, and the one a label's flag is in.
LABEL_RANGE_EXT = "{02D57815-91ED-43cb-92C2-25804820EDAC}"
LABEL_FLAGS_EXT = "{CE6537A1-D6FC-4f65-9D91-7224C49458BB}"

#: Axis names a caller may give: the roles, or the directions on the page.
AXIS_NAMES = ("category", "value", "x", "y")

#: Plot types whose series are areas (a fill) rather than lines (markers).
_FILLED = {"barChart", "bar3DChart", "pieChart", "pie3DChart", "doughnutChart", "ofPieChart",
           "areaChart", "area3DChart", "bubbleChart"}
_LINED = {"lineChart", "line3DChart", "scatterChart", "radarChart", "stockChart"}

#: The data label positions each plot type takes (ST_DLblPos; PowerPoint repairs others).
_POSITIONS = {
    "bar_clustered": {"ctr", "inEnd", "inBase", "outEnd"},
    "bar_stacked": {"ctr", "inEnd", "inBase"},
    "lineChart": {"ctr", "l", "r", "t", "b"},
    "scatterChart": {"ctr", "l", "r", "t", "b"},
    "bubbleChart": {"ctr", "l", "r", "t", "b"},
    "pieChart": {"ctr", "inEnd", "outEnd", "bestFit"},
}


def _finite(value: Any, what: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)):
        raise FormatError(f"{what}: {value!r} is not a finite number")
    if positive and value <= 0:
        raise FormatError(f"{what} must be greater than 0")
    return value


def _number_text(value: float) -> str:
    from .workbook import number_text

    return number_text(value)


def _plot_kind(ser: Element) -> str:
    plot = ser.getparent()
    return local_name(plot) if plot is not None else ""


def _is_filled(plot: Element) -> bool:
    name = local_name(plot)
    if name == "radarChart":
        style = plot.find(qn("c:radarStyle"))
        return style is not None and style.get("val") == "filled"
    return name in _FILLED


def _guid(seed: str) -> str:
    digest = hashlib.sha256(seed.encode()).hexdigest().upper()
    return f"{{{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}}}"


class ChartStyling:
    """The formatting half of :class:`~.chart.Chart` (see the module)."""

    # -- axes --------------------------------------------------------------------------------

    def _axis_node(self, root: Element, axis: str) -> Element:
        """The primary axis ``axis`` names: ``category``/``value`` by role, ``x``/``y`` by
        direction (the horizontal and the vertical one)."""
        from .chart import AXIS_TAGS, _axis_of

        if axis not in AXIS_NAMES:
            raise FormatError(f"axis {axis!r}; one of {', '.join(AXIS_NAMES)}")
        plot_area = find(root, "c:chart/c:plotArea")
        axes = [] if plot_area is None else [a for a in plot_area
                                             if a.tag in {qn(t) for t in AXIS_TAGS}]
        node = None
        if axis in ("category", "value"):
            node = _axis_of(root, axis)
        else:
            primary = self._primary_axes(root)
            wanted = {"b", "t"} if axis == "x" else {"l", "r"}
            node = next((a for a in primary if _ax_pos(a) in wanted), None)
        if node is None:
            have = [name for name in AXIS_NAMES if self._has_axis(root, name, axes)]
            raise FormatError(f"{self.address}: the chart has no {axis} axis"
                              + (f"; it has {', '.join(have)}" if have else " (a pie has none)"))
        return node

    def _has_axis(self, root: Element, name: str, axes: list) -> bool:
        if not axes:
            return False
        try:
            from .chart import _axis_of

            if name in ("category", "value"):
                return _axis_of(root, name) is not None
            wanted = {"b", "t"} if name == "x" else {"l", "r"}
            return any(_ax_pos(a) in wanted for a in self._primary_axes(root))
        except ValueError:
            return False

    @staticmethod
    def _primary_axes(root: Element) -> list[Element]:
        """The axes the first plot uses."""
        from .chart import AXIS_TAGS, PLOT_TAGS

        plot_area = find(root, "c:chart/c:plotArea")
        if plot_area is None:
            return []
        plot = next((c for c in plot_area if c.tag in {qn(t) for t in PLOT_TAGS}), None)
        ids = [] if plot is None else [n.get("val") for n in plot.findall(qn("c:axId"))]
        axes = [a for a in plot_area if a.tag in {qn(t) for t in AXIS_TAGS}]
        return [a for a in axes if _ax_id(a) in ids]

    @staticmethod
    def _crossing(root: Element, axis: Element) -> Element | None:
        """The axis ``axis`` crosses (its ``c:crossAx``)."""
        node = axis.find(qn("c:crossAx"))
        if node is None:
            return None
        from .chart import AXIS_TAGS

        plot_area = find(root, "c:chart/c:plotArea")
        return next((a for a in plot_area if a.tag in {qn(t) for t in AXIS_TAGS}
                     and _ax_id(a) == node.get("val")), None)

    def axis_names(self) -> list[str]:
        """The names the chart's axes go by here: ``["category", "value"]``, ``["x", "y"]``
        for a scatter or bubble chart, none for a pie::

            chart.axis_names()              # ['category', 'value']
        """
        root = self._root()
        names = []
        for name in ("category", "value") if not self._xy(root) else ("x", "y"):
            try:
                self._axis_node(root, name)
                names.append(name)
            except FormatError:
                pass
        return names

    @staticmethod
    def _xy(root: Element) -> bool:
        plot_area = find(root, "c:chart/c:plotArea")
        return plot_area is not None and not plot_area.findall(qn("c:catAx")) \
            and not plot_area.findall(qn("c:dateAx")) and len(plot_area.findall(qn("c:valAx"))) >= 2

    def axis_scale(self, axis: str) -> dict[str, Any]:
        """How an axis is scaled: ``{"minimum", "maximum", "major_unit", "minor_unit",
        "number_format", "reverse", "major_gridlines", "minor_gridlines"}``.  A bound or
        unit that is ``None`` is automatic; ``number_format`` ``None`` follows the
        values'.  A category axis has no bounds or units::

            chart.axis_scale("value")["maximum"]        # 5, or None: automatic
        """
        node = self._axis_node(self._root(), axis)
        scaling = node.find(qn("c:scaling"))

        def number(parent, tag):
            child = None if parent is None else parent.find(qn(tag))
            if child is None:
                return None
            from .chart import decode_number

            return decode_number(child.get("val") or "")

        orientation = None if scaling is None else scaling.find(qn("c:orientation"))
        fmt = node.find(qn("c:numFmt"))
        out = {"minimum": number(scaling, "c:min"), "maximum": number(scaling, "c:max"),
               "major_unit": number(node, "c:majorUnit"), "minor_unit": number(node, "c:minorUnit"),
               "number_format": None if fmt is None or fmt.get("sourceLinked") in ("1", "true")
               else fmt.get("formatCode"),
               "reverse": orientation is not None and orientation.get("val") == "maxMin",
               "major_gridlines": node.find(qn("c:majorGridlines")) is not None,
               "minor_gridlines": node.find(qn("c:minorGridlines")) is not None}
        if local_name(node) == "catAx":
            for key in ("minimum", "maximum", "major_unit", "minor_unit"):
                del out[key]
        return out

    @property
    def scales(self) -> dict[str, dict[str, Any]]:
        """:meth:`axis_scale` of every axis, by name (``category``/``value`` or ``x``/``y``)::

            chart.scales["value"]["major_unit"]          # 1
        """
        return {name: self.axis_scale(name) for name in self.axis_names()}

    def set_axis_scale(self, axis: str, *, minimum: Any = KEEP, maximum: Any = KEEP,
                       major_unit: Any = KEEP, minor_unit: Any = KEEP,
                       number_format: Any = KEEP, reverse: Any = KEEP):
        """Fix an axis' scale: ``minimum``, ``maximum``, ``major_unit``, ``minor_unit``
        (``None``: automatic again), ``number_format`` (an Excel code; ``None`` follows the
        values') and ``reverse``.  ``axis`` is ``"value"`` or ``"category"``, or ``"x"`` and
        ``"y"`` (the horizontal and vertical axis; a scatter or bubble chart's).

        ``reverse=True`` turns the axis around as PowerPoint's "Categories in reverse order"
        does -- the first category at the top of a bar chart -- and keeps the other axis
        where it was by crossing it at the maximum (``c:crosses val="max"``), as PowerPoint
        must be told to (measured: without it the value axis moves to the top).  A category
        axis has no numeric bounds or units::

            chart.set_axis_scale("value", minimum=0, maximum=5, major_unit=1)
            chart.set_axis_scale("category", reverse=True)     # a ranked bar chart, top-down
        """
        root = self._root()
        node = self._axis_node(root, axis)
        numeric = {"minimum": minimum, "maximum": maximum, "major_unit": major_unit,
                   "minor_unit": minor_unit}
        given = {k: v for k, v in numeric.items() if v is not KEEP}
        if local_name(node) == "catAx" and any(v is not None for v in given.values()):
            raise FormatError(f"{self.address}: the {axis} axis shows categories, which have "
                              f"no numeric bounds or units; give them for the value axis")
        for key, value in given.items():
            if value is not None:
                _finite(value, key.replace("_", " "), positive=key.endswith("unit"))
        current = self.axis_scale(axis) if local_name(node) != "catAx" else {}
        low = given.get("minimum", current.get("minimum"))
        high = given.get("maximum", current.get("maximum"))
        if low is not None and high is not None and low >= high:
            raise FormatError(f"{self.address}: the minimum ({low}) must be below the "
                              f"maximum ({high})")
        major = given.get("major_unit", current.get("major_unit"))
        minor = given.get("minor_unit", current.get("minor_unit"))
        if major is not None and minor is not None and minor > major:
            raise FormatError(f"{self.address}: the minor unit ({minor}) is larger than the "
                              f"major unit ({major})")
        if number_format is not KEEP and number_format is not None and (
                not isinstance(number_format, str) or not number_format.strip()
                or len(number_format) > 255):
            raise FormatError(f"{self.address}: a number format is an Excel format code, such "
                              f"as 0.0")
        if reverse is not KEEP and not isinstance(reverse, bool):
            raise FormatError("reverse is true or false")
        with self._edit() as editor:
            node = self._axis_node(editor.root, axis)
            scaling = subelement(node, "c:scaling")
            if scaling.find(qn("c:orientation")) is None:
                append_in_order(scaling, make("c:orientation", val="minMax"))
            for key, tag in (("maximum", "c:max"), ("minimum", "c:min")):
                if key in given:
                    _set_value(scaling, tag, given[key])
            for key, tag in (("major_unit", "c:majorUnit"), ("minor_unit", "c:minorUnit")):
                if key in given:
                    _set_value(node, tag, given[key])
            if number_format is not KEEP:
                fmt = node.find(qn("c:numFmt"))
                if fmt is None:
                    fmt = append_in_order(node, make("c:numFmt"))
                if number_format is None:
                    fmt.set("formatCode", self.number_format or "General")
                    fmt.set("sourceLinked", "1")
                else:
                    fmt.set("formatCode", number_format)
                    fmt.set("sourceLinked", "0")
            if reverse is not KEEP:
                scaling.find(qn("c:orientation")).set("val", "maxMin" if reverse else "minMax")
                other = self._crossing(editor.root, node)
                if other is not None:
                    _set_crosses(other, "max" if reverse else "autoZero")
            editor.changed = True
        return self

    def set_gridlines(self, axis: str, *, major: Any = KEEP, minor: Any = KEEP):
        """Show or hide an axis' major and minor gridlines (Office's: 0.75 pt at 15% and 5%
        of the text colour, measured)::

            chart.set_gridlines("value", major=False)          # a clean chart with labels
        """
        for name, value in (("major", major), ("minor", minor)):
            if value is not KEEP and not isinstance(value, bool):
                raise FormatError(f"{name} is true or false")
        with self._edit() as editor:
            node = self._axis_node(editor.root, axis)
            for value, tag, level in ((major, "c:majorGridlines", 15000),
                                      (minor, "c:minorGridlines", 5000)):
                if value is KEEP:
                    continue
                present = node.find(qn(tag))
                if value and present is None:
                    append_in_order(node, _fmt.gridline(level))
                elif not value and present is not None:
                    remove(present)
            editor.changed = True
        return self

    # -- series and points -------------------------------------------------------------------

    def _ser(self, root: Element, which) -> Element:
        from .chart import Series

        series = self._series(root)
        if isinstance(which, Series):
            position = which.index
        else:
            position = self._series_position(which, series)
        return series[position].element

    def _point(self, root: Element, ser: Element, which) -> int:
        from .chart import _Series

        data = _Series(ser)
        count = max([s.count for s in data.point_sources] or [0])
        if isinstance(which, bool):
            raise FormatError("a point is a category label or a position from 0")
        if isinstance(which, int):
            if not -count <= which < count:
                raise IndexError(f"{self.address}: no point {which} (have {count})")
            return which % count
        labels = data.categories.points()
        hits = [k for k, label in enumerate(labels) if label == which
                or (isinstance(label, (int, float)) and str(label) == str(which))]
        if len(hits) != 1:
            text = [str(x) for x in labels[:50]]
            if not hits and isinstance(which, str) and which.isdigit() and int(which) < count:
                return int(which)
            raise KeyError(f"{self.address}: {'no' if not hits else 'more than one'} category "
                           f"{which!r}; the categories are {text}")
        return hits[0]

    def format_series(self, series, *, fill: Any = KEEP, line: Any = KEEP,
                      line_width: Any = KEEP, dash: Any = KEEP, marker: Any = KEEP,
                      marker_size: Any = KEEP, marker_fill: Any = KEEP, marker_line: Any = KEEP):
        """A series' colours and lines -- by name, position or :class:`Series`:

        * ``fill``: a bar's, slice's, area's or bubble's colour (``None``: no fill);
        * ``line``: the line of a line, scatter or radar series, the outline of the others
          (``None``: none); ``line_width`` in points; ``dash`` (``solid``, ``dash``,
          ``dot``, ``dash_dot``, ``long_dash``...);
        * ``marker`` (``none``, ``circle``, ``diamond``, ``square``, ``triangle``, ``dash``,
          ``dot``, ``star``, ``x``, ``plus``), ``marker_size`` 2-72, ``marker_fill`` and
          ``marker_line`` (default: the fill's colour) -- line, scatter and radar series.

        Colours are theme names with modifiers or hex (``"tx2"``, ``"accent1 lumMod=60%
        lumOff=40%"``, ``"1F4E79"``)::

            chart.format_series("Current", fill="tx2")
            chart.format_series("Target", line=None, marker="diamond", marker_size=10,
                                marker_fill="accent6")
        """
        return self._format(series, None, fill=fill, line=line, line_width=line_width,
                            dash=dash, marker=marker, marker_size=marker_size,
                            marker_fill=marker_fill, marker_line=marker_line)

    def format_point(self, series, category, *, fill: Any = KEEP, line: Any = KEEP,
                     line_width: Any = KEEP, dash: Any = KEEP, marker: Any = KEEP,
                     marker_size: Any = KEEP, marker_fill: Any = KEEP, marker_line: Any = KEEP):
        """One point's look (a ``c:dPt``), as :meth:`format_series` takes it; ``category``
        is a label or a position from 0.  The point starts from its series' look::

            chart.format_point("Current", "Procure", fill="accent2")   # highlight one bar
        """
        return self._format(series, category, fill=fill, line=line, line_width=line_width,
                            dash=dash, marker=marker, marker_size=marker_size,
                            marker_fill=marker_fill, marker_line=marker_line)

    def _format(self, series, category, **wanted) -> Any:
        given = {k: v for k, v in wanted.items() if v is not KEEP}
        if not given:
            raise FormatError("give something to change: fill, line, line_width, dash, marker, "
                              "marker_size, marker_fill or marker_line")
        root = self._root()
        ser = self._ser(root, series)
        plot = ser.getparent()
        filled = _is_filled(plot)
        if "fill" in given and not filled:
            raise FormatError(f"{self.address}: a {local_name(plot)[:-5]} series is a line; "
                              f"colour it with line, or its markers with marker_fill")
        markers = {k for k in given if k.startswith("marker")}
        if markers and (filled or local_name(plot) not in _LINED):
            raise FormatError(f"{self.address}: only line, scatter and radar series have "
                              f"markers")
        for key in ("fill", "line", "marker_fill", "marker_line"):
            if given.get(key) is not None and key in given:
                _fmt.color_element(given[key])  # refused before anything changes
        if "line_width" in given:
            _fmt.points_to_emu(given["line_width"])
        if "dash" in given:
            _fmt.dash_value(given["dash"])
        if category is not None:
            point = self._point(root, ser, category)
        with self._edit() as editor:
            ser = self._ser(editor.root, series)
            owner = ser if category is None else _data_point(ser, point)
            shape = owner.find(qn("c:spPr"))
            if shape is None:
                shape = append_in_order(owner, make("c:spPr"))
            if "fill" in given:
                _fmt.set_fill(shape, given["fill"])
            if {"line", "line_width", "dash"} & set(given):
                _fmt.set_line(shape, given.get("line"), width=given.get("line_width"),
                              dash=given.get("dash"), keep_color="line" not in given)
            if shape.find(qn("a:effectLst")) is None:
                _fmt.place(shape, make("a:effectLst"), _fmt._SHAPE_ORDER)
            if markers:
                fill = given.get("marker_fill", KEEP)
                line = given.get("marker_line", KEEP)
                if line is KEEP and fill is not KEEP:
                    line = fill  # Office outlines a marker in its fill colour
                _fmt.set_marker(owner, given.get("marker", KEEP), given.get("marker_size", KEEP),
                                fill, line, keep=KEEP)
            editor.changed = True
        return self

    def series_format(self, series) -> dict[str, Any]:
        """A series' own look, as :meth:`format_series` takes it, with ``points`` --
        ``{position: look}`` -- for points formatted on their own.  What the series does
        not set (the application's automatic colour) is absent::

            chart.series_format("Current")  # {'fill': 'tx2', 'points': {5: {'fill': 'accent2'}}}
        """
        ser = self._ser(self._root(), series)
        out = _look(ser)
        points = {}
        for dpt in ser.findall(qn("c:dPt")):
            idx = dpt.find(qn("c:idx"))
            if idx is not None and (idx.get("val") or "").isdigit():
                points[int(idx.get("val"))] = _look(dpt)
        if points:
            out["points"] = points
        return out

    # -- combination charts ------------------------------------------------------------------

    def set_series_type(self, series, kind: str, *, secondary_axis: bool = False):
        """Draw a series of a column chart as a ``"line"`` -- a combination chart, the
        target drawn over the actual -- or back as a ``"column"``.  The series moves into a
        line plot on the same axes (``secondary_axis=True``: on a second value axis at the
        right, as PowerPoint's "Clustered Column - Line on Secondary Axis" builds it,
        measured); its colour, data, workbook cells and position in the legend stay.
        PowerPoint cannot combine horizontal bars with a line, so a bar chart is refused::

            chart.set_series_type("Target", "line")
            chart.format_series("Target", line=None, marker="diamond", marker_size=10)
        """

        if kind not in ("line", "column"):
            raise ChartDataError(f"{self.address}: a series type is 'line' or 'column'")
        if not isinstance(secondary_axis, bool):
            raise ChartDataError("secondary_axis is true or false")
        root = self._root()
        types = self.chart_types
        bars = find(root, "c:chart/c:plotArea/c:barChart")
        if bars is not None and (bars.find(qn("c:barDir")) is not None
                                 and bars.find(qn("c:barDir")).get("val") == "bar"):
            raise ChartDataError(f"{self.address}: PowerPoint cannot combine horizontal bars "
                                 f"with a line; make it a column chart")
        if not set(types) <= {"bar", "line"} or "bar" not in types and kind == "line":
            raise ChartDataError(f"{self.address}: only a column chart takes a line series "
                                 f"(this one is {'/'.join(types) or 'empty'})")
        ser = self._ser(root, series)
        current = "line" if local_name(ser.getparent()) == "lineChart" else "column"
        on_secondary = self._on_secondary(root, ser.getparent())
        if current == kind and on_secondary == secondary_axis:
            return self
        if kind == "column" and secondary_axis:
            raise ChartDataError(f"{self.address}: columns stay on the primary axis")
        with self._edit() as editor:
            root = editor.root
            ser = self._ser(root, series)
            source = ser.getparent()
            plot_area = find(root, "c:chart/c:plotArea")
            if kind == "line":
                target = self._line_plot(root, plot_area, secondary_axis)
                _to_line(ser)
            else:
                target = find(root, "c:chart/c:plotArea/c:barChart")
                if target is None:
                    raise ChartDataError(f"{self.address}: the chart has no column plot to "
                                         f"move the series into")
                _to_column(ser)
            _insert_series(target, ser)
            if not source.findall(qn("c:ser")):
                self._drop_plot(root, plot_area, source)
            editor.changed = True
        return self

    def _on_secondary(self, root: Element, plot: Element) -> bool:
        primary = {_ax_id(a) for a in self._primary_axes(root)}
        ids = {n.get("val") for n in plot.findall(qn("c:axId"))}
        return bool(ids) and not ids <= primary

    def _line_plot(self, root: Element, plot_area: Element, secondary: bool) -> Element:
        """The line plot on the primary or secondary axes, created when missing."""
        primary = self._primary_axes(root)
        primary_ids = [_ax_id(a) for a in primary]
        for plot in plot_area.findall(qn("c:lineChart")):
            if self._on_secondary(root, plot) == secondary:
                return plot
        if secondary:
            ids = self._secondary_axes(root, plot_area, primary)
        else:
            bars = plot_area.find(qn("c:barChart"))
            ids = [n.get("val") for n in bars.findall(qn("c:axId"))] if bars is not None \
                else primary_ids
        plot = make("c:lineChart")
        plot.append(make("c:grouping", val="standard"))
        plot.append(make("c:varyColors", val="0"))
        labels = make("c:dLbls")
        for flag in ("showLegendKey", "showVal", "showCatName", "showSerName", "showPercent",
                     "showBubbleSize"):
            labels.append(make(f"c:{flag}", val="0"))
        plot.append(labels)
        plot.append(make("c:marker", val="1"))
        plot.append(make("c:smooth", val="0"))
        for axis_id in ids:
            plot.append(make("c:axId", val=axis_id))
        anchor = plot_area.findall(qn("c:barChart"))
        if not secondary and anchor:
            anchor[-1].addnext(plot)
        else:
            plots = [c for c in plot_area if local_name(c).endswith("Chart")]
            plots[-1].addnext(plot)
        return plot

    def _secondary_axes(self, root: Element, plot_area: Element, primary: list) -> list[str]:
        """A secondary value axis at the right and its hidden category axis, as PowerPoint
        writes them (measured); returns their ids, category first."""
        taken = {n.get("val") for n in root.iter(qn("c:axId"))}
        seed = hashlib.sha256("\n".join(sorted(taken)).encode()).hexdigest()
        cat_id, val_id = str(int(seed[:7], 16) + 1), str(int(seed[7:14], 16) + 1)
        values = next((a for a in primary if local_name(a) == "valAx"), None)
        value = make("c:valAx")
        value.append(make("c:axId", val=val_id))
        scaling = make("c:scaling")
        scaling.append(make("c:orientation", val="minMax"))
        value.append(scaling)
        value.append(make("c:delete", val="0"))
        value.append(make("c:axPos", val="r"))
        value.append(make("c:numFmt", formatCode=self.number_format or "General",
                          sourceLinked="1"))
        value.append(make("c:majorTickMark", val="none"))
        value.append(make("c:minorTickMark", val="none"))
        value.append(make("c:tickLblPos", val="nextTo"))
        shape = make("c:spPr")
        shape.append(make("a:noFill"))
        line = make("a:ln")
        line.append(make("a:noFill"))
        shape.append(line)
        shape.append(make("a:effectLst"))
        value.append(shape)
        text = None if values is None else values.find(qn("c:txPr"))
        if text is not None:
            value.append(copy.deepcopy(text))
        value.append(make("c:crossAx", val=cat_id))
        value.append(make("c:crosses", val="max"))
        value.append(make("c:crossBetween", val="between"))
        category = make("c:catAx")
        category.append(make("c:axId", val=cat_id))
        scaling = make("c:scaling")
        scaling.append(make("c:orientation", val="minMax"))
        category.append(scaling)
        category.append(make("c:delete", val="1"))
        category.append(make("c:axPos", val="b"))
        category.append(make("c:numFmt", formatCode="General", sourceLinked="1"))
        category.append(make("c:majorTickMark", val="none"))
        category.append(make("c:minorTickMark", val="none"))
        category.append(make("c:tickLblPos", val="nextTo"))
        category.append(make("c:crossAx", val=val_id))
        for flag, val in (("auto", "1"), ("lblAlgn", "ctr"), ("lblOffset", "100"),
                          ("noMultiLvlLbl", "0")):
            category.append(make(f"c:{flag}", val=val))
        last = primary[-1] if primary else None
        if last is not None:
            last.addnext(value)
            value.addnext(category)
        else:
            append_in_order(plot_area, value)
            append_in_order(plot_area, category)
        return [cat_id, val_id]

    def _drop_plot(self, root: Element, plot_area: Element, plot: Element) -> None:
        """Remove an emptied plot -- and its axes when no other plot uses them."""
        ids = {n.get("val") for n in plot.findall(qn("c:axId"))}
        remove(plot)
        used = {n.get("val") for other in plot_area if local_name(other).endswith("Chart")
                for n in other.findall(qn("c:axId"))}
        for axis in list(plot_area):
            if _ax_id(axis) in ids - used:
                remove(axis)

    # -- text --------------------------------------------------------------------------------

    def set_text_style(self, part: str | None = None, *, font: Any = KEEP, size: Any = KEEP,
                       bold: Any = KEEP, color: Any = KEEP):
        """The chart's text, or one part of it: ``"title"``, ``"axis_titles"``,
        ``"axis_labels"``, ``"data_labels"`` or ``"legend"`` (``None``: all of it, and the
        chart's default, so text shown later follows).  ``size`` in points, ``font`` a
        typeface (``"+mn-lt"`` the theme's body font), ``color`` a colour::

            chart.set_text_style(size=10)                       # small text for a dense page
            chart.set_text_style("title", size=14, bold=True)

        Data labels shown afterwards take their own size again, so show them first.
        """
        if part is not None and part not in _fmt.TEXT_PARTS:
            raise FormatError(f"part {part!r}; one of {', '.join(_fmt.TEXT_PARTS)} or None")
        if all(v is KEEP for v in (font, size, bold, color)):
            raise FormatError("give font, size, bold or color")
        if size is not KEEP:
            _fmt.text_size(size)
        if color is not KEEP:
            _fmt.color_element(color)
        if bold is not KEEP and not isinstance(bold, bool):
            raise FormatError("bold is true or false")
        with self._edit() as editor:
            holders = self._text_holders(editor, part)
            if not holders:
                raise FormatError(f"{self.address}: the chart shows no {part.replace('_', ' ')}")
            for properties in holders:
                _fmt.style_characters(properties, font=font, size=size, bold=bold, color=color,
                                      keep=KEEP)
            editor.changed = True
        return self

    def text_style(self, part: str | None = None) -> dict[str, Any]:
        """``{"font", "size", "bold", "color"}`` a part's text is given (the first holder's;
        absent: inherited from the application)::

            chart.text_style("legend")      # {'size': 10.0, ...}
        """
        root = self._root()
        for properties in self._text_holders(None, part, root=root, create=False):
            return _fmt.text_style_of(properties)
        return {}

    def _text_holders(self, editor, part: str | None, *, root: Element | None = None,
                      create: bool = True) -> list[Element]:
        from .chart import AXIS_TAGS
        from .create import text_properties

        root = editor.root if editor is not None else root
        look = editor.host.look if editor is not None else None
        lang = editor.host.lang if editor is not None else None
        chart = find(root, "c:chart")
        plot_area = find(root, "c:chart/c:plotArea")
        axes = [] if plot_area is None else [a for a in plot_area
                                             if a.tag in {qn(t) for t in AXIS_TAGS}]
        holders: list[Element] = []

        def title_props(title: Element | None) -> None:
            if title is None:
                return
            rich = find(title, "c:tx/c:rich")
            if rich is not None:
                holders.extend(_fmt.rich_character_properties(rich))
            text = title.find(qn("c:txPr"))
            if text is not None:
                holders.append(_fmt.defaults_of(text))

        def txpr(owner: Element, size: int | None) -> None:
            text = owner.find(qn("c:txPr"))
            if text is None:
                if not create:
                    return
                text = append_in_order(owner, text_properties(size or 1000, lang=lang))
                if size is None:
                    del _fmt.defaults_of(text).attrib["sz"]
            holders.append(_fmt.defaults_of(text))

        if part in (None, "title"):
            title_props(None if chart is None else chart.find(qn("c:title")))
        if part in (None, "axis_titles"):
            for axis in axes:
                title_props(axis.find(qn("c:title")))
        if part in (None, "axis_labels"):
            for axis in axes:
                deleted = axis.find(qn("c:delete"))
                if deleted is None or deleted.get("val") not in ("1", "true"):
                    txpr(axis, look.labels if look is not None else None)
        if part in (None, "data_labels"):
            for labels in root.iter(qn("c:dLbls")):
                if _deleted(labels):
                    continue
                txpr(labels, look.data_labels if look is not None else None)
                for label in labels.findall(qn("c:dLbl")):
                    if _deleted(label):
                        continue
                    rich = find(label, "c:tx/c:rich")
                    if rich is not None:
                        holders.extend(_fmt.rich_character_properties(rich))
                    elif label.find(qn("c:txPr")) is not None:
                        holders.append(_fmt.defaults_of(label.find(qn("c:txPr"))))
        if part in (None, "legend"):
            legend = None if chart is None else chart.find(qn("c:legend"))
            if legend is not None:
                txpr(legend, look.labels if look is not None else None)
        if part is None:
            space = root
            text = space.find(qn("c:txPr"))
            if text is None and create:
                text = make("c:txPr")
                text.append(make("a:bodyPr"))
                text.append(make("a:lstStyle"))
                text.append(make("a:p"))
                after = next((space.find(qn(t)) for t in ("c:externalData", "c:printSettings",
                                                          "c:userShapes", "c:extLst")
                              if space.find(qn(t)) is not None), None)
                if after is not None:
                    after.addprevious(text)
                else:
                    space.append(text)
            if text is not None:
                holders.append(_fmt.defaults_of(text))
        return holders

    # -- point labels ------------------------------------------------------------------------

    def set_point_labels(self, series, labels: "Sequence[str | None] | None", *,
                         position: str | None = None, show_value: bool = False,
                         cells: bool = False):
        """Label each point of a series with its own text -- ``labels``, one per point
        (``None``: that point keeps the series' label) -- such as R1..R6 on an
        effort-against-impact matrix.  Each becomes the point's ``c:dLbl`` with that text;
        ``show_value`` adds the value after it.  ``position`` is ``center``, ``inside_end``,
        ``inside_base``, ``outside_end`` (bars, pie), ``left``, ``right``, ``top``,
        ``bottom`` (lines, scatter, bubble) or ``best_fit`` (pie); ``None`` the default.

        ``cells=True`` also writes the labels into the embedded workbook, in the column
        after the data, and links them as PowerPoint's "Value From Cells" does
        (``c15:datalabelsRange``, the labels as ``CELLRANGE`` fields, measured): Edit Data
        shows and changes them.  ``labels=None`` removes the series' point labels::

            chart.set_point_labels("Recommendations", ["R1", "R2", "R3"], position="right")
        """
        from .chart import _Series

        root = self._root()
        ser = self._ser(root, series)
        data = _Series(ser)
        count = max([s.count for s in data.point_sources] or [0])
        if labels is not None:
            labels = list(labels)
            if len(labels) != count:
                raise ChartDataError(f"{self.address}: {len(labels)} labels for {count} points")
            for text in labels:
                if text is not None and (not isinstance(text, str) or len(text) > 255):
                    raise ChartDataError("a point label is text of at most 255 characters")
        code = None
        if position is not None:
            code = _fmt.LABEL_POSITIONS.get(position)
            allowed = _positions(ser.getparent())
            if code is None or code not in allowed:
                names = [k for k, v in _fmt.LABEL_POSITIONS.items() if v in allowed]
                raise ChartDataError(f"{self.address}: label position {position!r}; this chart "
                                     f"takes {', '.join(names) or 'none (leave it out)'}")
        if not isinstance(show_value, bool) or not isinstance(cells, bool):
            raise ChartDataError("show_value and cells are true or false")
        with self._edit() as editor:
            ser = self._ser(editor.root, series)
            existing = ser.find(qn("c:dLbls"))
            if existing is not None:
                for label in existing.findall(qn("c:dLbl")):
                    remove(label)
            _drop_label_range(ser)
            if labels is None:
                if existing is not None and not _shows_anything(existing):
                    remove(existing)
                editor.changed = True
                return self
            holder = existing
            if holder is None or _deleted(holder):
                if holder is not None:
                    remove(holder)
                from .chart import _label_size, _value_labels

                look = editor.host.look
                size = look.data_labels if look is not None else _label_size(editor.root)
                holder = _value_labels(size, None, pie=local_name(ser.getparent()) == "pieChart",
                                       lang=editor.host.lang)
                holder.find(qn("c:showVal")).set("val", "0")
                append_in_order(ser, holder)
            if code is not None:
                _set_label_position(holder, code)
            if cells:
                self._label_cells(editor, ser, labels)
                _set_range_flag(holder, True)
            body, defaults = _label_text_style(holder)
            plot = ser.getparent()
            value_field = "YVALUE" if local_name(plot) in ("scatterChart", "bubbleChart") \
                else "VALUE"
            values = _Series(ser).values.points()
            fmt = holder.find(qn("c:numFmt"))
            for index, text in enumerate(labels):
                if text is None:
                    continue
                label = make("c:dLbl")
                label.append(make("c:idx", val=str(index)))
                paragraph = make("a:p")
                properties = make("a:pPr")
                properties.append(copy.deepcopy(defaults))
                paragraph.append(properties)
                seed = f"{self.part}\n{_ser_idx(ser)}\n{index}"
                if cells:
                    paragraph.append(_field("CELLRANGE", "[CELLRANGE]", _guid(seed + "\nc"),
                                            editor.host.lang))
                else:
                    paragraph.append(_run(text, editor.host.lang))
                if show_value:
                    paragraph.append(_run(", ", editor.host.lang))
                    shown = values[index] if index < len(values) else None
                    paragraph.append(_field(value_field, "" if shown is None
                                            else _number_text(shown), _guid(seed + "\nv"),
                                            editor.host.lang))
                end = make("a:endParaRPr")
                if editor.host.lang:
                    end.set("lang", editor.host.lang)
                paragraph.append(end)
                rich = make("c:rich")
                rich.append(copy.deepcopy(body))
                rich.append(make("a:lstStyle"))
                rich.append(paragraph)
                tx = make("c:tx")
                tx.append(rich)
                label.append(tx)
                if fmt is not None:
                    label.append(copy.deepcopy(fmt))
                shape = holder.find(qn("c:spPr"))
                if shape is not None:
                    label.append(copy.deepcopy(shape))
                if code is not None:
                    label.append(make("c:dLblPos", val=code))
                for flag, on in (("showLegendKey", False), ("showVal", show_value or not cells),
                                 ("showCatName", False), ("showSerName", False),
                                 ("showPercent", False), ("showBubbleSize", False)):
                    label.append(make(f"c:{flag}", val="1" if on else "0"))
                if cells:
                    extensions = make("c:extLst")
                    ext = etree.SubElement(extensions, qn("c:ext"), nsmap={"c15": C15_NS})
                    ext.set("uri", LABEL_FLAGS_EXT)
                    etree.SubElement(ext, f"{{{C15_NS}}}dlblFieldTable")
                    etree.SubElement(ext, f"{{{C15_NS}}}xForSave").set("val", "1")
                    etree.SubElement(ext, f"{{{C15_NS}}}showDataLabelsRange").set("val", "1")
                    label.append(extensions)
                _insert_label(holder, label)
            editor.changed = True
        return self

    def point_labels(self, series) -> list:
        """A series' own point labels, one per point (``None`` where a point has none):
        what :meth:`set_point_labels` wrote, a label's text, or its cell's for Value From
        Cells::

            chart.point_labels("Recommendations")       # ['R1', 'R2', ...]
        """
        from .chart import _Series

        ser = self._ser(self._root(), series)
        data = _Series(ser)
        count = max([s.count for s in data.point_sources] or [0])
        out: list = [None] * count
        cached = _label_range_points(ser)
        holder = ser.find(qn("c:dLbls"))
        for label in [] if holder is None else holder.findall(qn("c:dLbl")):
            idx = label.find(qn("c:idx"))
            rich = find(label, "c:tx/c:rich")
            if idx is None or rich is None or not (idx.get("val") or "").isdigit():
                continue
            index = int(idx.get("val"))
            if index >= count:
                continue
            parts = []
            for node in rich.iter(qn("a:r"), qn("a:fld")):
                if local_name(node) == "fld":
                    if node.get("type") == "CELLRANGE":
                        parts.append(cached[index] if index < len(cached) and cached[index]
                                     is not None else "")
                    break  # a value field and what follows it are not the label
                text = node.find(qn("a:t"))
                parts.append("" if text is None or text.text is None else text.text)
            text = "".join(parts)
            out[index] = text[:-2] if text.endswith(", ") else text
        return out

    def _label_cells(self, editor, ser: Element, labels: list) -> int | None:
        """Write a series' labels into the workbook column after the chart's data (or the
        column it already has) and link them (``c15:datalabelsRange``)."""
        from .chart import _line_of

        series = self._series(editor.root)
        count = self.point_count
        block = self._block(editor, series, count)
        if block is None:
            raise ChartDataError(f"{self.address}: Value From Cells needs the chart's embedded "
                                 f"workbook, and {editor.cache_only or 'it has none'}; label "
                                 f"the points without cells=True")
        own = next(s for s in series if s.element is ser)
        lines = self._lines(block, series)
        across = max(a for _, a in lines) + 1
        header = self._header(block, series)
        positions = list(range(block.start, block.start + block.count))
        if header is not None:
            positions.insert(0, header)
        self._check_free(block, series, positions, [across])
        template = own.values.range
        if template is None:
            raise ChartDataError(f"{self.address}: the series' values are not in the workbook")
        sheet = block.sheet
        previous = _line_of(template, block.down)[0]
        for offset, text in enumerate(labels):
            sheet.set_value(block.cell(across, block.start + offset), text)
        if header is not None:
            sheet.set_value(block.cell(across, header), "Labels",
                            style_from=block.cell(previous, header))
        sheet.sync_tables()
        formula = block.line_range(template, across, block.start, block.count).formula
        extensions = ser.find(qn("c:extLst"))
        if extensions is None:
            extensions = append_in_order(ser, make("c:extLst"))
        ext = etree.Element(qn("c:ext"), nsmap={"c15": C15_NS})
        ext.set("uri", LABEL_RANGE_EXT)
        holder = etree.SubElement(ext, f"{{{C15_NS}}}datalabelsRange")
        etree.SubElement(holder, f"{{{C15_NS}}}f").text = formula
        cache = etree.SubElement(holder, f"{{{C15_NS}}}dlblRangeCache")
        cache.append(make("c:ptCount", val=str(len(labels))))
        for index, text in enumerate(labels):
            if text is None:
                continue
            point = make("c:pt", idx=str(index))
            value = make("c:v")
            value.text = text
            point.append(value)
            cache.append(point)
        extensions.insert(0, ext)
        return across


# ------------------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------------------


def _ax_id(axis: Element) -> str | None:
    node = axis.find(qn("c:axId"))
    return None if node is None else node.get("val")


def _ax_pos(axis: Element) -> str | None:
    node = axis.find(qn("c:axPos"))
    return None if node is None else node.get("val")


def _ser_idx(ser: Element) -> str:
    node = ser.find(qn("c:idx"))
    return "" if node is None else node.get("val", "")


def _set_value(parent: Element, tag: str, value) -> None:
    node = parent.find(qn(tag))
    if value is None:
        if node is not None:
            remove(node)
        return
    if node is None:
        node = append_in_order(parent, make(tag))
    node.set("val", _number_text(value))


def _set_crosses(axis: Element, where: str) -> None:
    for tag in ("c:crosses", "c:crossesAt"):
        node = axis.find(qn(tag))
        if node is not None:
            remove(node)
    append_in_order(axis, make("c:crosses", val=where))


def _deleted(holder: Element) -> bool:
    node = holder.find(qn("c:delete"))
    return node is not None and node.get("val", "1") in ("1", "true")


def _shows_anything(holder: Element) -> bool:
    return any((holder.find(qn(f"c:{flag}")) is not None
                and holder.find(qn(f"c:{flag}")).get("val") in ("1", "true"))
               for flag in ("showLegendKey", "showVal", "showCatName", "showSerName",
                            "showPercent", "showBubbleSize"))


def _positions(plot: Element) -> set[str]:
    name = local_name(plot)
    if name == "barChart":
        grouping = plot.find(qn("c:grouping"))
        stacked = grouping is not None and grouping.get("val") in ("stacked", "percentStacked")
        return _POSITIONS["bar_stacked" if stacked else "bar_clustered"]
    return _POSITIONS.get(name, set())


def _set_label_position(holder: Element, code: str) -> None:
    node = holder.find(qn("c:dLblPos"))
    if node is None:
        node = append_in_order(holder, make("c:dLblPos"))
    node.set("val", code)


def _set_range_flag(holder: Element, on: bool) -> None:
    """The series labels' Value From Cells flag (``c15:showDataLabelsRange``)."""
    extensions = holder.find(qn("c:extLst"))
    if extensions is None:
        extensions = append_in_order(holder, make("c:extLst"))
    ext = next((e for e in extensions.findall(qn("c:ext")) if e.get("uri") == LABEL_FLAGS_EXT),
               None)
    if ext is None:
        ext = etree.SubElement(extensions, qn("c:ext"), nsmap={"c15": C15_NS})
        ext.set("uri", LABEL_FLAGS_EXT)
    flag = ext.find(f"{{{C15_NS}}}showDataLabelsRange")
    if flag is None:
        flag = etree.SubElement(ext, f"{{{C15_NS}}}showDataLabelsRange")
    flag.set("val", "1" if on else "0")


def _drop_label_range(ser: Element) -> None:
    extensions = ser.find(qn("c:extLst"))
    if extensions is None:
        return
    for ext in extensions.findall(qn("c:ext")):
        if ext.get("uri") == LABEL_RANGE_EXT:
            remove(ext)
    if not len(extensions):
        remove(extensions)
    holder = ser.find(qn("c:dLbls"))
    extensions = None if holder is None else holder.find(qn("c:extLst"))
    if extensions is not None:
        for ext in extensions.findall(qn("c:ext")):
            if ext.get("uri") == LABEL_FLAGS_EXT:
                remove(ext)
        if not len(extensions):
            remove(extensions)


def _label_range_points(ser: Element) -> list:
    extensions = ser.find(qn("c:extLst"))
    for ext in [] if extensions is None else extensions.findall(qn("c:ext")):
        if ext.get("uri") != LABEL_RANGE_EXT:
            continue
        cache = ext.find(f"{{{C15_NS}}}datalabelsRange/{{{C15_NS}}}dlblRangeCache")
        if cache is None:
            return []
        count = cache.find(qn("c:ptCount"))
        size = int(count.get("val")) if count is not None and (count.get("val") or "").isdigit() \
            else 0
        out: list = [None] * size
        for point in cache.findall(qn("c:pt")):
            idx = point.get("idx", "")
            value = point.find(qn("c:v"))
            if idx.isdigit() and int(idx) < size:
                out[int(idx)] = "" if value is None else (value.text or "")
        return out
    return []


def _label_text_style(holder: Element) -> tuple[Element, Element]:
    """The ``a:bodyPr`` and ``a:defRPr`` a series' labels are drawn with, for a point's own
    text to look the same."""
    text = holder.find(qn("c:txPr"))
    body = None if text is None else text.find(qn("a:bodyPr"))
    defaults = None if text is None else find(text, "a:p/a:pPr/a:defRPr")
    return (copy.deepcopy(body) if body is not None else make("a:bodyPr"),
            copy.deepcopy(defaults) if defaults is not None else make("a:defRPr"))


def _run(text: str, lang: str | None) -> Element:
    run = make("a:r")
    properties = make("a:rPr")
    if lang:
        properties.set("lang", lang)
    run.append(properties)
    node = make("a:t")
    node.text = text
    run.append(node)
    return run


def _field(kind: str, text: str, identifier: str, lang: str | None) -> Element:
    field = make("a:fld", id=identifier, type=kind)
    properties = make("a:rPr")
    if lang:
        properties.set("lang", lang)
    field.append(properties)
    field.append(make("a:pPr"))
    node = make("a:t")
    node.text = text
    field.append(node)
    return field


def _insert_label(holder: Element, label: Element) -> None:
    """A ``c:dLbl`` in index order, before the series-wide settings."""
    index = int(label.find(qn("c:idx")).get("val"))
    for other in holder.findall(qn("c:dLbl")):
        node = other.find(qn("c:idx"))
        if node is not None and (node.get("val") or "").isdigit() and int(node.get("val")) > index:
            other.addprevious(label)
            return
    existing = holder.findall(qn("c:dLbl"))
    if existing:
        existing[-1].addnext(label)
    else:
        holder.insert(0, label)


def _data_point(ser: Element, index: int) -> Element:
    """The series' ``c:dPt`` for point ``index``, created from the series' look."""
    for dpt in ser.findall(qn("c:dPt")):
        node = dpt.find(qn("c:idx"))
        if node is not None and node.get("val") == str(index):
            return dpt
    plot = ser.getparent()
    dpt = make("c:dPt")
    dpt.append(make("c:idx", val=str(index)))
    if local_name(plot) in ("barChart", "bar3DChart", "bubbleChart"):
        dpt.append(make("c:invertIfNegative", val="0"))
    marker = ser.find(qn("c:marker"))
    if marker is not None and local_name(plot) in _LINED:
        dpt.append(copy.deepcopy(marker))
    dpt.append(make("c:bubble3D", val="0"))
    shape = ser.find(qn("c:spPr"))
    if shape is not None:
        dpt.append(copy.deepcopy(shape))
    later = [d for d in ser.findall(qn("c:dPt"))
             if (d.find(qn("c:idx")).get("val") or "0").isdigit()
             and int(d.find(qn("c:idx")).get("val")) > index]
    if later:
        later[0].addprevious(dpt)
    else:
        append_in_order(ser, dpt)
    return dpt


def _look(owner: Element) -> dict[str, Any]:
    out: dict[str, Any] = {}
    shape = owner.find(qn("c:spPr"))
    fill = _fmt.fill_text(shape)
    if fill is not False and fill is not True:
        out["fill"] = fill
    out.update(_fmt.line_text(shape))
    out.update(_fmt.marker_text(owner))
    return out


def _to_line(ser: Element) -> None:
    """A bar series' elements as a line series': its colour becomes the line's."""
    color = None
    shape = ser.find(qn("c:spPr"))
    if shape is not None:
        fill = shape.find(qn("a:solidFill"))
        if fill is not None:
            color = copy.deepcopy(fill)
        remove(shape)
    for tag in ("c:invertIfNegative", "c:pictureOptions", "c:shape", "c:explosion"):
        for node in ser.findall(qn(tag)):
            remove(node)
    for dpt in ser.findall(qn("c:dPt")):
        node = dpt.find(qn("c:invertIfNegative"))
        if node is not None:
            remove(node)
    shape = make("c:spPr")
    line = make("a:ln", w="28575", cap="rnd")
    line.append(color if color is not None else make("a:noFill"))
    line.append(make("a:round"))
    shape.append(line)
    shape.append(make("a:effectLst"))
    append_in_order(ser, shape)
    if ser.find(qn("c:marker")) is None:
        marker = make("c:marker")
        marker.append(make("c:symbol", val="none"))
        append_in_order(ser, marker)
    if ser.find(qn("c:smooth")) is None:
        append_in_order(ser, make("c:smooth", val="0"))


def _to_column(ser: Element) -> None:
    """A line series' elements as a column series': its line colour becomes the fill."""
    color = None
    shape = ser.find(qn("c:spPr"))
    if shape is not None:
        fill = find(shape, "a:ln/a:solidFill")
        if fill is not None:
            color = copy.deepcopy(fill)
        remove(shape)
    for tag in ("c:marker", "c:smooth"):
        for node in ser.findall(qn(tag)):
            remove(node)
    for dpt in ser.findall(qn("c:dPt")):
        for node in dpt.findall(qn("c:marker")):
            remove(node)
    shape = make("c:spPr")
    shape.append(color if color is not None else make("a:noFill"))
    line = make("a:ln")
    line.append(make("a:noFill"))
    shape.append(line)
    shape.append(make("a:effectLst"))
    append_in_order(ser, shape)
    append_in_order(ser, make("c:invertIfNegative", val="0"))


def _insert_series(plot: Element, ser: Element) -> None:
    """``ser`` into ``plot`` in plot order (``c:order``), after the plot's other series."""
    def order(node: Element) -> int:
        value = node.find(qn("c:order"))
        try:
            return int(value.get("val")) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    others = plot.findall(qn("c:ser"))
    later = [s for s in others if order(s) > order(ser)]
    if later:
        later[0].addprevious(ser)
    elif others:
        others[-1].addnext(ser)
    else:
        first = next((c for c in plot if local_name(c) not in ("barDir", "grouping",
                                                               "varyColors", "radarStyle",
                                                               "scatterStyle")), None)
        if first is not None:
            first.addprevious(ser)
        else:
            plot.append(ser)


__all__ = ["AXIS_NAMES", "ChartStyling", "FormatError", "KEEP"]
