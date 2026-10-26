"""The actions of the shared ``edit_chart`` and ``edit_smartart`` tools, on a
:class:`~.chart.Chart` or :class:`~.diagram.Diagram`.

Both format libraries' handlers find the chart or diagram their own way and say what it is
called in a result (a deck's ``address``, a document's ``id``); everything else is here, once
(review, N4 and S10): the series, category or SmartArt node a call names
(:func:`resolve_series`, :func:`resolve_category`, :func:`resolve_node`: a name first), the
data actions (:func:`apply_data_action`), the formatting ones (:func:`apply_style_action`),
``add``'s arguments and look (:func:`add_plan`, :func:`finish_add`), the SmartArt actions
(:func:`apply_smartart_action`) and what each returns (:func:`chart_json`,
:func:`chart_read_json`, :func:`smartart_json`).  Refusals are
:class:`~.errors.ChartDataError` (the handlers answer ``invalid_arguments``), but for a
lookup or a missing argument: the tool layer's errors, with their codes.
"""

from __future__ import annotations

from typing import Any, Mapping

from . import format as _fmt
from .errors import ChartDataError
from .styling import KEEP

#: ``edit_chart``'s formatting actions.
STYLE_ACTIONS = ("set_axis_scale", "set_gridlines", "format_series", "format_point",
                 "set_series_type", "set_text_style", "set_point_labels")

#: ``scale`` keys -> :meth:`~.styling.ChartStyling.set_axis_scale` arguments.
_SCALE = {"min": "minimum", "max": "maximum", "major_unit": "major_unit",
          "minor_unit": "minor_unit", "number_format": "number_format"}
_SERIES_STYLE = ("fill", "line", "line_width", "dash", "marker", "marker_size", "marker_fill")
_TEXT_STYLE = ("font", "size", "bold", "color")
_GRIDLINES = {"none": (False, False), "major": (True, False), "minor": (False, True),
              "both": (True, True)}


def _colour(value: Any) -> Any:
    """``"none"`` is no colour."""
    return None if isinstance(value, str) and value.strip().lower() == "none" else value


def scale_arguments(scale: Mapping[str, Any]) -> dict[str, Any]:
    """``scale`` (``{min, max, major_unit, minor_unit, number_format, reverse, auto}``) as
    :meth:`set_axis_scale` keywords; ``auto`` lists the bounds to make automatic again."""
    unknown = set(scale) - set(_SCALE) - {"reverse", "auto"}
    if unknown:
        raise ChartDataError(f"scale: unknown key {sorted(unknown)[0]!r}")
    out = {_SCALE[k]: v for k, v in scale.items() if k in _SCALE}
    for key in scale.get("auto") or []:
        if key not in _SCALE:
            raise ChartDataError(f"scale.auto: {key!r} is not one of {', '.join(_SCALE)}")
        if key in scale:
            raise ChartDataError(f"scale: {key} is given and listed in auto")
        out[_SCALE[key]] = None
    if "reverse" in scale:
        out["reverse"] = scale["reverse"]
    if not out:
        raise ChartDataError("scale is empty: give min, max, major_unit, minor_unit, "
                             "number_format, reverse or auto")
    return out


def category_labels(chart) -> list[str]:
    """The categories as a model reads and names them: ISO dates for dates kept as serials
    (``chart.category_dates``), else each label as text."""
    dates = chart.category_dates
    if dates is None:
        return [str(c) for c in chart.categories]
    return [str(c) if day is None else day for day, c in zip(dates, chart.categories)]


def choices(names: list, *, limit: int = 12) -> str:
    """What a refusal offers for a series or category: the names, a long list's first
    ``limit`` and how many more, and the positions the tool takes too::

        choices(["North", "South"])     # "'North' or 'South' (or a number from 0 to 1)"
    """
    names = [str(name) for name in names]
    if not names:
        return "the chart has none"
    shown = [repr(name) for name in names[:limit]]
    if len(names) > limit:
        listed = ", ".join(shown) + f" and {len(names) - limit} more"
    else:
        listed = shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " or " + shown[-1]
    return listed + (" (or 0)" if len(names) == 1 else f" (or a number from 0 to {len(names) - 1})")


# -- what edit_chart's series and category, and edit_smartart's node, name (review, N4) -------
#
# Each format's handler finds the series, category or node a call names here, so that a
# chart whose series are called "1" and "2" means the same in a deck and a document.  A
# NAME wins; a number from 0 is a position only when nothing has that name.  The refusals
# are the tool layer's errors (``ToolError``), with the code each case is.


