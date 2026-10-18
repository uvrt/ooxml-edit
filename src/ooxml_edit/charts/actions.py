"""The formatting actions of the shared ``edit_chart`` tool, on a :class:`~.chart.Chart`.

Both format libraries' handlers resolve the chart, the series and the category their own
way, then call :func:`apply_style_action`; what the tool's arguments mean is decided here
once.  Refusals are :class:`~.errors.ChartDataError` (the handlers answer
``invalid_arguments``).
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


__all__ = ["STYLE_ACTIONS", "add_style_actions", "apply_add_style", "apply_style_action",
           "chart_style_json", "gridline_arguments", "scale_arguments"]
