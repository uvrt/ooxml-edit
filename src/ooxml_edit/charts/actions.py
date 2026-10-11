"""The formatting actions of the shared ``edit_chart`` tool, on a :class:`~.chart.Chart`.

Both format libraries' handlers resolve the chart, the series and the category their own
way, then call :func:`apply_style_action`; what the tool's arguments mean is decided here
once.  Refusals are :class:`~.errors.ChartDataError` (the handlers answer
``invalid_arguments``).
"""

from __future__ import annotations

from typing import Any, Mapping

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
            raise ChartDataError(f"{action} needs {name}")
        return value

    style = dict(style or {})
    if action == "set_axis_scale":
        chart.set_axis_scale(need("axis", axis), **scale_arguments(need("scale", scale)))
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


__all__ = ["STYLE_ACTIONS", "apply_style_action", "chart_style_json", "gridline_arguments",
           "scale_arguments"]