def _loose(text: str) -> str:
    """``text`` compared loosely: whitespace collapsed, full-width forms and case ignored."""
    import unicodedata

    return unicodedata.normalize("NFKC", " ".join(text.split())).casefold()


def _resolve(names: list[str], which: Any, what: str, action: str, hint: str = "") -> int:
    from ..tools.results import ToolError  # here, not at import: charts stand alone

    offered = names[:50]
    if which is None:
        raise ToolError("invalid_arguments", f"{action} needs {what}: {choices(names)}",
                        field=what, valid_options=offered)
    which = str(which)
    for same in (lambda text: " ".join(text.split()), _loose):
        wanted = same(which)
        hits = [k for k, name in enumerate(names) if name and same(name) == wanted]
        if len(hits) == 1:
            return hits[0]
        if hits:
            plural = "series" if what == "series" else "categories"
            raise ToolError("label_not_found", f"{which!r} names {len(hits)} {plural} "
                            f"({', '.join(map(str, hits))}); give the position instead",
                            field=what, valid_options=offered)
    if which.isdigit():
        if int(which) < len(names):
            return int(which)
        raise ToolError("not_found", f"no {what} {which}; there are {len(names)}{hint}",
                        field=what, valid_options=offered)
    raise ToolError("label_not_found", f"no {what} {which!r}{hint}; give {choices(names)}",
                    field=what, valid_options=offered)


def resolve_series(chart: Any, which: Any, action: str = "this action") -> Any:
    """The series of ``chart`` that ``edit_chart``'s ``series`` names: by name (whitespace
    collapsed; failing that, full-width forms and case ignored too), or -- only when no
    series has that name -- by position from 0.  ``invalid_arguments`` when ``action`` is
    given none, ``label_not_found`` for a name no series has or two share, ``not_found`` for
    a position past the end; each lists the names (``valid_options``)::

        resolve_series(chart, "1", "set_value")   # the series named "1", else series 1
    """
    names = ["" if series.name is None else str(series.name) for series in chart.series]
    return chart.series[_resolve(names, which, "series", action)]


def resolve_category(chart: Any, which: Any, action: str = "this action") -> int:
    """The position of the category of ``chart`` that ``edit_chart``'s ``category`` names,
    found as :func:`resolve_series` finds a series.  Categories are named as a read lists
    them (:func:`category_labels`): dates kept as serials by their ISO
    date, which a refusal then says."""
    hint = ("; its categories are dates, named as YYYY-MM-DD"
            if chart.category_dates is not None else "")
    return _resolve(category_labels(chart), which, "category", action, hint)


def resolve_node(diagram: Any, node: Any, action: str = "this action") -> Any:
    """The node of a SmartArt ``diagram`` that ``edit_smartart``'s ``node`` numbers (depth
    first, from 0): ``invalid_arguments`` when ``action`` is given none, ``not_found`` past
    the end, each with the numbers there are."""
    nodes = diagram.nodes
    numbers = list(range(len(nodes)))
    from ..tools.results import ToolError

    if node is None:
        raise ToolError("invalid_arguments", f"{action} needs node", field="node",
                        valid_options=numbers)
    if isinstance(node, bool) or not isinstance(node, int) or not 0 <= node < len(nodes):
        raise ToolError("not_found", f"no node {node}; there are {len(nodes)}", field="node",
                        valid_options=numbers)
    return nodes[node]


def _either(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


#: ``scale`` keys only an axis of numbers (or dates) takes; the rest suit a category axis too.
_NUMERIC_SCALE = ("min", "max", "major_unit", "minor_unit")


def scale_axis(chart, scale: Mapping[str, Any] | None, *, action: str = "set_axis_scale") -> str:
    """The axis a ``scale`` given without ``axis`` is for: the value axis, when it is the
    one axis of the chart that takes the scale's bounds or units (a bar, column, line or
    area chart's).  Refused (:class:`~.errors.ChartDataError`), naming the axes it could
    be, where more than one could take it -- a scatter's x and y, a date axis, a secondary
    value axis -- or the scale holds only ``reverse`` or ``number_format``, which any
    axis takes::

        scale_axis(chart, {"max": 5})       # 'value'
    """
    from ..xml import find, local_name

    names = chart.axis_names()
    if not names:
        raise ChartDataError(f"{chart.address}: a {chart.chart_type} chart has no axes to scale")
    given = dict(scale or {})
    numeric = any(k in given for k in _NUMERIC_SCALE) or any(
        k in _NUMERIC_SCALE for k in given.get("auto") or [])
    if not numeric:
        raise ChartDataError(f"{action} needs axis: {_either(names)}; reverse and "
                             f"number_format suit either")
    plot_area = find(chart._root(), "c:chart/c:plotArea")
    kinds = [local_name(child) for child in plot_area] if plot_area is not None else []
    if names == ["category", "value"] and kinds.count("valAx") == 1 \
            and "dateAx" not in kinds:
        return "value"
    if "dateAx" in kinds:
        raise ChartDataError(f"{action} needs axis: category (its dates take bounds and units "
                             f"too) or value")
    if names == ["category", "value"]:
        raise ChartDataError(f"{action} needs axis: {chart.address} has a secondary value "
                             f"axis too, which edit_chart does not scale; give value for the "
                             f"primary one")
    raise ChartDataError(f"{action} needs axis: {_either(names)}; both take bounds and units")


def gridline_arguments(gridlines: str) -> dict[str, bool]:
    if gridlines not in _GRIDLINES:
        raise ChartDataError(f"gridlines {gridlines!r}; one of {', '.join(_GRIDLINES)}")
    major, minor = _GRIDLINES[gridlines]
    return {"major": major, "minor": minor}


def apply_style_action(chart, action: str, *, series=None, category=None, axis: str | None = None,
                       scale: Mapping[str, Any] | None = None, gridlines: str | None = None,
                       style: Mapping[str, Any] | None = None, part: str | None = None,
                       labels=None, cells: bool | None = None, position: str | None = None,
                       chart_type: str | None = None, secondary: bool | None = None) -> None:
    """Run one of :data:`STYLE_ACTIONS`.  ``series`` and ``category`` are what the format
    resolved (a series object or position, a category position)."""
    def need(name: str, value: Any) -> Any:
        if value is None:
            raise ChartDataError(f"{action} needs {name}: {_expected(chart, name)}")
        return value

    style = dict(style or {})
    if action == "set_axis_scale":
        scale = need("scale", scale)
        chart.set_axis_scale(axis or scale_axis(chart, scale), **scale_arguments(scale))
    elif action == "set_gridlines":
        chart.set_gridlines(need("axis", axis), **gridline_arguments(need("gridlines", gridlines)))
    elif action in ("format_series", "format_point"):
        unknown = set(style) - set(_SERIES_STYLE)
        if unknown:
            raise ChartDataError(f"style: {sorted(unknown)[0]!r} is not for {action}; give "
                                 f"{', '.join(_SERIES_STYLE)}")
        if not style:
            raise ChartDataError(f"{action} needs style: {', '.join(_SERIES_STYLE)}")
        arguments = {k: (_colour(v) if k in ("fill", "line", "marker_fill") else v)
                     for k, v in style.items()}
        if action == "format_series":
            chart.format_series(need("series", series), **arguments)
        else:
            chart.format_point(need("series", series), need("category", category), **arguments)
    elif action == "set_series_type":
        if chart_type not in ("line", "column"):
            raise ChartDataError("set_series_type needs chart_type line or column")
        chart.set_series_type(need("series", series), chart_type,
                              secondary_axis=bool(secondary))
    elif action == "set_text_style":
        unknown = set(style) - set(_TEXT_STYLE)
        if unknown:
            raise ChartDataError(f"style: {sorted(unknown)[0]!r} is not for set_text_style; "
                                 f"give {', '.join(_TEXT_STYLE)}")
        if not style:
            raise ChartDataError(f"set_text_style needs style: {', '.join(_TEXT_STYLE)}")
        chart.set_text_style(part, **{k: style.get(k, KEEP) for k in _TEXT_STYLE if k in style})
    elif action == "set_point_labels":
        chart.set_point_labels(need("series", series), labels, position=position,
                               cells=bool(cells))
    else:  # pragma: no cover -- the schema's enum
        raise ChartDataError(f"unknown action {action!r}")


def _expected(chart, name: str) -> str:
    """What a missing argument of :func:`apply_style_action` could be, on this chart."""
    if name == "axis":
        names = chart.axis_names()
        return _either(names) if names else f"a {chart.chart_type} chart has no axes"
    if name == "series":
        return choices([series.name for series in chart.series])
    if name == "category":
        return choices(category_labels(chart))
    if name == "scale":
        return "min, max, major_unit, minor_unit, number_format, reverse or auto"
    return _either(list(_GRIDLINES))


#: How ``add``'s ``colors`` colour a series of each kind of chart: what Office's "series
#: colour" is in the chart :func:`~.create.add_chart` makes -- a bar's, area's or bubble's
#: fill, a line's line, a scatter or dot series' markers (its line is off).  A pie's
#: colours are its slices'.
_SERIES_COLOUR = {"line": "line", "radar": "line", "scatter": "marker_fill", "dot": "marker_fill",
                  "pie": None}


def add_style_actions(chart_type: str, data: Any, *, data_labels: bool | None = None,
                      colors: Any = None, style: Mapping[str, Any] | None = None,
                      part: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """``edit_chart``'s ``add`` with its look (round 5, T2), as the separate calls it
    stands for, in the order that gives that look: ``[(action, arguments)]``, the
    arguments as the tool takes them (a series or category by its position from 0, as a
    string).  The data labels come first (labels shown after a text style take their own
    size again), then each series' colour, then the text.

    ``data_labels`` true is ``show_data_labels`` on every series (its format follows the
    values', ``add``'s ``number_format``); ``colors`` one ``format_series`` per colour, in
    the series' order (the rest keep Office's), filling or lining each as its kind is drawn
    (:data:`_SERIES_COLOUR`; a column chart's line series is a line), a pie's one
    ``format_point`` per slice; ``style`` with ``part`` is ``set_text_style``.  Refuses
    (:class:`~.errors.ChartDataError`) a list, colour or style key the separate calls would
    refuse, before anything is made; the rest is refused as those calls refuse it, and the
    handlers make the chart and its look in one undo step, so nothing stays."""
    actions: list[tuple[str, dict[str, Any]]] = []
    entries = list(data or [])
    if data_labels is not None and not isinstance(data_labels, bool):
        raise ChartDataError("data_labels is true or false")
    if data_labels:
        actions.append(("show_data_labels", {}))
    if colors is not None:
        if isinstance(colors, str) or not isinstance(colors, (list, tuple)):
            raise ChartDataError("colors is a list of colours, one per series in order")
        pie = chart_type == "pie"
        if pie:
            points = len((entries[0].get("values") or []) if entries else [])
            if len(colors) > points:
                raise ChartDataError(f"colors: {len(colors)} colours for a pie of {points} "
                                     f"slices; give at most one per slice")
        elif len(colors) > len(entries):
            raise ChartDataError(f"colors: {len(colors)} colours for {len(entries)} series; "
                                 f"give at most one per series, in order")
        for index, colour in enumerate(colors):
            if not isinstance(colour, str) or not colour.strip():
                raise ChartDataError(f"colors[{index}] is a colour, as shapes take them")
            if _colour(colour) is not None:
                try:
                    _fmt.color_element(colour)
                except ValueError as error:
                    raise ChartDataError(f"colors[{index}]: {error}") from None
            if pie:
                actions.append(("format_point", {"series": "0", "category": str(index),
                                                 "style": {"fill": colour}}))
                continue
            line = isinstance(entries[index], Mapping) and entries[index].get("type") == "line"
            key = "line" if line else _SERIES_COLOUR.get(chart_type, "fill")
            actions.append(("format_series", {"series": str(index), "style": {key: colour}}))
    if style is not None:
        unknown = set(style) - set(_TEXT_STYLE)
        if unknown:
            raise ChartDataError(f"style: {sorted(unknown)[0]!r} is not for add, which takes "
                                 f"the text's {', '.join(_TEXT_STYLE)}; colour series with "
                                 f"colors")
        if not style:
            raise ChartDataError(f"style is empty: give {', '.join(_TEXT_STYLE)}")
        actions.append(("set_text_style", {"style": dict(style), **(
            {"part": part} if part is not None else {})}))
    elif part is not None:
        raise ChartDataError("part is the text style's: give style too")
    return actions


def apply_add_style(chart, actions: list[tuple[str, dict[str, Any]]]) -> None:
    """Run :func:`add_style_actions`' calls on the chart ``add`` just made."""
    for action, arguments in actions:
        if action == "show_data_labels":
            chart.set_data_labels(True)
            continue
        given = dict(arguments)
        for key in ("series", "category"):
            if key in given:
                given[key] = int(given[key])
        apply_style_action(chart, action, **given)


def chart_style_json(chart) -> dict[str, Any]:
    """What ``read`` adds about the look: each axis' scale, each series' own colours and
    lines (where it sets any) and point labels (where it has any)."""
    out: dict[str, Any] = {"types": chart.chart_types, "scales": chart.scales}
    looks = {}
    labels = {}
    for series in chart.series:
        look = chart.series_format(series.index)
        if look:
            looks[str(series.name if series.name is not None else series.index)] = look
        own = chart.point_labels(series.index)
        if any(text is not None for text in own):
            labels[str(series.name if series.name is not None else series.index)] = own
    if looks:
        out["series_format"] = looks
    if labels:
        out["point_labels"] = labels
    return out


# -- the data actions, add, SmartArt and the results (review, S10) ------------------------------
#
# What the two agents' handlers did twice, and did differently in places, is one code here:
# a label for add_category comes from ``text``; add_series' and add_category's values may be
# left out (blanks); set_legend without a position puts the legend on the right;
# hide_data_labels takes number_format as show_data_labels does (and refuses it: a format is
# for labels shown); SmartArt's set_text takes "" ; a SmartArt result lists each node's
# number, text and level.

#: ``edit_chart``'s data, title and legend actions: :func:`apply_data_action`.
DATA_ACTIONS = ("set_values", "set_value", "add_category", "remove_category", "rename_category",
                "add_series", "remove_series", "rename_series", "set_title", "set_axis_title",
                "set_legend", "show_data_labels", "hide_data_labels", "set_gap_width")

#: ``edit_smartart``'s actions: :func:`apply_smartart_action`.
SMARTART_ACTIONS = ("set_text", "add_node", "remove_node", "add_child")

#: A legend's ``position`` -> :meth:`~.chart.Chart.set_legend`'s.
LEGEND_POSITIONS = {"right": "r", "left": "l", "top": "t", "bottom": "b"}

#: What ``add`` needs, said when it is missing.
#: (``chart_type``: one of the tool's ``CHART_TYPES``.)
ADD_NEEDS = {"chart_type": "one of <types>",
             "data": "each series as {name, values}, one value per category",
             "categories": "the category labels, in order"}

#: At most this many values (categories times series) in a chart ``add`` makes.
MAX_ADD_VALUES = 10_000


def _missing(chart: Any, action: str, name: str) -> str:
    """What a missing ``name`` of the data action ``action`` could be, on this chart."""
    if name == "axis":
        return " or ".join(chart.axes) or "the chart has no axes"
    if name == "values":
        return f"one number per category ({len(chart.categories)})"
    if name == "value":
        return ("the gap as a whole % of a bar's width, 0-500" if action == "set_gap_width"
                else "the number")
    return {"add_category": "the new category's label", "rename_category": "the new label",
            "add_series": "the new series' name"}.get(action, "the new name")


def need(chart: Any, action: str, name: str, given: Any) -> Any:
    """``given``, or ``invalid_arguments`` (field ``name``) saying what ``action`` needs
    ``name`` to be on ``chart`` (``None`` for ``add``, :data:`ADD_NEEDS`)::

        need(chart, "set_value", "value", None)   # ToolError: set_value needs value: the number
    """
    if given is not None:
        return given
    from ..tools.results import ToolError

    if action == "add":
        from ..tools.shared import CHART_TYPES  # here, not at import: charts stand alone

        expected = ADD_NEEDS.get(name, name).replace("<types>", ", ".join(CHART_TYPES))
    else:
        expected = _missing(chart, action, name)
    raise ToolError("invalid_arguments", f"{action} needs {name}: {expected}", field=name)


def legend_position(position: str | None) -> str | None:
    """``set_legend``'s or ``add``'s ``position`` as :meth:`~.chart.Chart.set_legend` takes
    it: ``None`` for ``"none"`` (no legend), the right for none given; a label position is
    refused (``invalid_arguments``)."""
    where = position or "right"
    if where == "none":
        return None
    if where not in LEGEND_POSITIONS:
        from ..tools.results import ToolError

        raise ToolError("invalid_arguments", f"{where} is a label position; a legend is "
                        "right, left, top, bottom or none", field="position")
    return LEGEND_POSITIONS[where]


def apply_data_action(chart: Any, action: str, *, series: Any = None, category: Any = None,
                      values: Any = None, value: Any = None, text: str | None = None,
                      axis: str | None = None, position: str | None = None,
                      number_format: str | None = None) -> None:
    """Run one of :data:`DATA_ACTIONS` with the tool's arguments as given: a series or
    category is resolved here (:func:`resolve_series`, :func:`resolve_category`), a missing
    argument refused (:func:`need`)."""
    def want(name: str, given: Any) -> Any:
        return need(chart, action, name, given)

    def chosen() -> list[int] | None:
        return None if series is None else [resolve_series(chart, series, action).index]

    if action == "set_values":
        resolve_series(chart, series, action).set_values(want("values", values))
    elif action == "set_value":
        resolve_series(chart, series, action).set_value(
            resolve_category(chart, category, action), want("value", value))
    elif action == "add_category":
        chart.add_category(want("text", text), None if values is None else list(values))
    elif action == "remove_category":
        chart.remove_category(resolve_category(chart, category, action))
    elif action == "rename_category":
        chart.set_category(resolve_category(chart, category, action), want("text", text))
    elif action == "add_series":
        chart.add_series(want("text", text), None if values is None else list(values))
    elif action == "remove_series":
        chart.remove_series(resolve_series(chart, series, action).index)
    elif action == "rename_series":
        resolve_series(chart, series, action).set_name(want("text", text))
    elif action == "set_title":
        chart.set_title(text)
    elif action == "set_axis_title":
        chart.set_axis_title(want("axis", axis), text)
    elif action == "set_legend":
        where = legend_position(position)
        if where is None:
            chart.set_legend(False)
        else:
            chart.set_legend(True, where)
    elif action in ("show_data_labels", "hide_data_labels"):
        chart.set_data_labels(action == "show_data_labels", number_format=number_format,
                              series=chosen())
    elif action == "set_gap_width":
        given = want("value", value)
        if given != int(given):
            from ..tools.results import ToolError

            raise ToolError("invalid_arguments", "a gap width is a whole percentage, 0-500",
                            field="value")
        chart.set_gap_width(int(given))
    else:  # pragma: no cover -- the schema's enum
        raise ChartDataError(f"unknown action {action!r}")


def add_plan(chart_type: Any, categories: Any, data: Any, values: Any = None, *,
             position: str | None = None, axis: str | None = None,
             scale: Mapping[str, Any] | None = None, data_labels: bool | None = None,
             colors: Any = None, style: Mapping[str, Any] | None = None,
             part: str | None = None,
             required: Mapping[str, tuple[Any, str]] | None = None) -> dict[str, Any]:
    """``edit_chart``'s ``add`` arguments checked and read, before anything is made:
    ``{"labels", "legend", "category_order", "scale", "look"}`` -- the categories (a
    scatter's or bubble's x values), what ``add_chart`` takes for the legend and the order,
    the scale :func:`finish_add` applies, and the look (:func:`add_style_actions`).
    ``required`` is what else the format needs, ``{name: (given, what it is)}``, checked
    after ``chart_type`` and ``data`` (a deck's ``box``)."""
    from ..tools.results import ToolError

    chart_type = need(None, "add", "chart_type", chart_type)
    data = need(None, "add", "data", data)
    for name, (given, what) in (required or {}).items():
        if given is None:
            raise ToolError("invalid_arguments", f"add needs {name}: {what}", field=name)
    if chart_type in ("scatter", "bubble"):
        if values is None or categories is not None:
            raise ToolError("invalid_arguments", f"a {chart_type} chart takes its x values in "
                            "values, not categories", field="values")
        labels = list(values)
    else:
        categories = need(None, "add", "categories", categories)
        if values is not None:
            raise ToolError("invalid_arguments", "values are a scatter or bubble chart's x "
                            "values; give each series' values in data", field="values")
        labels = list(categories)
    if len(labels) * max(len(data), 1) > MAX_ADD_VALUES:
        raise ToolError("limit", f"at most {MAX_ADD_VALUES:,} chart values", field="data")
    if position not in (None, "none", *LEGEND_POSITIONS):
        raise ToolError("invalid_arguments", f"{position} is a label position; a legend is "
                        "right, left, top, bottom or none", field="position")
    look = add_style_actions(chart_type, data, data_labels=data_labels, colors=colors,
                             style=style, part=part)
    reverse = bool((scale or {}).get("reverse")) and axis == "category"
    return {"labels": labels,
            "legend": None if position == "none" else (position or "default"),
            "category_order": "reversed" if reverse else "normal",
            "scale": {k: v for k, v in (scale or {}).items() if not (reverse and k == "reverse")},
            "look": look}


def finish_add(chart: Any, chart_type: str, plan: Mapping[str, Any], *,
               axis: str | None = None, gridlines: str | None = None) -> None:
    """Give the chart ``add`` just made from ``plan`` (:func:`add_plan`) its scale, minor
    gridlines and look, inside the same undo step: a scale without ``axis`` is the chart's
    one value axis (refused, and undone, if it has more)."""
    rest = plan["scale"]
    if rest:
        apply_style_action(chart, "set_axis_scale", scale=rest,
                           axis=axis or scale_axis(chart, rest, action="add with scale"))
    if gridlines not in (None, "major", "none"):
        apply_style_action(chart, "set_gridlines", gridlines=gridlines,
                           axis="y" if chart_type in ("scatter", "bubble") else "value")
    apply_add_style(chart, plan["look"])


def chart_categories(chart: Any) -> list:
    """The categories a result lists: ISO dates for dates kept as serials, else the labels
    as they are (numbers stay numbers)."""
    return category_labels(chart) if chart.category_dates is not None else list(chart.categories)


def chart_json(chart: Any, key: str = "address") -> dict[str, Any]:
    """What every ``edit_chart`` result holds: the chart under ``key`` (a deck's
    ``address``, a document's ``id``: :attr:`~.chart.Chart.address`), its type, title,
    categories, series, legend and number formats, its categories' format when they are
    dates, its gap width when it has bars."""
    data: dict[str, Any] = {
        key: chart.address, "type": "dot" if chart.is_dot else chart.chart_type,
        "title": chart.title, "categories": chart_categories(chart),
        "series": [{"name": series.name, "values": list(series.values)}
                   for series in chart.series],
        "legend": chart.has_legend, "number_formats": chart.number_formats}
    if chart.category_dates is not None:
        # Dates kept as serials: listed as ISO dates, and how the chart shows them.
        data["category_format"] = chart.category_format
    if chart.gap_width is not None:
        data["gap_width"] = chart.gap_width
    return data


def chart_read_json(chart: Any, key: str = "address") -> dict[str, Any]:
    """``read``'s result: :func:`chart_json`, each axis' title, the look
    (:func:`chart_style_json`) and what the embedded workbook holds (``"unreadable: ..."``
    when it cannot be read)."""
    data = chart_json(chart, key)
    data["axes"] = {axis: chart.axis_title(axis) for axis in chart.axes}
    data.update(chart_style_json(chart))
    try:
        data["workbook"] = chart.workbook_values()
    except Exception as error:  # noqa: BLE001 -- a chart without a readable workbook
        data["workbook"] = f"unreadable: {error}"
    return data


def apply_smartart_action(diagram: Any, action: str, *, node: Any = None,
                          text: str | None = None) -> None:
    """Run one of :data:`SMARTART_ACTIONS`: ``node`` resolved by :func:`resolve_node` (for
    ``add_child`` the parent); no ``text`` is empty text."""
    chosen = None if action == "add_node" else resolve_node(diagram, node, action)
    if action == "set_text":
        diagram.set_text(chosen, text or "")
    elif action == "add_node":
        diagram.add_node(text or "")
    elif action == "remove_node":
        diagram.remove_node(chosen)
    elif action == "add_child":
        diagram.add_node(text or "", parent=chosen)
    else:  # pragma: no cover -- the schema's enum
        raise ChartDataError(f"unknown action {action!r}")


def smartart_json(diagram: Any) -> dict[str, Any]:
    """What an ``edit_smartart`` result holds: every node, depth first, as ``{n, text,
    level}``."""
    return {"nodes": [{"n": index, "text": node.text, "level": node.level}
                      for index, node in enumerate(diagram.nodes)]}


__all__ = ["ADD_NEEDS", "DATA_ACTIONS", "LEGEND_POSITIONS", "MAX_ADD_VALUES", "SMARTART_ACTIONS",
           "STYLE_ACTIONS", "add_plan", "apply_data_action", "apply_smartart_action",
           "chart_categories", "chart_json", "chart_read_json", "finish_add", "legend_position",
           "need", "smartart_json", "add_style_actions", "apply_add_style", "apply_style_action",
           "chart_style_json", "gridline_arguments", "resolve_category", "resolve_node",
           "resolve_series", "scale_arguments"]
